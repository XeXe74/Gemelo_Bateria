"""Regla de decisión provisional del gemelo mínimo.

Sirve para cerrar el lazo de extremo a extremo mientras no existan los modelos de uso
(MOD-001) y de decisión (MOD-003), que la sustituirán cuando tengan su ficha. La usan el
servicio de decisión y la simulación por lotes, de forma que los dos deciden igual.

La regla estima el consumo de mañana con un día base en casa y le suma lo que gastan de
más los eventos del calendario, con los consumos que contó el usuario (GDB-CONOPS). Después
elige el límite más bajo que deja una reserva al final del día, o el 100 % si ninguno la deja.
No ve el fichero de uso ni los imprevistos: solo el calendario, como el gemelo real.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

VERSION = "regla-provisional 0.1"
LIMITES = (80, 85, 90, 95, 100)

CONSUMO_BASE_DIA = 45.0  # % en un día entero en casa (unas 15,5 h despierto a ~2,8 %/h)
CONSUMO_CASA_H = 2.8
CONSUMO_H = {  # % por hora fuera de casa, según el usuario y las capturas de Ajustes
    "clase": 2.5, "clase_datos": 11.25, "carplay": 18.75, "gimnasio": 4.0, "social": 10.0,
    "cena": 7.0, "cine": 1.0, "noche_fuera": 9.0, "salida_corta": 6.0, "dia_fuera": 7.0, "casa": CONSUMO_CASA_H,
}
RESERVA = 20.0  # % que se quiere conservar al final del día
CONSUMO_DORMIR_H = 1.2


@dataclass
class Decision:
    limite: int
    consumo_previsto: float
    detalle: list[str]

    def explicacion(self) -> str:
        eventos = "; ".join(self.detalle) if self.detalle else "ningún evento en el calendario"
        if self.limite == 100 and self.consumo_previsto + RESERVA > 100:
            motivo = "ningún límite deja la reserva, por lo que se carga al 100 %"
        else:
            motivo = f"es el límite más bajo que deja al menos un {RESERVA:.0f} % al final del día"
        return (f"Consumo previsto de mañana: {self.consumo_previsto:.0f} % ({eventos}). "
                f"Límite del {self.limite} %, ya que {motivo}.")


def decidir(eventos: list[dict]) -> Decision:
    """eventos: lista con inicio y fin (datetime) y actividad, como el calendario de mañana."""
    consumo, detalle = CONSUMO_BASE_DIA, []
    for e in eventos:
        horas = (e["fin"] - e["inicio"]) / timedelta(hours=1)
        extra = (CONSUMO_H.get(e["actividad"], 8.0) - CONSUMO_CASA_H) * horas
        consumo += extra
        detalle.append(f"{e.get('titulo') or e['actividad']}, {horas:.1f} h, {extra:+.0f} %".replace(".", ","))
    for limite in LIMITES:
        if limite - consumo >= RESERVA:
            return Decision(limite, consumo, detalle)
    return Decision(100, consumo, detalle)


def velocidad_carga(nivel: float) -> float:
    """% por hora con el cargador de 20 W (dato del fabricante, curva simplificada)."""
    if nivel < 50:
        return 100.0
    if nivel < 80:
        return 45.0
    if nivel < 95:
        return 20.0
    return 8.0


def hora_corte(nivel: float, limite: int, enchufe: datetime) -> datetime:
    """Instante estimado en que el cargador alcanza el límite y corta la carga."""
    t, paso = enchufe, timedelta(minutes=5)
    for _ in range(24 * 12):
        if nivel >= limite:
            return t
        nivel += (velocidad_carga(nivel) - CONSUMO_DORMIR_H) * paso / timedelta(hours=1)
        t += paso
    return t
