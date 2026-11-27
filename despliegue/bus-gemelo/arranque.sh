#!/bin/sh
# Crea el fichero de contraseñas a partir de USUARIOS («usuario:clave usuario:clave …»),
# que llega del fichero .env, y arranca Mosquitto. Las contraseñas no se guardan en el repositorio.
set -e
rm -f /mosquitto/passwd
for par in $USUARIOS; do
  mosquitto_passwd -b $( [ -f /mosquitto/passwd ] || echo -c ) /mosquitto/passwd "${par%%:*}" "${par#*:}"
done
chown mosquitto:mosquitto /mosquitto/passwd
chmod 0700 /mosquitto/passwd
exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
