"""Genera los flujos del panel de Node-RED (servicios/nodered/flujos/flows.json).

Uso (desde la carpeta Gemelo_Bateria):
    .venv/Scripts/python herramientas/panel_flujos.py

El panel también se puede modificar desde el editor de Node-RED, que guarda los cambios en el mismo
fichero. Este script sirve para crear la versión inicial con identificadores estables; si se ha
editado el panel en Node-RED, volver a ejecutarlo descarta esos cambios.
"""
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "servicios" / "nodered" / "flujos"
Z = "flujo-panel"
AZUL, VERDE, NARANJA = "#1E3A5F", "#2C7A7B", "#E07A2F"

nodos = []
_y = {"x": 0}


def nodo(**n):
    nodos.append(n)
    return n["id"]


def pos(col, fila):
    return {"x": 180 + 260 * col, "y": 60 + 50 * fila}


# --- Utilidades de JavaScript compartidas por las funciones ------------------------------
JS_HORA = r"""
function hora(ts) {
    if (!ts) return "—";
    return new Date(ts).toLocaleString("es-ES", {timeZone: "Europe/Madrid", day: "2-digit", month: "2-digit",
        year: "numeric", hour: "2-digit", minute: "2-digit"});
}
"""

# --- Configuración: bus, panel, tema y páginas --------------------------------------------
nodo(id=Z, type="tab", label="Panel del gemelo", disabled=False,
     info="Panel del gemelo digital de la batería (PAN). Se genera con herramientas/panel_flujos.py.")
nodo(id="bus-gemelo", type="mqtt-broker", name="Bus del gemelo", broker="mosquitto", port="1883", clientid="panel",
     autoConnect=True, usetls=False, protocolVersion="5", keepalive="30", cleansession=False, autoUnsubscribe=True,
     birthTopic="gemelo/sistema/estado/panel", birthQos="1", birthRetain="true",
     birthPayload='{"estado":"activo"}', birthMsg={},
     closeTopic="gemelo/sistema/estado/panel", closeQos="1", closeRetain="true",
     closePayload='{"estado":"detenido"}', closeMsg={},
     willTopic="gemelo/sistema/estado/panel", willQos="1", willRetain="true",
     willPayload='{"estado":"caido"}', willMsg={}, userProps="", sessionExpiry="86400")
nodo(id="base-panel", type="ui-base", name="Gemelo de la batería", path="/dashboard", appIcon="",
     includeClientData=True, acceptsClientConfig=["ui-notification", "ui-control"], showPathInSidebar=False,
     showPageTitle=True, navigationStyle="default", titleBarStyle="default")
nodo(id="tema-panel", type="ui-theme", name="Paleta del proyecto",
     colors={"surface": AZUL, "primary": VERDE, "bgPage": "#F3F6FA", "groupBg": "#ffffff", "groupOutline": "#d5dde8"},
     sizes={"density": "default", "pagePadding": "12px", "groupGap": "12px", "groupBorderRadius": "6px",
            "widgetGap": "12px"})
PUNTOS = [{"name": "Default", "px": "0", "cols": "3"}, {"name": "Tablet", "px": "576", "cols": "6"},
          {"name": "Small Desktop", "px": "768", "cols": "9"}, {"name": "Desktop", "px": "1024", "cols": "12"}]
for i, (pid, nombre, ruta, icono) in enumerate([("p-estado", "Estado", "/estado", "battery-charging"),
                                                ("p-operacion", "Operación", "/operacion", "tune"),
                                                ("p-admin", "Administración", "/administracion", "cog")], start=1):
    nodo(id=pid, type="ui-page", name=nombre, ui="base-panel", path=ruta, icon=icono, layout="grid", theme="tema-panel",
         breakpoints=PUNTOS, order=i, className="", visible="true", disabled="false")


def grupo(gid, nombre, pagina, ancho, orden):
    nodo(id=gid, type="ui-group", name=nombre, page=pagina, width=str(ancho), height="1", order=orden,
         showTitle=True, className="", visible="true", disabled="false", groupType="default")


grupo("g-movil", "Móvil y cargador", "p-estado", 4, 1)
grupo("g-decision", "Decisión de esta noche", "p-estado", 8, 2)
grupo("g-grafica", "Nivel de carga (últimos dos días simulados)", "p-estado", 12, 3)
grupo("g-simulacion", "Simulación", "p-operacion", 4, 1)
grupo("g-calendario", "Calendario de mañana", "p-operacion", 8, 2)
grupo("g-evento", "Añadir un evento a mañana", "p-operacion", 6, 3)
grupo("g-servicios", "Servicios", "p-admin", 6, 1)
grupo("g-avisos", "Avisos", "p-admin", 6, 2)
grupo("g-registro", "Registro", "p-admin", 12, 3)


def funcion(fid, nombre, codigo, salidas, col, fila, n_salidas=1):
    return nodo(id=fid, type="function", z=Z, name=nombre, func=codigo, outputs=n_salidas, timeout=0, noerr=0,
                initialize="", finalize="", libs=[], **pos(col, fila), wires=salidas)


def mqtt_in(mid, tema, salidas, fila):
    return nodo(id=mid, type="mqtt in", z=Z, name=tema, topic=tema, qos="1", datatype="json", broker="bus-gemelo",
                nl=False, rap=True, rh=0, inputs=0, **pos(0, fila), wires=[salidas])


def mqtt_out(mid, nombre, tema, col, fila):
    return nodo(id=mid, type="mqtt out", z=Z, name=nombre, topic=tema, qos="1", retain="false", respTopic="",
                contentType="", userProps="", correl="", expiry="", broker="bus-gemelo", **pos(col, fila), wires=[])


def texto(tid, grupo_id, etiqueta, orden, col, fila):
    return nodo(id=tid, type="ui-text", z=Z, group=grupo_id, order=orden, width=0, height=0, name=etiqueta,
                label=etiqueta, format="{{msg.payload}}", layout="row-spread", style=False, font="", fontSize=16,
                color="#1E3A5F", wrapText=False, className="", **pos(col, fila), wires=[])


def plantilla(tid, grupo_id, nombre, html, orden, col, fila):
    return nodo(id=tid, type="ui-template", z=Z, group=grupo_id, page="", ui="", name=nombre, order=orden, width=0,
                height=0, head="", format=html, storeOutMessages=True, passthru=False, resendOnRefresh=True,
                templateScope="local", className="", **pos(col, fila), wires=[[]])


# --- Página Estado: señales del móvil y del cargador ----------------------------------------
COD_SENAL = JS_HORA + r"""
// Cada señal llega como {"type":"tag","body":{t, v, q, k, ts}}. Si la calidad o la coherencia
// no son correctas, se indica junto al valor.
const b = msg.payload && msg.payload.body;
if (!b) return null;
const nota = b.q === 1 ? " (sin comunicación)" : (b.k === 10 ? "" : (b.k !== 0 ? " (dato dudoso, K = " + b.k + ")" : ""));
const salidas = [null, null, null, null, null, null, null];
const v = b.v;
switch (b.t) {
    case "PHONE1.BAT1.level":
        if (v === null) return null;
        salidas[0] = {payload: Math.round(v)};
        salidas[6] = {payload: {x: new Date(b.ts).getTime(), y: v}, topic: "Nivel"};
        flow.set("instante", b.ts);
        break;
    case "PHONE1.status.plugged":
        salidas[1] = {payload: (v === 1 ? "Enchufado" : v === 0 ? "Desenchufado" : "—") + nota}; break;
    case "PHONE1.status.sleep":
        salidas[2] = {payload: (v === 1 ? "Activado" : v === 0 ? "Desactivado" : "—") + nota}; break;
    case "PHONE1.BAT1.temperature":
        salidas[3] = {payload: (v === null ? "—" : v.toFixed(1).replace(".", ",") + " °C") + nota}; break;
    case "PHONE1.BAT1.soh":
        salidas[4] = {payload: (v === null ? "—" : v + " %") + nota}; break;
    case "CHARGER1.limit.command.feedback":
        salidas[5] = {payload: (v === null ? "—" : v + " %") + nota}; break;
    default:
        return null;
}
return salidas;
"""
mqtt_in("in-movil", "gemelo/PHONE1/#", ["f-senales"], 1)
mqtt_in("in-cargador", "gemelo/CHARGER1/limit/command/feedback", ["f-senales"], 2)
funcion("f-senales", "Señal -> valor", COD_SENAL,
        [["w-nivel"], ["w-enchufado"], ["w-dormir"], ["w-temp"], ["w-salud"], ["w-limite"], ["w-grafica"]], 1, 1,
        n_salidas=7)
nodo(id="w-nivel", type="ui-gauge", z=Z, name="Nivel", group="g-movil", order=1, width=4, height=3,
     gtype="gauge-half", gstyle="rounded", title="Nivel de carga", units="", icon="", prefix="", suffix="%",
     segments=[{"from": "0", "color": NARANJA}, {"from": "20", "color": VERDE}], min=0, max="100",
     sizeThickness=16, sizeGap=4, sizeKeyThickness=8, styleRounded=True, styleGlow=False, className="",
     **pos(2, 0), wires=[])
texto("w-enchufado", "g-movil", "Cargador", 2, 2, 1)
texto("w-dormir", "g-movil", "Modo Dormir", 3, 2, 2)
texto("w-temp", "g-movil", "Temperatura de la batería", 4, 2, 3)
texto("w-salud", "g-movil", "Salud (Ajustes)", 5, 2, 4)
texto("w-limite", "g-movil", "Límite aplicado", 6, 2, 5)
nodo(id="w-grafica", type="ui-chart", z=Z, group="g-grafica", name="Nivel", label="", order=1, chartType="line",
     category="topic", categoryType="msg", xAxisLabel="", xAxisProperty="x", xAxisPropertyType="property",
     xAxisType="time", xAxisFormat="", xAxisFormatType="auto", xmin="", xmax="",
     yAxisLabel="%", yAxisProperty="y", yAxisPropertyType="property", ymin="0", ymax="100", bins=10,
     action="append", stackSeries=False, pointShape="false", pointRadius=0, showLegend=False,
     removeOlder="52", removeOlderUnit="604800", removeOlderPoints="192", colors=[VERDE, NARANJA, AZUL],
     textColor=["#666666"], textColorDefault=True, gridColor=["#e5e5e5"], gridColorDefault=True,
     width=12, height=5, className="", interpolation="linear", **pos(2, 6), wires=[[]])

# --- Página Estado: decisión de esta noche ---------------------------------------------------
COD_REC = JS_HORA + r"""
const b = msg.payload && msg.payload.body;
if (!b || !b.v) return null;
const v = b.v;
msg.payload = {
    titulo: v.tipo === "decision" ? "Decisión tomada" : "Previsión (se confirma al enchufar)",
    dia: new Date(v.para_dia + "T12:00:00Z").toLocaleDateString("es-ES", {weekday: "long", day: "numeric", month: "long"}),
    limite: v.limite,
    corte: hora(v.hora_corte_estimada),
    consumo: Math.round(v.consumo_previsto),
    nivel: Math.round(v.nivel_al_enchufar),
    explicacion: v.explicacion,
    modelo: v.modelo,
    instante: hora(b.ts),
};
return msg;
"""
mqtt_in("in-rec", "gemelo/GD/PHONE1/recommendation", ["f-rec"], 8)
funcion("f-rec", "Recomendación", COD_REC, [["w-rec"]], 1, 8)
plantilla("w-rec", "g-decision", "Decisión", """<template>
  <div v-if="msg && msg.payload" style="color:#1E3A5F">
    <div style="color:#2C7A7B;font-weight:600">{{ msg.payload.titulo }} · para el {{ msg.payload.dia }}</div>
    <div style="font-size:44px;font-weight:700;margin:4px 0">{{ msg.payload.limite }} %</div>
    <div>Corte estimado de la carga: <b>{{ msg.payload.corte }}</b> (al enchufar con un {{ msg.payload.nivel }} %)</div>
    <div>Consumo previsto de mañana: <b>{{ msg.payload.consumo }} %</b></div>
    <p style="margin-top:10px">{{ msg.payload.explicacion }}</p>
    <div style="font-size:12px;color:#8A94A3">Modelo: {{ msg.payload.modelo }} · instante simulado: {{ msg.payload.instante }}</div>
  </div>
  <div v-else>Todavía no hay ninguna previsión. Se publica a partir de las 18:00 del día simulado.</div>
</template>""", 1, 2, 8)

# --- Página Operación: control de la simulación ------------------------------------------------
COD_LATIDO = JS_HORA + r"""
const v = msg.payload || {};
const vel = v.velocidad >= 2880 ? "un día cada 30 s" : ("x" + v.velocidad);
msg.payload = hora(v.instante_simulado) + " · " + vel + (v.pausa ? " · en pausa" : "");
flow.set("instante", v.instante_simulado);
return msg;
"""
mqtt_in("in-latido-activo", "gemelo/sistema/activo/latido", ["f-latido"], 10)
funcion("f-latido", "Reloj simulado", COD_LATIDO, [["w-reloj"]], 1, 10)
texto("w-reloj", "g-simulacion", "Instante simulado", 1, 2, 10)
nodo(id="w-velocidad", type="ui-dropdown", z=Z, group="g-simulacion", name="Velocidad", label="Velocidad", tooltip="",
     order=2, width=0, height=0, passthru=False, multiple=False, chips=False, clearable=False,
     options=[{"label": "Tiempo real (x1)", "value": 1, "type": "num"},
              {"label": "1 hora por minuto (x60)", "value": 60, "type": "num"},
              {"label": "1 día cada 4 min (x360)", "value": 360, "type": "num"},
              {"label": "1 día por minuto (x1440)", "value": 1440, "type": "num"},
              {"label": "1 día cada 30 s (x2880)", "value": 2880, "type": "num"}],
     payload="", topic="velocidad", topicType="str", className="", typeIsComboBox=True, msgTrigger="onChange",
     **pos(0, 11), wires=[["f-control"]])
for i, (bid, etiqueta, valor, icono) in enumerate([("w-pausa", "Pausar", "true", "pause"),
                                                   ("w-seguir", "Reanudar", "false", "play")]):
    nodo(id=bid, type="ui-button", z=Z, group="g-simulacion", name=etiqueta, label=etiqueta, order=3 + i, width="2",
         height="1", emulateClick=False, tooltip="", color="", bgcolor="", className="", icon=icono,
         iconPosition="left", payload=valor, payloadType="bool", topic="pausa", topicType="str", buttonColor="",
         textColor="", iconColor="", enableClick=True, enablePointerdown=False, pointerdownPayload="",
         pointerdownPayloadType="str", enablePointerup=False, pointerupPayload="", pointerupPayloadType="str",
         **pos(0, 12 + i), wires=[["f-control"]])
COD_CONTROL = r"""
const n = (context.get("n") || 0) + 1;
context.set("n", n);
const control = {id: "SIM-PAN-" + String(n).padStart(4, "0")};
control[msg.topic] = msg.payload;
msg.payload = JSON.stringify(control);
return msg;
"""
funcion("f-control", "Control de la simulación", COD_CONTROL, [["out-simulacion"]], 1, 12)
mqtt_out("out-simulacion", "gemelo/sistema/simulacion", "gemelo/sistema/simulacion", 2, 12)

# --- Página Operación: calendario de mañana y evento nuevo ---------------------------------------
COD_CAL = JS_HORA + r"""
const b = msg.payload && msg.payload.body;
if (!b) return null;
flow.set("calendario_ts", b.ts);
const eventos = Array.isArray(b.v) ? b.v : [];
msg.payload = {
    nota: b.q === 1 ? "Sin comunicación con el calendario: se muestra la última versión recibida." : "",
    eventos: eventos.map(e => ({titulo: e.titulo, actividad: e.actividad, inicio: hora(e.inicio), fin: hora(e.fin)})),
};
return msg;
"""
mqtt_in("in-cal", "gemelo/USER1/CAL1/events/tomorrow", ["f-cal"], 15)
funcion("f-cal", "Calendario", COD_CAL, [["w-cal"]], 1, 15)
plantilla("w-cal", "g-calendario", "Calendario", """<template>
  <div v-if="msg && msg.payload">
    <div v-if="msg.payload.nota" style="color:#E07A2F">{{ msg.payload.nota }}</div>
    <div v-if="msg.payload.eventos.length === 0">No hay eventos en el calendario de mañana.</div>
    <table v-else style="width:100%;border-collapse:collapse">
      <tr style="background:#1E3A5F;color:white"><th style="text-align:left;padding:4px">Evento</th>
        <th style="text-align:left">Actividad</th><th style="text-align:left">Inicio</th><th style="text-align:left">Fin</th></tr>
      <tr v-for="(e, i) in msg.payload.eventos" :key="i" :style="{background: i % 2 ? '#F3F6FA' : 'white'}">
        <td style="padding:4px">{{ e.titulo }}</td><td>{{ e.actividad }}</td><td>{{ e.inicio }}</td><td>{{ e.fin }}</td></tr>
    </table>
  </div>
</template>""", 1, 2, 15)
nodo(id="w-actividad", type="ui-dropdown", z=Z, group="g-evento", name="Actividad", label="Actividad", tooltip="",
     order=1, width=0, height=0, passthru=False, multiple=False, chips=False, clearable=False,
     options=[{"label": e, "value": e, "type": "str"} for e in
              ("social", "cena", "cine", "noche_fuera", "salida_corta", "dia_fuera", "gimnasio", "clase",
               "clase_datos", "carplay", "casa")],
     payload="", topic="actividad", topicType="str", className="", typeIsComboBox=True, msgTrigger="onChange",
     **pos(0, 17), wires=[["f-actividad"]])
funcion("f-actividad", "Guardar actividad", 'flow.set("actividad", msg.payload);\nreturn null;', [[]], 1, 17)
nodo(id="w-form", type="ui-form", z=Z, name="Evento nuevo", group="g-evento", label="", order=2, width=0, height=0,
     options=[{"label": "Título", "key": "titulo", "type": "text", "required": True, "rows": None},
              {"label": "Hora de inicio", "key": "inicio", "type": "time", "required": True, "rows": None},
              {"label": "Duración (horas)", "key": "duracion", "type": "number", "required": True, "rows": None}],
     formValue={"titulo": "", "inicio": "", "duracion": ""}, payload="", submit="Añadir", cancel="Limpiar",
     resetOnSubmit=True, topic="evento", topicType="str", splitLayout="", className="", passthru=False,
     dropdownOptions=[], **pos(0, 18), wires=[["f-evento"]])
COD_EVENTO = r"""
// Construye el evento del día siguiente en hora peninsular y lo publica como señal de escritura
// USER1.CAL1.event.command (GDB-IDS-CAL, tabla 6). Las horas antes de las 7:00 son de la madrugada posterior.
const ts = flow.get("calendario_ts");
const actividad = flow.get("actividad");
const f = msg.payload || {};
if (!ts || !actividad || !f.inicio || !(Number(f.duracion) > 0)) {
    node.warn("Faltan datos del evento o todavía no hay calendario");
    return [null, {payload: "Elige la actividad y rellena todos los campos."}];
}
const madrid = (d) => new Date(d.toLocaleString("en-US", {timeZone: "Europe/Madrid"}));
// Día siguiente según el instante del calendario: antes de las 7:00, el propio día.
const t = madrid(new Date(ts));
const dia = new Date(Date.UTC(t.getFullYear(), t.getMonth(), t.getDate() + (t.getHours() < 7 ? 0 : 1)));
const [h, m] = f.inicio.split(":").map(Number);
// Instante UTC cuya hora en Madrid es la indicada: se corrige con la diferencia horaria de ese día.
let inicio = new Date(Date.UTC(dia.getUTCFullYear(), dia.getUTCMonth(), dia.getUTCDate() + (h < 7 ? 1 : 0), h, m));
const desfase = madrid(inicio).getTime() - new Date(inicio.toLocaleString("en-US", {timeZone: "UTC"})).getTime();
inicio = new Date(inicio.getTime() - desfase);
const fin = new Date(inicio.getTime() + Number(f.duracion) * 3600 * 1000);
const n = (context.get("n") || 0) + 1;
context.set("n", n);
const v = {id: "EVT-PAN-" + String(n).padStart(4, "0"), inicio: inicio.toISOString(), fin: fin.toISOString(),
           titulo: f.titulo, actividad: actividad};
msg.payload = JSON.stringify({type: "tag", body: {t: "USER1.CAL1.event.command", v: v, q: 0, k: 0,
                                                   ts: flow.get("instante") || ts}});
return [msg, {payload: "Evento «" + f.titulo + "» enviado al calendario."}];
"""
funcion("f-evento", "Evento nuevo", COD_EVENTO, [["out-evento"], ["w-aviso-evento"]], 1, 18, n_salidas=2)
mqtt_out("out-evento", "gemelo/USER1/CAL1/event/command", "gemelo/USER1/CAL1/event/command", 2, 18)
nodo(id="w-aviso-evento", type="ui-notification", z=Z, ui="base-panel", position="top right", colorDefault=False,
     color=VERDE, displayTime="4", showCountdown=True, outputs=0, allowDismiss=True, dismissText="Cerrar",
     allowConfirm=False, confirmText="", raw=False, className="", name="Aviso del evento", **pos(2, 19), wires=[])

# --- Página Administración: servicios, avisos y registro -------------------------------------------
COD_SERV = JS_HORA + r"""
// Estado de cada servicio a partir de su estado retenido y de su latido. Se considera caído si
// publica su último aviso o si no llega su latido durante 30 s (REQ-0210).
const s = flow.get("servicios") || {};
if (msg.topic && msg.topic.startsWith("gemelo/sistema/")) {
    const partes = msg.topic.split("/");
    const nombre = partes[3];
    if (nombre && (partes[2] === "estado" || partes[2] === "latido")) {
        s[nombre] = s[nombre] || {};
        if (partes[2] === "estado") s[nombre].estado = (msg.payload || {}).estado;
        if (partes[2] === "latido") s[nombre].latido = Date.now();
    }
    if (partes[2] === "activo" && partes.length === 3) {
        s["activo simulado"] = {estado: (msg.payload || {}).estado, latido: Date.now(), sinLatido: true};
    }
    flow.set("servicios", s);
}
const filas = Object.keys(s).sort().map(n => {
    const x = s[n];
    let estado = x.estado || "—";
    if (estado === "activo" && !x.sinLatido && (!x.latido || Date.now() - x.latido > 30000)) estado = "caido (sin latido)";
    return {nombre: n, estado: estado, latido: x.latido ? new Date(x.latido).toLocaleTimeString("es-ES") : "—"};
});
return {payload: filas};
"""
mqtt_in("in-estados", "gemelo/sistema/estado/+", ["f-servicios"], 21)
mqtt_in("in-latidos", "gemelo/sistema/latido/+", ["f-servicios"], 22)
mqtt_in("in-activo", "gemelo/sistema/activo", ["f-servicios"], 23)
nodo(id="i-cada5s", type="inject", z=Z, name="cada 5 s", props=[], repeat="5", crontab="", once=True,
     onceDelay="2", topic="", **pos(0, 24), wires=[["f-servicios"]])
funcion("f-servicios", "Estado de los servicios", COD_SERV, [["w-servicios"]], 1, 22)
plantilla("w-servicios", "g-servicios", "Servicios", """<template>
  <table v-if="msg && msg.payload" style="width:100%;border-collapse:collapse">
    <tr style="background:#1E3A5F;color:white"><th style="text-align:left;padding:4px">Servicio</th>
      <th style="text-align:left">Estado</th><th style="text-align:left">Último latido</th></tr>
    <tr v-for="(s, i) in msg.payload" :key="s.nombre" :style="{background: i % 2 ? '#F3F6FA' : 'white'}">
      <td style="padding:4px">{{ s.nombre }}</td>
      <td :style="{color: s.estado === 'activo' ? '#2C7A7B' : '#E07A2F', fontWeight: 600}">{{ s.estado }}</td>
      <td>{{ s.latido }}</td></tr>
  </table>
</template>""", 1, 2, 22)

COD_LISTA = JS_HORA + r"""
// Guarda las últimas 15 entradas para mostrarlas en orden inverso.
const clave = msg.topic === "gemelo/sistema/registro" ? "registro" : "avisos";
const lista = flow.get(clave) || [];
const m = msg.payload || {};
lista.unshift({instante: hora(m.instante), origen: m.servicio || m.origen, nivel: m.gravedad || m.nivel,
               destino: m.destino || "", mensaje: m.mensaje});
flow.set(clave, lista.slice(0, 15));
return clave === "registro" ? [{payload: lista.slice(0, 15)}, null] : [null, {payload: lista.slice(0, 15)}];
"""
mqtt_in("in-registro", "gemelo/sistema/registro", ["f-listas"], 26)
mqtt_in("in-avisos", "gemelo/notificaciones/entrada", ["f-listas"], 27)
funcion("f-listas", "Registro y avisos", COD_LISTA, [["w-registro"], ["w-avisos"]], 1, 26, n_salidas=2)
TABLA_LISTA = """<template>
  <div v-if="!msg || !msg.payload || msg.payload.length === 0">Sin entradas.</div>
  <table v-else style="width:100%;border-collapse:collapse;font-size:13px">
    <tr style="background:#1E3A5F;color:white"><th style="text-align:left;padding:4px">Instante (real)</th>
      <th style="text-align:left">Origen</th><th style="text-align:left">Nivel</th><th style="text-align:left">Mensaje</th></tr>
    <tr v-for="(e, i) in msg.payload" :key="i" :style="{background: i % 2 ? '#F3F6FA' : 'white'}">
      <td style="padding:4px;white-space:nowrap">{{ e.instante }}</td><td style="padding:0 8px">{{ e.origen }}</td>
      <td style="padding:0 8px" :style="{color: ['error', 'alarma', 'aviso'].includes(e.nivel) ? '#E07A2F' : '#1E3A5F'}">{{ e.nivel }}</td>
      <td>{{ e.mensaje }}<span v-if="e.destino" style="color:#8A94A3"> (para: {{ e.destino }})</span></td></tr>
  </table>
</template>"""
plantilla("w-registro", "g-registro", "Registro", TABLA_LISTA, 1, 2, 26)
plantilla("w-avisos", "g-avisos", "Avisos", TABLA_LISTA, 1, 2, 27)

# --- Latido del propio panel (DD-14) ------------------------------------------------------------
nodo(id="i-latido", type="inject", z=Z, name="cada 10 s", props=[{"p": "payload"}], repeat="10", crontab="",
     once=True, onceDelay="1", topic="", payload="", payloadType="date", **pos(0, 30), wires=[["f-latido-panel"]])
funcion("f-latido-panel", "Latido del panel",
        'msg.payload = JSON.stringify({instante_real: new Date().toISOString()});\nreturn msg;',
        [["out-latido"]], 1, 30)
mqtt_out("out-latido", "gemelo/sistema/latido/panel", "gemelo/sistema/latido/panel", 2, 30)

SALIDA.mkdir(parents=True, exist_ok=True)
(SALIDA / "flows.json").write_text(json.dumps(nodos, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
# Credenciales del bus: solo los nombres de las variables de entorno, que Node-RED sustituye al cargar.
(SALIDA.parent / "flows_cred.json").write_text(json.dumps(
    {"bus-gemelo": {"user": "${BUS_USUARIO}", "password": "${BUS_CLAVE}"}}, indent=4) + "\n", encoding="utf-8")
print(f"Generados {len(nodos)} nodos en {SALIDA / 'flows.json'}")
