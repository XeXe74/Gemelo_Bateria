"""Servicio del activo simulado (móvil, cargador y calendario) en el bus del activo.

Avanza el activo de sim/activo.py con un reloj simulado de velocidad ajustable y publica
sus mensajes según GDB-IDS-ACT y GDB-IDS-CAL:
    - activo/PHONE1/medidas cada 15 min simulados, con número de secuencia;
    - activo/USER1/calendario (retenido) con los eventos visibles del día siguiente;
    - activo/CHARGER1/confirmacion tras cada orden o tras aplicar el 100 % por defecto;
    - activo/sistema/estado (retenido y último aviso) y activo/sistema/latido cada 10 s.
Atiende la orden de límite, los eventos nuevos, la solicitud de histórico y el control de
la simulación. En la conexión nocturna detiene su reloj hasta recibir la orden o hasta
que pasan 5 s, y entonces aplica el 100 % (DD-12).
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import queue
import signal
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from sim.activo import Activo
from sim.generar_uso import leer_uso

ZONA = ZoneInfo("Europe/Madrid")
QOS = 1
PASO_S = 15 * 60
ESPERA_ORDEN_S = 5.0
LATIDO_S = 10
VELOCIDAD_MAX = 2880
INICIO_NOCHE, FIN_NOCHE = 20, 7  # franja de la conexión nocturna (hora peninsular)
ACTIVIDADES = ("casa", "clase", "clase_datos", "carplay", "gimnasio", "social", "cena", "cine",
               "noche_fuera", "salida_corta", "dia_fuera")

T_MEDIDAS = "activo/PHONE1/medidas"
T_ORDEN = "activo/CHARGER1/orden"
T_CONFIRMACION = "activo/CHARGER1/confirmacion"
T_CALENDARIO = "activo/USER1/calendario"
T_EVENTO_NUEVO = "activo/USER1/calendario/nuevo"
T_HIST_SOLICITUD = "activo/PHONE1/historico/solicitud"
T_HIST_RESPUESTA = "activo/PHONE1/historico/respuesta"
T_ESTADO = "activo/sistema/estado"
T_LATIDO = "activo/sistema/latido"
T_SIMULACION = "activo/sistema/simulacion"


def utc(t: datetime) -> str:
    """Hora peninsular sin zona -> UTC ISO 8601 con milisegundos."""
    t = t.replace(tzinfo=ZONA) if t.tzinfo is None else t
    t = t.astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def local(s: str) -> datetime:
    """UTC ISO 8601 -> hora peninsular sin zona."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(ZONA).replace(tzinfo=None)


def fecha_calendario(t: datetime) -> date:
    """Día siguiente al que se refiere el calendario en el instante t (hora peninsular).

    Es el día que vive el usuario tras la siguiente noche: hasta el final de la franja
    nocturna (7:00) es el propio día, y a partir de ahí, el día siguiente.
    """
    return t.date() if t.hour < FIN_NOCHE else t.date() + timedelta(days=1)


class ServicioSimulador:
    def __init__(self):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        ruta = Path(os.environ.get("USO", "/datos/uso/uso_s0.xlsx"))
        seed = int(os.environ.get("SEED_UNIDAD", "1234"))
        self.ruta_guardado = Path(os.environ.get("GUARDADO", "/estado/simulador.pkl"))
        # La versión de la batería invalida los puntos de guardado hechos con leyes de envejecimiento anteriores.
        self.clave = f"{ruta.name}|{seed}|{os.environ.get('DIAS', '')}|bateria-mod002-v1.1"
        self.velocidad = float(os.environ.get("VELOCIDAD", str(VELOCIDAD_MAX)))
        self.pausa = os.environ.get("PAUSA_INICIAL", "0") == "1"
        if not self._cargar():
            datos = leer_uso(ruta)
            if os.environ.get("DIAS"):
                datos = datos.recortar(int(os.environ["DIAS"]))
            self.activo = Activo(datos, seed)
            self.secuencia = 0
            self.historico: list[dict] = []  # medidas publicadas, para la solicitud de histórico
            self.version_cal = 0
            self.ordenes_aplicadas: set[str] = set()
            logging.info("Uso cargado: %s, %d días. Empieza el año simulado", ruta.name, datos.n_dias)
        self.entrada: queue.Queue = queue.Queue()
        self.fin = threading.Event()
        self.instante_real_arranque = utc(datetime.now(timezone.utc))

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="simulador", protocol=mqtt.MQTTv5)
        c.username_pw_set(os.environ["BUS_USUARIO"], os.environ["BUS_CLAVE"])
        c.will_set(T_ESTADO, json.dumps({"estado": "caido"}), qos=QOS, retain=True)
        c.reconnect_delay_set(1, 10)
        c.on_connect = self._al_conectar
        c.on_message = lambda _c, _u, m: self.entrada.put((m.topic, m.payload))
        prop = Properties(PacketTypes.CONNECT)
        prop.SessionExpiryInterval = 24 * 3600
        c.connect_async(os.environ.get("BUS_HOST", "bus-activo"), int(os.environ.get("BUS_PUERTO", "1883")),
                        keepalive=30, clean_start=False, properties=prop)
        self.cliente = c
        self.conectado = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: self.fin.set())
        signal.signal(signal.SIGINT, lambda *_: self.fin.set())

    # --- Punto de guardado -------------------------------------------------------------
    # El estado se guarda cada día simulado y al parar, para que tras un reinicio el activo
    # continúe donde estaba en lugar de empezar el año de nuevo.

    def _cargar(self) -> bool:
        if os.environ.get("REINICIAR") == "1" or not self.ruta_guardado.exists():
            return False
        try:
            g = pickle.loads(self.ruta_guardado.read_bytes())
        except Exception as e:
            logging.warning("No se pudo leer el punto de guardado (%r): empieza el año", e)
            return False
        if g.get("clave") != self.clave:
            logging.info("El punto de guardado es de otro año de uso o de otra versión de la batería: empieza el año")
            return False
        self.activo, self.secuencia, self.historico = g["activo"], g["secuencia"], g["historico"]
        self.version_cal, self.ordenes_aplicadas = g["version_cal"], g["ordenes_aplicadas"]
        logging.info("Continúa desde el punto de guardado: %s", self.activo.instante)
        return True

    def guardar(self) -> None:
        self.ruta_guardado.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta_guardado.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps({
            "clave": self.clave, "activo": self.activo, "secuencia": self.secuencia, "historico": self.historico,
            "version_cal": self.version_cal, "ordenes_aplicadas": self.ordenes_aplicadas}))
        tmp.replace(self.ruta_guardado)

    # --- Bus -----------------------------------------------------------------------

    def _al_conectar(self, cliente, _u, _flags, codigo, _p):
        if codigo.is_failure:
            logging.error("Conexión rechazada por el bus del activo: %s", codigo)
            return
        for t in (T_ORDEN, T_EVENTO_NUEVO, T_HIST_SOLICITUD, T_SIMULACION):
            cliente.subscribe(t, qos=QOS)
        cliente.publish(T_ESTADO, json.dumps({"estado": "activo", "instante_real": self.instante_real_arranque}),
                        qos=QOS, retain=True)
        logging.info("Conectado al bus del activo")
        self.conectado.set()

    def publicar(self, tema: str, mensaje: dict, retener: bool = False) -> None:
        self.cliente.publish(tema, json.dumps(mensaje, ensure_ascii=False), qos=QOS, retain=retener)

    def _latido(self) -> None:
        while not self.fin.wait(LATIDO_S):
            if self.cliente.is_connected():
                with open("/tmp/vivo", "w") as f:  # comprobación de salud del contenedor
                    f.write(utc(datetime.now(timezone.utc)))
            self.publicar(T_LATIDO, {"instante_real": utc(datetime.now(timezone.utc)),
                                     "instante_simulado": utc(self.activo.instante),
                                     "velocidad": self.velocidad, "pausa": self.pausa})

    # --- Mensajes recibidos ----------------------------------------------------------

    def atender(self, espera: float = 0.0, esperando_orden: bool = False) -> dict | None:
        """Procesa los mensajes pendientes. Si espera > 0, espera hasta ese tiempo.
        Devuelve la orden de límite si llega una mientras se espera la orden."""
        limite_t = time.monotonic() + espera
        while True:
            restante = limite_t - time.monotonic()
            try:
                tema, carga = self.entrada.get(timeout=max(0.0, restante)) if restante > 0 else self.entrada.get_nowait()
            except queue.Empty:
                return None
            try:
                m = json.loads(carga)
            except ValueError:
                logging.warning("Mensaje ilegible en %s", tema)
                continue
            if tema == T_ORDEN:
                orden = self._orden(m)
                if orden and esperando_orden:
                    return orden
                if orden:
                    logging.warning("Orden %s recibida fuera de la conexión nocturna: se ignora", orden.get("id"))
            elif tema == T_EVENTO_NUEVO:
                self._evento_nuevo(m)
            elif tema == T_HIST_SOLICITUD:
                self._historico(m)
            elif tema == T_SIMULACION:
                self._control(m)

    def _orden(self, m: dict) -> dict | None:
        if not isinstance(m, dict) or m.get("limite") not in (80, 85, 90, 95, 100):
            logging.warning("Orden no válida: %s", m)
            return None
        if m.get("id") in self.ordenes_aplicadas:
            logging.info("Orden %s duplicada: no se aplica de nuevo", m.get("id"))
            return None
        return m

    def _evento_nuevo(self, m: dict) -> None:
        try:
            inicio, fin = local(m["inicio"]), local(m["fin"])
            actividad = m["actividad"]
            if actividad not in ACTIVIDADES or not (timedelta(minutes=15) <= fin - inicio <= timedelta(hours=24)):
                raise ValueError("actividad o duración fuera de rango")
            self.activo.anadir_evento(inicio, fin, actividad, m.get("titulo") or actividad, m.get("id"))
        except (KeyError, TypeError, ValueError) as e:
            logging.warning("Evento nuevo rechazado (%s): %s", e, m)
            return
        logging.info("Evento añadido: %s de %s a %s", m.get("titulo"), inicio, fin)
        self.publicar_calendario()

    def _historico(self, m: dict) -> None:
        desde = m.get("desde")
        medidas = [x for x in self.historico if desde is None or x["instante"] > desde]
        self.publicar(T_HIST_RESPUESTA, {"id_solicitud": m.get("id_solicitud"), "medidas": medidas})
        logging.info("Solicitud de histórico %s: %d medidas", m.get("id_solicitud"), len(medidas))

    def _control(self, m: dict) -> None:
        if "velocidad" in m:
            v = float(m["velocidad"])
            if 1 <= v <= VELOCIDAD_MAX:
                self.velocidad = v
        if "pausa" in m:
            self.pausa = bool(m["pausa"])
        logging.info("Control de la simulación %s: velocidad %g, pausa %s", m.get("id"), self.velocidad, self.pausa)

    # --- Publicaciones -----------------------------------------------------------------

    def publicar_calendario(self) -> None:
        t = self.activo.instante
        fecha = fecha_calendario(t)
        self.version_cal += 1
        # Los eventos añadidos desde el panel conservan su identificador; el resto lo recibe aquí.
        eventos = [{"id": e["id"] or f"EVT-{fecha:%Y%m%d}-{i:02d}", "inicio": utc(e["inicio"]), "fin": utc(e["fin"]),
                    "titulo": e["titulo"], "actividad": e["actividad"]}
                   for i, e in enumerate(self.activo.calendario_de(fecha), start=1)]
        self.publicar(T_CALENDARIO, {"fecha": fecha.isoformat(), "version": self.version_cal,
                                     "instante": utc(t), "eventos": eventos}, retener=True)

    def publicar_medida(self) -> dict:
        self.secuencia += 1
        m = {"secuencia": self.secuencia, "instante": utc(self.activo.instante)} | self.activo.medida()
        self.historico.append(m)
        self.publicar(T_MEDIDAS, m)
        return m

    # --- Bucle principal ----------------------------------------------------------------

    def ejecutar(self) -> None:
        self.cliente.loop_start()
        if not self.conectado.wait(60):
            logging.error("No se pudo conectar con el bus del activo")
        threading.Thread(target=self._latido, daemon=True).start()
        act = self.activo
        self.publicar_calendario()
        while not self.fin.is_set() and not act.terminado:
            self.atender()
            if self.pausa:
                self.atender(espera=0.2)
                continue
            inicio_paso = time.monotonic()
            t = act.instante
            if t.hour == FIN_NOCHE and t.minute == 0:
                self.publicar_calendario()  # empieza el nuevo «día siguiente»
                self.guardar()
            ctx = act.inicio_paso()
            if ctx is None:
                self.publicar_medida()
            else:
                self.conexion_nocturna(t)
            act.fin_paso()
            self.esperar_siguiente_paso(inicio_paso)
        if act.terminado:
            logging.info("Fin del año simulado")
            self.publicar(T_ESTADO, {"estado": "finalizado", "instante_real": utc(datetime.now(timezone.utc))},
                          retener=True)
            self.fin.wait()
        self.guardar()
        self.publicar(T_ESTADO, {"estado": "detenido", "instante_real": utc(datetime.now(timezone.utc))},
                      retener=True)
        time.sleep(0.5)
        self.cliente.disconnect()
        self.cliente.loop_stop()

    def esperar_siguiente_paso(self, inicio_paso: float) -> None:
        """Espera hasta el siguiente paso en tramos cortos, recalculando el plazo con la velocidad
        actual. Así, un cambio de velocidad o una pausa se notan al momento: a x1 un paso dura
        15 minutos reales, y no hay que esperar a que termine para pasar a x2880."""
        while not self.fin.is_set():
            restante = inicio_paso + PASO_S / self.velocidad - time.monotonic()
            if restante <= 0 or self.pausa:
                return
            self.atender(espera=min(0.2, restante))

    def conexion_nocturna(self, t: datetime) -> None:
        """El móvil se enchufa para dormir: se publica la medida y se espera la orden (DD-12)."""
        self.publicar_medida()
        en_franja = t.hour >= INICIO_NOCHE or t.hour < FIN_NOCHE
        orden = self.atender(espera=ESPERA_ORDEN_S, esperando_orden=True) if en_franja else None
        if orden:
            limite, por_defecto, ident = int(orden["limite"]), False, orden.get("id")
            self.ordenes_aplicadas.add(ident)
        else:
            limite, por_defecto, ident = 100, True, None
            logging.warning("Sin orden en %g s: se aplica el límite del 100 %%", ESPERA_ORDEN_S)
        self.activo.fijar_limite(limite, por_defecto=por_defecto, id_orden=ident)
        self.publicar(T_CONFIRMACION, {"id": ident, "limite_aplicado": limite, "por_defecto": por_defecto,
                                       "instante": utc(t)})


if __name__ == "__main__":
    ServicioSimulador().ejecutar()
