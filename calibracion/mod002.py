"""Calibración de MOD-002: reúne las tres fuentes y guarda los parámetros en gemelo/parametros/mod002.json.

    1. Calendario: tabla de la forma D ajustada con WMG (calibracion/wmg_calendario.py).
    2. Ciclado, forma: pesos por zona de carga que reproducen el desgaste por ciclo de las tres ventanas de la
       figura S1e de Preger et al. (2020) (calibracion/sandia_figura_s1e.py). Se supone simétrico: los dos
       extremos (por debajo del 20 % y por encima del 80 %) pesan lo mismo, ya que la figura no los separa.
    3. Ciclado, magnitud: escala que hace que una batería ciclada del 0 al 100 % a 25 °C conserve el 80 % a
       los 1000 ciclos completos, como declara Apple para el iPhone 15. Se supone un ensayo continuo de unas
       3 horas por ciclo (carga a 0,5C y descarga a 1C), por lo que el envejecimiento por calendario durante
       el ensayo es pequeño pero se descuenta.
Comprobación cruzada: el artículo indica que, a 200 ciclos, las celdas NMC cicladas del 0 al 100 % tienen
entre un 5 y un 10 % menos de capacidad que las cicladas en ventanas intermedias.

Uso (desde la carpeta Gemelo_Bateria): .venv/Scripts/python -m calibracion.mod002
"""
from __future__ import annotations

import csv
import json

import numpy as np

from gemelo.degradacion import EstadoDegradacion, Leyes

from .wmg_calendario import CALIBRACION, RAIZ, TEMPS

ZONAS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
VIDA_APPLE_CICLOS = 1000
SALUD_APPLE = 0.80
HORAS_POR_CICLO = 3.0


def calendario() -> dict:
    d = json.loads((RAIZ / "data" / "calibracion" / "wmg_calendario.json").read_text(encoding="utf-8"))
    p = d["formas"]["D"]["parametros"]
    tabla = np.asarray(p[3:]).reshape(len(TEMPS), len(CALIBRACION))
    return {"temps": list(TEMPS), "niveles": [float(n) for n in CALIBRACION], "log_v": tabla.tolist(), "z": p[2]}


def ciclado_forma() -> tuple[list[float], dict]:
    """Pesos por zona (fracción de capacidad por ciclo completo) a partir de la pérdida por ciclo de cada ventana."""
    puntos = list(csv.DictReader(open(RAIZ / "data" / "ensayos" / "sandia" / "figura_S1e_puntos.csv", encoding="utf-8")))
    tasa = {}
    for v in ("40-60", "20-80", "0-100"):
        x = np.array([float(p["efc"]) for p in puntos if p["ventana"] == v])
        y = (100 - np.array([float(p["capacidad_pct"]) for p in puntos if p["ventana"] == v])) / 100
        tasa[v] = float(x @ y / (x @ x))  # recta por el origen: pérdida por ciclo
    # Media del peso en cada ventana: 40-60 -> A; 20-80 -> (0,2 A + 0,4 B) / 0,6; 0-100 -> 0,2 A + 0,4 B + 0,4 C
    a = tasa["40-60"]
    b = (tasa["20-80"] * 0.6 - 0.2 * a) / 0.4
    c = (tasa["0-100"] - 0.2 * a - 0.4 * b) / 0.4
    return [c, b, a, b, c], tasa


def main() -> None:
    cal = calendario()
    pesos, tasa = ciclado_forma()
    print("Pérdida por cada 100 ciclos en la figura S1e: "
          + ", ".join(f"{v} %: {100 * 100 * t:.2f} puntos" for v, t in tasa.items()))
    print(f"Pesos por zona (puntos por cada 100 ciclos): extremos {100 * 100 * pesos[0]:.2f}, "
          f"intermedia {100 * 100 * pesos[1]:.2f}, central {100 * 100 * pesos[2]:.2f}")
    dif_200 = 100 * 200 * (tasa["0-100"] - tasa["40-60"])
    print(f"Comprobación cruzada: a 200 ciclos, la ventana 0-100 % pierde {dif_200:.1f} puntos más que la 40-60 % "
          f"(el artículo indica entre 5 y 10): {'coincide' if 5 <= dif_200 <= 10 else 'NO coincide'}")

    # Escala del ciclado: ensayo de Apple simulado con escala 1 y despejada la escala (la pérdida es lineal en ella)
    leyes = Leyes(**cal, zonas=ZONAS, pesos=pesos, escala_ciclado=1.0)
    est = EstadoDegradacion(leyes)
    pasos = 20
    for _ in range(VIDA_APPLE_CICLOS):
        for nivel in (np.linspace(0, 100, pasos + 1), np.linspace(100, 0, pasos + 1)):
            for n0, n1 in zip(nivel[:-1], nivel[1:]):
                est.paso(n0, n1, 25.0, HORAS_POR_CICLO / (2 * pasos))
    escala = (1 - SALUD_APPLE - est.perdida_cal) / est.perdida_cic
    print(f"Ensayo de Apple: calendario {100 * est.perdida_cal:.1f} puntos en {VIDA_APPLE_CICLOS * HORAS_POR_CICLO / 24:.0f} días; "
          f"escala del ciclado {escala:.3f} (Sandia frente a iPhone 15)")
    leyes.escala_ciclado = float(escala)
    leyes.guardar()
    print("Guardado gemelo/parametros/mod002.json")


if __name__ == "__main__":
    main()
