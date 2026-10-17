"""Plantillas de correo en espanol (texto plano + HTML). Puras: sin red ni base de datos."""

import html
from datetime import timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from urllib.parse import quote

COLOMBIA = timezone(timedelta(hours=-5))  # Colombia no tiene horario de verano
ADVERTENCIA = "Estación de bajo costo, no sustituye mediciones oficiales."
AVISO_SIMULACION = ("SIMULACIÓN: esta estación usa datos inventados para demostración; "
                    "no son mediciones de un sensor real.")
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
    "isolation_forest": "Anomalía (modelo, experimental)",
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
    ps = "".join(p[1] if isinstance(p, tuple) else f'<p style="margin:0 0 12px;color:#333">{p}</p>'
                 for p in parrafos)
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


def recomendacion_de_alerta(alerta):
    """Recomendacion completa si la alerta trae la categoria del ICA (regla ica_1h); si no, None.

    Las alertas de sensor, de dispositivo caido, de la Tabla 4 y de anomalias no tienen una
    categoria del ICA asociada y no llevan recomendaciones de salud.
    """
    if alerta.get("kind") == "anomaly" or alerta.get("rule_code") != "ica_1h":
        return None
    codigo = ((alerta.get("details") or {}).get("ica") or {}).get("category_code")
    from recomendaciones import RECOMENDACIONES, recomendacion  # puente a shared/recomendaciones.py
    return recomendacion(codigo) if codigo in RECOMENDACIONES else None


def correo_alerta(remitente, destino, alerta, kind, links, simulado=False):
    """kind: 'open' (alerta abierta) o 'close' (alerta cerrada, con duracion).

    simulado: alerta sobre datos simulados -> [SIMULACIÓN] en el asunto y aviso en el cuerpo.
    """
    pre = "[SIMULACIÓN] " if simulado else ""
    regla = REGLAS.get(alerta["rule_code"], alerta["rule_code"] or alerta["kind"])
    nivel = SEVERIDAD.get(alerta["severity"], alerta["severity"] or "Información")
    device = alerta["device_id"]
    inicio = hora_colombia(alerta["started_at"])
    anomalia = alerta.get("kind") == "anomaly"
    nota = ("Detección EXPERIMENTAL de un modelo (Isolation Forest), no es una regla de la norma. "
            "Puede haber falsas alarmas.") if anomalia else None
    if anomalia:
        asunto = f"{pre}[EcoAir AI] Anomalía experimental (modelo) · {device}"
        estado = f"Se detectó a las {inicio}."
    elif kind == "close":
        asunto = f"{pre}[EcoAir AI] Alerta cerrada: {regla} · {device}"
        estado = (f"Se cerró a las {hora_colombia(alerta['ended_at'])}. "
                  f"Duración: {duracion(alerta['started_at'], alerta['ended_at'])}.")
    else:
        asunto = f"{pre}[EcoAir AI] Alerta {nivel.lower()}: {regla} · {device}"
        estado = f"Se abrió a las {inicio}."
    rec = recomendacion_de_alerta(alerta)
    rec_txt, rec_html = bloque_recomendaciones(rec) if rec else ([], [])
    texto = (
        (f"{AVISO_SIMULACION}\n\n" if simulado else "")
        + f"Estación: {device}\nNivel: {nivel}\nRegla: {regla}\nInicio: {inicio}\n{estado}\n\n"
        + (f"{nota}\n\n" if nota else "")
        + f"{alerta['message']}\n\n"
        + ("\n".join(rec_txt) + "\n\n" if rec_txt else "")
        + f"Ver el panel: {links['dashboard']}\n\n"
        f"Darte de baja: {links['baja']}\n{ADVERTENCIA}\n"
    )
    cuerpo = _html(
        (f"{pre}Anomalía experimental (modelo)" if anomalia else
         f"{pre}{'Alerta cerrada' if kind == 'close' else 'Alerta ' + nivel.lower()}: {html.escape(regla)}"),
        ([f'<b style="color:#b45309">{html.escape(AVISO_SIMULACION)}</b>'] if simulado else []) +
        [f"<b>Estación:</b> {html.escape(device)}<br><b>Nivel:</b> {html.escape(nivel)}<br>"
         f"<b>Regla:</b> {html.escape(regla)}<br><b>Inicio:</b> {inicio}",
         html.escape(estado)]
        + ([f"<b>{html.escape(nota)}</b>"] if nota else [])
        + [html.escape(alerta["message"])] + rec_html,
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


AVISO_INDICATIVO = "Datos indicativos, sensor de bajo costo. No sustituye mediciones oficiales."
PREDICCION_NO_DISPONIBLE = "No disponible todavía."
MIN_SIN_DATOS_AVISO = 10


def aviso_ultima_medicion(resp):
    """'Última medición hace X min (el nodo puede estar apagado)' si pasaron mas de 10 min.

    Usa la antiguedad real de la respuesta de calidad del aire; sin mediciones no dice nada.
    """
    ultima = (resp or {}).get("ultima_medicion")
    if not ultima or ultima.get("antiguedad_s") is None:
        return None
    minutos = int(ultima["antiguedad_s"] // 60)
    if minutos <= MIN_SIN_DATOS_AVISO:
        return None
    return f"Última medición hace {minutos} min (el nodo puede estar apagado)"


def bloque_recomendaciones(rec):
    """(lineas de texto, bloques html) con las recomendaciones completas de una categoria.

    rec: salida de shared.recomendaciones.recomendacion (la misma que ve el dashboard).
    Formato pensado para Gmail en el movil: titulos cortos en negrita, vinetas y fuente al final.
    """
    t = rec["titulos"]
    g = rec["grupos_sensibles"]
    fuentes = "; ".join(f"{f['documento']}, pág. {f['pagina']}" for f in rec["fuente"])
    texto = [f"{t['efectos'].upper()}", rec["efectos"], "",
             f"{t['poblacion_general'].upper()}", rec["poblacion_general"], "",
             f"{t['grupos_sensibles'].upper()}", f"({g['quienes']})", g["recomendacion"], "",
             f"{t['acciones'].upper()}"] + [f"- {a}" for a in rec["acciones"]]
    if rec["cuando_consultar"]:
        texto += ["", f"{t['cuando_consultar'].upper()}", rec["cuando_consultar"]]
    if rec["si_sube"]:
        texto += ["", f"{t['si_sube'].upper()}", rec["si_sube"]]
    texto += ["", f"Fuente: {fuentes}. {rec['etiqueta']}."]

    def seccion(titulo, cuerpo):
        return ("raw", f'<p style="margin:0 0 12px;color:#333"><b>{html.escape(titulo)}</b><br>{cuerpo}</p>')

    lista = "".join(f'<li style="margin:0 0 4px">{html.escape(a)}</li>' for a in rec["acciones"])
    bloques = [
        seccion(t["efectos"], html.escape(rec["efectos"])),
        seccion(t["poblacion_general"], html.escape(rec["poblacion_general"])),
        seccion(t["grupos_sensibles"], f'<span style="color:#666;font-size:13px">{html.escape(g["quienes"])}'
                                       f'</span><br>{html.escape(g["recomendacion"])}'),
        ("raw", f'<p style="margin:0 0 4px;color:#333"><b>{html.escape(t["acciones"])}</b></p>'
                f'<ul style="margin:0 0 12px;padding-left:20px;color:#333">{lista}</ul>'),
    ]
    if rec["cuando_consultar"]:
        bloques.append(seccion(t["cuando_consultar"], html.escape(rec["cuando_consultar"])))
    if rec["si_sube"]:
        bloques.append(seccion(t["si_sube"], html.escape(rec["si_sube"])))
    bloques.append(("raw", f'<p style="margin:0 0 12px;color:#777;font-size:12px">Fuente: {html.escape(fuentes)}. '
                           f'{html.escape(rec["etiqueta"])}.</p>'))
    return texto, bloques


def _bloque_ica(resp):
    """(lineas de texto, parrafos html) con el ICA actual y la recomendacion de texto fijo."""
    if not resp or resp.get("estado") != "ok":
        t = "Ahora no hay datos recientes del sensor: no se puede calcular el ICA en este momento."
        return [t], [html.escape(t)]
    g, rec = resp["ica"]["general"], resp["recomendacion"]
    contaminante = {"pm25": "PM2.5", "pm10": "PM10"}.get(g["definido_por"], g["definido_por"])
    ica = (f"ICA actual: {g['ica']} ({g['category_name']}), definido por {contaminante}. "
           "Base: media de la última hora (indicativo; la Res. 2254/2017 usa promedios de 24 h).")
    rec_txt, rec_html = bloque_recomendaciones(rec)
    return [ica, ""] + rec_txt, [f"<b>{html.escape(ica)}</b>"] + rec_html


def correo_bienvenida(remitente, destino, device_id, resp, links, prediccion=None, simulado=False):
    """Correo unico al confirmar la suscripcion.

    resp: salida de calidad_aire.construir_respuesta (la misma del dashboard).
    prediccion: None -> "no disponible"; o dict con 'texto' (y opcional 'aviso') desde el paso 3.
    simulado: True si la estacion usa datos simulados -> [SIMULACIÓN] en asunto y cuerpo.
    """
    pre = "[SIMULACIÓN] " if simulado else ""
    asunto = f"{pre}[EcoAir AI] Suscripción confirmada · estación {device_id}"
    ica_txt, ica_html = _bloque_ica(resp)
    ultima = aviso_ultima_medicion(resp)
    if ultima:
        ica_txt, ica_html = ica_txt + [ultima], ica_html + [f"<i>{html.escape(ultima)}</i>"]
    pred_txt = PREDICCION_NO_DISPONIBLE if not prediccion else prediccion["texto"]
    pred_aviso = (prediccion or {}).get("aviso")
    texto = "\n".join(
        ([AVISO_SIMULACION, ""] if simulado else [])
        + [f"Tu suscripción a las alertas de la estación {device_id} está confirmada.", ""]
        + ica_txt
        + ["", "Predicción experimental:", pred_txt] + ([pred_aviso] if pred_aviso else [])
        + ["", AVISO_INDICATIVO, f"Ver el panel: {links['dashboard']}", "",
           f"Darte de baja: {links['baja']}", ADVERTENCIA, ""]
    )
    parrafos = (
        ([f'<b style="color:#b45309">{html.escape(AVISO_SIMULACION)}</b>'] if simulado else [])
        + [f"Tu suscripción a las alertas de la estación <b>{html.escape(device_id)}</b> está confirmada."]
        + ica_html
        + [f"<b>Predicción experimental:</b> {html.escape(pred_txt)}"
           + (f"<br><i>{html.escape(pred_aviso)}</i>" if pred_aviso else ""),
           html.escape(AVISO_INDICATIVO)]
    )
    cuerpo = _html(f"{pre}Suscripción confirmada", parrafos, boton=("Ver el panel", links["dashboard"]),
                   pie=[f'<a href="{html.escape(links["baja"])}">Darme de baja</a>', ADVERTENCIA])
    return _mensaje(remitente, destino, asunto, texto, cuerpo, links["baja"])
