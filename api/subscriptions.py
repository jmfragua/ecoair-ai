"""Suscripciones a alertas por correo: logica pura (sin base de datos ni red).

La usan la API (main.py) y el servicio notifier/ (se copia al contenedor, no se duplica).

- Tokens: el de confirmacion es aleatorio y de un solo uso; el de baja es un HMAC-SHA256 de
  (subscriber_id, created_at) con SUBSCRIPTION_SECRET, asi el notifier puede volver a ponerlo
  en cada correo. En la base solo se guarda el SHA-256 de cada token.
- Respuestas genericas: la API nunca dice si un correo ya existe.
- Limites: por IP en memoria (20 cada 10 min) y 3 confirmaciones por correo cada 24 h.
- Logs: el correo se enmascara (a***@gmail.com) y los tokens nunca se escriben.
"""

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

CONSENT_VERSION = "v1"
SEVERIDADES = ["aviso", "critica"]          # severidad minima que puede elegir el suscriptor
RANGO_SEVERIDAD = {"info": 0, "aviso": 1, "critica": 2}
LIMITE_IP = 20
VENTANA_IP_S = 600
LIMITE_CONFIRMACIONES = 3
VENTANA_CONFIRMACIONES = timedelta(hours=24)
VIGENCIA_CONFIRMACION = timedelta(hours=24)

MENSAJE_GENERICO = (
    "Si el correo es válido, recibirás un mensaje para confirmar la suscripción. "
    "Revisa también la carpeta de spam."
)
MENSAJE_LIMITE = "Demasiadas solicitudes. Intenta de nuevo en unos minutos."
CONTACTO_NO_CONFIGURADO = "contacto no configurado"

_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


# ---------- Correo ----------

def normalizar_email(email):
    """Devuelve el correo en minusculas si el formato es valido, o None.

    Solo valida sintaxis (sin consultar DNS): parte local <= 64, total <= 254, sin puntos
    al inicio/fin ni dobles en la parte local, dominio con al menos un punto.
    """
    if not isinstance(email, str):
        return None
    e = email.strip().lower()
    if len(e) > 254 or not _EMAIL_RE.match(e):
        return None
    local, dominio = e.split("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
        return None
    if any(p == "" or p.startswith("-") or p.endswith("-") for p in dominio.split(".")):
        return None
    return e


def enmascarar_email(email):
    """'ana.perez@gmail.com' -> 'a***@gmail.com'. Para logs fuera del modo DRY_RUN."""
    if not email or "@" not in email:
        return "***"
    local, dominio = email.split("@", 1)
    return f"{local[:1]}***@{dominio}"


# ---------- Tokens ----------

def generar_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_baja(secret, subscriber_id, created_at):
    """Token de baja deterministico: HMAC-SHA256(secret, "id:created_at"), base64url."""
    if not secret:
        raise ValueError("SUBSCRIPTION_SECRET no configurado")
    msg = f"{subscriber_id}:{created_at.isoformat()}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


# ---------- Limites ----------

class LimitadorVentana:
    """Limite de peticiones por clave en una ventana deslizante, en memoria del proceso."""

    def __init__(self, maximo, ventana_s, reloj=time.monotonic):
        self.maximo, self.ventana_s, self.reloj = maximo, ventana_s, reloj
        self._eventos = defaultdict(deque)

    def permitir(self, clave):
        ahora = self.reloj()
        q = self._eventos[clave]
        while q and ahora - q[0] >= self.ventana_s:
            q.popleft()
        if len(q) >= self.maximo:
            return False
        q.append(ahora)
        return True

    def reiniciar(self):
        self._eventos.clear()


def _ip_valida(valor):
    try:
        return str(ipaddress.ip_address(valor.strip()))
    except (ValueError, AttributeError):
        return None


def ip_cliente(ip_conexion, cabeceras, confiar_proxy):
    """IP real del cliente.

    Solo si confiar_proxy (TRUST_PROXY_HEADERS=true) se leen CF-Connecting-IP (tunel de
    Cloudflare) o, si no esta, la primera IP de X-Forwarded-For (Railway y otros proxies).
    Sin proxy esas cabeceras las puede inventar cualquiera, por eso por defecto se ignoran.
    """
    if confiar_proxy:
        cf = _ip_valida(cabeceras.get("cf-connecting-ip", ""))
        if cf:
            return cf
        xff = cabeceras.get("x-forwarded-for", "")
        if xff:
            primera = _ip_valida(xff.split(",")[0])
            if primera:
                return primera
    return ip_conexion or "desconocida"


def puede_enviar_confirmacion(envios, ahora):
    """envios: lista de (confirm_sent_count, last_confirm_sent_at) de las filas de ese correo.

    Cuenta solo los envios cuya ultima fecha esta dentro de las ultimas 24 h.
    """
    recientes = sum(c for c, ultimo in envios if ultimo and ahora - ultimo < VENTANA_CONFIRMACIONES)
    return recientes < LIMITE_CONFIRMACIONES


def nuevo_conteo_confirmaciones(conteo, ultimo, ahora):
    """Conteo tras enviar una confirmacion: se reinicia si el ultimo envio tiene mas de 24 h."""
    if not ultimo or ahora - ultimo >= VENTANA_CONFIRMACIONES:
        return 1
    return conteo + 1


def decidir_registro(fila, envios, ahora):
    """Que hacer ante POST /subscriptions. La respuesta HTTP es la misma en todos los casos.

    fila: la suscripcion existente de (email, device_id) o None.
    Devuelve "insertar", "reactivar" (pedir de nuevo la confirmacion) o "nada".
    """
    if fila is not None and fila["confirmed_at"] and not fila["unsubscribed_at"]:
        return "nada"  # ya esta activa: no se reenvia nada ni se cambia la preferencia
    if not puede_enviar_confirmacion(envios, ahora):
        return "nada"  # limite de 3 confirmaciones por correo cada 24 h
    return "insertar" if fila is None else "reactivar"


# ---------- Destinatarios ----------

def severidad_cumple(min_severity, severity):
    """'aviso' recibe aviso y critica; 'critica' solo critica."""
    return RANGO_SEVERIDAD.get(severity or "info", 0) >= RANGO_SEVERIDAD.get(min_severity, 1)


# ---------- Consentimiento (Ley 1581 de 2012) ----------

def texto_consentimiento(contact_email):
    """Texto corto de autorizacion. contact_email sale de CONTACT_EMAIL (sin valor inventado)."""
    contacto = (contact_email or "").strip() or None
    return {
        "version": CONSENT_VERSION,
        "contacto_configurado": contacto is not None,
        "contacto": contacto or CONTACTO_NO_CONFIGURADO,
        "texto": (
            "Autorizo el tratamiento de mi correo electrónico con la única finalidad de recibir "
            "alertas de calidad del aire de esta estación EcoAir AI. Responsable: José Mario "
            "Fragua Rengifo (proyecto académico). Solo se guardan el correo y la fecha de esta "
            "autorización; no se comparten con terceros. Puedo darme de baja en cualquier "
            "momento con el enlace de cada correo, y consultar, corregir o suprimir mis datos "
            f"escribiendo a: {contacto or CONTACTO_NO_CONFIGURADO}. (Ley 1581 de 2012)"
        ),
    }
