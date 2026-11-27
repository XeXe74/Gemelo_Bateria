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

from datetime import timedelta
from typing import Callable

import pandas as pd

from .activo import Activo, ContextoDecision
from .generar_uso import DatosUso
from .rutina import PASO

Politica = Callable[[ContextoDecision], int | str]  # límite, o "optimizada" para la carga optimizada de iOS


def politica_fija(tope: int) -> Politica:
    return lambda ctx: tope


def politica_optimizada() -> Politica:
    """Carga optimizada de iOS: 80 % durante la noche y 100 % poco antes de levantarse."""
    return lambda ctx: "optimizada"


def simular(datos: DatosUso, politica: Politica, seed_unidad: int = 1234,
            parametros: dict | None = None) -> dict:
    """Recorre el año entero de una vez (modo por lotes) con el mismo activo que el servicio."""
    act = Activo(datos, seed_unidad, parametros)
    while not act.terminado:
        ctx = act.inicio_paso()
        if ctx is not None:
            r = politica(ctx)
            if r == "optimizada":
                act.fijar_limite(100, optimizada=True)
            else:
                act.fijar_limite(int(r))
        act.fin_paso()

    pasos = pd.DataFrame(act.filas)
    reales = pd.concat([datos.calendario.assign(en_calendario=True),
                        datos.imprevistos.assign(en_calendario=False)]).sort_values("inicio")
    return {
        "pasos": pasos,
        "dias": resumen_diario(pasos, datos.resumen),
        "calendario": datos.calendario.drop(columns="dia_semana"),
        "eventos_reales": reales.drop(columns="dia_semana"),
        "decisiones": pd.DataFrame(act.decisiones),
        "ocultos": act.ocultos(),
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
