"""Batería simulada: la «realidad» que el gemelo tiene que descubrir.

Usa las leyes de envejecimiento calibradas en el paso de modelos (ficha MOD-002 v1.1), congeladas en
sim/parametros_bateria.json para que la realidad no cambie si después se recalibra el modelo del gemelo:
    - Calendario: pérdida = v(T, nivel) · t^z, con la tabla de velocidades ajustada con los ensayos de WMG y el
      método del tiempo equivalente cuando las condiciones cambian.
    - Ciclado: cada variación del nivel desgasta según la zona de carga en la que ocurre (forma de los ensayos de
      Sandia y magnitud de la vida declarada por Apple para el iPhone 15).
La unidad simulada tiene dos factores ocultos, uno de calendario y otro de ciclado, sorteados alrededor de 1 con
la semilla de la unidad: el gemelo no los ve y tiene que estimarlos a partir de la salud que muestra Ajustes.
La historia hasta el 16/11/2026 (14 puntos de pérdida, salud del 86 %) se reparte entre calendario y ciclado
en la misma proporción que en la validación del modelo con la batería real.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

PARAMETROS = Path(__file__).with_name("parametros_bateria.json")


class BateriaSimulada:
    def __init__(self, soh_inicial: float, edad_dias: float, rng_unidad: np.random.Generator,
                 parametros: dict | None = None, dispersion_unidad: float = 0.2):
        p = json.loads(PARAMETROS.read_text(encoding="utf-8")) | (parametros or {})
        self.temps, self.niveles = p["temps"], p["niveles"]
        self.log_v, self.z = np.asarray(p["log_v"]), p["z"]
        self.zonas, self.pesos = p["zonas"], p["pesos"]
        self.escala_ciclado = p["escala_ciclado"] * p.get("factor_ciclado", 1.0)  # factor para la sensibilidad
        # Parámetros ocultos de esta batería concreta
        self.f_cal = float(np.exp(rng_unidad.normal(0, dispersion_unidad)))
        self.f_cic = float(np.exp(rng_unidad.normal(0, dispersion_unidad)))
        # Historia hasta el inicio: la pérdida real repartida como en el modelo
        h = p["historia_inicial"]
        perdida0 = 1.0 - soh_inicial
        parte_cal = h["perdida_cal"] / (h["perdida_cal"] + h["perdida_cic"])
        self.perdida_cal = perdida0 * parte_cal
        self.perdida_cyc = perdida0 * (1 - parte_cal)
        self.soh = soh_inicial
        self.edad_dias = edad_dias
        self.ciclos_eq = 0.0

    def _v(self, temp_c: float, nivel_pct: float) -> float:
        filas = [np.interp(nivel_pct, self.niveles, fila) for fila in self.log_v]
        inv = [1 / (t + 273.15) for t in self.temps]
        t = min(max(temp_c, self.temps[0]), self.temps[-1])
        return self.f_cal * float(np.exp(np.interp(1 / (t + 273.15), inv[::-1], filas[::-1])))

    def _integral_pesos(self, a: float, b: float) -> float:
        lo, hi = sorted((min(max(a, 0.0), 1.0), min(max(b, 0.0), 1.0)))
        return sum(w * max(0.0, min(hi, z1) - max(lo, z0))
                   for (z0, z1), w in zip(zip(self.zonas[:-1], self.zonas[1:]), self.pesos))

    def paso(self, nivel_antes: float, nivel_despues: float, temp_c: float, dt_h: float) -> None:
        """Aplica la degradación de un paso. Los niveles van en % (0-100)."""
        v = self._v(temp_c, (nivel_antes + nivel_despues) / 2)
        t_eq = (self.perdida_cal / v) ** (1 / self.z) if self.perdida_cal > 0 else 0.0
        dq_cal = v * (t_eq + dt_h / 24) ** self.z - self.perdida_cal
        dq_cyc = self.f_cic * self.escala_ciclado / 2 * self._integral_pesos(nivel_antes / 100, nivel_despues / 100)
        self.perdida_cal += dq_cal
        self.perdida_cyc += dq_cyc
        self.soh -= dq_cal + dq_cyc
        self.ciclos_eq += abs(nivel_despues - nivel_antes) / 200
        self.edad_dias += dt_h / 24

    def ocultos(self) -> dict:
        return {"f_cal": self.f_cal, "f_cic": self.f_cic, "escala_ciclado": self.escala_ciclado}


def velocidad_carga(nivel: float) -> float:
    """% por hora con un cargador de 20 W (curva simplificada CC-CV)."""
    if nivel < 50:
        return 100.0
    if nivel < 80:
        return 45.0
    if nivel < 95:
        return 20.0
    return 8.0
