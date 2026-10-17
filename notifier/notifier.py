"""Servicio notifier: correos de confirmacion y de alertas (cola en la base de datos).

- Cada INTERVALO_CONFIRMACIONES_S (10 s): envia los correos de confirmacion pendientes.
  Genera el token, guarda solo su SHA-256 y envia el enlace. La API nunca usa SMTP.
- Cada INTERVALO_ALERTAS_S (30 s): encola en notifications_sent una fila por (alerta,
  suscriptor, tipo) y envia las pendientes, con reintentos y espera creciente.
  La restriccion UNIQUE de la tabla garantiza un correo por episodio.
- Bienvenida (migracion 007): al confirmarse una suscripcion se envia UNA vez un correo con el
  ICA actual (media de 1 h, igual que el dashboard), la recomendacion de texto fijo de la norma y
  la seccion "Prediccion experimental". welcome_sent_at se marca en la misma transaccion del
  envio: abrir el enlace de confirmacion dos veces no genera un segundo correo.
- DRY_RUN (por defecto activo): el correo completo se escribe en el log y no se envia.
  Fuera de DRY_RUN el log nunca lleva tokens ni correos completos (a***@gmail.com).
Requiere db/migrations/005_subscriptions.sql; si falta, lo registra y espera sin morirse.
"""

import json
import logging
import smtplib
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

import config
from correos import correo_alerta, correo_bienvenida, correo_confirmacion, enlaces, payload_brevo
from logica import destinatarios, error_seguro, limitar_anomalias, puede_intentar
from subscriptions import (  # noqa: E402  (logica.py ya anadio api/ al path)
    VIGENCIA_CONFIRMACION,
    enmascarar_email,
    generar_token,
    hash_token,
    nuevo_conteo_confirmaciones,
    token_baja,
)
from calidad_aire import SQL_ULTIMA, SQL_VENTANAS, construir_respuesta, ventanas_desde_fila  # noqa: E402
from shared.prediccion import SQL_SERIE_PM25, cargar_modelo, prediccion, serie_por_minuto  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notifier")

db = None


class ErrorEnvio(Exception):
    """Fallo del proveedor de correo (HTTP de Brevo). Nunca incluye la clave de la API."""


class Mailer:
    """Envia por SMTP (STARTTLS) o por la API HTTP de Brevo; en DRY_RUN escribe el correo en el log."""

    def __init__(self, dry_run, host, port, user, password, proveedor="smtp", brevo_api_key="",
                 brevo_url="https://api.brevo.com/v3/smtp/email"):
        self.dry_run, self.host, self.port, self.user, self.password = dry_run, host, port, user, password
        self.proveedor, self.brevo_api_key, self.brevo_url = proveedor, brevo_api_key, brevo_url

    def enviar(self, msg):
        destino = str(msg["To"])
        if self.dry_run:
            texto = msg.get_body(("plain",)).get_content()
            log.info("DRY_RUN (%s): correo NO enviado\nPara: %s\nAsunto: %s\nList-Unsubscribe: %s\n%s",
                     self.proveedor, destino, msg["Subject"], msg["List-Unsubscribe"], texto)
            return "dry_run"
        if self.proveedor == "brevo":
            return self._enviar_brevo(msg, destino)
        with smtplib.SMTP(self.host, self.port, timeout=20) as s:
            s.starttls()
            s.login(self.user, self.password)
            s.send_message(msg)
        log.info("Correo enviado (smtp) a %s: %s", enmascarar_email(destino), msg["Subject"])
        return "sent"

    def _enviar_brevo(self, msg, destino):
        req = urllib.request.Request(
            self.brevo_url, method="POST",
            data=json.dumps(payload_brevo(msg)).encode("utf-8"),
            headers={"api-key": self.brevo_api_key, "content-type": "application/json",
                     "accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                respuesta = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detalle = e.read()[:300].decode("utf-8", "replace")
            raise ErrorEnvio(f"Brevo respondio HTTP {e.code}: {detalle}") from None
        except urllib.error.URLError as e:
            raise ErrorEnvio(f"No se pudo contactar a Brevo: {e.reason}") from None
        log.info("Correo enviado (brevo) a %s: %s (messageId %s)", enmascarar_email(destino),
                 msg["Subject"], respuesta.get("messageId", "?"))
        return "sent"


def get_db():
    global db
    if db is None or db.closed:
        db = psycopg.connect(config.DB_DSN, autocommit=True, row_factory=dict_row)
        log.info("Conectado a PostgreSQL")
    return db


def reset_db():
    global db
    if db is not None:
        try:
            db.close()
        except psycopg.Error:
            pass
    db = None


def migracion_ok(conn):
    row = conn.execute(
        "SELECT count(*) AS n FROM information_schema.tables "
        "WHERE table_name IN ('subscribers', 'notifications_sent')"
    ).fetchone()
    return row["n"] == 2


def procesar_confirmaciones(conn, mailer):
    """Una transaccion por suscriptor: si el envio falla, no queda un token sin enviar.

    Si falla, se cuenta el intento fuera de la transaccion y se aplica la misma espera
    creciente que a las alertas (1, 2, 4, 8 min; maximo MAX_INTENTOS). Tras el ultimo
    intento se deja de reintentar hasta que la persona vuelva a pedir la suscripcion
    (la API pone confirm_attempts a 0).
    """
    ahora = datetime.now(timezone.utc)
    filas = conn.execute(
        "SELECT id, confirm_attempts, confirm_last_attempt_at FROM subscribers "
        "WHERE confirm_requested_at IS NOT NULL AND confirmed_at IS NULL AND unsubscribed_at IS NULL "
        "AND (last_confirm_sent_at IS NULL OR last_confirm_sent_at < confirm_requested_at) "
        "AND confirm_attempts < %s ORDER BY confirm_requested_at LIMIT 20",
        (config.MAX_INTENTOS,),
    ).fetchall()
    ids = [f["id"] for f in filas
           if puede_intentar(f["confirm_attempts"], f["confirm_last_attempt_at"], ahora, config.MAX_INTENTOS)]
    for sid in ids:
        try:
            with conn.transaction():
                s = conn.execute(
                    "SELECT id, email, device_id, created_at, confirm_sent_count, last_confirm_sent_at "
                    "FROM subscribers WHERE id = %s AND confirmed_at IS NULL FOR UPDATE SKIP LOCKED",
                    (sid,),
                ).fetchone()
                if s is None:
                    continue
                ahora = datetime.now(timezone.utc)
                token = generar_token()
                baja = token_baja(config.SUBSCRIPTION_SECRET, s["id"], s["created_at"])
                conn.execute(
                    "UPDATE subscribers SET confirm_token_hash = %s, confirm_expires_at = %s, "
                    "unsubscribe_token_hash = %s, confirm_sent_count = %s, last_confirm_sent_at = %s, "
                    "confirm_attempts = 0, confirm_last_attempt_at = NULL WHERE id = %s",
                    (hash_token(token), ahora + VIGENCIA_CONFIRMACION, hash_token(baja),
                     nuevo_conteo_confirmaciones(s["confirm_sent_count"], s["last_confirm_sent_at"], ahora),
                     ahora, s["id"]),
                )
                links = enlaces(config.BASE_URL, s["device_id"], token_confirmacion=token, token_baja=baja)
                mailer.enviar(correo_confirmacion(config.EMAIL_FROM or "EcoAir AI <noreply@ecoair.local>",
                                                  s["email"], s["device_id"], links))
        except (smtplib.SMTPException, OSError, ErrorEnvio) as e:
            # La transaccion ya se deshizo (sin token guardado); se cuenta el intento aparte.
            row = conn.execute(
                "UPDATE subscribers SET confirm_attempts = confirm_attempts + 1, "
                "confirm_last_attempt_at = now() WHERE id = %s RETURNING confirm_attempts",
                (sid,),
            ).fetchone()
            log.error("No se pudo enviar la confirmación (suscriptor %s, intento %d de %d): %s",
                      sid, row["confirm_attempts"] if row else 0, config.MAX_INTENTOS, error_seguro(e))


SQL_ULTIMO_SIMULADO = (
    "SELECT is_simulated FROM measurements WHERE device_id = %(device_id)s "
    "ORDER BY received_at DESC LIMIT 1"
)
_aviso_007 = False


def bienvenida_disponible(conn):
    """True si existe la migracion 007 (columna welcome_sent_at)."""
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'subscribers' AND column_name = 'welcome_sent_at') AS ok"
    ).fetchone()["ok"]


def estado_estacion(conn, device_id, ahora):
    """(respuesta de calidad del aire como en el dashboard, es_simulada)."""
    params = {"device_id": device_id, "ahora": ahora}
    v1, v24 = ventanas_desde_fila(conn.execute(SQL_VENTANAS, params).fetchone())
    ultima = conn.execute(SQL_ULTIMA, params).fetchone()
    sim = conn.execute(SQL_ULTIMO_SIMULADO, params).fetchone()
    return construir_respuesta(device_id, v1, v24, ultima, ahora), bool(sim and sim["is_simulated"])


def prediccion_para_correo(r):
    """Resultado de shared.prediccion -> {'texto', 'aviso'} para el correo de bienvenida.

    Si ningun horizonte supero a la linea base (o faltan datos), se dice y se da la TENDENCIA de las
    ultimas 3 h, marcada como observacion; nunca la persistencia presentada como prediccion.
    """
    if r["estado"] == "sin_modelo":
        return None
    texto = r["texto"]
    if r["estado"] in ("no_supera_linea_base", "sin_datos") and r.get("tendencia"):
        texto = f"{texto}\n{r['tendencia']}"
    aviso = " ".join(x for x in (r.get("fuentes"), r.get("aviso")) if x)
    return {"texto": texto, "aviso": aviso or None}


def obtener_prediccion(conn, device_id):
    """Prediccion experimental para el correo, con los datos en vivo de la base."""
    ahora = datetime.now(timezone.utc)
    filas = conn.execute(SQL_SERIE_PM25, {"device_id": device_id, "ahora": ahora}).fetchall()
    serie = serie_por_minuto([(f["t"], f["pm25"]) for f in filas])
    return prediccion_para_correo(prediccion(cargar_modelo(config.MODELO_PREDICCION_PATH), serie, ahora))


def procesar_bienvenidas(conn, mailer):
    """Envia el correo de bienvenida UNA vez por suscriptor confirmado.

    Solo suscripciones confirmadas en las ultimas BIENVENIDA_VENTANA_H horas (las confirmadas
    antes de existir esta funcion no reciben un correo tardio). En DRY_RUN tambien se marca
    como enviada, para no repetirla en cada ciclo.
    """
    global _aviso_007
    if not bienvenida_disponible(conn):
        if not _aviso_007:
            log.warning("Falta db/migrations/007_welcome_email.sql: no se envian correos de bienvenida")
            _aviso_007 = True
        return 0
    ahora = datetime.now(timezone.utc)
    filas = conn.execute(
        "SELECT id, welcome_attempts, welcome_last_attempt_at FROM subscribers "
        "WHERE confirmed_at IS NOT NULL AND unsubscribed_at IS NULL AND welcome_sent_at IS NULL "
        "AND confirmed_at > now() - make_interval(hours => %s) AND welcome_attempts < %s "
        "ORDER BY confirmed_at LIMIT 20",
        (config.BIENVENIDA_VENTANA_H, config.MAX_INTENTOS),
    ).fetchall()
    ids = [f["id"] for f in filas
           if puede_intentar(f["welcome_attempts"], f["welcome_last_attempt_at"], ahora, config.MAX_INTENTOS)]
    enviados = 0
    for sid in ids:
        try:
            with conn.transaction():
                s = conn.execute(
                    "SELECT id, email, device_id, created_at FROM subscribers "
                    "WHERE id = %s AND welcome_sent_at IS NULL FOR UPDATE SKIP LOCKED",
                    (sid,),
                ).fetchone()
                if s is None:
                    continue  # ya enviada (u otro proceso la tiene): nada que hacer
                resp, simulado = estado_estacion(conn, s["device_id"], ahora)
                conn.execute(
                    "UPDATE subscribers SET welcome_sent_at = now(), welcome_attempts = 0, "
                    "welcome_last_attempt_at = NULL WHERE id = %s AND welcome_sent_at IS NULL",
                    (sid,),
                )
                baja = token_baja(config.SUBSCRIPTION_SECRET, s["id"], s["created_at"])
                links = enlaces(config.BASE_URL, s["device_id"], token_baja=baja)
                mailer.enviar(correo_bienvenida(
                    config.EMAIL_FROM or "EcoAir AI <noreply@ecoair.local>", s["email"], s["device_id"],
                    resp, links, prediccion=obtener_prediccion(conn, s["device_id"]), simulado=simulado))
                enviados += 1
        except (smtplib.SMTPException, OSError, ErrorEnvio) as e:
            # La transaccion se deshizo: welcome_sent_at sigue NULL. Se cuenta el intento aparte.
            row = conn.execute(
                "UPDATE subscribers SET welcome_attempts = welcome_attempts + 1, "
                "welcome_last_attempt_at = now() WHERE id = %s RETURNING welcome_attempts",
                (sid,),
            ).fetchone()
            log.error("No se pudo enviar la bienvenida (suscriptor %s, intento %d de %d): %s",
                      sid, row["welcome_attempts"] if row else 0, config.MAX_INTENTOS, error_seguro(e))
    return enviados


def encolar(conn):
    """Crea filas 'pending' en notifications_sent. ON CONFLICT DO NOTHING = una vez por episodio."""
    nuevas = 0
    tipos = [("open", "a.started_at IS NOT NULL")]
    if config.NOTIFY_ON_CLOSE:
        tipos.append(("close", "a.ended_at IS NOT NULL AND EXISTS (SELECT 1 FROM notifications_sent o "
                               "WHERE o.alert_id = a.id AND o.subscriber_id = s.id AND o.kind = 'open' "
                               "AND o.status IN ('sent', 'dry_run'))"))
    for kind, cond in tipos:
        filas = conn.execute(
            "SELECT a.id AS alert_id, a.severity, s.id AS subscriber_id, s.min_severity "
            "FROM alerts a JOIN subscribers s ON s.device_id = a.device_id "
            f"WHERE {cond} AND a.rule_code IS NOT NULL AND a.kind <> 'anomaly' "
            "AND a.started_at > now() - interval '24 hours' AND (%(sim)s OR NOT a.is_simulated) "
            "AND s.confirmed_at IS NOT NULL AND s.unsubscribed_at IS NULL "
            "AND s.confirmed_at <= a.started_at "
            "AND NOT EXISTS (SELECT 1 FROM notifications_sent n WHERE n.alert_id = a.id "
            "  AND n.subscriber_id = s.id AND n.kind = %(kind)s)",
            {"sim": config.INCLUIR_SIMULADOS, "kind": kind},
        ).fetchall()
        for f in filas:
            if not destinatarios(f["severity"], [f]):
                continue
            cur = conn.execute(
                "INSERT INTO notifications_sent (alert_id, subscriber_id, kind) VALUES (%s, %s, %s) "
                "ON CONFLICT (alert_id, subscriber_id, kind) DO NOTHING",
                (f["alert_id"], f["subscriber_id"], kind),
            )
            nuevas += cur.rowcount
    if config.ANOMALIAS_CORREO:
        nuevas += encolar_anomalias(conn)
    if nuevas:
        log.info("Notificaciones encoladas: %d", nuevas)


SQL_ANOMALIAS_A_ENCOLAR = (
    "SELECT a.id AS alert_id, a.device_id, s.id AS subscriber_id "
    "FROM alerts a JOIN subscribers s ON s.device_id = a.device_id "
    "WHERE a.kind = 'anomaly' AND a.started_at IS NOT NULL "
    "AND a.started_at > now() - interval '24 hours' AND (%(sim)s OR NOT a.is_simulated) "
    "AND s.confirmed_at IS NOT NULL AND s.unsubscribed_at IS NULL AND s.confirmed_at <= a.started_at "
    "AND NOT EXISTS (SELECT 1 FROM notifications_sent n WHERE n.alert_id = a.id "
    "  AND n.subscriber_id = s.id AND n.kind = 'open') "
    "AND NOT EXISTS (SELECT 1 FROM notifications_sent n2 JOIN alerts a2 ON a2.id = n2.alert_id "
    "  WHERE a2.device_id = a.device_id AND a2.kind = 'anomaly' AND n2.subscriber_id = s.id "
    "  AND n2.created_at > now() - make_interval(hours => %(cada_h)s)) "
    "ORDER BY a.started_at"
)


def encolar_anomalias(conn):
    """Anomalias EXPERIMENTALES (severidad info): solo con ANOMALIAS_CORREO=true y como maximo
    una por dispositivo y suscriptor cada ANOMALIAS_CORREO_CADA_H horas."""
    filas = conn.execute(SQL_ANOMALIAS_A_ENCOLAR,
                         {"sim": config.INCLUIR_SIMULADOS, "cada_h": config.ANOMALIAS_CORREO_CADA_H}).fetchall()
    nuevas = 0
    for f in limitar_anomalias(filas):
        cur = conn.execute(
            "INSERT INTO notifications_sent (alert_id, subscriber_id, kind) VALUES (%s, %s, 'open') "
            "ON CONFLICT (alert_id, subscriber_id, kind) DO NOTHING",
            (f["alert_id"], f["subscriber_id"]),
        )
        nuevas += cur.rowcount
    return nuevas


def enviar_pendientes(conn, mailer):
    ahora = datetime.now(timezone.utc)
    filas = conn.execute(
        "SELECT n.id, n.kind, n.attempts, n.last_attempt_at, "
        "a.device_id, a.kind AS alert_kind, a.rule_code, a.severity, a.message, a.started_at, a.ended_at, "
        "a.is_simulated, a.details, "
        "s.id AS sid, s.email, s.created_at AS s_created_at, s.unsubscribed_at "
        "FROM notifications_sent n JOIN alerts a ON a.id = n.alert_id "
        "JOIN subscribers s ON s.id = n.subscriber_id "
        "WHERE n.status IN ('pending', 'failed') AND n.attempts < %s ORDER BY n.created_at LIMIT 50",
        (config.MAX_INTENTOS,),
    ).fetchall()
    for f in filas:
        if not puede_intentar(f["attempts"], f["last_attempt_at"], ahora, config.MAX_INTENTOS):
            continue
        if f["unsubscribed_at"]:
            conn.execute("UPDATE notifications_sent SET status = 'skipped' WHERE id = %s", (f["id"],))
            continue
        baja = token_baja(config.SUBSCRIPTION_SECRET, f["sid"], f["s_created_at"])
        links = enlaces(config.BASE_URL, f["device_id"], token_baja=baja)
        alerta = {"device_id": f["device_id"], "kind": f["alert_kind"], "rule_code": f["rule_code"],
                  "severity": f["severity"], "message": f["message"],
                  "started_at": f["started_at"], "ended_at": f["ended_at"], "details": f["details"]}
        try:
            estado = mailer.enviar(correo_alerta(config.EMAIL_FROM or "EcoAir AI <noreply@ecoair.local>",
                                                 f["email"], alerta, f["kind"], links,
                                                 simulado=bool(f["is_simulated"])))
            conn.execute(
                "UPDATE notifications_sent SET status = %s, attempts = attempts + 1, last_attempt_at = now(), "
                "sent_at = now(), last_error = NULL WHERE id = %s",
                (estado, f["id"]),
            )
        except (smtplib.SMTPException, OSError, ErrorEnvio) as e:
            err = error_seguro(e)
            conn.execute(
                "UPDATE notifications_sent SET status = 'failed', attempts = attempts + 1, "
                "last_attempt_at = now(), last_error = %s WHERE id = %s",
                (err, f["id"]),
            )
            log.error("Fallo el envío %s (intento %d de %d): %s", f["id"], f["attempts"] + 1, config.MAX_INTENTOS, err)


def main():
    log.info("Notifier iniciado: proveedor=%s, confirmaciones cada %ss, alertas cada %ss, DRY_RUN=%s, cierre=%s",
             config.EMAIL_PROVIDER, config.INTERVALO_CONFIRMACIONES_S, config.INTERVALO_ALERTAS_S,
             config.DRY_RUN, config.NOTIFY_ON_CLOSE)
    mailer = Mailer(config.DRY_RUN, config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER, config.SMTP_PASSWORD,
                    proveedor=config.EMAIL_PROVIDER, brevo_api_key=config.BREVO_API_KEY,
                    brevo_url=config.BREVO_API_URL)
    proxima = {"confirmaciones": 0.0, "alertas": 0.0}
    while True:
        ahora = time.monotonic()
        faltan = config.faltantes()
        if faltan:
            log.error("Faltan variables de entorno: %s; no se envía nada", ", ".join(faltan))
            time.sleep(60)
            continue
        try:
            conn = get_db()
            if not migracion_ok(conn):
                log.error("Falta aplicar db/migrations/005_subscriptions.sql; reintento en 60 s")
                time.sleep(60)
                continue
            if ahora >= proxima["confirmaciones"]:
                procesar_confirmaciones(conn, mailer)
                procesar_bienvenidas(conn, mailer)
                proxima["confirmaciones"] = ahora + config.INTERVALO_CONFIRMACIONES_S
            if ahora >= proxima["alertas"]:
                encolar(conn)
                enviar_pendientes(conn, mailer)
                proxima["alertas"] = ahora + config.INTERVALO_ALERTAS_S
        except psycopg.Error as e:
            log.error("Error de base de datos: %s; reconecto", type(e).__name__)
            reset_db()
        except Exception as e:  # noqa: BLE001 - el servicio no debe morir
            log.error("Error inesperado: %s", error_seguro(e))
        time.sleep(1)


if __name__ == "__main__":
    main()
