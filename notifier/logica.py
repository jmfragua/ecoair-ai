"""Logica pura del notifier: reintentos, destinatarios y textos seguros para el log."""

import re
import sys
from datetime import timedelta
from pathlib import Path

# En desarrollo api/subscriptions.py esta en api/; en Docker se copia a /app.
_API = Path(__file__).resolve().parent.parent / "api"
if _API.is_dir() and str(_API) not in sys.path:
    sys.path.insert(0, str(_API))

from subscriptions import enmascarar_email, severidad_cumple  # noqa: E402

_EMAIL_EN_TEXTO = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def listo_para_reintento(intentos, ultimo_intento, ahora):
    """Espera creciente: 1, 2, 4, 8... minutos tras cada intento fallido."""
    if intentos == 0 or ultimo_intento is None:
        return True
    return ahora - ultimo_intento >= timedelta(minutes=2 ** (intentos - 1))


def puede_intentar(intentos, ultimo_intento, ahora, max_intentos):
    """Misma politica para confirmaciones y alertas: espera creciente y maximo de intentos."""
    return intentos < max_intentos and listo_para_reintento(intentos, ultimo_intento, ahora)


def error_seguro(exc, limite=200):
    """Texto del error para el log o la base, con los correos enmascarados."""
    texto = f"{type(exc).__name__}: {exc}"[:limite]
    return _EMAIL_EN_TEXTO.sub(lambda m: enmascarar_email(m.group(0)), texto)


def destinatarios(alerta_severity, suscriptores):
    """Suscriptores cuya severidad minima se cumple con la severidad de la alerta."""
    return [s for s in suscriptores if severidad_cumple(s["min_severity"], alerta_severity)]


def limitar_anomalias(filas):
    """Una anomalia por (dispositivo, suscriptor) en la tanda (la mas antigua). La consulta ya
    excluye a quien recibio una en las ultimas ANOMALIAS_CORREO_CADA_H horas."""
    vistos, out = set(), []
    for f in filas:
        clave = (f["device_id"], f["subscriber_id"])
        if clave not in vistos:
            vistos.add(clave)
            out.append(f)
    return out
