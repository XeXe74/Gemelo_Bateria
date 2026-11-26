"""Paso 2: simula la batería sobre un Excel de uso y guarda el resultado en data/<etiqueta>/.

Primero hay que generar el Excel (paso 1): .venv/bin/python -m sim.generar_uso

Ejemplos (desde la carpeta Gemelo_Bateria):
    .venv/bin/python -m sim.run_sim --tope 100 --etiqueta anual_t100
    .venv/bin/python -m sim.run_sim --dias 28 --tope 100 --etiqueta prueba_28d
    .venv/bin/python -m sim.run_sim --semilla 3 --tope 80 --etiqueta s3_t80
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .generar_uso import RAIZ, leer_uso, ruta_uso
from .simulador import politica_fija, simular

DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Paso 2: batería simulada sobre un Excel de uso")
    ap.add_argument("--semilla", type=int, default=0, help="qué año usar: data/uso/uso_s<semilla>.xlsx")
    ap.add_argument("--uso", default=None, help="ruta a otro Excel de uso (en lugar de --semilla)")
    ap.add_argument("--dias", type=int, default=None, help="solo los primeros N días del Excel")
    ap.add_argument("--tope", type=int, default=100, help="tope fijo de carga (80–100)")
    ap.add_argument("--seed-unidad", type=int, default=1234, help="semilla de la batería (oculta)")
    ap.add_argument("--etiqueta", default=None)
    args = ap.parse_args()

    ruta = Path(args.uso) if args.uso else ruta_uso(args.semilla)
    if not ruta.exists():
        raise SystemExit(f"No existe {ruta}. Genera antes el Excel: python -m sim.generar_uso")
    datos = leer_uso(ruta)
    if args.dias:
        datos = datos.recortar(args.dias)
    res = simular(datos, politica_fija(args.tope), args.seed_unidad)

    etiqueta = args.etiqueta or f"s{datos.semilla}_d{datos.n_dias}_t{args.tope}"
    salida = RAIZ / "data" / etiqueta
    salida.mkdir(parents=True, exist_ok=True)
    for nombre in ("pasos", "dias", "calendario", "eventos_reales", "decisiones"):
        res[nombre].to_csv(salida / f"{nombre}.csv", index=False)
    ocultos = res["ocultos"] | {"uso": ruta.name, "seed_unidad": args.seed_unidad}
    (salida / "ocultos.json").write_text(json.dumps(ocultos, indent=2), encoding="utf-8")

    dias = res["dias"]
    print(f"Guardado en {salida}")
    print(f"Uso: {ruta.name} · {len(dias)} días · tope fijo {args.tope} %")
    print("Consumo medio por día de la semana (%):")
    medias = dias.groupby("dia_semana")["consumo_dia"].agg(["mean", "min", "max"]).round(0)
    for d, fila in medias.iterrows():
        print(f"  {DIAS[d]}: media {fila['mean']:.0f}  (mín {fila['min']:.0f}, máx {fila['max']:.0f})")
    print(f"Días con recarga de emergencia: {(dias['recargas_emergencia'] > 0).sum()} · "
          f"con carga antes de salir: {(dias['recargas_antes_de_salir'] > 0).sum()} · "
          f"en el coche: {(dias['recargas_coche'] > 0).sum()} · en clase: {(dias['recargas_clase'] > 0).sum()}")
    print(f"Incidentes (bajar del 5 % fuera de casa): {int(dias['incidente'].sum())}")
    print(f"Salud real: {dias['soh_real'].iloc[0]:.2f} % -> {dias['soh_real'].iloc[-1]:.2f} %")
    oc = res["ocultos"]
    print(f"Pérdida por calendario {100 * oc['perdida_cal']:.2f} pts · por ciclado "
          f"{100 * oc['perdida_cyc']:.2f} pts · ciclos equivalentes {oc['ciclos_eq']:.0f}")


if __name__ == "__main__":
    main()
