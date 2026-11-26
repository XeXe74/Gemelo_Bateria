"""Modelo 3D aligerado de la maqueta digital (glTF binario) y su vista para los documentos.

Uso (desde la carpeta Gemelo_Bateria):
    .venv/bin/python herramientas/maqueta_3d.py
Genera maqueta/modelo_3d.glb y figuras/maqueta_3d.png.

Cada pieza es un nodo glTF cuyo nombre es el identificador del elemento en maqueta/maqueta.json,
con su traslación respecto al nodo padre. Las medidas del móvil son las de Apple; las de la
batería y del cargador son aproximadas, ya que el modelo es solo de visualización.
"""
import json
import struct
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

RAIZ = Path(__file__).resolve().parent.parent
MM = 0.001  # glTF trabaja en metros

# Materiales: nombre -> (color RGBA, modo alfa)
MATERIALES = {
    "carcasa": ((0.55, 0.60, 0.66, 0.30), "BLEND"),
    "pantalla": ((0.10, 0.12, 0.16, 1.0), "OPAQUE"),
    "camara": ((0.25, 0.27, 0.30, 1.0), "OPAQUE"),
    "bateria": ((0.17, 0.48, 0.48, 1.0), "OPAQUE"),
    "cargador": ((0.93, 0.93, 0.93, 1.0), "OPAQUE"),
}

# Nodos: id, nombre, padre, traslación respecto al padre (mm), piezas [(material, tamaño mm, centro mm)]
NODOS = [
    ("ACT1", "Móvil y cargador del usuario", None, (0, 0, 0), []),
    ("PHONE1", "iPhone 15", "ACT1", (0, 0, 0), [
        ("carcasa", (71.6, 147.6, 7.8), (0, 0, 0)),
        ("pantalla", (68.0, 144.0, 0.4), (0, 0, 4.1)),
        ("camara", (22.0, 22.0, 1.6), (-19.0, 57.0, -4.7)),
    ]),
    ("BAT1", "Batería", "PHONE1", (0, -12.0, 0), [
        ("bateria", (52.0, 82.0, 4.5), (0, 0, 0)),
    ]),
    ("CHARGER1", "Cargador inteligente (simulado)", "ACT1", (0, -150.0, 0), [
        ("cargador", (35.0, 28.0, 35.0), (0, -30.0, 0)),
        ("cargador", (3.0, 92.0, 3.0), (0, 30.0, 0)),
    ]),
]


def caja(tam, centro):
    """Vértices, normales e índices de una caja con caras planas."""
    sx, sy, sz = (t * MM / 2 for t in tam)
    cx, cy, cz = (c * MM for c in centro)
    caras = [((1, 0, 0), [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)]),
             ((-1, 0, 0), [(-1, -1, 1), (-1, 1, 1), (-1, 1, -1), (-1, -1, -1)]),
             ((0, 1, 0), [(-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1)]),
             ((0, -1, 0), [(-1, -1, 1), (-1, -1, -1), (1, -1, -1), (1, -1, 1)]),
             ((0, 0, 1), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
             ((0, 0, -1), [(-1, 1, -1), (1, 1, -1), (1, -1, -1), (-1, -1, -1)])]
    pos, nor, idx = [], [], []
    for k, (n, vs) in enumerate(caras):
        for v in vs:
            pos.append((cx + v[0] * sx, cy + v[1] * sy, cz + v[2] * sz))
            nor.append(n)
        b = 4 * k
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return np.array(pos, np.float32), np.array(nor, np.float32), np.array(idx, np.uint16)


def exportar_glb(salida):
    binario = bytearray()
    vistas, accesores, mallas = [], [], []
    mat_idx = {m: i for i, m in enumerate(MATERIALES)}

    def añadir(datos, objetivo, tipo, comp, minmax=False):
        while len(binario) % 4:
            binario.append(0)
        vistas.append({"buffer": 0, "byteOffset": len(binario), "byteLength": datos.nbytes, "target": objetivo})
        binario.extend(datos.tobytes())
        acc = {"bufferView": len(vistas) - 1, "componentType": comp, "count": len(datos), "type": tipo}
        if minmax:
            acc["min"] = datos.min(axis=0).tolist()
            acc["max"] = datos.max(axis=0).tolist()
        accesores.append(acc)
        return len(accesores) - 1

    nodos = []
    for ident, nombre, padre, tras, piezas in NODOS:
        nodo = {"name": ident, "translation": [t * MM for t in tras], "extras": {"id": ident, "nombre": nombre}}
        if piezas:
            prims = []
            for mat, tam, centro in piezas:
                p, n, i = caja(tam, centro)
                prims.append({"attributes": {"POSITION": añadir(p, 34962, "VEC3", 5126, True),
                                             "NORMAL": añadir(n, 34962, "VEC3", 5126)},
                              "indices": añadir(i, 34963, "SCALAR", 5123), "material": mat_idx[mat]})
            mallas.append({"name": ident, "primitives": prims})
            nodo["mesh"] = len(mallas) - 1
        nodos.append(nodo)
    ids = [n[0] for n in NODOS]
    for (ident, _, padre, _, _) in NODOS:
        if padre:
            nodos[ids.index(padre)].setdefault("children", []).append(ids.index(ident))
    while len(binario) % 4:
        binario.append(0)
    gltf = {
        "asset": {"version": "2.0", "generator": "herramientas/maqueta_3d.py", "extras": {"maqueta": "GDB-MAQ 1.1"}},
        "scene": 0, "scenes": [{"name": "GDB-MAQ", "nodes": [0]}], "nodes": nodos, "meshes": mallas,
        "materials": [{"name": m, "pbrMetallicRoughness": {"baseColorFactor": list(c), "metallicFactor": 0.1, "roughnessFactor": 0.6},
                       "alphaMode": a, "doubleSided": True} for m, (c, a) in MATERIALES.items()],
        "accessors": accesores, "bufferViews": vistas, "buffers": [{"byteLength": len(binario)}],
    }
    js = json.dumps(gltf, ensure_ascii=False).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(binario)
    with open(salida, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(binario), 0x004E4942) + bytes(binario))


def posicion_global(ident):
    total = np.zeros(3)
    while ident:
        nodo = next(n for n in NODOS if n[0] == ident)
        total += np.array(nodo[3])
        ident = nodo[2]
    return total


def dibujar(salida):
    plt.rcParams["font.family"] = "Calibri"
    fig = plt.figure(figsize=(9, 6.2), dpi=200)
    ax = fig.add_subplot(projection="3d")
    ax.computed_zorder = False  # orden fijo: la batería se ve a través de la carcasa transparente
    orden = {"pantalla": 0, "carcasa": 1, "camara": 2, "cargador": 2, "bateria": 3}
    for ident, _, _, _, piezas in NODOS:
        base = posicion_global(ident)
        for mat, tam, centro in piezas:
            c = base + np.array(centro)
            s = np.array(tam) / 2
            v = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * s + c
            caras = [[v[i] for i in f] for f in ([0, 1, 3, 2], [4, 5, 7, 6], [0, 1, 5, 4], [2, 3, 7, 6], [0, 2, 6, 4], [1, 3, 7, 5])]
            color, _ = MATERIALES[mat]
            ax.add_collection3d(Poly3DCollection(caras, facecolors=[color], edgecolors=(0.15, 0.2, 0.3, 0.6), linewidths=0.4,
                                                 zorder=orden[mat]))
    for ident, texto, desp in (("PHONE1", "PHONE1 · iPhone 15", (45, 60, 0)), ("BAT1", "BAT1 · batería", (35, -10, 0)),
                               ("CHARGER1", "CHARGER1 · cargador", (40, -30, 0))):
        p = posicion_global(ident) + np.array(desp)
        ax.text(*p, texto, fontsize=10, color="#1E3A5F", fontweight="bold", zorder=5)
    ax.set_xlim(-50, 110); ax.set_ylim(-200, 80); ax.set_zlim(-40, 40)
    ax.set_box_aspect((160, 280, 80))
    ax.view_init(elev=-30, azim=-58)
    ax.set_axis_off()
    fig.savefig(salida, bbox_inches="tight", facecolor="white")
    # Recorta el margen blanco que deja la proyección 3D
    from PIL import Image
    img = Image.open(salida).convert("RGB")
    a = np.asarray(img)
    filas, cols = np.where(a.min(axis=2) < 245)
    m = 20
    img.crop((max(cols.min() - m, 0), max(filas.min() - m, 0), cols.max() + m, filas.max() + m)).save(salida)


if __name__ == "__main__":
    (RAIZ / "maqueta").mkdir(exist_ok=True)
    exportar_glb(RAIZ / "maqueta" / "modelo_3d.glb")
    dibujar(RAIZ / "figuras" / "maqueta_3d.png")
    print("Generados maqueta/modelo_3d.glb y figuras/maqueta_3d.png")
