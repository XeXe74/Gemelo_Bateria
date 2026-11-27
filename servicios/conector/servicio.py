"""Conector entre el bus del activo y el bus del gemelo (GDB-IDS-ACT y GDB-IDS-CAL).

Es el único contenedor conectado a las dos redes. Del activo al gemelo:
    1. reenvía una copia sin modificar de cada mensaje a gemelo/bruto/… (lago de datos);
    2. comprueba cada dato, le asigna la calidad (Q), la coherencia (K) y la marca temporal,
       y lo publica como señal retenida en el bus del gemelo;
    3. vigila el estado y el latido del activo y, si cae, vuelve a publicar el último valor
       de cada señal con Q = 1;
    4. pide el histórico al activo si la sesión no existía o si falta alguna medida.
Del gemelo al activo traslada solo la lista blanca: la orden de límite, el evento nuevo del
calendario y el control de la simulación, después de comprobar su rango.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from paho.mqtt.subscribeoptions import SubscribeOptions

from gemelo import senales as S
from gemelo.bus import QOS, Servicio, conectar

ZONA = ZoneInfo("Europe/Madrid")
CAIDA_S = 30  # sin latido del activo durante 30 s, se considera caído
ESPERA_CONFIRMACION_S = 5

A_MEDIDAS = "activo/PHONE1/medidas"
A_ORDEN = "activo/CHARGER1/orden"
A_CONFIRMACION = "activo/CHARGER1/confirmacion"
A_CALENDARIO = "activo/USER1/calendario"
A_EVENTO_NUEVO = "activo/USER1/calendario/nuevo"
A_HIST_SOLICITUD = "activo/PHONE1/historico/solicitud"
A_HIST_RESPUESTA = "activo/PHONE1/historico/respuesta"
A_ESTADO = "activo/sistema/estado"
A_LATIDO = "activo/sistema/latido"
A_SIMULACION = "activo/sistema/simulacion"
A_ESTADO_CONECTOR = "activo/sistema/estado/conector"

G_SIMULACION = "gemelo/sistema/simulacion"
G_ACTIVO = "gemelo/sistema/activo"

# Campos de las medidas del móvil -> señal, tipo esperado y rango físico
CAMPOS = {
    "nivel": (S.LEVEL, float, (0, 100)),
    "temperatura": (S.TEMPERATURE, float, (0, 50)),
    "salud": (S.SOH, int, (0, 100)),
    "enchufado": (S.PLUGGED, "bit", None),
    "modo_dormir": (S.SLEEP, "bit", None),
}


class Conector:
    def __init__(self):
        self.lock = threading.RLock()
        self.ruta_estado = Path(os.environ.get("ESTADO", "/datos/conector.json"))
        self.estado = self._leer_estado()
        self.ultimos: dict[str, dict] = {}  # último cuerpo publicado de cada señal
        self.anterior: dict[str, float] = {}  # valores del paso anterior, para la regla K = 80
        self.activo_caido = True
        self.ultimo_latido = 0.0
        self.ultima_orden: dict | None = None
        self.espera_confirmacion: float | None = None
        self.fecha_calendario: str | None = None
        self.n_solicitud = 0
        self.n_evento = 0
        self.arranque_activo = self.estado.get("arranque_activo")
        self.nuevo_arranque = False

        self.g = Servicio("conector", [S.tema(S.COMMAND), S.tema(S.EVENT_COMMAND), G_SIMULACION])
        self.g.al_recibir(S.tema(S.COMMAND), self.orden)
        self.g.al_recibir(S.tema(S.EVENT_COMMAND), self.evento_nuevo)
        self.g.al_recibir(G_SIMULACION, self.control_simulacion)
        self.g.al_conectar = lambda _previa: self._publicar_no_inicializadas()

        self.a = conectar("conector", os.environ.get("ACTIVO_HOST", "bus-activo"),
                          int(os.environ.get("ACTIVO_PUERTO", "1883")),
                          os.environ["ACTIVO_USUARIO"], os.environ["ACTIVO_CLAVE"],
                          A_ESTADO_CONECTOR, self._al_conectar_activo)
        self.a.on_message = self._al_recibir_activo

    # --- Estado persistente (última medida recibida) ------------------------------------

    def _leer_estado(self) -> dict:
        try:
            return json.loads(self.ruta_estado.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _guardar_estado(self) -> None:
        self.ruta_estado.parent.mkdir(parents=True, exist_ok=True)
        self.ruta_estado.write_text(json.dumps(self.estado), encoding="utf-8")

    # --- Publicación de señales ------------------------------------------------------------

    def publicar_senal(self, nombre: str, v, q: int, k: int, ts: str) -> None:
        self.ultimos[nombre] = {"v": v, "k": k, "ts": ts}
        self.g.publicar(S.tema(nombre), S.senal(nombre, v, q, k, ts), retener=True)

    def _publicar_no_inicializadas(self) -> None:
        """Al arrancar, cada señal de lectura sin valor todavía se publica con K = 10."""
        with self.lock:
            for nombre in [c[0] for c in CAMPOS.values()] + [S.FEEDBACK, S.EVENTS_TOMORROW]:
                if nombre not in self.ultimos:
                    self.g.publicar(S.tema(nombre), S.senal(nombre, None, S.Q_OK, S.K_NO_INICIALIZADO), retener=True)

    def marcar_caido(self, motivo: str) -> None:
        """Fallo de comunicación: último valor conocido de cada señal con Q = 1."""
        if self.activo_caido:
            return
        self.activo_caido = True
        self.g.registro("aviso", f"Activo caído ({motivo}): las señales pasan a Q = 1")
        self.g.notificar("alarma", f"Se ha perdido la comunicación con el activo ({motivo}).", "administrador")
        for nombre, u in self.ultimos.items():
            self.g.publicar(S.tema(nombre), S.senal(nombre, u["v"], S.Q_FALLO, u["k"], u["ts"]), retener=True)
        self.g.publicar(G_ACTIVO, json.dumps({"estado": "caido", "motivo": motivo, "instante_real": S.ahora_utc()}),
                        retener=True)

    def marcar_activo(self) -> None:
        if self.activo_caido:
            self.activo_caido = False
            self.g.registro("info", "Comunicación con el activo establecida")
            self.g.publicar(G_ACTIVO, json.dumps({"estado": "activo", "instante_real": S.ahora_utc()}), retener=True)

    # --- Bus del activo ----------------------------------------------------------------------

    def _al_conectar_activo(self, cliente, sesion_previa: bool) -> None:
        # Suscripciones de GDB-IDS-ACT (4.2) y GDB-IDS-CAL (4.2). Con noLocal, el bus no le
        # devuelve al conector lo que él mismo publica en activo/sistema/.
        for t in (A_MEDIDAS, A_CONFIRMACION, A_HIST_RESPUESTA, "activo/sistema/#", A_CALENDARIO):
            cliente.subscribe(t, options=SubscribeOptions(qos=QOS, noLocal=True))
        self.g.registro("info", f"Conectado al bus del activo (sesión previa: {'sí' if sesion_previa else 'no'})")
        if not sesion_previa and self.estado.get("ultimo_instante"):
            self.pedir_historico()

    def pedir_historico(self) -> None:
        self.n_solicitud += 1
        ident = f"HIS-{self.n_solicitud:04d}"
        self.a.publish(A_HIST_SOLICITUD, json.dumps({"id_solicitud": ident, "desde": self.estado.get("ultimo_instante")}),
                       qos=QOS)
        self.g.registro("info", f"Solicitud de histórico {ident} desde {self.estado.get('ultimo_instante')}")

    def _al_recibir_activo(self, _c, _u, msg) -> None:
        if msg.topic == A_ESTADO_CONECTOR:
            return  # su propio estado retenido, que el bus entrega al suscribirse
        with self.lock:
            # 1. Copia sin modificar al lago de datos, antes de procesarla (REQ-0260)
            self.g.publicar("gemelo/bruto/" + msg.topic, msg.payload)
            try:
                m = json.loads(msg.payload)
                if not isinstance(m, dict):
                    raise ValueError
            except ValueError:
                self.g.registro("aviso", f"Mensaje ilegible en {msg.topic}")
                self._ilegible(msg.topic)
                return
            try:
                if msg.topic == A_MEDIDAS:
                    self.medidas(m)
                elif msg.topic == A_HIST_RESPUESTA:
                    for x in m.get("medidas", []):
                        self.medidas(x, desde_historico=True)
                elif msg.topic == A_CONFIRMACION:
                    self.confirmacion(m)
                elif msg.topic == A_CALENDARIO:
                    self.calendario(m)
                elif msg.topic == A_ESTADO:
                    self.estado_activo(m)
                elif msg.topic == A_LATIDO:
                    self.ultimo_latido = time.monotonic()
                    self.g.publicar(G_ACTIVO + "/latido", msg.payload)
            except Exception as e:
                self.g.registro("error", f"Error al procesar {msg.topic}: {e!r}")

    def _ilegible(self, tema: str) -> None:
        """Mensaje que no se puede interpretar: las señales que lleva pasan a Q = 1."""
        nombres = [c[0] for c in CAMPOS.values()] if tema == A_MEDIDAS else \
            [S.EVENTS_TOMORROW] if tema == A_CALENDARIO else [S.FEEDBACK] if tema == A_CONFIRMACION else []
        for n in nombres:
            if n in self.ultimos:
                u = self.ultimos[n]
                self.g.publicar(S.tema(n), S.senal(n, u["v"], S.Q_FALLO, u["k"], u["ts"]), retener=True)

    def estado_activo(self, m: dict) -> None:
        if m.get("estado") in ("caido", "detenido"):
            self.marcar_caido(f"estado «{m.get('estado')}»")
        elif m.get("estado") == "finalizado":
            self.g.registro("info", "El activo ha terminado el año simulado")
            self.g.publicar(G_ACTIVO, json.dumps({"estado": "finalizado", "instante_real": S.ahora_utc()}),
                            retener=True)
        elif m.get("estado") == "activo":
            self.ultimo_latido = time.monotonic()
            arranque = m.get("instante_real")
            if arranque != self.arranque_activo:
                # El simulador ha arrancado de nuevo. Con la primera medida se sabrá si continúa
                # su secuencia o si ha empezado otra.
                self.arranque_activo = arranque
                self.estado["arranque_activo"] = arranque
                self.nuevo_arranque = True
                self.anterior.clear()
            self.marcar_activo()

    # --- Medidas del móvil -----------------------------------------------------------------

    def medidas(self, m: dict, desde_historico: bool = False) -> None:
        sec, ts = m.get("secuencia"), m.get("instante")
        if not isinstance(sec, int) or not isinstance(ts, str):
            self.g.registro("aviso", "Medida sin secuencia o sin instante")
            return
        if self.nuevo_arranque and not desde_historico:
            self.nuevo_arranque = False
            if sec <= self.estado.get("ultima_secuencia", 0):
                self.g.registro("aviso", f"El activo ha empezado una secuencia nueva en la medida {sec}")
                self.estado.update(ultima_secuencia=sec - 1, ultimo_instante=None)
        ultima = self.estado.get("ultima_secuencia", 0)
        if sec <= ultima:
            return  # duplicada (calidad de servicio 1) o ya recibida por el histórico
        if sec > ultima + 1 and not desde_historico:
            self.g.registro("aviso", f"Salto en la secuencia de medidas ({ultima} -> {sec})")
            self.pedir_historico()
        self.marcar_activo()
        self.ultimo_latido = time.monotonic()
        valores = {}
        for campo, (nombre, tipo, rango) in CAMPOS.items():
            v, k = m.get(campo), S.K_OK
            if tipo == "bit":
                if v not in (0, 1) or isinstance(v, bool):
                    k = S.K_FALLO_SENSOR
            elif not isinstance(v, (int, float)) or isinstance(v, bool) or (tipo is int and not float(v).is_integer()):
                k = S.K_FALLO_SENSOR
            else:
                k = S.k_rango(float(v), *rango)
            valores[campo] = (v, k)
        # Reglas de coherencia K = 80 de las fichas de señal
        nivel, k_nivel = valores["nivel"]
        if k_nivel == S.K_OK and self.anterior.get("nivel") is not None and self.anterior.get("enchufado") == 0 \
                and nivel - self.anterior["nivel"] > 2:
            valores["nivel"] = (nivel, S.K_INCOMPATIBLE)
        salud, k_salud = valores["salud"]
        if k_salud == S.K_OK and self.anterior.get("salud") is not None and salud - self.anterior["salud"] > 1:
            valores["salud"] = (salud, S.K_INCOMPATIBLE)
            self.g.notificar("aviso", f"La salud de la batería ha subido de {self.anterior['salud']:.0f} % a "
                                      f"{salud} % sin una sustitución registrada en la maqueta.", "administrador")
        for campo, (nombre, _t, _r) in CAMPOS.items():
            v, k = valores[campo]
            self.publicar_senal(nombre, v, S.Q_OK, k, ts)
            if k in (S.K_OK, S.K_INCOMPATIBLE):
                self.anterior[campo] = v
        self.estado.update(ultima_secuencia=sec, ultimo_instante=ts)
        self._guardar_estado()

    # --- Orden de límite y su confirmación ---------------------------------------------------

    def orden(self, _tema: str, carga: bytes) -> None:
        with self.lock:
            b = S.leer_senal(carga)
            if b is None or b["v"] not in S.LIMITES or isinstance(b["v"], bool):
                self.g.registro("aviso", f"Orden de límite descartada: {carga[:200]!r}")
                self.g.notificar("alarma", "Se ha descartado una orden de límite de carga fuera del conjunto "
                                           "permitido (80, 85, 90, 95 o 100 %).", "administrador")
                return
            t = S.leer_iso(b["ts"]).astimezone(ZONA)
            orden = {"id": f"ORD-{t:%Y%m%d-%H%M}", "limite": int(b["v"]), "instante": b["ts"]}
            self.ultima_orden = orden
            self.espera_confirmacion = time.monotonic()
            self.a.publish(A_ORDEN, json.dumps(orden), qos=QOS)

    def confirmacion(self, m: dict) -> None:
        v, ts = m.get("limite_aplicado"), m.get("instante") or S.ahora_utc()
        k = S.K_OK
        if v not in S.LIMITES or isinstance(v, bool):
            k = S.K_FALLO_SENSOR
        elif not m.get("por_defecto") and (self.ultima_orden is None or v != self.ultima_orden["limite"]):
            k = S.K_INCOMPATIBLE
        self.espera_confirmacion = None
        self.publicar_senal(S.FEEDBACK, v, S.Q_OK, k, ts)
        if m.get("por_defecto"):
            self.g.notificar("aviso", "El cargador no recibió la orden a tiempo y aplicó el límite del 100 %.",
                             "administrador")

    # --- Calendario ---------------------------------------------------------------------------

    def calendario(self, m: dict) -> None:
        eventos, ts, fecha = m.get("eventos"), m.get("instante") or S.ahora_utc(), m.get("fecha")
        k = S.K_OK
        if not isinstance(eventos, list) or not isinstance(fecha, str):
            k = S.K_FALLO_SENSOR
        elif len(eventos) > 30:
            k = S.K_RANGO_SUP
        else:
            for e in eventos:
                if self._error_evento(e, fecha):
                    k = S.K_INCOMPATIBLE
                    break
        self.fecha_calendario = fecha
        self.publicar_senal(S.EVENTS_TOMORROW, eventos, S.Q_OK, k, ts)

    @staticmethod
    def _error_evento(e: dict, fecha: str) -> str | None:
        """Comprueba un evento del día siguiente (fichas USER1.CAL1.*). Devuelve el error o None."""
        try:
            inicio, fin = S.leer_iso(e["inicio"]).astimezone(ZONA), S.leer_iso(e["fin"]).astimezone(ZONA)
        except (KeyError, TypeError, ValueError):
            return "faltan el inicio o el fin"
        if e.get("actividad") not in S.ACTIVIDADES:
            return "actividad no válida"
        if fin <= inicio:
            return "termina antes de empezar"
        if not timedelta(minutes=15) <= fin - inicio <= timedelta(hours=24):
            return "duración fuera de rango"
        # El día siguiente va desde sus 00:00 hasta el final de la madrugada posterior (7:00).
        desde = datetime.combine(date.fromisoformat(fecha), dtime(), ZONA)
        if inicio < desde or fin > desde + timedelta(days=1, hours=7):
            return "no corresponde al día siguiente"
        return None

    def evento_nuevo(self, _tema: str, carga: bytes) -> None:
        with self.lock:
            b = S.leer_senal(carga)
            e = b["v"] if b else None
            if isinstance(e, str):
                try:
                    e = json.loads(e)
                except ValueError:
                    e = None
            error = "formato no válido" if not isinstance(e, dict) else \
                "todavía no se conoce el día siguiente" if self.fecha_calendario is None else \
                self._error_evento(e, self.fecha_calendario)
            if error:
                self.g.registro("aviso", f"Evento nuevo descartado ({error})")
                self.g.notificar("aviso", f"No se ha podido añadir el evento: {error}.", "operador")
                return
            if not e.get("id"):
                self.n_evento += 1
                e["id"] = f"EVT-PAN-{self.n_evento:04d}"
            nuevo = {c: e.get(c) for c in ("id", "inicio", "fin", "titulo", "actividad")}
            self.a.publish(A_EVENTO_NUEVO, json.dumps(nuevo, ensure_ascii=False), qos=QOS)
            self.g.registro("info", f"Evento {nuevo['id']} trasladado al calendario")

    # --- Control de la simulación ---------------------------------------------------------------

    def control_simulacion(self, _tema: str, carga: bytes) -> None:
        try:
            m = json.loads(carga)
        except ValueError:
            return
        control = {"id": m.get("id") or f"SIM-{int(time.time())}"}
        if isinstance(m.get("velocidad"), (int, float)) and 1 <= m["velocidad"] <= 2880:
            control["velocidad"] = m["velocidad"]
        if isinstance(m.get("pausa"), bool):
            control["pausa"] = m["pausa"]
        if len(control) > 1:
            self.a.publish(A_SIMULACION, json.dumps(control), qos=QOS)
            self.g.registro("info", f"Control de la simulación trasladado: {control}")

    # --- Vigilancia periódica ---------------------------------------------------------------------

    def vigilar(self) -> None:
        with self.lock:
            if not self.activo_caido and time.monotonic() - self.ultimo_latido > CAIDA_S:
                self.marcar_caido(f"sin latido durante {CAIDA_S} s")
            if self.espera_confirmacion and time.monotonic() - self.espera_confirmacion > ESPERA_CONFIRMACION_S:
                self.espera_confirmacion = None
                u = self.ultimos.get(S.FEEDBACK, {"v": None, "k": S.K_NO_INICIALIZADO, "ts": S.ahora_utc()})
                self.g.publicar(S.tema(S.FEEDBACK), S.senal(S.FEEDBACK, u["v"], S.Q_FALLO, u["k"], u["ts"]),
                                retener=True)
                self.g.registro("aviso", "No ha llegado la confirmación del límite en 5 s")

    def ejecutar(self) -> None:
        self.a.loop_start()
        try:
            self.g.ejecutar(self.vigilar, periodo_s=1.0)
        finally:
            self.a.disconnect()
            self.a.loop_stop()


if __name__ == "__main__":
    Conector().ejecutar()
