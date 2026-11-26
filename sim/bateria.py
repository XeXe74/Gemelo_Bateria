"""Batería simulada: la «realidad» que el gemelo tiene que descubrir.

Modelo semiempírico de pérdida de salud (SoH):
  - Calendario: depende del tiempo, de la temperatura (Arrhenius) y, sobre todo, del
    nivel de carga al que se mantiene la celda. Ley de raíz del tiempo:
        dQ_cal = k_cal · f(T) · g(SoC) · dt / (2·√edad)
  - Ciclado: proporcional a los ciclos equivalentes, con un recargo para la energía
    cargada por encima del 80 %:
        dQ_cyc = k_cyc · (ciclos_eq + β · carga_por_encima_80)

Los parámetros NOMINALES son provisionales: se sustituirán por los ajustados con los
datasets WMG (calendario) y Sandia (ciclado). Los parámetros REALES de esta unidad
(k_cal_real, k_cyc_real) se sortean alrededor de los nominales y quedan ocultos: el
gemelo no los ve y tiene que estimarlos a partir del SoH observado.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np

R = 8.314  # J/(mol·K)


@dataclass
class ParametrosDegradacion:
    k_cal: float = 2.0e-3      # pérdida de SoH (fracción) por √día a 25 °C y 50 % de carga
    ea: float = 50_000.0       # energía de activación (J/mol)
    t_ref_c: float = 25.0
    alpha_soc: float = 2.0     # g(SoC) = exp(alpha · (SoC − 0,5))
    k_cyc: float = 1.4e-4      # pérdida de SoH por ciclo equivalente completo
    beta_alto: float = 2.0     # recargo por cada fracción cargada por encima del 80 %


class BateriaSimulada:
    def __init__(self, soh_inicial: float, edad_dias: float,
                 nominal: ParametrosDegradacion, rng_unidad: np.random.Generator,
                 dispersion_unidad: float = 0.2):
        self.nominal = nominal
        # Parámetros ocultos de esta batería concreta.
        self.k_cal_real = nominal.k_cal * float(np.exp(rng_unidad.normal(0, dispersion_unidad)))
        self.k_cyc_real = nominal.k_cyc * float(np.exp(rng_unidad.normal(0, dispersion_unidad)))
        self.soh = soh_inicial
        self.edad_dias = edad_dias
        self.ciclos_eq = 0.0
        self.perdida_cal = 0.0
        self.perdida_cyc = 0.0

    def _f_temp(self, temp_c: float) -> float:
        t, tr = temp_c + 273.15, self.nominal.t_ref_c + 273.15
        return math.exp(-self.nominal.ea / R * (1 / t - 1 / tr))

    def _g_soc(self, soc: float) -> float:
        return math.exp(self.nominal.alpha_soc * (soc - 0.5))

    def paso(self, nivel_antes: float, nivel_despues: float, temp_c: float, dt_h: float) -> None:
        """Aplica la degradación de un paso. Los niveles van en % (0–100)."""
        dt_d = dt_h / 24
        soc = (nivel_antes + nivel_despues) / 200
        dq_cal = (self.k_cal_real * self._f_temp(temp_c) * self._g_soc(soc)
                  * dt_d / (2 * math.sqrt(self.edad_dias)))

        cargado = max(0.0, nivel_despues - nivel_antes) / 100
        descargado = max(0.0, nivel_antes - nivel_despues) / 100
        ciclos = (cargado + descargado) / 2
        lo, hi = sorted((nivel_antes, nivel_despues))
        alto = max(0.0, hi - max(lo, 80.0)) / 100 if nivel_despues > nivel_antes else 0.0
        dq_cyc = self.k_cyc_real * (ciclos + self.nominal.beta_alto * alto)

        self.soh -= dq_cal + dq_cyc
        self.perdida_cal += dq_cal
        self.perdida_cyc += dq_cyc
        self.ciclos_eq += ciclos
        self.edad_dias += dt_d

    def ocultos(self) -> dict:
        return {"k_cal_real": self.k_cal_real, "k_cyc_real": self.k_cyc_real,
                "nominal": asdict(self.nominal)}


def velocidad_carga(nivel: float) -> float:
    """% por hora con un cargador de 20 W (curva simplificada CC-CV)."""
    if nivel < 50:
        return 100.0
    if nivel < 80:
        return 45.0
    if nivel < 95:
        return 20.0
    return 8.0
