"""Gráficas de comprobación de los datos simulados.

Uso (desde la carpeta Gemelo_Bateria, tras generar los datos):
    .venv/bin/python -m sim.graficas --semanas prueba_28d --anual anual_t100 --comparar anual_t80
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
FIG = RAIZ / "figuras"
NAVY, ORANGE, INK, MUTED, GRID = "#1E3A5F", "#E07A2F", "#262626", "#6B7785", "#E3E8EE"
CARGA = "#DCE7F2"
DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _fmt_dia(x, _pos=None):
    f = mdates.num2date(x)
    return f"{DIAS[f.weekday()]} {f.day}"


def _fmt_mes(x, _pos=None):
    f = mdates.num2date(x)
    return f"{MESES[f.month - 1]} {f.year % 100:02d}"

plt.rcParams.update({
    "font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "axes.titleweight": "bold", "axes.titlesize": 12, "axes.titlelocation": "left",
})


def _cargar(etiqueta: str, nombre: str, fechas=()) -> pd.DataFrame:
    return pd.read_csv(RAIZ / "data" / etiqueta / f"{nombre}.csv", parse_dates=list(fechas))


def nivel_dos_semanas(etiqueta: str) -> Path:
    p = _cargar(etiqueta, "pasos", ["momento"])
    p = p[p["momento"] < p["momento"].iloc[0] + pd.Timedelta(days=14)]
    fig, ax = plt.subplots(figsize=(13, 4.2), dpi=160)

    # Franjas de carga (el móvil enchufado).
    enchufado = p["enchufado"].astype(bool).to_numpy()
    t = p["momento"].to_numpy()
    i = 0
    while i < len(p):
        if enchufado[i]:
            j = i
            while j < len(p) and enchufado[j]:
                j += 1
            ax.axvspan(t[i], t[min(j, len(p) - 1)], color=CARGA, lw=0)
            i = j
        else:
            i += 1

    ax.plot(p["momento"], p["nivel"], color=NAVY, lw=1.8)
    inc = p[p["incidente"].astype(bool)]
    ax.scatter(inc["momento"], inc["nivel"], s=60, color=ORANGE, zorder=5, edgecolor="white", lw=1.5)
    for _, r in inc.iterrows():
        ax.annotate("incidente", (r["momento"], r["nivel"]), xytext=(6, 8),
                    textcoords="offset points", color=INK, fontsize=9)

    ax.set_ylim(0, 105)
    ax.set_ylabel("Nivel de batería (%)")
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_fmt_dia))
    ax.set_title("Nivel de batería simulado · primeras dos semanas (tope fijo 100 %)")
    ax.text(0.995, 1.02, "franjas azules = móvil enchufado", transform=ax.transAxes,
            ha="right", va="bottom", color=MUTED, fontsize=9)
    fig.tight_layout()
    out = FIG / "nivel_dos_semanas.png"
    fig.savefig(out)
    plt.close(fig)
    return out


def consumo_por_dia(etiqueta: str) -> Path:
    d = _cargar(etiqueta, "dias")
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=160)
    rng = pd.Series(range(len(d))).sample(frac=1, random_state=0).to_numpy() / len(d)
    x = d["dia_semana"] + (rng - 0.5) * 0.45
    ax.scatter(x, d["consumo_dia"], s=12, color=NAVY, alpha=0.45, lw=0)
    med = d.groupby("dia_semana")["consumo_dia"].median()
    ax.hlines(med.values, med.index - 0.3, med.index + 0.3, color=INK, lw=2.2)
    for dia, v in med.items():
        ax.text(dia + 0.33, v, f"{v:.0f} %", va="center", color=INK, fontsize=9)
    ax.axhline(100, color=MUTED, lw=1, ls=(0, (4, 3)))
    ax.text(-0.45, 102, "una carga completa", color=MUTED, fontsize=8.5, va="bottom")
    ax.set_xticks(range(7), DIAS)
    ax.set_xlim(-0.5, 6.7)
    ax.set_ylabel("Batería gastada en el día (%)")
    ax.set_title("Consumo diario simulado · 1 año", pad=22)
    ax.text(0.0, 1.02, "punto = un día · raya = mediana", transform=ax.transAxes,
            ha="left", va="bottom", color=MUTED, fontsize=9)
    fig.tight_layout()
    out = FIG / "consumo_por_dia_semana.png"
    fig.savefig(out)
    plt.close(fig)
    return out


def salud_anual(etiqueta_a: str, etiqueta_b: str, nombre_a: str, nombre_b: str) -> Path:
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=160)
    for et, nombre, color in ((etiqueta_a, nombre_a, NAVY), (etiqueta_b, nombre_b, ORANGE)):
        d = _cargar(et, "dias", ["fecha"])
        ax.plot(d["fecha"], d["soh_real"], color=color, lw=2)
        ax.text(d["fecha"].iloc[-1] + pd.Timedelta(days=4), d["soh_real"].iloc[-1],
                f"{nombre}\n{d['soh_real'].iloc[-1]:.1f} %", color=INK, va="center", fontsize=9)
    ax.axhline(80, color=MUTED, lw=1, ls=(0, (4, 3)))
    ax.text(d["fecha"].iloc[0], 79.6, "80 %: Apple recomienda cambiar la batería", color=MUTED,
            fontsize=8.5, va="top")
    ax.set_ylabel("Salud de la batería (%)")
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_fmt_mes))
    ax.set_xlim(right=d["fecha"].iloc[-1] + pd.Timedelta(days=60))
    ax.set_title("Salud simulada a 12 meses según el tope de carga")
    fig.tight_layout()
    out = FIG / "salud_12_meses.png"
    fig.savefig(out)
    plt.close(fig)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--semanas", default="prueba_28d")
    ap.add_argument("--anual", default="anual_t100")
    ap.add_argument("--comparar", default="anual_t80")
    a = ap.parse_args()
    FIG.mkdir(exist_ok=True)
    for f in (nivel_dos_semanas(a.semanas), consumo_por_dia(a.anual),
              salud_anual(a.anual, a.comparar, "siempre al 100 %", "siempre al 80 %")):
        print("Guardada", f)


if __name__ == "__main__":
    main()
