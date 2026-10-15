"""Constantes del alerter, en un solo sitio. Todas se pueden cambiar por variables de entorno."""

import os


def _int(nombre, defecto):
    return int(os.getenv(nombre, str(defecto)))


def _bool(nombre, defecto):
    return os.getenv(nombre, str(defecto)).strip().lower() in ("1", "true", "si", "sí", "yes")


INTERVALO_S = _int("ALERTER_INTERVALO_S", 60)
REVISIONES_ABRIR = _int("ALERTER_REVISIONES_ABRIR", 2)
REVISIONES_CERRAR = _int("ALERTER_REVISIONES_CERRAR", 3)
VENTANA_DISPOSITIVOS_H = _int("ALERTER_VENTANA_DISPOSITIVOS_H", 24)
INCLUIR_SIMULADOS = _bool("ALERTER_INCLUIR_SIMULADOS", False)

# Ventana de datos por regla de sensor, para que la alerta refleje el estado actual.
VENTANA_SENSOR_MIN = {
    "sensor_congelado": _int("ALERTER_VENTANA_CONGELADO_MIN", 45),
    "salto_temperatura": _int("ALERTER_VENTANA_SALTO_MIN", 15),
    "fuera_de_rango_fisico": _int("ALERTER_VENTANA_RANGO_MIN", 15),
}

DB_DSN = (
    f"host={os.getenv('DB_HOST', 'localhost')} port={os.getenv('DB_PORT', '5433')} "
    f"dbname=ecoair user=ecoair password={os.environ.get('POSTGRES_PASSWORD', '')} connect_timeout=5"
)

if REVISIONES_ABRIR < 1 or REVISIONES_CERRAR < 1:
    raise ValueError("ALERTER_REVISIONES_ABRIR y ALERTER_REVISIONES_CERRAR deben ser >= 1")
