"""Validación de MOD-002 con la batería real del iPhone 15 (configuración as-built de la maqueta).

Dato real, no usado en la calibración: salud del 86 % el 16/11/2026, con unos 1005 días desde la fabricación
(febrero de 2024), en uso desde mayo de 2024 y 610 ciclos. El 86 % es un entero, por lo que la salud real está
entre el 85,5 y el 86,5 %, es decir, una pérdida de 13,5 a 14,5 puntos.

Historia supuesta:
    - 80 días almacenada desde la fabricación hasta el primer uso, al 50 % y a 20 °C;
    - 925 días de uso con la rutina del usuario (año de uso s0 del simulador, repetido) y la carga optimizada
      de iOS, que el móvil tenía activada. Es la predicción oficial, fijada antes de calcular. Las estrategias
      de cargar siempre al 100 % y siempre al 80 % se calculan como límites.
Métrica (ficha MOD-002): error relativo de la pérdida prevista frente a la real, inferior al 20 %.

Uso (desde la carpeta Gemelo_Bateria): .venv/Scripts/python -m calibracion.validacion_iphone
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from gemelo.degradacion import EstadoDegradacion, Leyes
from sim.generar_uso import leer_uso, ruta_uso
from sim.simulador import politica_fija, politica_optimizada, simular

from .wmg_calendario import RAIZ

DIAS_ALMACEN, NIVEL_ALMACEN, TEMP_ALMACEN = 80, 50.0, 20.0
DIAS_USO = 925
PERDIDA_REAL = 14.0  # puntos (86 % de salud)
CICLOS_REALES = 610


def historia(pasos: pd.DataFrame) -> tuple[float, float, EstadoDegradacion]:
    """Pérdida prevista (puntos) y ciclos equivalentes tras el almacén y DIAS_USO días de uso."""
    est = EstadoDegradacion(Leyes.cargar())
    for _ in range(DIAS_ALMACEN):
        est.paso(NIVEL_ALMACEN, NIVEL_ALMACEN, TEMP_ALMACEN, 24.0)
    niveles = pasos["nivel"].to_numpy(dtype=float)
    temps = pasos["temperatura"].to_numpy(dtype=float)
    n_pasos = DIAS_USO * 96
    ciclos = 0.0
    anterior = niveles[-1]
    for i in range(n_pasos):
        j = i % len(niveles)
        est.paso(anterior, niveles[j], temps[j], 0.25)
        ciclos += abs(niveles[j] - anterior) / 200
        anterior = niveles[j]
    return 100 * (1 - est.salud), ciclos, est


def politica_es_prediccion(nombre: str) -> bool:
    return nombre.startswith("carga optimizada")


def main() -> None:
    prediccion = None
    datos = leer_uso(ruta_uso(0))
    resultados = {}
    for nombre, politica in (("carga optimizada (predicción)", politica_optimizada()),
                             ("siempre 100 %", politica_fija(100)), ("siempre 80 %", politica_fija(80))):
        pasos = simular(datos, politica)["pasos"]
        perdida, ciclos, est = historia(pasos)
        if politica_es_prediccion(nombre):
            prediccion = est
        error = abs(perdida - PERDIDA_REAL) / PERDIDA_REAL
        resultados[nombre] = (perdida, ciclos, error)
        print(f"{nombre}: pérdida prevista {perdida:.1f} puntos (real {PERDIDA_REAL:.0f}), error relativo {100 * error:.0f} %, "
              f"ciclos {ciclos:.0f} (reales {CICLOS_REALES})")
    perdida, _, error = resultados["carga optimizada (predicción)"]
    print(f"\nValidación: {'CUMPLE' if error < 0.20 else 'NO CUMPLE'} el criterio del 20 % "
          f"(salud prevista {100 - perdida:.1f} %, real 86 %)")
    # Reparto de la pérdida de la historia entre calendario y ciclado, según el modelo: lo usan el factor propio
    # del gemelo y la batería simulada para empezar en el mismo estado que la batería real.
    leyes = Leyes.cargar()
    leyes.historia_inicial = {"perdida_cal": prediccion.perdida_cal, "perdida_cic": prediccion.perdida_cic,
                              "dias": DIAS_ALMACEN + DIAS_USO}
    leyes.guardar()
    print(f"Historia inicial del modelo: calendario {100 * prediccion.perdida_cal:.1f} puntos, "
          f"ciclado {100 * prediccion.perdida_cic:.1f} puntos (guardada en gemelo/parametros/mod002.json)")
    pd.DataFrame([{"estrategia": k, "perdida_prevista": round(v[0], 2), "ciclos": round(v[1]), "error_rel": round(v[2], 3)}
                  for k, v in resultados.items()]).to_csv(RAIZ / "data" / "calibracion" / "validacion_iphone.csv", index=False)


if __name__ == "__main__":
    main()
