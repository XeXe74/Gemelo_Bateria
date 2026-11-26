"""Paso 1: generador de uso -> Excel fijo con un año inventado del usuario.

Guarda solo lo que NO depende de las decisiones de carga: qué hace el usuario cada
15 minutos, cuánto gasta, qué apunta en el calendario y qué imprevistos tiene. El
nivel de batería, la salud y las recargas NO van aquí: los calcula el paso 2
(sim/simulador.py), porque dependen de hasta dónde se cargó cada noche.

Hojas del Excel:
    Resumen: una fila por día, para revisar el año de un vistazo
    Uso: una fila cada 15 min: actividad, gasto y temperatura
    Calendario: lo que ve el gemelo
    Imprevistos: lo que el gemelo NO ve
    Info: semilla y configuración usadas (para poder repetirlo)

Ejemplos (desde la carpeta Gemelo_Bateria):
    .venv/bin/python -m sim.generar_uso  # año principal (semilla 0)
    .venv/bin/python -m sim.generar_uso --semillas 0-9  # 10 años para la evaluación
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from openpyxl.styles import Alignment, Font, PatternFill

from .rutina import PASO, generar_planes

RAIZ = Path(__file__).resolve().parent.parent
CARPETA_USO = RAIZ / "data" / "uso"
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

TEMP_ACTIVIDAD = {  # temperatura típica de la batería por actividad, sin cargar (°C)
    "dormir": 23, "casa": 24, "clase": 27, "clase_datos": 30, "carplay": 31, "gimnasio": 28, "social": 27,
    "cena": 26, "cine": 25, "noche_fuera": 27, "salida_corta": 26, "dia_fuera": 29,
}

# Colores de la propuesta v2.
AZUL, VERDE, NARANJA = "1E3A5F", "2C7A7B", "E07A2F"
COLOR_HOJA = {"Resumen": AZUL, "Uso": AZUL, "Calendario": VERDE, "Imprevistos": NARANJA, "Info": "8A94A3"}


def ruta_uso(semilla: int) -> Path:
    return CARPETA_USO / f"uso_s{semilla}.xlsx"


# --- Generación ---------------------------------------------------------------------

def generar_uso(cfg: dict, n_dias: int, semilla: int) -> dict[str, pd.DataFrame]:
    """Inventa n_dias de vida del usuario y devuelve las hojas del Excel."""
    rng = np.random.default_rng(semilla)
    consumos = cfg["consumos"]
    fecha0 = date.fromisoformat(cfg["simulacion"]["fecha_inicio"])
    # Un día de más para que la última noche también tenga el calendario de «mañana».
    planes = generar_planes(cfg, fecha0, n_dias + 1, rng)
    t0 = datetime.combine(fecha0, time())
    n = int(timedelta(days=n_dias) / PASO)
    dt_h = PASO / timedelta(hours=1)

    def idx(t: datetime) -> int:
        return int((t - t0) / PASO)

    # Qué hace en cada paso de 15 min.
    actividad = np.array(["dormir"] * n, dtype=object)
    titulo = np.array([""] * n, dtype=object)
    en_cal = np.zeros(n, dtype=bool)
    # Día «lógico»: de levantarse a levantarse (las 2:00 de fiesta cuentan como el día anterior).
    dia = np.array([fecha0] * n, dtype=object)
    for i, p in enumerate(planes):
        a, b = max(0, idx(p.despertar)), min(n, idx(p.acostarse))
        actividad[a:b] = "casa"
        fin_dia = idx(planes[i + 1].despertar) if i + 1 < len(planes) else n
        dia[max(0, a):min(n, fin_dia)] = p.fecha
        for bl in p.bloques:
            x, y = max(0, idx(bl.inicio)), min(n, idx(bl.fin))
            actividad[x:y] = bl.actividad
            titulo[x:y] = bl.titulo
            en_cal[x:y] = bl.en_calendario
    en_casa = np.array([consumos[a]["en_casa"] for a in actividad])

    # Gasto: media de la actividad × lo intenso que es el día × ruido de cada paso.
    # En % de la batería tal y como está al empezar (salud inicial); el paso 2 lo corrige
    # cuando la batería se degrada.
    mult_dia = {p.fecha: float(np.exp(rng.normal(0, cfg["variabilidad_diaria"]))) for p in planes}
    ruido = np.maximum(0.0, 1 + rng.normal(0, cfg["ruido_paso"], n))
    # Algunas actividades varían más de un día a otro (p. ej. la clase compartiendo datos).
    con_var = [a for a, c in consumos.items() if c.get("variabilidad")]
    mult_act = {(p.fecha, a): float(np.exp(rng.normal(0, consumos[a]["variabilidad"])))
                for p in planes for a in con_var}
    media = np.array([consumos[a]["media"] * mult_act.get((d, a), 1.0) for a, d in zip(actividad, dia)])
    gasto = media * np.array([mult_dia[d] for d in dia]) * ruido * dt_h

    # Temperatura sin cargar: actividad + estación + ruido. El calor de cargar lo suma el paso 2.
    momentos = [t0 + k * PASO for k in range(n)]
    estacion = np.array([3 * math.sin(2 * math.pi * (t.timetuple().tm_yday - 110) / 365) for t in momentos])
    temperatura = np.array([TEMP_ACTIVIDAD[a] for a in actividad]) + estacion + rng.normal(0, 0.5, n)

    uso = pd.DataFrame({
        "momento": momentos, "dia": dia, "dia_semana": [DIAS[d.weekday()] for d in dia],
        "actividad": actividad, "titulo": titulo, "en_casa": en_casa, "en_calendario": en_cal,
        "gasto_pct": gasto.round(3), "temperatura_c": temperatura.round(1),
    })

    def bloques(visibles: bool) -> pd.DataFrame:
        return pd.DataFrame([
            {"fecha": p.fecha, "dia_semana": DIAS[p.fecha.weekday()], "inicio": b.inicio,
             "fin": b.fin, "titulo": b.titulo, "actividad": b.actividad}
            for p in planes for b in p.bloques if b.en_calendario == visibles
        ], columns=["fecha", "dia_semana", "inicio", "fin", "titulo", "actividad"])

    gasto_dia = uso.groupby("dia")["gasto_pct"].sum()
    resumen = pd.DataFrame([{
        "fecha": p.fecha, "dia_semana": DIAS[p.fecha.weekday()],
        "despertar": p.despertar.strftime("%H:%M"), "acostarse": p.acostarse.strftime("%H:%M"),
        "eventos": ", ".join(b.titulo for b in p.bloques if b.en_calendario),
        "imprevistos": ", ".join(b.titulo for b in p.bloques if not b.en_calendario),
        "gasto_total_pct": round(float(gasto_dia.get(p.fecha, 0.0)), 1),
    } for p in planes[:n_dias]])

    info = pd.DataFrame([
        ("semilla", semilla),
        ("dias", n_dias),
        ("fecha_inicio", fecha0.isoformat()),
        ("fecha_fin", (fecha0 + timedelta(days=n_dias - 1)).isoformat()),
        ("paso_min", int(PASO / timedelta(minutes=1))),
        ("salud_referencia_gasto", cfg["movil"]["salud_inicial"]),
        ("generado", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("nota", "Calendario e Imprevistos incluyen un día más para que la última noche tenga «mañana»."),
        ("configuracion", yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)),
    ], columns=["clave", "valor"])

    return {"Resumen": resumen, "Uso": uso, "Calendario": bloques(True),
            "Imprevistos": bloques(False), "Info": info}


# --- Excel ---------------------------------------------------------------------------

ANCHOS = {"momento": 17, "inicio": 17, "fin": 17, "fecha": 11, "dia": 11, "dia_semana": 6,
          "titulo": 18, "actividad": 13, "eventos": 40, "imprevistos": 28, "clave": 24, "valor": 60}


def escribir_excel(hojas: dict[str, pd.DataFrame], ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(ruta, engine="openpyxl", datetime_format="dd/mm/yyyy hh:mm",
                        date_format="dd/mm/yyyy") as w:
        for nombre, df in hojas.items():
            df.to_excel(w, sheet_name=nombre, index=False)
            ws = w.sheets[nombre]
            ws.sheet_properties.tabColor = COLOR_HOJA[nombre]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for celda in ws[1]:
                celda.font = Font(bold=True, color="FFFFFF")
                celda.fill = PatternFill("solid", fgColor=AZUL)
                celda.alignment = Alignment(horizontal="center")
            for i, col in enumerate(df.columns):
                ws.column_dimensions[ws.cell(1, i + 1).column_letter].width = ANCHOS.get(col, 13)


# --- Lectura (lo usa el paso 2) ------------------------------------------------------

@dataclass
class DatosUso:
    uso: pd.DataFrame
    calendario: pd.DataFrame
    imprevistos: pd.DataFrame
    resumen: pd.DataFrame
    cfg: dict
    semilla: int

    @property
    def n_dias(self) -> int:
        return len(self.resumen)

    def recortar(self, n_dias: int) -> "DatosUso":
        """Se queda con los primeros n_dias (útil para pruebas cortas)."""
        if n_dias >= self.n_dias:
            return self
        resumen = self.resumen.iloc[:n_dias].copy()
        ultimo = resumen["fecha"].iloc[-1] + timedelta(days=1)  # +1: el «mañana» de la última noche
        n = int(timedelta(days=n_dias) / PASO)
        return DatosUso(self.uso.iloc[:n].copy(), self.calendario[self.calendario["fecha"] <= ultimo],
                        self.imprevistos[self.imprevistos["fecha"] <= ultimo], resumen, self.cfg, self.semilla)


def leer_uso(ruta: Path) -> DatosUso:
    hojas = pd.read_excel(ruta, sheet_name=None, engine="openpyxl")
    info = dict(zip(hojas["Info"]["clave"], hojas["Info"]["valor"]))
    uso = hojas["Uso"]
    uso["momento"] = pd.to_datetime(uso["momento"])
    uso["dia"] = pd.to_datetime(uso["dia"]).dt.date
    uso["titulo"] = uso["titulo"].fillna("").astype(str)
    for nombre in ("Calendario", "Imprevistos"):
        df = hojas[nombre]
        df["fecha"] = pd.to_datetime(df["fecha"]).dt.date
        df["inicio"] = pd.to_datetime(df["inicio"])
        df["fin"] = pd.to_datetime(df["fin"])
    resumen = hojas["Resumen"]
    resumen["fecha"] = pd.to_datetime(resumen["fecha"]).dt.date
    for col in ("eventos", "imprevistos"):
        resumen[col] = resumen[col].fillna("").astype(str)
    return DatosUso(uso, hojas["Calendario"], hojas["Imprevistos"], resumen,
                    yaml.safe_load(info["configuracion"]), int(info["semilla"]))


# --- CLI -----------------------------------------------------------------------------

def _semillas(texto: str) -> list[int]:
    """«0», «0-9» o «0,3,5»."""
    out: list[int] = []
    for parte in texto.split(","):
        if "-" in parte:
            a, b = parte.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(parte))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Paso 1: genera el Excel de uso (un año inventado)")
    ap.add_argument("--dias", type=int, default=365)
    ap.add_argument("--semillas", default="0", help="«0», «0-9» o «0,3,5»; un Excel por semilla")
    ap.add_argument("--config", default=str(RAIZ / "config" / "rutina.yaml"))
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    for s in _semillas(args.semillas):
        hojas = generar_uso(cfg, args.dias, s)
        ruta = ruta_uso(s)
        escribir_excel(hojas, ruta)
        r = hojas["Resumen"]
        print(f"Guardado {ruta.relative_to(RAIZ)} · {args.dias} días · "
              f"{len(hojas['Calendario'])} eventos en el calendario, "
              f"{len(hojas['Imprevistos'])} imprevistos · gasto medio {r['gasto_total_pct'].mean():.0f} %/día")


if __name__ == "__main__":
    main()
