"""Conexión de un servicio del gemelo con su bus de mensajes (GDB-DIS, DD-06 y DD-14).

Cada servicio:
    - se conecta con su usuario y su contraseña, con MQTT 5 y sesión persistente;
    - deja registrado un último aviso («caido»), que el bus publica si se desconecta sin cerrar;
    - publica su estado («activo», «detenido») como mensaje retenido y un latido cada 10 s;
    - anota sus arranques, paradas y fallos en el registro, con los campos de syslog;
    - se detiene de forma ordenada con la orden de parada del sistema o con SIGTERM.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
from typing import Callable

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from .senales import ahora_utc

QOS = 1
LATIDO_S = 10
# Gravedades de syslog (RFC 5424) que se usan
GRAVEDADES = {"error": 3, "aviso": 4, "info": 6}

T_ESTADO = "gemelo/sistema/estado/{}"
T_LATIDO = "gemelo/sistema/latido/{}"
T_REGISTRO = "gemelo/sistema/registro"
T_PARADA = "gemelo/sistema/parada"
T_NOTIFICACION = "gemelo/notificaciones/entrada"


def marcar_vivo() -> None:
    """Marca que usa la comprobación de salud del contenedor (healthcheck de Docker Compose)."""
    with open("/tmp/vivo", "w") as f:
        f.write(ahora_utc())


def conectar(nombre: str, host: str, puerto: int, usuario: str, clave: str,
             tema_estado: str, al_conectar: Callable[[mqtt.Client, bool], None]) -> mqtt.Client:
    """Cliente MQTT 5 con sesión persistente, último aviso y reconexión automática."""
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=nombre, protocol=mqtt.MQTTv5)
    c.username_pw_set(usuario, clave)
    c.will_set(tema_estado, json.dumps({"estado": "caido"}), qos=QOS, retain=True)
    c.reconnect_delay_set(1, 10)
    prop = Properties(PacketTypes.CONNECT)
    prop.SessionExpiryInterval = 24 * 3600  # la sesión se conserva un día si el servicio cae

    def on_connect(cliente, _u, flags, codigo, _p):
        if codigo.is_failure:
            logging.error("%s: conexión rechazada por %s:%s (%s)", nombre, host, puerto, codigo)
            return
        cliente.publish(tema_estado, json.dumps({"estado": "activo", "instante_real": ahora_utc()}),
                        qos=QOS, retain=True)
        al_conectar(cliente, bool(flags.session_present))

    c.on_connect = on_connect
    c.connect_async(host, puerto, keepalive=30, clean_start=False, properties=prop)
    return c


class Servicio:
    """Servicio del gemelo conectado al bus del gemelo."""

    def __init__(self, nombre: str, suscripciones: list[str] | None = None):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        self.nombre = nombre
        self.suscripciones = list(suscripciones or []) + [T_PARADA]
        self.manejadores: dict[str, Callable[[str, bytes], None]] = {}
        self.fin = threading.Event()
        self.cliente = conectar(
            nombre, os.environ.get("BUS_HOST", "mosquitto"), int(os.environ.get("BUS_PUERTO", "1883")),
            os.environ["BUS_USUARIO"], os.environ["BUS_CLAVE"], T_ESTADO.format(nombre), self._al_conectar)
        self.cliente.on_message = self._al_recibir
        signal.signal(signal.SIGTERM, lambda *_: self.fin.set())
        signal.signal(signal.SIGINT, lambda *_: self.fin.set())
        self.al_conectar: Callable[[bool], None] | None = None

    # --- Publicación ------------------------------------------------------------------

    def publicar(self, tema: str, carga: str | bytes, retener: bool = False) -> None:
        self.cliente.publish(tema, carga, qos=QOS, retain=retener)

    def registro(self, gravedad: str, mensaje: str) -> None:
        """Evento del registro de auditoría, con los campos de syslog."""
        getattr(logging, {"error": "error", "aviso": "warning"}.get(gravedad, "info"))(mensaje)
        self.publicar(T_REGISTRO, json.dumps({
            "instante": ahora_utc(), "maquina": socket.gethostname(), "servicio": self.nombre,
            "gravedad": gravedad, "codigo_gravedad": GRAVEDADES[gravedad], "mensaje": mensaje,
        }, ensure_ascii=False))

    def notificar(self, nivel: str, mensaje: str, destino: str = "operador", **datos) -> None:
        """Aviso para el servicio de notificaciones: nivel «informacion», «aviso» o «alarma»."""
        self.publicar(T_NOTIFICACION, json.dumps({
            "instante": ahora_utc(), "origen": self.nombre, "nivel": nivel, "destino": destino,
            "mensaje": mensaje, "datos": datos,
        }, ensure_ascii=False))

    # --- Suscripción ------------------------------------------------------------------

    def al_recibir(self, filtro: str, funcion: Callable[[str, bytes], None]) -> None:
        self.manejadores[filtro] = funcion
        if filtro not in self.suscripciones:
            self.suscripciones.append(filtro)

    def _al_conectar(self, cliente: mqtt.Client, sesion_previa: bool) -> None:
        for filtro in self.suscripciones:
            cliente.subscribe(filtro, qos=QOS)
        self.registro("info", f"Conectado al bus del gemelo (sesión previa: {'sí' if sesion_previa else 'no'})")
        if self.al_conectar:
            self.al_conectar(sesion_previa)

    def _al_recibir(self, _c, _u, msg: mqtt.MQTTMessage) -> None:
        if msg.topic == T_PARADA:
            self.registro("info", "Orden de parada recibida")
            self.fin.set()
            return
        for filtro, funcion in self.manejadores.items():
            if mqtt.topic_matches_sub(filtro, msg.topic):
                try:
                    funcion(msg.topic, msg.payload)
                except Exception as e:  # un mensaje erróneo no debe tirar el servicio
                    self.registro("error", f"Error al procesar {msg.topic}: {e!r}")

    # --- Ciclo de vida ----------------------------------------------------------------

    def ejecutar(self, tarea_periodica: Callable[[], None] | None = None, periodo_s: float = 1.0) -> None:
        """Arranca el bucle de red y el latido, y espera hasta la parada."""
        self.cliente.loop_start()
        self.registro("info", "Arranque del servicio")
        siguiente_latido = 0.0
        reloj = 0.0
        while not self.fin.wait(periodo_s if tarea_periodica else 1.0):
            reloj += periodo_s if tarea_periodica else 1.0
            if tarea_periodica:
                try:
                    tarea_periodica()
                except Exception as e:
                    self.registro("error", f"Error en la tarea periódica: {e!r}")
            if self.cliente.is_connected():
                marcar_vivo()
            if reloj >= siguiente_latido:
                self.publicar(T_LATIDO.format(self.nombre), json.dumps({"instante_real": ahora_utc()}))
                siguiente_latido = reloj + LATIDO_S
        self.detener()

    def detener(self) -> None:
        self.registro("info", "Parada del servicio")
        info = self.cliente.publish(T_ESTADO.format(self.nombre),
                                    json.dumps({"estado": "detenido", "instante_real": ahora_utc()}),
                                    qos=QOS, retain=True)
        try:
            info.wait_for_publish(2)
        except (RuntimeError, ValueError):
            pass
        self.cliente.disconnect()
        self.cliente.loop_stop()
