"""Plantillas de correo en espanol (texto plano + HTML). Puras: sin red ni base de datos."""

import html
from datetime import timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from urllib.parse import quote

COLOMBIA = timezone(timedelta(hours=-5))  # Colombia no tiene horario de verano
ADVERTENCIA = "Estación de bajo costo, no sustituye mediciones oficiales."
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
SEVERIDAD = {"critica": "Crítica", "aviso": "Aviso", "info": "Información"}
REGLAS = {
    "ica_1h": "ICA (media de 1 h)",
    "tabla4_pm25": "Tabla 4 · PM2.5 (media de 24 h)",
    "tabla4_pm10": "Tabla 4 · PM10 (media de 24 h)",
    "sensor_congelado": "Sensor congelado",
    "salto_temperatura": "Salto de temperatura",
    "fuera_de_rango_fisico": "Valor fuera de rango",
    "dispositivo_caido": "Dispositivo caído",
}


def hora_colombia(dt):
    """datetime con zona -> '15 oct 2026, 07:00 (hora de Colombia)'."""
    d = dt.astimezone(COLOMBIA)
    return f"{d.day} {MESES[d.month - 1]} {d.year}, {d:%H:%M} (hora de Colombia)"


def duracion(desde, hasta):
    minutos = max(0, round((hasta - desde).total_seconds() / 60))
    if minutos < 60:
        return f"{minutos} min"
    h, m = divmod(minutos, 60)
    return f"{h} h {m} min" if m else f"{h} h"


def enlaces(base_url, device_id, token_confirmacion=None, token_baja=None):
    out = {"dashboard": f"{base_url}/?device={quote(device_id, safe='')}"}
    if token_confirmacion:
        out["confirmar"] = f"{base_url}/subscriptions/confirm?token={quote(token_confirmacion, safe='')}"
    if token_baja:
        out["baja"] = f"{base_url}/subscriptions/unsubscribe?token={quote(token_baja, safe='')}"
    return out


def _mensaje(remitente, destino, asunto, texto, cuerpo_html, link_baja=None):
    msg = EmailMessage()
    msg["From"] = remitente
    msg["To"] = destino
    msg["Subject"] = asunto
    msg["Message-ID"] = make_msgid(domain="ecoair.local")
    if link_baja:
        # Baja con un clic (RFC 8058): el cliente de correo hace POST a esta URL.
        msg["List-Unsubscribe"] = f"<{link_baja}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(texto)
    msg.add_alternative(cuerpo_html, subtype="html")
    return msg


def _html(titulo, parrafos, boton=None, pie=()):
    """HTML sencillo con estilos en linea (los clientes de correo ignoran <style> externos)."""
    ps = "".join(f'<p style="margin:0 0 12px;color:#333">{p}</p>' for p in parrafos)
    b = (f'<p style="margin:20px 0"><a href="{html.escape(boton[1])}" style="background:#1a7f37;color:#fff;'
         f'padding:10px 18px;border-radius:999px;text-decoration:none;font-weight:600">{boton[0]}</a></p>') if boton else ""
    pies = "".join(f'<p style="margin:4px 0;color:#777;font-size:12px">{p}</p>' for p in pie)
    return (f'<!doctype html><html lang="es"><body style="font-family:Arial,sans-serif;max-width:560px;margin:auto;'
            f'padding:16px"><h2 style="margin:0 0 12px;color:#111">{titulo}</h2>{ps}{b}'
            f'<hr style="border:0;border-top:1px solid #ddd;margin:20px 0">{pies}</body></html>')


def correo_confirmacion(remitente, destino, device_id, links):
    asunto = f"[EcoAir AI] Confirma tu suscripción a alertas de {device_id}"
    texto = (
        f"Hola:\n\nPediste recibir alertas de calidad del aire de la estación {device_id}.\n"
        f"Para confirmarlo abre este enlace (válido 24 h):\n{links['confirmar']}\n\n"
        "Si no fuiste tú, ignora este correo: sin confirmación no recibirás nada.\n\n"
        f"Darte de baja: {links['baja']}\n{ADVERTENCIA}\n"
    )
    cuerpo = _html(
        "Confirma tu suscripción",
        [f"Pediste recibir alertas de calidad del aire de la estación <b>{html.escape(device_id)}</b>.",
         "El enlace es válido durante 24 horas. Si no fuiste tú, ignora este correo: sin "
         "confirmación no recibirás nada."],
        boton=("Confirmar suscripción", links["confirmar"]),
        pie=[f'<a href="{html.escape(links["baja"])}">Darme de baja</a>', ADVERTENCIA],
    )
    return _mensaje(remitente, destino, asunto, texto, cuerpo, links["baja"])


def correo_alerta(remitente, destino, alerta, kind, links):
    """kind: 'open' (alerta abierta) o 'close' (alerta cerrada, con duracion)."""
    regla = REGLAS.get(alerta["rule_code"], alerta["rule_code"] or alerta["kind"])
    nivel = SEVERIDAD.get(alerta["severity"], alerta["severity"] or "Información")
    device = alerta["device_id"]
    inicio = hora_colombia(alerta["started_at"])
    if kind == "close":
        asunto = f"[EcoAir AI] Alerta cerrada: {regla} · {device}"
        estado = (f"Se cerró a las {hora_colombia(alerta['ended_at'])}. "
                  f"Duración: {duracion(alerta['started_at'], alerta['ended_at'])}.")
    else:
        asunto = f"[EcoAir AI] Alerta {nivel.lower()}: {regla} · {device}"
        estado = f"Se abrió a las {inicio}."
    texto = (
        f"Estación: {device}\nNivel: {nivel}\nRegla: {regla}\nInicio: {inicio}\n{estado}\n\n"
        f"{alerta['message']}\n\nVer el panel: {links['dashboard']}\n\n"
        f"Darte de baja: {links['baja']}\n{ADVERTENCIA}\n"
    )
    cuerpo = _html(
        f"{'Alerta cerrada' if kind == 'close' else 'Alerta ' + nivel.lower()}: {html.escape(regla)}",
        [f"<b>Estación:</b> {html.escape(device)}<br><b>Nivel:</b> {html.escape(nivel)}<br>"
         f"<b>Regla:</b> {html.escape(regla)}<br><b>Inicio:</b> {inicio}",
         html.escape(estado),
         html.escape(alerta["message"])],
        boton=("Ver el panel", links["dashboard"]),
        pie=[f'<a href="{html.escape(links["baja"])}">Darme de baja</a>', ADVERTENCIA],
    )
    return _mensaje(remitente, destino, asunto, texto, cuerpo, links["baja"])


def payload_brevo(msg):
    """JSON para POST /v3/smtp/email de Brevo a partir de un EmailMessage (sin la clave)."""
    nombre, remitente = parseaddr(str(msg["From"]))
    _, destino = parseaddr(str(msg["To"]))
    sender = {"email": remitente}
    if nombre:
        sender["name"] = nombre
    payload = {
        "sender": sender,
        "to": [{"email": destino}],
        "subject": str(msg["Subject"]),
        "textContent": msg.get_body(("plain",)).get_content(),
        "htmlContent": msg.get_body(("html",)).get_content(),
    }
    cabeceras = {h: str(msg[h]) for h in ("List-Unsubscribe", "List-Unsubscribe-Post") if msg[h]}
    if cabeceras:
        payload["headers"] = cabeceras
    return payload
