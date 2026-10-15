"""Servicio notifier: correos de confirmacion y de alertas (cola en la base de datos).

- Cada INTERVALO_CONFIRMACIONES_S (10 s): envia los correos de confirmacion pendientes.
  Genera el token, guarda solo su SHA-256 y envia el enlace. La API nunca usa SMTP.
- Cada INTERVALO_ALERTAS_S (30 s): encola en notifications_sent una fila por (alerta,
  suscriptor, tipo) y envia las pendientes, con reintentos y espera creciente.
  La restriccion UNIQUE de la tabla garantiza un correo por episodio.
- DRY_RUN (por defecto activo): el correo completo se escribe en el log y no se envia.
  Fuera de DRY_RUN el log nunca lleva tokens ni correos completos (a***@gmail.com).
Requiere db/migrations/005_subscriptions.sql; si falta, lo registra y espera sin morirse.
"""

import logging
import smtplib
import time
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

import config
from correos import correo_alerta, correo_confirmacion, enlaces
from logica import destinatarios, error_seguro, puede_intentar
from subscriptions import (  # noqa: E402  (logica.py ya anadio api/ al path)
    VIGENCIA_CONFIRMACION,
    enmascarar_email,
    generar_token,
    hash_token,
    nuevo_conteo_confirmaciones,
    token_baja,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notifier")

db = None


class Mailer:
    """Envia por SMTP (STARTTLS) o, en DRY_RUN, escribe el correo completo en el log."""

    def __init__(self, dry_run, host, port, user, password):
        self.dry_run, self.host, self.port, self.user, self.password = dry_run, host, port, user, password

    def enviar(self, msg):
        destino = str(msg["To"])
        if self.dry_run:
            texto = msg.get_body(("plain",)).get_content()
            log.info("DRY_RUN: correo NO enviado\nPara: %s\nAsunto: %s\nList-Unsubscribe: %s\n%s",
                     destino, msg["Subject"], msg["List-Unsubscribe"], texto)
            return "dry_run"
        with smtplib.SMTP(self.host, self.port, timeout=20) as s:
            s.starttls()
            s.login(self.user, self.password)
            s.send_message(msg)
        log.info("Correo enviado a %s: %s", enmascarar_email(destino), msg["Subject"])
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
                mailer.enviar(correo_confirmacion(config.SMTP_FROM or "EcoAir AI <noreply@ecoair.local>",
                                                  s["email"], s["device_id"], links))
        except (smtplib.SMTPException, OSError) as e:
            # La transaccion ya se deshizo (sin token guardado); se cuenta el intento aparte.
            row = conn.execute(
                "UPDATE subscribers SET confirm_attempts = confirm_attempts + 1, "
                "confirm_last_attempt_at = now() WHERE id = %s RETURNING confirm_attempts",
                (sid,),
            ).fetchone()
            log.error("No se pudo enviar la confirmación (suscriptor %s, intento %d de %d): %s",
                      sid, row["confirm_attempts"] if row else 0, config.MAX_INTENTOS, error_seguro(e))


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
            f"WHERE {cond} AND a.rule_code IS NOT NULL "
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
    if nuevas:
        log.info("Notificaciones encoladas: %d", nuevas)


def enviar_pendientes(conn, mailer):
    ahora = datetime.now(timezone.utc)
    filas = conn.execute(
        "SELECT n.id, n.kind, n.attempts, n.last_attempt_at, "
        "a.device_id, a.kind AS alert_kind, a.rule_code, a.severity, a.message, a.started_at, a.ended_at, "
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
                  "started_at": f["started_at"], "ended_at": f["ended_at"]}
        try:
            estado = mailer.enviar(correo_alerta(config.SMTP_FROM or "EcoAir AI <noreply@ecoair.local>",
                                                 f["email"], alerta, f["kind"], links))
            conn.execute(
                "UPDATE notifications_sent SET status = %s, attempts = attempts + 1, last_attempt_at = now(), "
                "sent_at = now(), last_error = NULL WHERE id = %s",
                (estado, f["id"]),
            )
        except (smtplib.SMTPException, OSError) as e:
            err = error_seguro(e)
            conn.execute(
                "UPDATE notifications_sent SET status = 'failed', attempts = attempts + 1, "
                "last_attempt_at = now(), last_error = %s WHERE id = %s",
                (err, f["id"]),
            )
            log.error("Fallo el envío %s (intento %d de %d): %s", f["id"], f["attempts"] + 1, config.MAX_INTENTOS, err)


def main():
    log.info("Notifier iniciado: confirmaciones cada %ss, alertas cada %ss, DRY_RUN=%s, cierre=%s",
             config.INTERVALO_CONFIRMACIONES_S, config.INTERVALO_ALERTAS_S, config.DRY_RUN, config.NOTIFY_ON_CLOSE)
    mailer = Mailer(config.DRY_RUN, config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER, config.SMTP_PASSWORD)
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
