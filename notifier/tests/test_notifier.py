import logging
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier  # noqa: E402
from correos import ADVERTENCIA, correo_alerta, correo_confirmacion, duracion, enlaces, hora_colombia  # noqa: E402
from logica import destinatarios, error_seguro, listo_para_reintento  # noqa: E402

UTC = timezone.utc
INICIO = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)  # 07:00 en Colombia
BASE = "https://ecoair.example"
EMAIL = "ana.perez@gmail.com"
TOKEN_BAJA = "TOKENBAJA123"


def _alerta(**extra):
    return {"device_id": "ecoair-node-01", "kind": "device_down", "rule_code": "dispositivo_caido",
            "severity": "critica", "message": "Sin datos durante 6 min. <script>x</script>",
            "started_at": INICIO, "ended_at": None, **extra}


def _texto(msg, tipo="plain"):
    return msg.get_body((tipo,)).get_content()


# ---------- Correos ----------

def test_hora_colombia_y_duracion():
    assert hora_colombia(INICIO) == "15 oct 2026, 07:00 (hora de Colombia)"
    assert duracion(INICIO, INICIO + timedelta(minutes=10)) == "10 min"
    assert duracion(INICIO, INICIO + timedelta(minutes=135)) == "2 h 15 min"


def test_correo_de_alerta_abierta():
    links = enlaces(BASE, "ecoair-node-01", token_baja=TOKEN_BAJA)
    msg = correo_alerta("EcoAir <a@b.co>", EMAIL, _alerta(), "open", links)
    texto, cuerpo = _texto(msg), _texto(msg, "html")
    assert msg["Subject"] == "[EcoAir AI] Alerta crítica: Dispositivo caído · ecoair-node-01"
    for esperado in ("Nivel: Crítica", "Regla: Dispositivo caído", "07:00 (hora de Colombia)",
                     f"{BASE}/panel?device=ecoair-node-01", f"{BASE}/subscriptions/unsubscribe?token={TOKEN_BAJA}",
                     ADVERTENCIA):
        assert esperado in texto
    assert msg["List-Unsubscribe"] == f"<{BASE}/subscriptions/unsubscribe?token={TOKEN_BAJA}>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "<script>" not in cuerpo and "&lt;script&gt;" in cuerpo  # el mensaje se escapa en HTML


def test_correo_de_alerta_cerrada_con_duracion():
    links = enlaces(BASE, "ecoair-node-01", token_baja=TOKEN_BAJA)
    msg = correo_alerta("x@y.co", EMAIL, _alerta(ended_at=INICIO + timedelta(minutes=10)), "close", links)
    assert msg["Subject"].startswith("[EcoAir AI] Alerta cerrada")
    assert "Duración: 10 min" in _texto(msg)


def test_correo_de_confirmacion():
    links = enlaces(BASE, "ecoair-node-01", token_confirmacion="CONF", token_baja=TOKEN_BAJA)
    msg = correo_confirmacion("x@y.co", EMAIL, "ecoair-node-01", links)
    texto = _texto(msg)
    assert f"{BASE}/subscriptions/confirm?token=CONF" in texto and "24 h" in texto
    assert ADVERTENCIA in texto and "List-Unsubscribe" in msg


# ---------- Logica ----------

def test_reintentos_con_espera_creciente():
    assert listo_para_reintento(0, None, INICIO)
    assert not listo_para_reintento(1, INICIO, INICIO + timedelta(seconds=59))
    assert listo_para_reintento(1, INICIO, INICIO + timedelta(minutes=1))
    assert not listo_para_reintento(3, INICIO, INICIO + timedelta(minutes=3))
    assert listo_para_reintento(3, INICIO, INICIO + timedelta(minutes=4))


def test_destinatarios_por_severidad():
    subs = [{"id": 1, "min_severity": "aviso"}, {"id": 2, "min_severity": "critica"}]
    assert [s["id"] for s in destinatarios("aviso", subs)] == [1]
    assert [s["id"] for s in destinatarios("critica", subs)] == [1, 2]
    assert destinatarios("info", subs) == []


def test_error_seguro_enmascara_correos():
    e = smtplib.SMTPRecipientsRefused({"ana.perez@gmail.com": (550, b"no")})
    texto = error_seguro(e)
    assert "ana.perez@gmail.com" not in texto and "a***@gmail.com" in texto


# ---------- Mailer y logs ----------

def test_dry_run_no_usa_smtp_y_escribe_el_correo(monkeypatch, caplog):
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: pytest.fail("no debe conectar a SMTP"))
    links = enlaces(BASE, "ecoair-node-01", token_baja=TOKEN_BAJA)
    m = notifier.Mailer(True, "h", 587, "u", "p")
    with caplog.at_level(logging.INFO, logger="notifier"):
        assert m.enviar(correo_alerta("x@y.co", EMAIL, _alerta(), "open", links)) == "dry_run"
    assert EMAIL in caplog.text and "DRY_RUN" in caplog.text  # en DRY_RUN si se ve completo


def test_envio_real_no_escribe_correo_ni_tokens_en_el_log(monkeypatch, caplog):
    enviados = []

    class SMTPFalso:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, p): pass
        def send_message(self, msg): enviados.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", SMTPFalso)
    links = enlaces(BASE, "ecoair-node-01", token_confirmacion="CONFSECRETO", token_baja=TOKEN_BAJA)
    m = notifier.Mailer(False, "h", 587, "u", "p")
    with caplog.at_level(logging.INFO, logger="notifier"):
        assert m.enviar(correo_confirmacion("x@y.co", EMAIL, "ecoair-node-01", links)) == "sent"
    assert len(enviados) == 1
    assert EMAIL not in caplog.text and "a***@gmail.com" in caplog.text
    assert "CONFSECRETO" not in caplog.text and TOKEN_BAJA not in caplog.text


def test_dry_run_activo_por_defecto(monkeypatch):
    import importlib

    import config
    monkeypatch.delenv("NOTIFIER_DRY_RUN", raising=False)
    assert importlib.reload(config).DRY_RUN is True
    monkeypatch.setenv("NOTIFIER_DRY_RUN", "false")
    assert importlib.reload(config).DRY_RUN is False
    assert set(importlib.reload(config).faltantes()) >= {"SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"}


# ---------- Espera creciente de la confirmacion ----------

from contextlib import contextmanager  # noqa: E402

from logica import puede_intentar  # noqa: E402


def test_confirmacion_espera_creciente_y_maximo():
    t = INICIO
    assert puede_intentar(0, None, t, 5)                                   # primer intento
    assert not puede_intentar(1, t, t + timedelta(seconds=30), 5)          # tras 1 fallo: 1 min
    assert puede_intentar(1, t, t + timedelta(minutes=1), 5)
    assert not puede_intentar(2, t, t + timedelta(minutes=1), 5)           # tras 2 fallos: 2 min
    assert puede_intentar(4, t, t + timedelta(minutes=8), 5)               # tras 4 fallos: 8 min
    assert not puede_intentar(5, t, t + timedelta(days=1), 5)              # 5 intentos: se para


class _Res:
    def __init__(self, filas):
        self.filas = filas

    def fetchall(self):
        return self.filas

    def fetchone(self):
        return self.filas[0] if self.filas else None


class ConexionFalsa:
    """Simula psycopg: registra el SQL y deshace lo hecho dentro de una transaccion que falla."""

    def __init__(self, candidatos):
        self.candidatos = candidatos
        self.confirmados = []   # SQL que quedo aplicado
        self._tx = None

    @contextmanager
    def transaction(self):
        self._tx = []
        try:
            yield
            self.confirmados += self._tx
        finally:
            self._tx = None   # si hubo excepcion, lo de la transaccion se descarta

    def execute(self, sql, params=()):
        (self._tx if self._tx is not None else self.confirmados).append(sql)
        if sql.startswith("SELECT id, confirm_attempts"):
            return _Res(self.candidatos)
        if sql.startswith("SELECT id, email"):
            return _Res([{"id": 1, "email": EMAIL, "device_id": "ecoair-node-01", "created_at": INICIO,
                          "confirm_sent_count": 0, "last_confirm_sent_at": None}])
        if "RETURNING confirm_attempts" in sql:
            return _Res([{"confirm_attempts": 1}])
        return _Res([])


class MailerQueFalla:
    def enviar(self, msg):
        raise smtplib.SMTPServerDisconnected("sin conexion")


class MailerOk:
    def __init__(self):
        self.enviados = []

    def enviar(self, msg):
        self.enviados.append(msg)
        return "dry_run"


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setattr(notifier.config, "SUBSCRIPTION_SECRET", "secreto")
    monkeypatch.setattr(notifier.config, "BASE_URL", BASE)
    monkeypatch.setattr(notifier.config, "MAX_INTENTOS", 5)


def test_confirmacion_fallida_cuenta_el_intento_y_no_guarda_token(cfg):
    conn = ConexionFalsa([{"id": 1, "confirm_attempts": 0, "confirm_last_attempt_at": None}])
    notifier.procesar_confirmaciones(conn, MailerQueFalla())
    assert not any("confirm_token_hash = %s" in s for s in conn.confirmados)       # se deshizo
    assert any("confirm_attempts = confirm_attempts + 1" in s for s in conn.confirmados)


def test_confirmacion_exitosa_guarda_token_y_reinicia_intentos(cfg):
    conn = ConexionFalsa([{"id": 1, "confirm_attempts": 2, "confirm_last_attempt_at": None}])
    mailer = MailerOk()
    notifier.procesar_confirmaciones(conn, mailer)
    assert len(mailer.enviados) == 1
    guardado = [s for s in conn.confirmados if "confirm_token_hash = %s" in s]
    assert guardado and "confirm_attempts = 0" in guardado[0]


def test_confirmacion_en_espera_no_se_intenta(cfg):
    reciente = datetime.now(UTC) - timedelta(seconds=30)   # tras 1 fallo hay que esperar 1 min
    conn = ConexionFalsa([{"id": 1, "confirm_attempts": 1, "confirm_last_attempt_at": reciente}])
    mailer = MailerOk()
    notifier.procesar_confirmaciones(conn, mailer)
    assert mailer.enviados == []


# ---------- Arranque en DRY_RUN sin SMTP ----------

def test_dry_run_arranca_sin_variables_smtp(monkeypatch):
    import importlib

    import config
    for v in ("SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.delenv("NOTIFIER_DRY_RUN", raising=False)
    monkeypatch.setenv("BASE_URL", BASE)
    monkeypatch.setenv("SUBSCRIPTION_SECRET", "secreto")
    assert importlib.reload(config).faltantes() == []
    # BASE_URL y SUBSCRIPTION_SECRET si son obligatorios incluso en DRY_RUN (enlaces y token de baja)
    monkeypatch.delenv("BASE_URL")
    monkeypatch.delenv("SUBSCRIPTION_SECRET")
    assert set(importlib.reload(config).faltantes()) == {"BASE_URL", "SUBSCRIPTION_SECRET"}


# ---------- Alertas sobre datos simulados ----------

def test_correo_de_alerta_simulada_lo_dice_en_asunto_y_cuerpo():
    from correos import AVISO_SIMULACION
    links = enlaces(BASE, "ecoair-sim-01", token_baja=TOKEN_BAJA)
    msg = correo_alerta("x@y.co", EMAIL, _alerta(device_id="ecoair-sim-01"), "open", links, simulado=True)
    assert msg["Subject"] == "[SIMULACIÓN] [EcoAir AI] Alerta crítica: Dispositivo caído · ecoair-sim-01"
    assert _texto(msg).startswith(AVISO_SIMULACION) and "SIMULACIÓN" in _texto(msg, "html")
    real = correo_alerta("x@y.co", EMAIL, _alerta(), "open", links)
    assert "SIMULACIÓN" not in real["Subject"] and "SIMULACIÓN" not in _texto(real)


# ---------- Anomalias (paso 4) ----------

from logica import limitar_anomalias  # noqa: E402


def test_correo_de_anomalia_dice_que_es_experimental():
    links = enlaces(BASE, "ecoair-sim-01", token_baja=TOKEN_BAJA)
    a = _alerta(device_id="ecoair-sim-01", kind="anomaly", rule_code="isolation_forest", severity="info",
                message="[SIMULACIÓN] Lectura inusual según el modelo...")
    msg = correo_alerta("x@y.co", EMAIL, a, "open", links, simulado=True)
    assert msg["Subject"] == "[SIMULACIÓN] [EcoAir AI] Anomalía experimental (modelo) · ecoair-sim-01"
    t = _texto(msg)
    assert "Detección EXPERIMENTAL de un modelo (Isolation Forest), no es una regla de la norma" in t
    assert "Regla: Anomalía (modelo, experimental)" in t and "Puede haber falsas alarmas" in t


def test_limitar_anomalias_una_por_dispositivo_y_suscriptor():
    filas = [{"alert_id": 1, "device_id": "sim", "subscriber_id": 7},
             {"alert_id": 2, "device_id": "sim", "subscriber_id": 7},
             {"alert_id": 2, "device_id": "sim", "subscriber_id": 8}]
    assert [(f["alert_id"], f["subscriber_id"]) for f in limitar_anomalias(filas)] == [(1, 7), (2, 8)]


def test_sql_de_anomalias_limita_a_6_horas_y_las_reglas_las_excluyen():
    assert "make_interval(hours => %(cada_h)s)" in notifier.SQL_ANOMALIAS_A_ENCOLAR
    import inspect
    assert "a.kind <> 'anomaly'" in inspect.getsource(notifier.encolar)


def test_correo_de_anomalias_apagado_por_defecto(monkeypatch):
    import importlib

    import config
    monkeypatch.delenv("ANOMALIAS_CORREO", raising=False)
    assert importlib.reload(config).ANOMALIAS_CORREO is False
    assert importlib.reload(config).ANOMALIAS_CORREO_CADA_H == 6
