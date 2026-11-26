"""Vista operativa de alto nivel (OV-1) del gemelo de la batería.

Uso (desde la carpeta Gemelo_Bateria):
    .venv/bin/python herramientas/ov1.py
Genera figuras/ov1.png.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

NAVY, TEAL, ORANGE, INK, MUTED = "#1E3A5F", "#2C7A7B", "#E07A2F", "#262626", "#6B7785"
FONDO_GD, FONDO_ACT, FONDO_EXT = "#EEF3F8", "#E8F3F3", "#F5F6F8"

SALIDA = Path(__file__).resolve().parent.parent / "figuras" / "ov1.png"

fig, ax = plt.subplots(figsize=(13, 7.6), dpi=200)
ax.set_xlim(0, 100)
ax.set_ylim(0, 60)
ax.axis("off")
plt.rcParams["font.family"] = "Calibri"


def caja(x, y, w, h, texto, borde, fondo="white", estilo="-", tam=10.5, peso="normal", color=INK, lw=1.4):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.25,rounding_size=1.2",
                                ec=borde, fc=fondo, lw=lw, ls=estilo))
    if texto:
        ax.text(x + w / 2, y + h / 2, texto, ha="center", va="center", fontsize=tam,
                fontweight=peso, color=color, linespacing=1.3)


def titulo(x, y, texto, color):
    ax.text(x, y, texto, ha="left", va="top", fontsize=10, fontweight="bold", color=color)


def flecha(a, b, texto="", color=NAVY, estilo="-", desplaz=(0, 0), ha="center", tam=9, curva=0.0):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=13, color=color, lw=1.3,
                                 ls=estilo, connectionstyle=f"arc3,rad={curva}", shrinkA=2, shrinkB=2))
    if texto:
        mx, my = (a[0] + b[0]) / 2 + desplaz[0], (a[1] + b[1]) / 2 + desplaz[1]
        ax.text(mx, my, texto, ha=ha, va="center", fontsize=tam, color=MUTED, linespacing=1.25,
                bbox=dict(fc="white", ec="none", pad=0.6))


# --- Activo simulado (izquierda) ---------------------------------------------------
caja(1.5, 16, 27, 32, "", TEAL, FONDO_ACT, estilo="--")
titulo(3, 47, "ACTIVO (SIMULADO)", TEAL)
caja(4, 31, 22, 11, "Móvil (iPhone 15)\nbatería de 3349 mAh\nsalud inicial del 86 %", TEAL, tam=10.5)
caja(4, 19, 22, 7.5, "Cargador inteligente\n(20 W)", TEAL, tam=10.5)
flecha((15, 26.8), (15, 30.7), "", TEAL)
ax.text(16.2, 28.7, "carga", fontsize=9, color=MUTED, va="center")

caja(4, 51, 22, 6, "Calendario del usuario", TEAL, tam=10.5)

# --- Gemelo digital (centro) -------------------------------------------------------
caja(37, 14, 30, 43, "", NAVY, FONDO_GD, lw=2)
titulo(38.5, 56, "GEMELO DIGITAL", NAVY)
ax.text(38.5, 53.3, "una instancia en un PC, desplegada con Docker Compose", fontsize=8.5, color=MUTED, va="top")
caja(39.5, 43.5, 25, 5, "Conector: señales con valor, calidad,\ncoherencia y hora UTC", NAVY, tam=9.5)
caja(39.5, 36, 25, 5, "Bus de mensajes (MQTT) e historial", NAVY, tam=9.5)
caja(39.5, 26.5, 25, 7, "Modelos\nuso · degradación · decisión", NAVY, tam=9.5, peso="bold", color=NAVY)
caja(39.5, 16.5, 25, 7.5, "Servicios\ndecisión nocturna · previsión de\nsalud · avisos", NAVY, tam=9.5)
for y0, y1 in ((43.5, 41.2), (36, 33.7), (26.5, 24.2)):
    flecha((52, y0 - 0.2), (52, y1), "", NAVY)

# --- Usuarios e interfaz (derecha) -------------------------------------------------
caja(75, 49, 23, 8, "Usuario\n(propietario del móvil)", ORANGE, tam=10.5)
caja(75, 33, 23, 9, "Panel web\n(Node-RED Dashboard)", NAVY, tam=10.5)
caja(75, 17, 23, 8, "Observador\n(solo lectura)", MUTED, tam=10.5)
flecha((84, 42.4), (84, 48.6), "", ORANGE)
flecha((89, 48.6), (89, 42.4), "", ORANGE)
ax.text(83.2, 45.5, "estado\ny avisos", fontsize=8.5, color=MUTED, ha="right", va="center")
ax.text(89.8, 45.5, "eventos,\nrespuestas", fontsize=8.5, color=MUTED, ha="left", va="center")
flecha((86.5, 32.6), (86.5, 25.4), "", MUTED)

# --- Enlaces entre el activo, el gemelo y la interfaz -----------------------------
flecha((26.4, 38), (39.2, 46), "nivel, enchufado,\ntemperatura, salud", NAVY, desplaz=(0, 1.6))
flecha((26.4, 54), (39.2, 47.5), "eventos de\nmañana", NAVY, desplaz=(0.5, 1.8))
flecha((39.2, 20.8), (26.4, 23.4), "tope de carga\n(80–100 %)", ORANGE, desplaz=(0, 2.2))
flecha((26.4, 20.2), (39.2, 18.2), "confirmación", NAVY, desplaz=(0, -1.8))
flecha((64.8, 39.5), (74.6, 39.5), "estado, decisión\ny explicación", NAVY, desplaz=(0, 2.4))
flecha((74.6, 35.5), (64.8, 35.5), "eventos, respuestas\ny riesgo aceptado", ORANGE, desplaz=(0, -2.6))

# --- Fuentes de calibración (abajo) ------------------------------------------------
caja(14, 0.8, 66, 10.6, "", MUTED, FONDO_EXT, estilo="--")
titulo(15.3, 10.9, "FUENTES DE CALIBRACIÓN Y VALIDACIÓN (FUERA DE LÍNEA)", MUTED)
caja(17, 2, 28, 5.6, "Capturas de Ajustes → Batería\n(uso real, una por semana)", MUTED, tam=9.5)
caja(49, 2, 28, 5.6, "Ensayos públicos de envejecimiento\nde celdas (WMG, Sandia)", MUTED, tam=9.5)
flecha((42, 11.6), (45, 16.2), "", MUTED, estilo="--")
flecha((62, 11.6), (59, 16.2), "", MUTED, estilo="--")
flecha((16, 11.6), (13, 18.6), "", MUTED, estilo="--")
ax.text(7.5, 13.2, "calibra el\nsimulador", fontsize=8.5, color=MUTED, ha="center", va="center")
ax.text(66, 13.2, "calibra y valida los modelos", fontsize=8.5, color=MUTED, ha="left", va="center")

SALIDA.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(SALIDA, bbox_inches="tight", facecolor="white")
print("Guardada", SALIDA)
