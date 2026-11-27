"""Prueba de MOD-001 en operación simulada: aprende del año simulado y decide cada noche con la regla del límite
más bajo cuya probabilidad de incidente no supera 1/90 (versión preliminar de MOD-003).

Compara los incidentes y la salud final con cargar siempre al 100 % y siempre al 80 %, y la probabilidad prevista
con la frecuencia de incidentes observada.

Uso (desde la carpeta Gemelo_Bateria): .venv/Scripts/python -m calibracion.prueba_mod001 [semilla]
"""
from __future__ import annotations

import sys
import time

import numpy as np

from gemelo.decision import LIMITES
from gemelo.uso import ModeloUso
from sim.generar_uso import leer_uso, ruta_uso
from sim.simulador import politica_fija, simular

RIESGO = 1 / 90


def main() -> None:
    semilla = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    datos = leer_uso(ruta_uso(semilla))
    modelo = ModeloUso()
    registro = []

    def observador(t, m):
        modelo.registrar(t, m["nivel"], m["enchufado"], m["modo_dormir"])

    def politica(ctx):
        modelo.calendario(ctx.manana, ctx.calendario_manana)
        prev = modelo.prevision(ctx.manana, ctx.calendario_manana, ctx.nivel, ctx.momento, semilla=len(registro))
        probs = prev["probabilidad"]
        limite = next((L for L in LIMITES if probs[L]["p"] <= RIESGO), 100)
        registro.append({"dia": ctx.manana, "limite": limite, "p": probs[limite]["p"], "consumo": prev["consumo"]["media"]})
        return limite

    t0 = time.perf_counter()
    r = simular(datos, politica, observador=observador)
    duracion = time.perf_counter() - t0
    dias = r["dias"].set_index("fecha")
    inc = {d: bool(dias.loc[d, "incidente"]) if d in dias.index else False for d in (x["dia"] for x in registro)}
    p_prevista = sum(x["p"] for x in registro)
    print(f"Año s{semilla} con MOD-001 (riesgo 1/90): {int(r['dias']['incidente'].sum())} incidentes, salud final "
          f"{r['dias']['soh_real'].iloc[-1]:.2f} % ({duracion:.0f} s)")
    print("  límites elegidos: " + ", ".join(f"{L} %: {sum(x['limite'] == L for x in registro)}" for L in LIMITES))
    print(f"  incidentes previstos (suma de probabilidades) {p_prevista:.1f} frente a {sum(inc.values())} observados")
    err = [abs(x["consumo"] - dias.loc[x["dia"], "consumo_dia"]) for x in registro if x["dia"] in dias.index]
    print(f"  error absoluto medio del consumo diario frente al simulador: {np.mean(err):.1f} puntos")
    for tope in (100, 80):
        b = simular(datos, politica_fija(tope))["dias"]
        print(f"  siempre {tope} %: {int(b['incidente'].sum())} incidentes, salud final {b['soh_real'].iloc[-1]:.2f} %")


if __name__ == "__main__":
    main()
