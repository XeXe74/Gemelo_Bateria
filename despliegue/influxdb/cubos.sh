#!/bin/bash
# Se ejecuta una sola vez, tras la configuración inicial de InfluxDB. Crea los otros dos
# cubos del histórico (GDB-DIS, DD-15): «bruto» (lago de datos) y «registro». El cubo
# «gemelo» lo crea la propia configuración inicial. Los datos no caducan.
set -e
influx bucket create --name bruto --org "$DOCKER_INFLUXDB_INIT_ORG" --retention 0
influx bucket create --name registro --org "$DOCKER_INFLUXDB_INIT_ORG" --retention 0
