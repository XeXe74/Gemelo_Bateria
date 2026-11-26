"""Generador de uso: convierte la rutina del usuario en días concretos.

Para cada día produce la hora de despertar, la de acostarse y una lista de bloques
(actividades con inicio y fin). Unos bloques son fijos (clase, gimnasio, salida del
sábado) y otros aleatorios (café, cine, cenas, días fuera). Cada bloque sabe si
aparece en el calendario: lo que no aparece es un imprevisto para el gemelo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np

PASO = timedelta(minutes=15)


@dataclass
class Bloque:
    inicio: datetime
    fin: datetime
    actividad: str
    titulo: str
    en_calendario: bool
    origen: str  # "fijo" o "evento"

    def solapa(self, otro: "Bloque") -> bool:
        return self.inicio < otro.fin and otro.inicio < self.fin


@dataclass
class PlanDia:
    fecha: date
    despertar: datetime
    acostarse: datetime
    bloques: list[Bloque] = field(default_factory=list)


def _hm(s: str) -> timedelta:
    h, m = s.split(":")
    return timedelta(hours=int(h), minutes=int(m))


def _redondear(t: datetime) -> datetime:
    """Redondea al paso de 15 minutos más cercano."""
    base = datetime.combine(t.date(), time())
    pasos = round((t - base) / PASO)
    return base + pasos * PASO


def _uniforme(rng: np.random.Generator, rango: list[str]) -> timedelta:
    a, b = _hm(rango[0]), _hm(rango[1])
    if b < a:  # rango que cruza la medianoche
        b += timedelta(days=1)
    return a + (b - a) * rng.random()


def _horas(rng: np.random.Generator, valor) -> timedelta:
    if isinstance(valor, list):
        return timedelta(hours=float(rng.uniform(valor[0], valor[1])))
    return timedelta(hours=float(valor))


def generar_plan(cfg: dict, fecha: date, rng: np.random.Generator,
                 acostarse_anterior: datetime | None) -> PlanDia:
    hor = cfg["horarios"]
    dia = fecha.weekday()
    medianoche = datetime.combine(fecha, time())

    rango_despertar = hor["despertar_viernes"] if dia == 4 else hor["despertar"]
    despertar = _redondear(medianoche + _uniforme(rng, rango_despertar))
    if acostarse_anterior is not None:
        minimo = acostarse_anterior + timedelta(hours=hor["horas_sueno_min"])
        despertar = max(despertar, _redondear(minimo + PASO / 2))
    acostarse = _redondear(medianoche + timedelta(days=1) + _uniforme(rng, hor["acostarse"]))

    bloques: list[Bloque] = []

    for bf in cfg["bloques_fijos"]:
        if dia not in bf["dias"]:
            continue
        if rng.random() >= bf.get("p_ocurre", 1.0):
            continue
        if "inicio_rango" in bf:
            inicio = medianoche + _uniforme(rng, bf["inicio_rango"])
            fin = medianoche + timedelta(days=1) + _uniforme(rng, bf["fin_rango"])
        else:
            ini = bf["inicio"]
            ini = ini[int(rng.integers(len(ini)))] if isinstance(ini, list) else ini
            inicio = medianoche + _hm(ini)
            fin = medianoche + _hm(bf["fin"]) if "fin" in bf else inicio + _horas(rng, bf["duracion_h"])
        if inicio < despertar + timedelta(minutes=30):  # p. ej. domingo tras la salida
            duracion = fin - inicio
            inicio = despertar + timedelta(minutes=30)
            fin = inicio + duracion
        inicio, fin = _redondear(inicio), _redondear(fin)
        bloques.append(Bloque(inicio, fin, bf["actividad"], bf["titulo"],
                              bool(rng.random() < bf.get("p_calendario", 1.0)), "fijo"))

    for ev in cfg["eventos"]:
        if dia not in ev["dias"] or rng.random() >= ev["p"]:
            continue
        for _ in range(15):  # busca un hueco libre
            inicio = _redondear(medianoche + _uniforme(rng, ev["ventana"]))
            fin = _redondear(inicio + _horas(rng, ev["duracion_h"]))
            cand = Bloque(inicio, fin, ev["actividad"], ev["titulo"],
                          bool(rng.random() < ev.get("p_calendario", 1.0)), "evento")
            if inicio >= despertar + timedelta(minutes=30) and not any(cand.solapa(b) for b in bloques):
                bloques.append(cand)
                break

    bloques.sort(key=lambda b: b.inicio)
    if bloques:
        acostarse = max(acostarse, max(b.fin for b in bloques) + timedelta(minutes=30))
    return PlanDia(fecha, despertar, acostarse, bloques)


def generar_planes(cfg: dict, fecha_inicio: date, n_dias: int,
                   rng: np.random.Generator) -> list[PlanDia]:
    planes: list[PlanDia] = []
    anterior = None
    for i in range(n_dias):
        plan = generar_plan(cfg, fecha_inicio + timedelta(days=i), rng, anterior)
        planes.append(plan)
        anterior = plan.acostarse
    return planes
