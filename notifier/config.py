"""Constantes del notifier, en un solo sitio. Todas se configuran por variables de entorno."""

import os


def _int(nombre, defecto):
    return int(os.getenv(nombre, str(defecto)))


def _bool(nombre, defecto):
    return os.getenv(nombre, str(defecto)).strip().lower() in ("1", "true", "si", "sí", "yes")


INTERVALO_CONFIRMACIONES_S = _int("NOTIFIER_INTERVALO_CONFIRMACIONES_S", 10)
INTERVALO_ALERTAS_S = _int("NOTIFIER_INTERVALO_ALERTAS_S", 30)
MAX_INTENTOS = _int("NOTIFIER_MAX_INTENTOS", 5)
# Por seguridad el modo prueba esta ACTIVO salvo que se ponga NOTIFIER_DRY_RUN=false.
DRY_RUN = _bool("NOTIFIER_DRY_RUN", True)
NOTIFY_ON_CLOSE = _bool("NOTIFY_ON_CLOSE", False)
INCLUIR_SIMULADOS = _bool("NOTIFIER_INCLUIR_SIMULADOS", False)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
SUBSCRIPTION_SECRET = os.getenv("SUBSCRIPTION_SECRET", "")

DB_DSN = (
    f"host={os.getenv('DB_HOST', 'localhost')} port={os.getenv('DB_PORT', '5433')} "
    f"dbname=ecoair user=ecoair password={os.environ.get('POSTGRES_PASSWORD', '')} connect_timeout=5"
)


def faltantes():
    """Variables obligatorias sin valor (nombres, nunca valores)."""
    req = {"BASE_URL": BASE_URL, "SUBSCRIPTION_SECRET": SUBSCRIPTION_SECRET}
    if not DRY_RUN:
        req |= {"SMTP_USER": SMTP_USER, "SMTP_PASSWORD": SMTP_PASSWORD, "SMTP_FROM": SMTP_FROM}
    return [k for k, v in req.items() if not v]
