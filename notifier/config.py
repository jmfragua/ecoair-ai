"""Constantes del notifier, en un solo sitio. Todas se configuran por variables de entorno."""

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

# Proveedor de correo: smtp (por defecto) o brevo (API HTTP; util donde la salida SMTP esta bloqueada).
PROVEEDORES = ("smtp", "brevo")
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp").strip().lower()
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_API_URL = os.getenv("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
# Remitente: EMAIL_FROM, o SMTP_FROM por compatibilidad. En Brevo debe estar verificado.
EMAIL_FROM = os.getenv("EMAIL_FROM", "") or SMTP_FROM
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
SUBSCRIPTION_SECRET = os.getenv("SUBSCRIPTION_SECRET", "")

# DATABASE_URL (Railway) o DB_HOST/DB_PORT/DB_NAME/DB_USER/POSTGRES_PASSWORD (compose local).
DB_DSN = dsn_desde_entorno(connect_timeout=5)


def faltantes():
    """Variables obligatorias sin valor (nombres, nunca valores)."""
    req = {"BASE_URL": BASE_URL, "SUBSCRIPTION_SECRET": SUBSCRIPTION_SECRET}
    if EMAIL_PROVIDER not in PROVEEDORES:
        return [k for k, v in req.items() if not v] + ["EMAIL_PROVIDER (smtp|brevo)"]
    if not DRY_RUN:
        if EMAIL_PROVIDER == "brevo":
            req |= {"BREVO_API_KEY": BREVO_API_KEY, "EMAIL_FROM": EMAIL_FROM}
        else:
            req |= {"SMTP_USER": SMTP_USER, "SMTP_PASSWORD": SMTP_PASSWORD, "SMTP_FROM": EMAIL_FROM}
    return [k for k, v in req.items() if not v]
