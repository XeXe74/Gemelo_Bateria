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
    version: str = "MOD-002 v1.1"
    historia_inicial: dict | None = None  # pérdida nominal de la historia de la batería hasta el 16/11/2026

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


# --- Parte operativa del modelo (ficha MOD-002: actualización, estimación, proyección y coste) ---------------

PRECIO_SUSTITUCION = 109.0  # €
PUNTOS_VIDA = 20.0  # la batería se sustituye al perder 20 puntos (del 100 al 80 %)
LIMITES = (80, 85, 90, 95, 100)
_erf = np.frompyfunc(__import__("math").erf, 1, 1)


def _phi(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + _erf(x / np.sqrt(2.0)).astype(float))


class ModeloDegradacion:
    """Estimación de la salud real de la batería a partir de lo que ve el gemelo.

    La salud real se escribe como S = S0 − k · ΔL, donde S0 es la salud real al empezar (Ajustes mostraba un 86 %,
    que puede ser cualquier valor entre el 85,5 y el 86,5 %), ΔL es la pérdida que predicen las leyes desde
    entonces con las medidas recibidas y k es el factor propio de la unidad. S0 y k se estiman de forma bayesiana
    en una rejilla, comparando cada día la salud entera de Ajustes con la prevista.
    """

    SIGMA_AJUSTES = 0.4  # error supuesto de la salud de Ajustes antes del redondeo (puntos)

    def __init__(self, leyes: Leyes | None = None, salud_inicial: int = 86, dias_perfil: int = 28):
        self.leyes = leyes or Leyes.cargar()
        h = self.leyes.historia_inicial
        self.q_cal0 = h["perdida_cal"]  # pérdida nominal de calendario al empezar
        self.q_cal = self.q_cal0
        self.delta_cic = 0.0
        # Rejilla de S0 (%) y k, con el factor inicial de la configuración as-built (pérdida real / prevista)
        k0 = (100 - salud_inicial) / (100 * (h["perdida_cal"] + h["perdida_cic"]))
        self.s0 = np.linspace(salud_inicial - 1.0, salud_inicial + 1.0, 81)
        self.k = np.exp(np.linspace(np.log(0.25), np.log(4.0), 121))
        self.S0, self.K = np.meshgrid(self.s0, self.k, indexing="ij")
        self.log_post = -0.5 * (np.log(self.K / k0) / 0.3) ** 2
        self.observar_salud(salud_inicial)
        self.perfil: list[tuple[float, float, int, int, int]] = []  # nivel, temperatura, enchufado, dormir, límite
        self.max_perfil = dias_perfil * 96
        self.nivel_anterior: float | None = None

    @property
    def delta_l(self) -> float:
        return (self.q_cal - self.q_cal0) + self.delta_cic

    # --- Actualización con las medidas ------------------------------------------------------------
    def registrar(self, nivel: float, temp_c: float, enchufado: int, dormir: int, limite: int, dt_h: float = 0.25) -> None:
        if self.nivel_anterior is not None:
            antes = self.nivel_anterior
            v = self.leyes.v_calendario(temp_c, (antes + nivel) / 2)
            t_eq = (self.q_cal / v) ** (1 / self.leyes.z)
            self.q_cal = v * (t_eq + dt_h / 24) ** self.leyes.z
            self.delta_cic += self.leyes.escala_ciclado / 2 * self.leyes.integral_pesos(antes / 100, nivel / 100)
        self.nivel_anterior = nivel
        self.perfil.append((nivel, temp_c, enchufado, dormir, limite))
        if len(self.perfil) > self.max_perfil:
            self.perfil.pop(0)

    def observar_salud(self, entero: int) -> None:
        prevista = self.S0 - 100 * self.K * self.delta_l
        s = self.SIGMA_AJUSTES
        p = _phi((entero + 0.5 - prevista) / s) - _phi((entero - 0.5 - prevista) / s)
        self.log_post += np.log(np.maximum(p, 1e-300))
        self.log_post -= self.log_post.max()

    def _pesos(self) -> np.ndarray:
        w = np.exp(self.log_post)
        return w / w.sum()

    def estimacion(self) -> dict:
        """Salud real estimada (%) con su intervalo del 90 % y el factor propio medio."""
        w = self._pesos().ravel()
        s = (self.S0 - 100 * self.K * self.delta_l).ravel()
        orden = np.argsort(s)
        acum = np.cumsum(w[orden])
        q = lambda p: float(s[orden][np.searchsorted(acum, p)])
        return {"salud": float(w @ s), "inferior": q(0.05), "superior": q(0.95), "factor": float(w @ self.K.ravel()),
                "version": self.leyes.version}

    # --- Proyección y coste ------------------------------------------------------------------------------
    def _perfil_con_limite(self, limite: int | None) -> tuple[np.ndarray, np.ndarray]:
        """Niveles y temperaturas del perfil reciente; con un límite, se recalculan como si cada noche se hubiera
        cargado hasta ese límite: la meseta nocturna pasa a ser el límite y el resto del día se desplaza en la
        misma diferencia."""
        p = np.asarray(self.perfil, dtype=float)
        niveles, temps = p[:, 0].copy(), p[:, 1]
        if limite is not None:
            enchufado, dormir, lim_obs = p[:, 2], p[:, 3], p[:, 4]
            noche = (enchufado == 1) & (dormir == 1)
            meseta = noche & (niveles >= lim_obs - 0.5)
            rampa = noche & ~meseta
            dia = ~noche
            niveles[meseta] = limite
            niveles[rampa] = np.minimum(niveles[rampa], limite)
            niveles[dia] = np.clip(niveles[dia] + (limite - lim_obs[dia]), 0, 100)
        return niveles, temps

    def _tasas(self, limite: int | None) -> tuple[float, float]:
        """Velocidad de calendario equivalente (fracción / día^z) y pérdida nominal por ciclado al día."""
        niveles, temps = self._perfil_con_limite(limite)
        z = self.leyes.z
        medios = (niveles[:-1] + niveles[1:]) / 2
        v = np.array([self.leyes.v_calendario(t, n) for t, n in zip(temps[1:], medios)])
        v_eq = float(np.mean(v ** (1 / z)) ** z)
        cic = sum(self.leyes.integral_pesos(a / 100, b / 100) for a, b in zip(niveles[:-1], niveles[1:]))
        dias = (len(niveles) - 1) / 96
        return v_eq, self.leyes.escala_ciclado / 2 * cic / dias

    def dias_hasta_80(self, limite: int | None = None, muestras: int = 400, max_dias: int = 3650) -> dict:
        """Días hasta que la salud real llegue al 80 % repitiendo el uso reciente: con la estrategia observada
        (limite=None) o cargando siempre hasta un límite. Mediana e intervalo del 90 %."""
        v_eq, c = self._tasas(limite)
        z = self.leyes.z
        w = self._pesos().ravel()
        idx = np.random.default_rng(0).choice(w.size, size=muestras, p=w)
        s_hoy = (self.S0 - 100 * self.K * self.delta_l).ravel()[idx]
        k = self.K.ravel()[idx]
        dias = np.arange(0, max_dias + 1)
        perdida = (self.q_cal ** (1 / z) + v_eq ** (1 / z) * dias) ** z - self.q_cal + c * dias  # nominal
        salud = s_hoy[:, None] - 100 * k[:, None] * perdida[None, :]
        llegada = np.where((salud <= 80).any(axis=1), (salud <= 80).argmax(axis=1), max_dias)
        return {"mediana": float(np.median(llegada)), "inferior": float(np.percentile(llegada, 5)),
                "superior": float(np.percentile(llegada, 95))}

    def coste_limites(self) -> dict[int, dict]:
        """Pérdida de salud de un día típico con cada límite, en puntos y en euros."""
        k = float(self._pesos().ravel() @ self.K.ravel())
        z = self.leyes.z
        coste = {}
        for limite in LIMITES:
            v_eq, c = self._tasas(limite)
            cal = (self.q_cal ** (1 / z) + v_eq ** (1 / z)) ** z - self.q_cal
            puntos = 100 * k * (cal + c)
            coste[limite] = {"puntos": puntos, "euros": puntos * PRECIO_SUSTITUCION / PUNTOS_VIDA}
        return coste
