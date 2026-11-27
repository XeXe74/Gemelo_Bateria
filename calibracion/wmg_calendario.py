"""Calibración y validación de la ley de envejecimiento por calendario (MOD-002) con los ensayos de WMG.

Datos: data/ensayos/wmg/calAnal_<nivel>Per_<temp>degC.mat (AK y Widanage, 2024), 13 niveles de carga a
0, 25 y 45 °C. Se usa la capacidad media de las celdas en cada revisión (Capacity.meanCap) y los días
transcurridos (Time_d).

Se comparan dos formas de la ley, ambas con la dependencia de Arrhenius en la temperatura y una potencia
del tiempo:
    A: pérdida = k · f(T) · exp(alfa · (nivel − 0,5)) · t^z   (la de la ficha MOD-002 v1.0)
    B: pérdida = k · f(T) · g(nivel) · t^z, con g una curva por tramos sacada de los datos
Se ajusta con dos de cada tres niveles de carga y se valida con el tercero (ficha MOD-002): la métrica es el
error relativo de la pérdida al final de cada ensayo reservado, que tiene que ser inferior al 20 %.

Uso (desde la carpeta Gemelo_Bateria): .venv/Scripts/python -m calibracion.wmg_calendario
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import scipy.io as sio
from scipy.optimize import least_squares

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "data" / "ensayos" / "wmg"
R = 8.314
T_REF = 298.15
NIVELES = [0, 2, 5, 10, 30, 50, 60, 70, 80, 85, 90, 95, 100]
VALIDACION = NIVELES[2::3]  # uno de cada tres: 5, 50, 80 y 95 %
CALIBRACION = [n for n in NIVELES if n not in VALIDACION]


def cargar() -> list[dict]:
    ensayos = []
    for f in sorted(DATOS.glob("calAnal_*.mat")):
        nivel, temp = map(int, re.findall(r"calAnal_(\d+)Per_(\d+)degC", f.name)[0])
        r = sio.loadmat(f, squeeze_me=True, struct_as_record=False, variable_names=["calResults"])["calResults"]
        cap = np.atleast_1d(r.Capacity.meanCap).astype(float)
        dias = np.atleast_1d(r.Time_d).astype(float)
        ok = np.isfinite(cap) & np.isfinite(dias)
        cap, dias = cap[ok], dias[ok]
        perdida = 1 - cap / cap[0]
        ensayos.append({"nivel": nivel, "temp": temp, "dias": dias[1:], "perdida": perdida[1:]})
    return ensayos


def f_temp(temp_c, ea):
    return np.exp(-ea / R * (1 / (temp_c + 273.15) - 1 / T_REF))


def modelo_a(p, e):
    k, ea, alfa, z = p
    return k * f_temp(e["temp"], ea) * np.exp(alfa * (e["nivel"] / 100 - 0.5)) * e["dias"] ** z


def g_tramos(nivel, nodos, valores):
    return np.interp(nivel, nodos, valores)


def modelo_b(p, e, nodos):
    k, ea, z = p[:3]
    g = np.concatenate([[1.0], p[3:]])  # g = 1 en el primer nodo (0 %): normaliza k
    return k * f_temp(e["temp"], ea) * g_tramos(e["nivel"], nodos, g) * e["dias"] ** z


def modelo_c(p, e, nodos):
    """Forma B más un desgaste común a todos los ensayos, b · t^zb, que no depende de la temperatura ni del
    nivel: el de las propias revisiones de capacidad, que cargan y descargan la celda periódicamente."""
    b, zb = p[:2]
    return b * e["dias"] ** zb + modelo_b(p[2:], e, nodos)


TEMPS = (0, 25, 45)


def modelo_d(p, e, nodos):
    """Desgaste común de las revisiones más una tabla de velocidades de envejecimiento por temperatura y
    nivel de carga (una curva del nivel para cada temperatura): pérdida = b · t^zb + v(T, nivel) · t^z.
    Entre temperaturas se interpola con Arrhenius sobre el logaritmo de la velocidad."""
    b, zb, z = p[:3]
    tabla = np.exp(np.asarray(p[3:]).reshape(len(TEMPS), len(nodos)))
    v_t = [np.interp(e["nivel"], nodos, fila) for fila in tabla]
    inv = [1 / (t + 273.15) for t in TEMPS]
    v = np.exp(np.interp(1 / (e["temp"] + 273.15), inv[::-1], np.log(v_t)[::-1]))
    return b * e["dias"] ** zb + v * e["dias"] ** z


def ajustar(ensayos, forma):
    cal = [e for e in ensayos if e["nivel"] in CALIBRACION]
    if forma == "D":
        nodos = np.array(CALIBRACION, dtype=float)
        n = len(TEMPS) * len(nodos)
        p0 = [1e-3, 0.5, 0.5] + [np.log(1e-3)] * n
        lo = [0, 0.1, 0.2] + [np.log(1e-7)] * n
        hi = [1, 1.0, 1.0] + [np.log(1e-1)] * n
        res = least_squares(lambda p: np.concatenate([modelo_d(p, e, nodos) - e["perdida"] for e in cal]), p0, bounds=(lo, hi))
        return res.x, (lambda e: modelo_d(res.x, e, nodos))
    if forma == "C":
        nodos = np.array(CALIBRACION, dtype=float)
        p0 = [1e-3, 0.5, 1e-4, 5e4, 0.5] + [1.0] * (len(nodos) - 1)
        lo = [0, 0.1, 1e-8, 0, 0.2] + [0.05] * (len(nodos) - 1)
        hi = [1, 1.0, 1, 2e5, 1.0] + [20] * (len(nodos) - 1)
        res = least_squares(lambda p: np.concatenate([modelo_c(p, e, nodos) - e["perdida"] for e in cal]), p0, bounds=(lo, hi))
        return res.x, (lambda e: modelo_c(res.x, e, nodos))
    if forma == "A":
        p0, lo, hi = [1e-3, 3e4, 1.0, 0.5], [1e-6, 0, -5, 0.2], [1, 1.5e5, 5, 1.0]
        res = least_squares(lambda p: np.concatenate([modelo_a(p, e) - e["perdida"] for e in cal]), p0, bounds=(lo, hi))
        return res.x, (lambda e: modelo_a(res.x, e))
    nodos = np.array(CALIBRACION, dtype=float)
    p0 = [1e-3, 3e4, 0.5] + [1.0] * (len(nodos) - 1)
    lo, hi = [1e-6, 0, 0.2] + [0.1] * (len(nodos) - 1), [1, 1.5e5, 1.0] + [10] * (len(nodos) - 1)
    res = least_squares(lambda p: np.concatenate([modelo_b(p, e, nodos) - e["perdida"] for e in cal]), p0, bounds=(lo, hi))
    return res.x, (lambda e: modelo_b(res.x, e, nodos))


def evaluar(ensayos, prediccion):
    filas = []
    for e in ensayos:
        real, pred = e["perdida"][-1], prediccion(e)[-1]
        filas.append({"nivel": e["nivel"], "temp": e["temp"], "dias": float(e["dias"][-1]), "real_pct": 100 * real,
                      "prevista_pct": 100 * pred, "error_rel_pct": 100 * abs(pred - real) / real,
                      "uso": "validación" if e["nivel"] in VALIDACION else "calibración"})
    return filas


def main() -> None:
    ensayos = cargar()
    print(f"{len(ensayos)} ensayos · calibración con niveles {CALIBRACION} · validación con {VALIDACION}")
    resultados = {}
    for forma in ("A", "B", "C", "D"):
        p, pred = ajustar(ensayos, forma)
        filas = evaluar(ensayos, pred)
        val = [f["error_rel_pct"] for f in filas if f["uso"] == "validación"]
        cal = [f["error_rel_pct"] for f in filas if f["uso"] == "calibración"]
        resultados[forma] = {"parametros": [float(x) for x in p], "filas": filas}
        print(f"\nForma {forma}: parámetros {np.round(p, 4).tolist()}")
        print(f"  calibración: error relativo medio {np.mean(cal):.1f} %, máximo {np.max(cal):.1f} %")
        print(f"  validación:  error relativo medio {np.mean(val):.1f} %, máximo {np.max(val):.1f} % "
              f"({sum(v < 20 for v in val)} de {len(val)} por debajo del 20 %)")
        for f in filas:
            if f["uso"] == "validación":
                print(f"    {f['temp']} °C, {f['nivel']} %: real {f['real_pct']:.1f} %, prevista {f['prevista_pct']:.1f} %, "
                      f"error {f['error_rel_pct']:.0f} %")
    salida = RAIZ / "data" / "calibracion"
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "wmg_calendario.json").write_text(json.dumps({"calibracion": CALIBRACION, "validacion": VALIDACION,
                                                             "formas": resultados}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
