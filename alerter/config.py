"""Constantes del alerter, en un solo sitio. Todas se pueden cambiar por variables de entorno."""

import os
import sys
from pathlib import Path

# shared/ esta en la raiz del repo en desarrollo; en Docker se copia junto al codigo (/app/shared).
_RAIZ = Path(__file__).resolve().parent.parent
if (_RAIZ / "shared").is_dir() and str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from shared.settings import dsn_desde_entorno  # noqa: E402


def _int(nombre, defecto):
    return int(os.getenv(nombre, str(defecto)))


def _bool(nombre, defecto):
    return os.getenv(nombre, str(defecto)).strip().lower() in ("1", "true", "si", "sí", "yes")


INTERVALO_S = _int("ALERTER_INTERVALO_S", 60)
REVISIONES_ABRIR = _int("ALERTER_REVISIONES_ABRIR", 2)
REVISIONES_CERRAR = _int("ALERTER_REVISIONES_CERRAR", 3)
VENTANA_DISPOSITIVOS_H = _int("ALERTER_VENTANA_DISPOSITIVOS_H", 24)
INCLUIR_SIMULADOS = _bool("ALERTER_INCLUIR_SIMULADOS", False)

# Capa EXPERIMENTAL de anomalias (Isolation Forest). Apagada por defecto. Solo se aplica a
# dispositivos simulados porque el modelo se entreno con datos simulados (ver su procedencia).
ANOMALIAS_ACTIVAS = _bool("ANOMALIAS_ACTIVAS", False)
ANOMALIAS_MODELO_PATH = os.getenv("ANOMALIAS_MODELO_PATH") or None
ANOMALIAS_VENTANA_MIN = _int("ANOMALIAS_VENTANA_MIN", 60)

# Predicciones EXPERIMENTALES de PM2.5 a 3, 4 y 5 h (modelo v4) y sus metricas diarias. Apagadas por
# defecto. Solo dispositivos reales de la lista (el simulado nunca genera predicciones).
PREDICCION_ACTIVA = _bool("PREDICCION_ACTIVA", False)
PREDICCION_MINUTO = _int("PREDICCION_MINUTO", 5)
PREDICCION_DISPOSITIVOS = [d.strip() for d in os.getenv("PREDICCION_DISPOSITIVOS", "ecoair-node-01").split(",")
                           if d.strip()]
_MODELO_V4 = "prediccion_pm25_v4.json"
PREDICCION_MODELO_PATH = os.getenv("PREDICCION_MODELO_PATH") or str(next(
    (c for c in (Path(__file__).resolve().parent / "modelos" / _MODELO_V4, _RAIZ / "modelos" / _MODELO_V4)
     if c.is_file()), Path(__file__).resolve().parent / "modelos" / _MODELO_V4))

# Ventana de datos por regla de sensor, para que la alerta refleje el estado actual.
VENTANA_SENSOR_MIN = {
    "sensor_congelado": _int("ALERTER_VENTANA_CONGELADO_MIN", 45),
    "salto_temperatura": _int("ALERTER_VENTANA_SALTO_MIN", 15),
    "fuera_de_rango_fisico": _int("ALERTER_VENTANA_RANGO_MIN", 15),
}

# DATABASE_URL (Railway) o DB_HOST/DB_PORT/DB_NAME/DB_USER/POSTGRES_PASSWORD (compose local).
DB_DSN = dsn_desde_entorno(connect_timeout=5)

if not 0 <= PREDICCION_MINUTO <= 59:
    raise ValueError("PREDICCION_MINUTO debe estar entre 0 y 59")
if REVISIONES_ABRIR < 1 or REVISIONES_CERRAR < 1:
    raise ValueError("ALERTER_REVISIONES_ABRIR y ALERTER_REVISIONES_CERRAR deben ser >= 1")
