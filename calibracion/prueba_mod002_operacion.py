"""Prueba de MOD-002 en operación simulada (REQ-0160).

Se simula un año con el activo simulado, que usa la batería «real» con sus factores ocultos, y se pasan al modelo
del gemelo solo las señales que vería: nivel, temperatura, conexión, modo Dormir, límite aplicado y salud de
Ajustes (un entero al día). Cada día a las 12:00 se compara la salud real del simulador con el intervalo del 90 %
del modelo: tiene que contenerla en al menos el 90 % de los días.

Uso (desde la carpeta Gemelo_Bateria): .venv/Scripts/python -m calibracion.prueba_mod002_operacion
"""
from __future__ import annotations

import time

import numpy as np

from gemelo.degradacion import ModeloDegradacion
from sim.generar_uso import leer_uso, ruta_uso
from sim.simulador import politica_fija, simular


def probar(semilla_uso: int, seed_unidad: int, tope: int) -> dict:
    r = simular(leer_uso(ruta_uso(semilla_uso)), politica_fija(tope), seed_unidad=seed_unidad)
    pasos = r["pasos"]
    m = ModeloDegradacion()
    dentro, errores, anchos = [], [], []
    t0 = time.perf_counter()
    for f in pasos.itertuples():
        m.registrar(f.nivel, f.temperatura, int(f.enchufado), int(f.actividad == "dormir"), int(f.tope))
        if f.momento.hour == 12 and f.momento.minute == 0:
            m.observar_salud(int(f.soh_reportado))
            e = m.estimacion()
            dentro.append(e["inferior"] <= f.soh_real <= e["superior"])
            errores.append(abs(e["salud"] - f.soh_real))
            anchos.append(e["superior"] - e["inferior"])
    duracion = time.perf_counter() - t0
    t1 = time.perf_counter()
    fechas = {"observada": m.dias_hasta_80(), "siempre 100 %": m.dias_hasta_80(100)}
    coste = m.coste_limites()
    t_proy = time.perf_counter() - t1
    return {"cobertura": float(np.mean(dentro)), "error_medio": float(np.mean(errores)), "ancho_medio": float(np.mean(anchos)),
            "factor": m.estimacion()["factor"], "ocultos": r["ocultos"], "salud_final": float(pasos["soh_real"].iloc[-1]),
            "fechas": fechas, "coste": coste, "t_operacion": duracion, "t_proyeccion": t_proy}


def main() -> None:
    for semilla_uso, seed_unidad, tope in ((0, 1234, 100), (0, 1234, 80), (1, 7, 100), (2, 99, 90)):
        res = probar(semilla_uso, seed_unidad, tope)
        oc = res["ocultos"]
        print(f"Año s{semilla_uso}, unidad {seed_unidad}, límite {tope} % (factores ocultos: calendario {oc['f_cal']:.2f}, "
              f"ciclado {oc['f_cic']:.2f}; factor estimado {res['factor']:.2f})")
        print(f"  cobertura del intervalo del 90 %: {100 * res['cobertura']:.0f} % de los días · error medio "
              f"{res['error_medio']:.2f} puntos · ancho medio {res['ancho_medio']:.2f} puntos")
        f = res["fechas"]
        print(f"  días hasta el 80 % desde el final del año: estrategia observada {f['observada']['mediana']:.0f} "
              f"({f['observada']['inferior']:.0f}-{f['observada']['superior']:.0f}); siempre 100 % "
              f"{f['siempre 100 %']['mediana']:.0f} · salud real final {res['salud_final']:.2f} %")
        print("  coste de un día típico: " + ", ".join(f"{k} %: {v['puntos']:.4f} puntos ({v['euros']:.3f} €)"
                                                    for k, v in res["coste"].items()))
        print(f"  tiempo: {res['t_operacion']:.1f} s el año de medidas, {res['t_proyeccion']:.2f} s la proyección y el coste")


if __name__ == "__main__":
    main()
