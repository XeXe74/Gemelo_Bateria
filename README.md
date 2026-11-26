# Gemelo digital de la batería del móvil

Proyecto de Gemelos Digitales (UIE). Ver la propuesta en
`documentos/S01_Propuesta_Gemelo_Bateria_Tuset_Mario_v2.docx` y el contexto completo en `CONTEXTO.md`.

## Estado actual: activo simulado en dos pasos

1. **Paso 1. Generador de uso → Excel fijo** (`sim/generar_uso.py`). Inventa un año de vida del usuario
   (rutina, eventos aleatorios, imprevistos) y lo guarda en `data/uso/uso_s<semilla>.xlsx`. No depende de las
   decisiones de carga, así que se genera una vez.
2. **Paso 2. Móvil + cargador simulados** (`sim/simulador.py`). Lee el Excel fila a fila y calcula lo que sí
   depende de las decisiones: nivel, recargas, incidentes y salud. Cada noche pregunta el tope a una política
   (ahora un tope fijo; más adelante, el gemelo).

Así, todas las estrategias se comparan sobre exactamente los mismos días.

Otros ficheros:

- `config/rutina.yaml`: rutina del usuario, consumos por actividad (provisionales) y comportamiento de carga.
- `sim/rutina.py`: genera los días concretos (bloques fijos, eventos aleatorios, calendario).
- `sim/bateria.py`: batería simulada con degradación por calendario y por ciclado (parámetros ocultos).
- `sim/run_sim.py`: lanza el paso 2 y guarda el resultado en `data/<etiqueta>/`.
- `sim/graficas.py`: gráficas de comprobación en `figuras/`.

## Uso

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Paso 1: año principal (semilla 0) y 10 años para la evaluación (semillas 0–9)
.venv/bin/python -m sim.generar_uso --semillas 0-9

# Paso 2: batería simulada sobre el año principal
.venv/bin/python -m sim.run_sim --dias 28 --tope 100 --etiqueta prueba_28d
.venv/bin/python -m sim.run_sim --tope 100 --etiqueta anual_t100
.venv/bin/python -m sim.run_sim --tope 80 --etiqueta anual_t80
.venv/bin/python -m sim.graficas
```

En Windows, cambia `.venv/bin/` por `.venv\Scripts\`.

## Hojas del Excel de uso

| Hoja | Contenido | ¿Lo ve el gemelo? |
|---|---|---|
| Resumen | Una fila por día: horas de levantarse y acostarse, eventos, imprevistos y gasto total | No (es para revisar) |
| Uso | Una fila cada 15 min: actividad, en casa, gasto (% de la batería inicial) y temperatura sin cargar | No: le llega a través del móvil simulado |
| Calendario | Eventos apuntados | Sí |
| Imprevistos | Eventos no apuntados | No |
| Info | Semilla y configuración usadas | No |

## Ficheros generados por el paso 2

| Fichero | Contenido | ¿Lo ve el gemelo? |
|---|---|---|
| `pasos.csv` | Una fila cada 15 min: actividad, nivel, carga, tope, temperatura, salud | Solo nivel, carga, temperatura y salud reportada |
| `dias.csv` | Resumen por día: consumo, recargas, incidentes | Sí (lo puede calcular) |
| `calendario.csv` | Eventos apuntados en el calendario | Sí |
| `eventos_reales.csv` | Todos los eventos, incluidos los imprevistos | No |
| `decisiones.csv` | Tope decidido cada noche | Sí |
| `ocultos.json` | Parámetros reales de degradación de la batería | No (solo para evaluar) |
