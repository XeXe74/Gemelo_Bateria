"""Extrae los puntos de la figura S1e de Preger et al. (2020): capacidad de celdas NMC a 25 °C frente a los
ciclos equivalentes (EFC), con tres ventanas de carga (40-60 % en azul, 20-80 % en negro y 0-100 % en rojo).

Los datos brutos de Sandia solo se facilitan bajo petición, por lo que se usan los resultados publicados en
la figura. La página del material suplementario es una imagen de 300 ppp: el script localiza el marco de la
gráfica, detecta los marcadores por su color y convierte su posición en EFC y % de capacidad inicial.

Uso (desde la carpeta Gemelo_Bateria): .venv/Scripts/python -m calibracion.sandia_figura_s1e
Necesita PyMuPDF solo para sacar la imagen del PDF la primera vez (se guarda en data/ensayos/sandia/).
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

RAIZ = Path(__file__).resolve().parent.parent
PDF = RAIZ / "referencias" / "Preger_2020_Degradation_commercial_Li-ion_cells.pdf"
CARPETA = RAIZ / "data" / "ensayos" / "sandia"
IMAGEN = CARPETA / "preger_2020_figura_S1.jpg"
ZONA = (1000, 680, 1600, 965)  # recorte aproximado del panel e en la página (píxeles de la imagen)
EJE_X = (0, 2500)
EJE_Y = (80, 100)
SERIES = {"40-60": "azul", "20-80": "negro", "0-100": "rojo"}


def imagen() -> np.ndarray:
    if not IMAGEN.exists():
        import fitz  # PyMuPDF
        doc = fitz.open(PDF)
        xref = doc[28].get_images(full=True)[0][0]
        CARPETA.mkdir(parents=True, exist_ok=True)
        IMAGEN.write_bytes(doc.extract_image(xref)["image"])
    return np.asarray(Image.open(IMAGEN).convert("RGB")).astype(int)


def marco(oscuro: np.ndarray) -> tuple[int, int, int, int]:
    """Bordes del marco: las columnas y filas oscuras en más de la mitad de su longitud, que solo pueden ser
    sus lados. Cada lado mide dos píxeles de grosor, y se toma su centro."""
    cols = np.nonzero(oscuro.mean(axis=0) > 0.5)[0]
    filas = np.nonzero(oscuro.mean(axis=1) > 0.5)[0]
    mc, mf = oscuro.shape[1] / 2, oscuro.shape[0] / 2
    izq, der = cols[cols < mc].mean(), cols[cols > mc].mean()
    arriba, abajo = filas[filas < mf].mean(), filas[filas > mf].mean()
    return int(round(izq)), int(round(arriba)), int(round(der)), int(round(abajo))


def mascaras(rgb: np.ndarray) -> dict[str, np.ndarray]:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return {
        "rojo": (r > 170) & (g < 110) & (b < 110),
        "azul": (b > 170) & (r < 110) & (g < 130),
        "negro": (r < 70) & (g < 70) & (b < 70),
    }


def marcadores(mascara: np.ndarray, area_min: int) -> list[tuple[float, float]]:
    """Centros de los marcadores: zonas del color con área suficiente (las líneas discontinuas son finas)."""
    abierta = ndimage.binary_opening(mascara, structure=np.ones((5, 5)))  # elimina las líneas finas
    etiquetas, n = ndimage.label(abierta)
    centros = []
    for i in range(1, n + 1):
        ys, xs = np.nonzero(etiquetas == i)
        if len(xs) >= area_min:
            # Marcadores solapados: se reparten según el área típica de un marcador
            k = max(1, round(len(xs) / (2.2 * area_min)))
            if k == 1:
                centros.append((xs.mean(), ys.mean()))
            else:
                orden = np.argsort(xs)
                for parte in np.array_split(orden, k):
                    centros.append((xs[parte].mean(), ys[parte].mean()))
    return centros


def main() -> None:
    rgb = imagen()
    x0, y0, x1, y1 = ZONA
    zona = rgb[y0:y1, x0:x1]
    oscuro = zona.mean(axis=2) < 150
    izq, arriba, der, abajo = marco(oscuro)
    print(f"marco del panel: x {x0 + izq}-{x0 + der}, y {y0 + arriba}-{y0 + abajo}")
    interior = zona[arriba + 4:abajo - 3, izq + 4:der - 3]  # sin el marco ni las marcas de los ejes
    filas = []
    for ventana, color in SERIES.items():
        for cx, cy in marcadores(mascaras(interior)[color], area_min=40):
            px, py = cx + izq + 4, cy + arriba + 4
            efc = EJE_X[0] + (px - izq) / (der - izq) * (EJE_X[1] - EJE_X[0])
            cap = EJE_Y[1] - (py - arriba) / (abajo - arriba) * (EJE_Y[1] - EJE_Y[0])
            filas.append({"ventana": ventana, "efc": round(efc, 1), "capacidad_pct": round(cap, 2)})
    filas.sort(key=lambda f: (f["ventana"], f["efc"]))
    CARPETA.mkdir(parents=True, exist_ok=True)
    with open(CARPETA / "figura_S1e_puntos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ventana", "efc", "capacidad_pct"])
        w.writeheader()
        w.writerows(filas)
    for ventana in SERIES:
        pts = [f for f in filas if f["ventana"] == ventana]
        print(f"{ventana} %: {len(pts)} puntos, de {pts[0]['efc']:.0f} a {pts[-1]['efc']:.0f} EFC, "
              f"capacidad final {pts[-1]['capacidad_pct']:.1f} %")


if __name__ == "__main__":
    main()
