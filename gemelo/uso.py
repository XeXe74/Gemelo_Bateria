"""Modelo de uso (MOD-001): consumo previsto de mañana y probabilidad de incidente con cada límite de carga.

Solo usa lo que ve el gemelo (REQ-0350): el nivel, la conexión al cargador y el modo Dormir cada 15 minutos, y el
calendario del día siguiente. No conoce los imprevistos ni el fichero de uso del simulador.

    - Aprendizaje: al terminar cada día, reconstruye el consumo de cada intervalo con el móvil desconectado, lo
      asigna al sueño, a la actividad del calendario o a casa, y actualiza la estimación bayesiana del consumo de
      cada actividad (logaritmo con distribución normal). Los días en casa que gastan claramente más de lo previsto
      se cuentan como días con imprevistos, cuya frecuencia y consumo también se aprenden. Del modo Dormir se
      aprenden las horas de levantarse y acostarse de cada día de la semana.
    - Previsión: simula 1000 veces el día siguiente con cada límite, con los hábitos de carga del usuario recogidos
      en el concepto de operación, y cuenta los días en los que el nivel baja del 5 % fuera de casa.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, timedelta

import numpy as np

from .decision import LIMITES

VERSION = "MOD-001 v1.0"
PASO_H = 0.25
FUERA = {"clase", "clase_datos", "carplay", "gimnasio", "social", "cena", "cine", "noche_fuera", "salida_corta",
         "dia_fuera", "imprevisto"}
# Consumo inicial de cada actividad (%/h): concepto de operación y capturas de Ajustes del 19 al 25/11/2026
CONSUMO_INICIAL = {"casa": 2.8, "dormir": 1.2, "clase": 2.5, "clase_datos": 11.25, "carplay": 18.75, "gimnasio": 4.0,
                   "social": 10.0, "cena": 7.0, "cine": 1.0, "noche_fuera": 9.0, "salida_corta": 6.0, "dia_fuera": 7.0}
SIGMA_PREVIO = 0.3  # incertidumbre del consumo inicial (logaritmo)
SIGMA_DIA = 0.25  # variación del consumo de una actividad de un día a otro (logaritmo)
SIGMA_PASO = 0.25  # variación de un intervalo de 15 minutos a otro
INCIDENTE = 5.0
# Hábitos de carga del usuario (concepto de operación)
EMERGENCIA, EMERGENCIA_HASTA = 10.0, 80.0
COCHE, COCHE_FACTOR = 50.0, 0.8
CLASE, CLASE_HASTA, CLASE_FACTOR = 20.0, 50.0, 0.4
PREVIA_MIN_H, PREVIA_MARGEN = 3.0, 20.0
# Imprevistos: frecuencia inicial del 20 % de los días y unos 10 puntos de consumo extra, en una salida de 2 h
IMPREVISTO_MIN = 8.0
IMPREVISTO_PREVIO = (2.0, 8.0)  # Beta(a, b)
IMPREVISTO_EXTRA = 10.0
IMPREVISTO_H = 2.0
# Horas de levantarse y acostarse iniciales (horas desde las 00:00 del día; acostarse, del día siguiente)
DESPERTAR = {4: (8.25, 8.75)}
DESPERTAR_DEFECTO = (9.0, 10.0)
ACOSTARSE = (24.5, 25.5)
HORIZONTE_H = 32


def velocidad_carga(nivel: np.ndarray) -> np.ndarray:
    """% por hora del cargador de 20 W (curva del fabricante, simplificada)."""
    return np.select([nivel < 50, nivel < 80, nivel < 95], [100.0, 45.0, 20.0], 8.0)


class ModeloUso:
    def __init__(self):
        self.mu = {a: np.log(c) for a, c in CONSUMO_INICIAL.items()}
        self.var = {a: SIGMA_PREVIO ** 2 for a in CONSUMO_INICIAL}
        self.imp_a, self.imp_b = IMPREVISTO_PREVIO
        self.extras: deque[float] = deque(maxlen=30)
        self.despertar: dict[int, deque] = defaultdict(lambda: deque(maxlen=8))
        self.acostarse: dict[int, deque] = defaultdict(lambda: deque(maxlen=8))
        self.calendarios: dict[date, list[dict]] = {}
        self.dias_aprendidos = 0
        # Día en curso
        self._anterior: tuple | None = None
        self._dia: date | None = None
        self._consumo: dict[str, float] = defaultdict(float)
        self._horas: dict[str, float] = defaultdict(float)

    # --- Entradas ---------------------------------------------------------------------------------
    def calendario(self, fecha: date, eventos: list[dict]) -> None:
        """Eventos visibles de un día (inicio y fin en hora peninsular, sin zona, y actividad)."""
        self.calendarios[fecha] = eventos
        for vieja in [f for f in self.calendarios if f < fecha - timedelta(days=10)]:
            del self.calendarios[vieja]

    def registrar(self, t: datetime, nivel: float, enchufado: int, dormir: int) -> None:
        """Una medida de 15 minutos (t en hora peninsular, sin zona)."""
        if self._anterior is not None:
            t0, n0, e0, d0 = self._anterior
            if d0 == 1 and dormir == 0:  # se levanta: empieza otro día
                if self._dia is not None:
                    self._cerrar_dia()
                self._dia = t.date()
                self.despertar[t.date().weekday()].append(t.hour + t.minute / 60)
            elif d0 == 0 and dormir == 1 and self._dia is not None:  # se acuesta
                self.acostarse[self._dia.weekday()].append((t - datetime.combine(self._dia, datetime.min.time()))
                                                            / timedelta(hours=1))
            if self._dia is not None and e0 == 0 and enchufado == 0:
                act = "dormir" if dormir else self._actividad(t0)
                self._consumo[act] += max(0.0, n0 - nivel)
                self._horas[act] += PASO_H
        self._anterior = (t, nivel, enchufado, dormir)

    def _actividad(self, t: datetime) -> str:
        for e in self.calendarios.get(self._dia, []):
            if e["inicio"] <= t < e["fin"]:
                return e["actividad"]
        return "casa"

    def _cerrar_dia(self) -> None:
        h, c = self._horas, self._consumo
        imprevisto = False
        if h["casa"] >= 2:
            esperado = np.exp(self.mu["casa"]) * h["casa"]
            extra = c["casa"] - esperado
            if extra > IMPREVISTO_MIN:
                imprevisto = True
                self.extras.append(extra)
        self.imp_a += imprevisto
        self.imp_b += not imprevisto
        for act, horas in h.items():
            if horas < 0.5 or act not in self.mu or (act == "casa" and imprevisto) or c[act] <= 0:
                continue
            obs = np.log(c[act] / horas)
            # Actualización normal-normal del logaritmo del consumo, con la variación entre días como error
            prec = 1 / self.var[act] + 1 / SIGMA_DIA ** 2
            self.mu[act] = (self.mu[act] / self.var[act] + obs / SIGMA_DIA ** 2) / prec
            self.var[act] = 1 / prec
        self.dias_aprendidos += 1
        self._consumo, self._horas = defaultdict(float), defaultdict(float)

    # --- Previsión ------------------------------------------------------------------------------------
    def prevision(self, fecha: date, eventos: list[dict], nivel_enchufe: float, enchufe: datetime,
                  n: int = 1000, semilla: int = 0) -> dict:
        """Simula el día `fecha` n veces con cada límite. Devuelve el consumo previsto y, para cada límite, la
        probabilidad de incidente con su intervalo del 90 %."""
        rng = np.random.default_rng(semilla)
        base = datetime.combine(fecha, datetime.min.time())
        pasos = int(HORIZONTE_H / PASO_H)
        horas = np.arange(pasos) * PASO_H
        # Actividad de cada intervalo según el calendario (igual en todas las simulaciones)
        act = np.array(["casa"] * pasos, dtype=object)
        fin_eventos = 0.0
        necesario = np.zeros(pasos)
        for e in eventos:
            a = (e["inicio"] - base) / timedelta(hours=1)
            b = (e["fin"] - base) / timedelta(hours=1)
            act[(horas >= a) & (horas < b)] = e["actividad"]
            fin_eventos = max(fin_eventos, b)
            if e["actividad"] in FUERA and b - a >= PREVIA_MIN_H:
                need = min(95.0, np.exp(self.mu[e["actividad"]]) * (b - a) + PREVIA_MARGEN)
                necesario[(horas >= a - 1) & (horas < a)] = need
        # Horas de levantarse y acostarse
        dia = fecha.weekday()
        desp = self._muestra(self.despertar[dia], DESPERTAR.get(dia, DESPERTAR_DEFECTO), n, rng)
        acost = np.maximum(self._muestra(self.acostarse[dia], ACOSTARSE, n, rng), fin_eventos + 0.5)
        # Consumo de cada actividad en cada simulación (incertidumbre del parámetro y variación del día)
        acts = sorted(set(act) | {"casa", "dormir"})
        tasa = {a: np.exp(rng.normal(self.mu[a], np.sqrt(self.var[a] + SIGMA_DIA ** 2), n)) for a in acts}
        # Imprevistos
        p_imp = rng.beta(self.imp_a, self.imp_b, n)
        hay = rng.random(n) < p_imp
        extra_med = float(np.median(self.extras)) if len(self.extras) >= 3 else IMPREVISTO_EXTRA
        extra = extra_med * np.exp(rng.normal(0, 0.4, n))
        ini_imp = rng.uniform(16, 22, n)
        # Nivel al levantarse: carga nocturna hasta el límite, si da tiempo
        h_enchufe = (enchufe - base) / timedelta(hours=1)
        lim = np.array(LIMITES, dtype=float)[:, None]
        nivel = np.full((len(LIMITES), n), float(nivel_enchufe))
        t = np.full(n, h_enchufe)
        # Con el móvil por encima del límite, el cargador lo alimenta y el nivel no cambia.
        while (t < desp).any():
            activo = (t < desp) & (nivel < lim)
            sub = nivel + velocidad_carga(nivel) * PASO_H - CONSUMO_INICIAL["dormir"] * PASO_H
            nivel = np.where(activo, np.minimum(lim, sub), nivel)
            t = t + PASO_H
        # Día
        motivo = np.zeros((len(LIMITES), n), dtype=int)  # 0 nada, 1 emergencia o previa, 2 coche, 3 clase
        incidente = np.zeros((len(LIMITES), n), dtype=bool)
        consumo_dia = np.zeros((len(LIMITES), n))
        for s in range(pasos):
            hs = horas[s]
            despierto = (hs >= desp) & (hs < acost)
            if not despierto.any():
                continue
            a = act[s]
            en_imp = hay & (hs >= ini_imp) & (hs < ini_imp + IMPREVISTO_H) & (a == "casa")
            r = np.where(en_imp, tasa["casa"] + extra / IMPREVISTO_H, tasa[a])
            consumo = r * PASO_H * np.exp(rng.normal(-SIGMA_PASO ** 2 / 2, SIGMA_PASO, n))
            fuera = en_imp | (a in FUERA)
            # Hábitos de carga
            en_casa = ~fuera
            fin_casa = (motivo == 1) & ~en_casa
            fin_coche = (motivo == 2) & (a != "carplay")
            fin_clase = (motivo == 3) & ((a not in ("clase", "clase_datos")) | (nivel >= CLASE_HASTA))
            fin_emerg = (motivo == 1) & (nivel >= np.minimum(EMERGENCIA_HASTA, lim)) & (necesario[s] == 0)
            motivo = np.where(fin_casa | fin_coche | fin_clase | fin_emerg, 0, motivo)
            libre = motivo == 0
            motivo = np.where(libre & en_casa & ((nivel < EMERGENCIA) | (nivel < necesario[s])), 1, motivo)
            if a == "carplay":
                motivo = np.where((motivo == 0) & ~en_imp & (nivel < np.minimum(COCHE, lim)), 2, motivo)
            if a in ("clase", "clase_datos"):
                motivo = np.where((motivo == 0) & ~en_imp & (nivel < CLASE), 3, motivo)
            factor = np.select([motivo == 1, motivo == 2, motivo == 3], [1.0, COCHE_FACTOR, CLASE_FACTOR], 0.0)
            carga = factor * velocidad_carga(nivel) * PASO_H
            cargando = np.where(nivel < lim, np.minimum(lim, nivel + carga - consumo), nivel)
            nuevo = np.where(motivo > 0, cargando, nivel - consumo)
            nuevo = np.maximum(nuevo, 0.0)
            nivel = np.where(despierto, nuevo, nivel)
            consumo_dia += np.where(despierto, consumo, 0.0)
            incidente |= despierto & fuera & (nivel < INCIDENTE)
        # El día va de levantarse a levantarse: se añade la noche siguiente, durmiendo hasta una hora de levantarse
        # parecida a la de este día.
        consumo_dia += tasa["dormir"] * np.clip(desp + 24 - acost, 0, None)
        resultado = {"consumo": {"media": float(consumo_dia[-1].mean()), "inferior": float(np.percentile(consumo_dia[-1], 5)),
                                 "superior": float(np.percentile(consumo_dia[-1], 95))},
                     "probabilidad": {}, "version": VERSION, "dias_aprendidos": self.dias_aprendidos}
        for i, limite in enumerate(LIMITES):
            p, inf, sup = wilson(int(incidente[i].sum()), n)
            resultado["probabilidad"][limite] = {"p": p, "inferior": inf, "superior": sup}
        return resultado

    @staticmethod
    def _muestra(historial, defecto: tuple[float, float], n: int, rng) -> np.ndarray:
        if len(historial) >= 3:
            return rng.choice(np.asarray(historial, dtype=float), n) + rng.normal(0, 0.15, n)
        return rng.uniform(*defecto, n)


def wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float, float]:
    """Proporción con su intervalo de Wilson del 90 %."""
    p = k / n
    centro = (p + z ** 2 / (2 * n)) / (1 + z ** 2 / n)
    radio = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / (1 + z ** 2 / n)
    return p, max(0.0, centro - radio), min(1.0, centro + radio)
