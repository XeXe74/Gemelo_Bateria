"""Activo simulado (móvil + cargador) que avanza de paso en paso.

Es el mismo cálculo que hacía sim/simulador.py de una vez, partido en pasos para que lo
puedan usar tanto el modo por lotes (sim/simulador.py) como el servicio en tiempo real
(servicios/simulador). Así los dos ejecutan exactamente el mismo código (REQ-0200).

Cada paso de 15 minutos tiene dos fases:
    1. inicio_paso(): el comportamiento del usuario (enchufa, desenchufa, se lleva el
       móvil...). Si esa noche se va a dormir y enchufa el móvil, devuelve un
       ContextoDecision: hay que fijar el límite con fijar_limite() antes de seguir.
    2. fin_paso(): la física del paso (carga, gasto, temperatura y degradación).

medida() devuelve lo que publicaría el móvil en el instante del paso: el nivel y la
salud de ese instante y la temperatura del último intervalo, que es la que marcaría un
sensor en ese momento.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .bateria import BateriaSimulada, velocidad_carga
from .generar_uso import DIAS, TEMP_ACTIVIDAD, DatosUso
from .rutina import PASO

# Carga optimizada de iOS: mantiene el 80 % durante la noche y completa la carga en las últimas horas
# antes de levantarse. El simulador conoce la hora real de levantarse, igual que iOS la aprende de la rutina.
HORAS_FINAL_OPTIMIZADA = 2


@dataclass
class ContextoDecision:
    """Lo que ve una política en el momento de enchufar por la noche."""
    momento: datetime
    nivel: float
    soh_reportado: int
    manana: date
    calendario_manana: list[dict]


class Activo:
    def __init__(self, datos: DatosUso, seed_unidad: int = 1234,
                 parametros: dict | None = None):
        self.datos = datos
        cfg = datos.cfg
        self.cfg = cfg
        self.rng_unidad = np.random.default_rng(seed_unidad)
        self.carga_cfg, self.consumos = cfg["carga"], cfg["consumos"]
        uso = datos.uso
        self.n = len(uso)
        self.t0 = uso["momento"].iloc[0].to_pydatetime()
        self.dt_h = PASO / timedelta(hours=1)

        # --- Lo que viene del Excel ------------------------------------------------
        self.momentos = [t.to_pydatetime() for t in uso["momento"]]
        self.actividad = uso["actividad"].to_numpy().copy()
        self.titulo = uso["titulo"].to_numpy().copy()
        self.en_casa = uso["en_casa"].astype(bool).to_numpy().copy()
        self.en_cal = uso["en_calendario"].astype(bool).to_numpy().copy()
        self.dia_logico = uso["dia"].to_numpy()
        self.gasto = uso["gasto_pct"].to_numpy(dtype=float).copy()
        self.temp_base = uso["temperatura_c"].to_numpy(dtype=float).copy()

        self.calendario = datos.calendario.copy()
        self.calendario["id"] = None  # solo lo tienen los eventos añadidos desde el panel
        self.previa_necesario = np.zeros(self.n)
        for bl in pd.concat([datos.calendario, datos.imprevistos]).itertuples():
            self._marcar_previa(bl.inicio.to_pydatetime(), bl.fin.to_pydatetime(), bl.actividad)

        # Cargas fuera de casa, si lo necesita: en el coche y en clase.
        self.coche_cfg, self.clase_cfg = self.carga_cfg["coche"], self.carga_cfg["clase"]
        self.coche_acts = set(self.coche_cfg["actividades"])
        self.clase_acts = set(self.clase_cfg["actividades"])
        self.factor_velocidad = {"coche": self.coche_cfg["factor_velocidad"],
                                 "clase": self.clase_cfg["factor_velocidad"]}

        # --- Estado ----------------------------------------------------------------
        self.soh0 = cfg["movil"]["salud_inicial"]
        self.bat = BateriaSimulada(self.soh0, cfg["movil"]["edad_dias_inicial"], self.rng_unidad, parametros)
        self.nivel = float(cfg["simulacion"]["nivel_inicial"])
        self.tope = 100
        self.optimizada = False  # carga optimizada de iOS en la carga nocturna
        self.k_despertar: int | None = None
        self.motivo: str | None = None  # None, "noche", "emergencia", "previa", "coche", "clase"
        self.soh_rep = int(round(100 * self.bat.soh))
        self.temp = float(self.temp_base[0])
        self.incidente_dia: set[date] = set()
        self.k = 0
        self._pendiente = False  # hay que fijar el límite antes de seguir
        self.decisiones: list[dict] = []
        self.filas: list[dict] = []

    # --- Utilidades ------------------------------------------------------------------

    def _idx(self, t: datetime) -> int:
        return int((t - self.t0) / PASO)

    def _marcar_previa(self, inicio: datetime, fin: datetime, actividad: str) -> None:
        """Antes de una salida larga, el usuario carga si no le llega para la salida + margen.
        Lo sabe él aunque no esté en el calendario, así que cuenta también los imprevistos."""
        x = self._idx(inicio)
        dur_h = (fin - inicio) / timedelta(hours=1)
        c = self.carga_cfg
        if 0 < x <= self.n and not self.consumos[actividad]["en_casa"] and dur_h >= c["previa_salida_min_h"]:
            necesario = min(95.0, self.consumos[actividad]["media"] * dur_h + c["margen_salida"])
            self.previa_necesario[max(0, x - 4):x] = necesario  # la hora anterior

    @property
    def terminado(self) -> bool:
        return self.k >= self.n

    @property
    def instante(self) -> datetime:
        """Instante del paso actual, en hora peninsular (sin zona)."""
        return self.momentos[self.k] if self.k < self.n else self.momentos[-1] + PASO

    def calendario_de(self, fecha: date) -> list[dict]:
        cal = self.calendario[self.calendario["fecha"] == fecha]
        return [{"inicio": b.inicio.to_pydatetime(), "fin": b.fin.to_pydatetime(),
                 "titulo": b.titulo, "actividad": b.actividad, "id": getattr(b, "id", None)} for b in cal.itertuples()]

    def medida(self) -> dict:
        return {"nivel": round(self.nivel, 2), "enchufado": int(self.motivo is not None),
                "modo_dormir": int(self.actividad[min(self.k, self.n - 1)] == "dormir"),
                "temperatura": round(self.temp, 1), "salud": self.soh_rep}

    # --- Fase 1: comportamiento del usuario -----------------------------------------

    def inicio_paso(self) -> ContextoDecision | None:
        k = self.k
        t = self.momentos[k]
        act, casa = self.actividad[k], bool(self.en_casa[k])

        # SoH que muestra Ajustes: entero, ruidoso, se actualiza una vez al día.
        if t.hour == 12 and t.minute == 0:
            self.soh_rep = int(round(100 * self.bat.soh + self.rng_unidad.normal(0, 0.3)))

        if self.motivo in ("noche", "emergencia", "previa") and not casa:
            self.motivo = None  # se lleva el móvil
        if self.motivo == "coche" and act not in self.coche_acts:
            self.motivo = None  # llega y lo desenchufa del coche
        if self.motivo == "clase" and (act not in self.clase_acts or self.nivel >= self.clase_cfg["hasta"]):
            self.motivo = None  # acaba la clase o ya tiene suficiente
        if act == "dormir" and casa and self.motivo != "noche":
            self.motivo = "noche"
            manana = (t + timedelta(hours=8)).date()
            self._pendiente = True
            return ContextoDecision(t, self.nivel, self.soh_rep, manana, self.calendario_de(manana))
        if self.motivo == "noche" and act != "dormir":
            self.motivo = None  # se levanta y desenchufa
        self._resto_comportamiento()
        return None

    def fijar_limite(self, tope: int, optimizada: bool = False, **extra) -> None:
        """Aplica el límite de la carga nocturna y completa el comportamiento del paso.
        Con optimizada=True, el límite es el 100 % pero se mantiene el 80 % hasta poco antes de levantarse."""
        self.tope = int(tope)
        self.optimizada = optimizada
        if optimizada:
            j = self.k
            while j < self.n and self.actividad[j] == "dormir":
                j += 1
            self.k_despertar = j
            extra = extra | {"optimizada": True}
        t = self.momentos[self.k]
        self.decisiones.append({"momento": t, "para_dia": (t + timedelta(hours=8)).date(),
                                "nivel_al_enchufar": self.nivel, "tope": self.tope} | extra)
        self._pendiente = False
        self._resto_comportamiento()

    def _resto_comportamiento(self) -> None:
        k = self.k
        act, casa = self.actividad[k], bool(self.en_casa[k])
        c = self.carga_cfg
        if self.motivo is None and casa and act != "dormir":
            if self.nivel < c["emergencia_umbral"]:
                self.motivo = "emergencia"
            elif self.nivel < self.previa_necesario[k]:
                self.motivo = "previa"
        if self.motivo == "emergencia" and self.nivel >= min(c["emergencia_hasta"], self.tope):
            self.motivo = None
        # Fuera de casa: si lo necesita, en el coche o en clase.
        if self.motivo is None and not casa:
            if act in self.coche_acts and self.nivel < min(self.coche_cfg["umbral"], self.tope):
                self.motivo = "coche"
            elif act in self.clase_acts and self.nivel < self.clase_cfg["umbral"]:
                self.motivo = "clase"

    # --- Fase 2: física del paso -------------------------------------------------------

    def fin_paso(self) -> dict:
        if self._pendiente:
            raise RuntimeError("Falta fijar el límite de la carga nocturna (fijar_limite)")
        k = self.k
        t = self.momentos[k]
        act, casa, dia = self.actividad[k], bool(self.en_casa[k]), self.dia_logico[k]
        motivo, dt_h = self.motivo, self.dt_h
        nivel = antes = self.nivel
        temp = self.temp_base[k]
        # El gasto del Excel está en % de la batería inicial; con menos salud, gasta más %.
        # Si el móvil está apagado (nivel 0) y no carga, no se usa.
        consumo = self.gasto[k] * (self.soh0 / self.bat.soh) if (motivo or nivel > 0) else 0.0
        if motivo:
            # Enchufado: sube lo que mete el cargador menos lo que se gasta. Al llegar al
            # tope, el cargador alimenta el móvil y el nivel se queda en el tope.
            sub = dt_h / 3
            factor = self.factor_velocidad.get(motivo, 1.0)
            tope = self.tope
            if (self.optimizada and motivo == "noche" and self.k_despertar is not None
                    and (self.k_despertar - k) * dt_h > HORAS_FINAL_OPTIMIZADA):
                tope = min(tope, 80)
            for _ in range(3):
                if nivel < tope:
                    nivel = min(float(tope), nivel + velocidad_carga(nivel) * factor * sub - consumo / 3)
                    nivel = max(0.0, nivel)
            temp += 6 if antes < 80 else (2 if nivel < tope else 1)
        else:
            nivel = max(0.0, nivel - consumo)

        self.bat.paso(antes, nivel, temp, dt_h)
        self.nivel, self.temp = nivel, float(temp)

        incidente = False
        if not casa and nivel < self.carga_cfg["incidente_umbral"] and dia not in self.incidente_dia:
            self.incidente_dia.add(dia)
            incidente = True

        fila = {
            "momento": t, "dia_logico": dia, "dia_semana": dia.weekday(), "actividad": act,
            "titulo": self.titulo[k], "en_casa": casa, "en_calendario": bool(self.en_cal[k]),
            "nivel": round(nivel, 2), "enchufado": motivo is not None, "motivo_carga": motivo or "",
            "tope": self.tope, "consumo_pct_h": round(consumo / dt_h, 2), "temperatura": round(temp, 1),
            "soh_real": round(100 * self.bat.soh, 4), "soh_reportado": self.soh_rep, "incidente": incidente,
        }
        self.filas.append(fila)
        self.k += 1
        return fila

    # --- Eventos añadidos desde el panel -----------------------------------------------

    def anadir_evento(self, inicio: datetime, fin: datetime, actividad: str, titulo: str,
                      ident: str | None = None) -> date:
        """Añade un evento al calendario y sustituye esas horas del uso por la actividad.

        El gasto de esos pasos es la media de la actividad, sin ruido, y la temperatura
        la típica de la actividad. Devuelve el día lógico del evento.
        """
        x, y = max(self._idx(inicio), self.k), min(self._idx(fin), self.n)
        if x >= y:
            raise ValueError("El evento no cae en la parte del año que queda por simular")
        media = self.consumos[actividad]["media"]
        self.actividad[x:y] = actividad
        self.titulo[x:y] = titulo
        self.en_casa[x:y] = self.consumos[actividad]["en_casa"]
        self.en_cal[x:y] = True
        self.gasto[x:y] = media * self.dt_h
        self.temp_base[x:y] = TEMP_ACTIVIDAD[actividad]
        self._marcar_previa(inicio, fin, actividad)
        fecha = self.dia_logico[x]
        if "id" not in self.calendario.columns:  # puntos de guardado anteriores al 27/11/2026
            self.calendario["id"] = None
        nuevo = pd.DataFrame([{"fecha": fecha, "dia_semana": DIAS[fecha.weekday()],
                               "inicio": pd.Timestamp(inicio), "fin": pd.Timestamp(fin),
                               "titulo": titulo, "actividad": actividad, "id": ident}])
        self.calendario = pd.concat([self.calendario, nuevo[self.calendario.columns]],
                                    ignore_index=True).sort_values("inicio", ignore_index=True)
        return fecha

    # --- Resultado ---------------------------------------------------------------------

    def ocultos(self) -> dict:
        b = self.bat
        return b.ocultos() | {"ciclos_eq": b.ciclos_eq, "perdida_cal": b.perdida_cal, "perdida_cyc": b.perdida_cyc}
