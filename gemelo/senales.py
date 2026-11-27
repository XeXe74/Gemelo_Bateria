"""Señales del gemelo: formato, nombres y temas (GDB-IDS-ACT y GDB-IDS-CAL, apartado 4.3).

Cada señal lleva valor (v), calidad de la comunicación (q), coherencia del valor (k) y
marca temporal (ts) en UTC con formato ISO 8601. El tema del bus del gemelo es el nombre
de la señal con barras en lugar de puntos y el prefijo «gemelo/».
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

# Códigos de calidad (Q) y de coherencia (K)
Q_OK, Q_FALLO = 0, 1
K_OK, K_NO_INICIALIZADO, K_FALLO_SENSOR, K_RANGO_SUP, K_RANGO_INF, K_INCOMPATIBLE = 0, 10, 20, 30, 40, 80

LIMITES = (80, 85, 90, 95, 100)
ACTIVIDADES = ("casa", "clase", "clase_datos", "carplay", "gimnasio", "social", "cena", "cine",
               "noche_fuera", "salida_corta", "dia_fuera")

# Señales que el conector publica a partir de los mensajes del activo
LEVEL = "PHONE1.BAT1.level"
TEMPERATURE = "PHONE1.BAT1.temperature"
SOH = "PHONE1.BAT1.soh"
PLUGGED = "PHONE1.status.plugged"
SLEEP = "PHONE1.status.sleep"
FEEDBACK = "CHARGER1.limit.command.feedback"
EVENTS_TOMORROW = "USER1.CAL1.events.tomorrow"
# Señales de escritura (lista blanca)
COMMAND = "CHARGER1.limit.command"
EVENT_COMMAND = "USER1.CAL1.event.command"
# Señales generadas por el gemelo (prefijo GD)
RECOMMENDATION = "GD.PHONE1.recommendation"
RISK = "GD.USER1.risk"


def tema(nombre: str) -> str:
    return "gemelo/" + nombre.replace(".", "/")


def ahora_utc() -> str:
    return iso(datetime.now(timezone.utc))


def iso(t: datetime) -> str:
    """Instante en UTC con milisegundos, p. ej. 2026-11-20T00:00:00.000Z."""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def leer_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def senal(nombre: str, v, q: int = Q_OK, k: int = K_OK, ts: str | None = None) -> str:
    return json.dumps({"type": "tag", "body": {"t": nombre, "v": v, "q": q, "k": k, "ts": ts or ahora_utc()}},
                      ensure_ascii=False)


def leer_senal(carga: bytes | str) -> dict | None:
    """Devuelve el cuerpo de una señal (t, v, q, k, ts) o None si el mensaje no lo es."""
    try:
        m = json.loads(carga)
    except (ValueError, TypeError):
        return None
    if not isinstance(m, dict) or m.get("type") != "tag" or not isinstance(m.get("body"), dict):
        return None
    b = m["body"]
    return b if {"t", "v", "q", "k", "ts"} <= b.keys() else None


def k_rango(v: float, minimo: float, maximo: float) -> int:
    """Fuera de rango solo si se pasa en más de un 10 % del rango (K = 30 o 40)."""
    margen = 0.1 * (maximo - minimo)
    if v > maximo + margen:
        return K_RANGO_SUP
    if v < minimo - margen:
        return K_RANGO_INF
    return K_OK
