# Gemelo digital de la batería del móvil

Proyecto de Gemelos Digitales (UIE). Ver la propuesta en
`documentos/S01_Propuesta_Gemelo_Bateria_Tuset_Mario_v2.docx` y el contexto completo en `CONTEXTO.md`.

## Gemelo en contenedores

El gemelo completo se levanta con Docker Compose (diseño GDB-DIS). Hace falta Docker Desktop y un fichero
`.env` con las contraseñas: cópialo de `.env.ejemplo` y cambia los valores.

```powershell
# Producción
.\herramientas\gemelo.ps1 arrancar
# Desarrollo: publica además los buses y el histórico en 1883, 1884 y 8086
.\herramientas\gemelo.ps1 arrancar -Entorno desarrollo
.\herramientas\gemelo.ps1 estado
.\herramientas\gemelo.ps1 parar
# Copia de los datos en copias\ y restauración
.\herramientas\gemelo.ps1 copia
.\herramientas\gemelo.ps1 restaurar -Copia copias\produccion-AAAAMMDD-HHMM
```

El panel está en http://127.0.0.1:1880/dashboard (usuario `lector`) y el editor de Node-RED en
http://127.0.0.1:1880 (usuario `administrador`). Las contraseñas son las del `.env`.

| Contenedor | Qué hace | Código |
|---|---|---|
| bus-activo | Bus MQTT del activo, en su propia red | `despliegue/bus-activo/` |
| simulador | Móvil, cargador y calendario simulados; lee el Excel de uso | `servicios/simulador/`, `sim/` |
| conector | Único punto de paso entre las dos redes: valida y publica las señales con V/Q/K | `servicios/conector/` |
| mosquitto | Bus MQTT del gemelo | `despliegue/bus-gemelo/` |
| influxdb, telegraf | Histórico con los cubos «bruto», «gemelo» y «registro» | `despliegue/influxdb/`, `despliegue/telegraf/` |
| decision | Decide el límite de cada noche (regla provisional hasta tener los modelos) | `servicios/decision/`, `gemelo/` |
| nodered | Panel con las páginas Estado, Operación y Administración | `servicios/nodered/` |

Si el simulador se detiene, al volver continúa desde el último punto de guardado. Para empezar el año
simulado desde el principio, borra los volúmenes con `docker compose -p gdb-produccion down -v` (se pierde
el histórico). Los flujos del panel se generan con `herramientas/panel_flujos.py`, pero también se pueden
editar en Node-RED: los cambios se guardan en `servicios/nodered/flujos/flows.json`.

## Activo simulado en dos pasos

1. **Paso 1. Generador de uso → Excel fijo** (`sim/generar_uso.py`). Inventa un año de vida del usuario
   (rutina, eventos aleatorios, imprevistos) y lo guarda en `data/uso/uso_s<semilla>.xlsx`. No depende de las
   decisiones de carga, así que se genera una vez.
2. **Paso 2. Móvil + cargador simulados** (`sim/simulador.py`). Lee el Excel fila a fila y calcula lo que sí
   depende de las decisiones: nivel, recargas, incidentes y salud. Cada noche pregunta el tope a una política
   (un tope fijo o la regla del gemelo). El servicio del simulador usa el mismo código, paso a paso
   (`sim/activo.py`).

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
