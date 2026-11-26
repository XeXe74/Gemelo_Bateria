"""Paso 2: móvil + cargador simulados (el activo). Lee el Excel de uso del paso 1.

Recorre el Excel fila a fila (pasos de 15 minutos). En cada paso toma del Excel qué
hace el usuario y cuánto gasta, y calcula lo que depende de las decisiones de carga:
si el móvil está enchufado y a qué tope, el nivel, la temperatura con el calor de la
carga y cuánto se degrada la batería. Cada noche, al enchufar, pregunta el tope a una
«política»: ahora mismo políticas fijas (siempre 100 %, siempre 80 %…); más adelante,
el gemelo.

Todo el azar del uso está fijo en el Excel. El azar de la batería (sus parámetros
ocultos y el ruido de la salud que marca Ajustes) sale de seed_unidad y no depende de
las decisiones. Así, dos estrategias con el mismo Excel viven exactamente los mismos días.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

import numpy as np
import pandas as pd

from .bateria import BateriaSimulada, ParametrosDegradacion, velocidad_carga
from .generar_uso import DatosUso
from .rutina import PASO


@dataclass
class ContextoDecision:
    """Lo que ve una política en el momento de enchufar por la noche."""
    momento: datetime
    nivel: float
    soh_reportado: int
    manana: date
    calendario_manana: list[dict]


Politica = Callable[[ContextoDecision], int]


def politica_fija(tope: int) -> Politica:
    return lambda ctx: tope


def simular(datos: DatosUso, politica: Politica, seed_unidad: int = 1234,
            parametros: ParametrosDegradacion | None = None) -> dict:
    cfg = datos.cfg
    rng_unidad = np.random.default_rng(seed_unidad)
    carga_cfg, consumos = cfg["carga"], cfg["consumos"]
    uso = datos.uso
    n = len(uso)
    t0 = uso["momento"].iloc[0].to_pydatetime()
    dt_h = PASO / timedelta(hours=1)

    def idx(t: datetime) -> int:
        return int((t - t0) / PASO)

    # --- Lo que viene del Excel ----------------------------------------------------
    momentos = [t.to_pydatetime() for t in uso["momento"]]
    actividad = uso["actividad"].to_numpy()
    titulo = uso["titulo"].to_numpy()
    en_casa = uso["en_casa"].astype(bool).to_numpy()
    en_cal = uso["en_calendario"].astype(bool).to_numpy()
    dia_logico = uso["dia"].to_numpy()
    gasto = uso["gasto_pct"].to_numpy(dtype=float)
    temp_base = uso["temperatura_c"].to_numpy(dtype=float)

    calendario_por_dia = {f: g for f, g in datos.calendario.groupby("fecha")}

    # Antes de una salida larga, el usuario carga si no le llega para la salida + margen.
    # Lo sabe él aunque no esté en el calendario, así que usa también los imprevistos.
    previa_necesario = np.zeros(n)
    for bl in pd.concat([datos.calendario, datos.imprevistos]).itertuples():
        x = idx(bl.inicio.to_pydatetime())
        dur_h = (bl.fin - bl.inicio) / pd.Timedelta(hours=1)
        if 0 < x <= n and not consumos[bl.actividad]["en_casa"] and dur_h >= carga_cfg["previa_salida_min_h"]:
            necesario = min(95.0, consumos[bl.actividad]["media"] * dur_h + carga_cfg["margen_salida"])
            previa_necesario[max(0, x - 4):x] = necesario  # la hora anterior

    # Cargas fuera de casa, si lo necesita: en el coche y en clase.
    coche_cfg, clase_cfg = carga_cfg["coche"], carga_cfg["clase"]
    coche_acts, clase_acts = set(coche_cfg["actividades"]), set(clase_cfg["actividades"])
    factor_velocidad = {"coche": coche_cfg["factor_velocidad"], "clase": clase_cfg["factor_velocidad"]}

    # --- Estado ------------------------------------------------------------------
    soh0 = cfg["movil"]["salud_inicial"]
    bat = BateriaSimulada(soh0, cfg["movil"]["edad_dias_inicial"],
                          parametros or ParametrosDegradacion(), rng_unidad)
    nivel = float(cfg["simulacion"]["nivel_inicial"])
    tope = 100
    motivo = None  # None, "noche", "emergencia", "previa"
    soh_rep = int(round(100 * bat.soh))
    incidente_dia: set[date] = set()

    decisiones = []
    filas = []

    for k in range(n):
        t = momentos[k]
        act, casa, dia = actividad[k], bool(en_casa[k]), dia_logico[k]

        # SoH que muestra Ajustes: entero, ruidoso, se actualiza una vez al día.
        if t.hour == 12 and t.minute == 0:
            soh_rep = int(round(100 * bat.soh + rng_unidad.normal(0, 0.3)))

        # --- Comportamiento de carga del usuario ---------------------------------
        if motivo in ("noche", "emergencia", "previa") and not casa:
            motivo = None  # se lleva el móvil
        if motivo == "coche" and act not in coche_acts:
            motivo = None  # llega y lo desenchufa del coche
        if motivo == "clase" and (act not in clase_acts or nivel >= clase_cfg["hasta"]):
            motivo = None  # acaba la clase o ya tiene suficiente
        if act == "dormir" and casa and motivo != "noche":
            motivo = "noche"
            manana = (t + timedelta(hours=8)).date()
            cal_m = calendario_por_dia.get(manana)
            cal = [] if cal_m is None else [
                {"inicio": b.inicio.to_pydatetime(), "fin": b.fin.to_pydatetime(),
                 "titulo": b.titulo, "actividad": b.actividad} for b in cal_m.itertuples()]
            tope = int(politica(ContextoDecision(t, nivel, soh_rep, manana, cal)))
            decisiones.append({"momento": t, "para_dia": manana, "nivel_al_enchufar": nivel,
                               "tope": tope})
        elif motivo == "noche" and act != "dormir":
            motivo = None  # se levanta y desenchufa
        if motivo is None and casa and act != "dormir":
            if nivel < carga_cfg["emergencia_umbral"]:
                motivo = "emergencia"
            elif nivel < previa_necesario[k]:
                motivo = "previa"
        if motivo == "emergencia" and nivel >= min(carga_cfg["emergencia_hasta"], tope):
            motivo = None
        # Fuera de casa: si lo necesita, en el coche o en clase.
        if motivo is None and not casa:
            if act in coche_acts and nivel < min(coche_cfg["umbral"], tope):
                motivo = "coche"
            elif act in clase_acts and nivel < clase_cfg["umbral"]:
                motivo = "clase"

        # --- Dinámica del nivel ----------------------------------------------------
        temp = temp_base[k]
        antes = nivel
        # El gasto del Excel está en % de la batería inicial; con menos salud, gasta más %.
        # Si el móvil está apagado (nivel 0) y no carga, no se usa.
        consumo = gasto[k] * (soh0 / bat.soh) if (motivo or nivel > 0) else 0.0
        if motivo:
            # Enchufado: sube lo que mete el cargador menos lo que se gasta. Al llegar al
            # tope, el cargador alimenta el móvil y el nivel se queda en el tope.
            sub = dt_h / 3
            factor = factor_velocidad.get(motivo, 1.0)
            for _ in range(3):
                if nivel < tope:
                    nivel = min(float(tope), nivel + velocidad_carga(nivel) * factor * sub - consumo / 3)
                    nivel = max(0.0, nivel)
            temp += 6 if antes < 80 else (2 if nivel < tope else 1)
        else:
            nivel = max(0.0, nivel - consumo)

        bat.paso(antes, nivel, temp, dt_h)

        incidente = False
        if not casa and nivel < carga_cfg["incidente_umbral"] and dia not in incidente_dia:
            incidente_dia.add(dia)
            incidente = True

        filas.append({
            "momento": t, "dia_logico": dia, "dia_semana": dia.weekday(), "actividad": act,
            "titulo": titulo[k], "en_casa": casa, "en_calendario": bool(en_cal[k]),
            "nivel": round(nivel, 2), "enchufado": motivo is not None, "motivo_carga": motivo or "",
            "tope": tope, "consumo_pct_h": round(consumo / dt_h, 2), "temperatura": round(temp, 1),
            "soh_real": round(100 * bat.soh, 4), "soh_reportado": soh_rep, "incidente": incidente,
        })

    pasos = pd.DataFrame(filas)
    reales = pd.concat([datos.calendario.assign(en_calendario=True),
                        datos.imprevistos.assign(en_calendario=False)]).sort_values("inicio")
    return {
        "pasos": pasos,
        "dias": resumen_diario(pasos, datos.resumen),
        "calendario": datos.calendario.drop(columns="dia_semana"),
        "eventos_reales": reales.drop(columns="dia_semana"),
        "decisiones": pd.DataFrame(decisiones),
        "ocultos": bat.ocultos() | {"ciclos_eq": bat.ciclos_eq, "perdida_cal": bat.perdida_cal,
                                     "perdida_cyc": bat.perdida_cyc},
    }


def resumen_diario(pasos: pd.DataFrame, resumen_uso: pd.DataFrame) -> pd.DataFrame:
    filas = []
    por_dia = dict(tuple(pasos.groupby("dia_logico")))
    for r in resumen_uso.itertuples():
        d = por_dia.get(r.fecha)
        if d is None or d.empty:
            continue
        despierto = d[d["actividad"] != "dormir"]
        inicio_carga = d["motivo_carga"] != d["motivo_carga"].shift(fill_value="")
        recargas_em = int((inicio_carga & (d["motivo_carga"] == "emergencia")).sum())
        recargas_pre = int((inicio_carga & (d["motivo_carga"] == "previa")).sum())
        recargas_coche = int((inicio_carga & (d["motivo_carga"] == "coche")).sum())
        recargas_clase = int((inicio_carga & (d["motivo_carga"] == "clase")).sum())
        eventos = ", ".join(x for x in (r.eventos, r.imprevistos) if x)
        filas.append({
            "fecha": r.fecha, "dia_semana": r.fecha.weekday(),
            "nivel_al_levantarse": despierto["nivel"].iloc[0] if not despierto.empty else None,
            "nivel_minimo": d["nivel"].min(),
            # Todo lo gastado en el día, también mientras estaba enchufado.
            "consumo_dia": round(d["consumo_pct_h"].sum() * PASO / timedelta(hours=1), 1),
            "recargas_emergencia": recargas_em,
            "recargas_antes_de_salir": recargas_pre,
            "recargas_coche": recargas_coche,
            "recargas_clase": recargas_clase,
            "incidente": bool(d["incidente"].any()),
            "eventos": eventos,
            "imprevistos": r.imprevistos,
            "soh_real": d["soh_real"].iloc[-1],
            "soh_reportado": d["soh_reportado"].iloc[-1],
        })
    return pd.DataFrame(filas)
