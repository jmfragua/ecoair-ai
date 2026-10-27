"""URL del código QR del pie del panel (lógica pura, sin base de datos).

El QR apunta al panel público: BASE_URL (la misma variable que usa el notifier para los enlaces de
los correos) + "/panel". Si BASE_URL falta o no es una URL http(s) válida, la API no inventa una:
devuelve url_panel = None y el panel usa su propio origen (la dirección con la que se abrió).
El nombre, la descripción y los ODS son texto fijo en api/static/panel.html.
"""
import os
from urllib.parse import urlsplit


def url_panel(base):
    """'https://x.app/' -> 'https://x.app/panel'; None si falta o no es http(s) con host."""
    if not base or not str(base).strip():
        return None
    base = str(base).strip().rstrip("/")
    try:
        partes = urlsplit(base)
    except ValueError:
        return None
    if partes.scheme not in ("http", "https") or not partes.netloc or partes.query or partes.fragment:
        return None
    return f"{base}/panel"


def info_desde_entorno():
    u = url_panel(os.getenv("BASE_URL"))
    return {"url_panel": u, "configurada": u is not None}
