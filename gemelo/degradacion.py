"""Leyes de envejecimiento del modelo de degradación (MOD-002), con los parámetros de la calibración.

    - Calendario: pérdida = v(T, nivel) · t^z, con una tabla de velocidades por temperatura y nivel de carga
      ajustada con los ensayos de WMG (AK y Widanage, 2024). Entre temperaturas se interpola con Arrhenius.
      Con condiciones variables se aplica el método del tiempo equivalente: el tiempo que, en las
      condiciones actuales, habría producido la pérdida acumulada.
    - Ciclado: cada variación del nivel desgasta según la zona en la que ocurre, con un peso por zona sacado
      de los ensayos de Sandia (Preger et al., 2020) y una escala que reproduce la vida declarada por Apple
      para el iPhone 15 (80 % a los 1000 ciclos completos).

Los parámetros están en gemelo/parametros/mod002.json, que generan los scripts de calibracion/.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PARAMETROS = Path(__file__).with_name("parametros") / "mod002.json"


@dataclass
class Leyes:
    temps: list[float]  # °C de la tabla de calendario
    niveles: list[float]  # % de la tabla de calendario
    log_v: list[list[float]]  # logaritmo de la velocidad de calendario, por temperatura y nivel
    z: float  # exponente del tiempo
    zonas: list[float]  # límites de las zonas de ciclado (fracción de carga)
    pesos: list[float]  # pérdida por ciclo completo en cada zona (fracción), antes de la escala
    escala_ciclado: float  # escala para reproducir la vida declarada por el fabricante
    version: str = "MOD-002 v1.0"

    @classmethod
    def cargar(cls, ruta: Path = PARAMETROS) -> "Leyes":
        return cls(**json.loads(ruta.read_text(encoding="utf-8")))

    def guardar(self, ruta: Path = PARAMETROS) -> None:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(self.__dict__, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    # --- Calendario -------------------------------------------------------------------------
    def v_calendario(self, temp_c: float, nivel_pct: float) -> float:
        """Velocidad de envejecimiento por calendario (fracción / día^z)."""
        filas = [np.interp(nivel_pct, self.niveles, fila) for fila in self.log_v]
        inv = [1 / (t + 273.15) for t in self.temps]
        t = min(max(temp_c, self.temps[0]), self.temps[-1])
        return float(np.exp(np.interp(1 / (t + 273.15), inv[::-1], filas[::-1])))

    # --- Ciclado -------------------------------------------------------------------------------
    def integral_pesos(self, a: float, b: float) -> float:
        """Integral del peso de ciclado entre dos niveles de carga (fracciones 0-1)."""
        lo, hi = sorted((min(max(a, 0.0), 1.0), min(max(b, 0.0), 1.0)))
        total = 0.0
        for (z0, z1), w in zip(zip(self.zonas[:-1], self.zonas[1:]), self.pesos):
            total += w * max(0.0, min(hi, z1) - max(lo, z0))
        return total


@dataclass
class EstadoDegradacion:
    """Pérdida acumulada de una batería con unas leyes y un factor propio de la unidad."""
    leyes: Leyes
    perdida_cal: float = 0.0
    perdida_cic: float = 0.0
    factor: float = 1.0
    historial: list = field(default_factory=list)

    @property
    def salud(self) -> float:
        return 1.0 - self.factor * (self.perdida_cal + self.perdida_cic)

    def paso(self, nivel_antes: float, nivel_despues: float, temp_c: float, dt_h: float) -> None:
        """Niveles en %, temperatura en °C y duración en horas."""
        nivel_medio = (nivel_antes + nivel_despues) / 2
        v = self.leyes.v_calendario(temp_c, nivel_medio)
        z = self.leyes.z
        t_eq = (self.perdida_cal / v) ** (1 / z) if self.perdida_cal > 0 else 0.0
        self.perdida_cal = v * (t_eq + dt_h / 24) ** z
        # Medio ciclo por cada variación: una carga y una descarga completas son un ciclo.
        self.perdida_cic += (self.leyes.escala_ciclado / 2
                             * self.leyes.integral_pesos(nivel_antes / 100, nivel_despues / 100))
