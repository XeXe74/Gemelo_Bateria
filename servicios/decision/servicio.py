"""Servicio de decisión (DEC): elige el límite de carga de cada noche (GDB-DIS, apartado 5.2).

    - A partir de las 18:00 del día simulado publica la previsión de la decisión de esa noche
      y la hora estimada de corte, y la recalcula si cambia el calendario (REQ-0110).
    - Cuando el móvil se enchufa para dormir, decide el límite y publica la orden en
      gemelo/CHARGER1/limit/command y la recomendación con su explicación.
Mientras no existan los modelos de uso y de decisión, decide con la regla provisional de
gemelo/decision.py, la misma que usa la simulación por lotes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from gemelo import decision as D
from gemelo import senales as S
from gemelo.bus import Servicio

ZONA = ZoneInfo("Europe/Madrid")
HORA_PREVISION = 18
FIN_NOCHE = 7
LECTURAS = (S.LEVEL, S.PLUGGED, S.SLEEP, S.SOH, S.EVENTS_TOMORROW, S.FEEDBACK, S.RISK)


def local(ts: str) -> datetime:
    return S.leer_iso(ts).astimezone(ZONA).replace(tzinfo=None)


def fecha_calendario(t: datetime) -> date:
    """Día al que se refiere el calendario «de mañana» en el instante t (GDB-IDS-CAL)."""
    return t.date() if t.hour < FIN_NOCHE else t.date() + timedelta(days=1)


class Decisor:
    def __init__(self):
        self.s = Servicio("decision", [S.tema(n) for n in LECTURAS])
        for n in LECTURAS:
            self.s.al_recibir(S.tema(n), self.recibir)
        self.ultimo: dict[str, dict] = {}
        self.evaluado: str | None = None  # ts de la última medida evaluada
        self.dormido_antes: int | None = None  # None hasta recibir la primera medida
        self.prevision_para: tuple | None = None  # (día, ts del calendario) de la última previsión

    def recibir(self, _tema: str, carga: bytes) -> None:
        b = S.leer_senal(carga)
        if b is None:
            return
        self.ultimo[b["t"]] = b
        if b["t"] == S.EVENTS_TOMORROW:
            self.prevision()
        self.evaluar()

    def _actual(self, nombre: str) -> dict | None:
        b = self.ultimo.get(nombre)
        return b if b and b["q"] == S.Q_OK and b["k"] in (S.K_OK, S.K_INCOMPATIBLE) else None

    def eventos_manana(self) -> tuple[list[dict], str]:
        cal = self.ultimo.get(S.EVENTS_TOMORROW)
        if not cal or cal["k"] == S.K_NO_INICIALIZADO or not isinstance(cal["v"], list):
            return [], "sin calendario"
        eventos = [{"inicio": local(e["inicio"]), "fin": local(e["fin"]), "titulo": e.get("titulo"),
                    "actividad": e["actividad"]} for e in cal["v"]]
        nota = "" if cal["q"] == S.Q_OK and cal["k"] == S.K_OK else f"calendario con Q = {cal['q']} y K = {cal['k']}"
        return eventos, nota

    # --- Decisión nocturna ----------------------------------------------------------------

    def evaluar(self) -> None:
        nivel, enchufado, dormir = (self._actual(n) for n in (S.LEVEL, S.PLUGGED, S.SLEEP))
        if not (nivel and enchufado and dormir):
            return
        ts = nivel["ts"]
        if enchufado["ts"] != ts or dormir["ts"] != ts or ts == self.evaluado:
            return  # todavía no han llegado las tres señales de la misma medida
        self.evaluado = ts
        # Conexión nocturna: el móvil pasa al modo Dormir enchufado. Tras un arranque del servicio, la
        # primera medida solo sirve para conocer el estado; si el móvil ya dormía, esa noche se aplica el
        # límite por defecto del cargador.
        conexion_nocturna = dormir["v"] == 1 and enchufado["v"] == 1 and self.dormido_antes == 0
        self.dormido_antes = dormir["v"]
        t = local(ts)
        if t.hour >= HORA_PREVISION or t.hour < FIN_NOCHE:
            self.prevision(nivel_actual=nivel["v"], t=t)
        if conexion_nocturna:
            self.decidir(nivel["v"], t, ts)

    def decidir(self, nivel: float, t: datetime, ts: str) -> None:
        eventos, nota = self.eventos_manana()
        d = D.decidir(eventos)
        self.s.publicar(S.tema(S.COMMAND), S.senal(S.COMMAND, d.limite, ts=ts))
        corte = D.hora_corte(nivel, d.limite, t)
        self.publicar_recomendacion("decision", fecha_calendario(t), d, ts, nivel, corte, nota)
        self.s.registro("info", f"Decisión para el {fecha_calendario(t):%d/%m/%Y}: límite del {d.limite} %")

    # --- Previsión desde las 18:00 ------------------------------------------------------------

    def prevision(self, nivel_actual: float | None = None, t: datetime | None = None) -> None:
        cal = self.ultimo.get(S.EVENTS_TOMORROW)
        nivel = self._actual(S.LEVEL)
        if nivel is None or cal is None:
            return
        t = t or local(nivel["ts"])
        if not (t.hour >= HORA_PREVISION or t.hour < FIN_NOCHE) or self.dormido_antes:
            return
        dia = fecha_calendario(t)
        clave = (dia, cal["ts"])
        if clave == self.prevision_para:
            return  # ya publicada con este calendario
        self.prevision_para = clave
        eventos, nota = self.eventos_manana()
        d = D.decidir(eventos)
        # Se supone que enchufa a la 1:00 con lo que quede tras gastar como en casa hasta entonces.
        enchufe = datetime.combine(dia, datetime.min.time()) + timedelta(hours=1)
        horas = max(0.0, (enchufe - t) / timedelta(hours=1))
        nivel_enchufe = max(5.0, (nivel_actual if nivel_actual is not None else nivel["v"]) - D.CONSUMO_CASA_H * horas)
        corte = D.hora_corte(nivel_enchufe, d.limite, enchufe)
        self.publicar_recomendacion("prevision", dia, d, nivel["ts"], nivel_enchufe, corte, nota)

    def publicar_recomendacion(self, tipo: str, dia: date, d: D.Decision, ts: str, nivel: float,
                               corte: datetime, nota: str) -> None:
        soh = self.ultimo.get(S.SOH, {}).get("v")
        v = {
            "tipo": tipo, "para_dia": dia.isoformat(), "limite": d.limite,
            "consumo_previsto": round(d.consumo_previsto, 1),
            "nivel_al_enchufar": round(nivel, 1), "hora_corte_estimada": S.iso(corte.replace(tzinfo=ZONA)),
            "explicacion": d.explicacion() + (f" Nota: {nota}." if nota else ""),
            "datos": {"eventos": d.detalle, "salud": soh, "reserva": D.RESERVA},
            "modelo": D.VERSION,
        }
        self.s.publicar(S.tema(S.RECOMMENDATION), S.senal(S.RECOMMENDATION, v, ts=ts), retener=True)

    def ejecutar(self) -> None:
        self.s.ejecutar()


if __name__ == "__main__":
    Decisor().ejecutar()
