import importlib
import io
import json
import logging
import os
import sys
import urllib.error
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier  # noqa: E402
from correos import correo_alerta, correo_confirmacion, enlaces, payload_brevo  # noqa: E402

BASE = "https://ecoair.example"
EMAIL = "ana.perez@gmail.com"
CLAVE = "xkeysib-CLAVE-DE-PRUEBA"
INICIO = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
ALERTA = {"device_id": "ecoair-node-01", "kind": "device_down", "rule_code": "dispositivo_caido",
          "severity": "critica", "message": "Sin datos durante 6 min.", "started_at": INICIO, "ended_at": None}


def _correo():
    links = enlaces(BASE, "ecoair-node-01", token_baja="TOKENBAJA")
    return correo_alerta("EcoAir AI <alertas@ecoair.example>", EMAIL, ALERTA, "open", links)


# ---------- JSON para Brevo ----------

def test_payload_brevo_completo():
    p = payload_brevo(_correo())
    assert p["sender"] == {"name": "EcoAir AI", "email": "alertas@ecoair.example"}
    assert p["to"] == [{"email": EMAIL}]
    assert p["subject"] == "[EcoAir AI] Alerta crítica: Dispositivo caído · ecoair-node-01"
    assert "07:00 (hora de Colombia)" in p["textContent"] and "<html" in p["htmlContent"]
    assert p["headers"] == {"List-Unsubscribe": f"<{BASE}/subscriptions/unsubscribe?token=TOKENBAJA>",
                            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    json.dumps(p)  # serializable


def test_payload_brevo_remitente_sin_nombre():
    links = enlaces(BASE, "ecoair-node-01", token_confirmacion="C", token_baja="B")
    p = payload_brevo(correo_confirmacion("alertas@ecoair.example", EMAIL, "ecoair-node-01", links))
    assert p["sender"] == {"email": "alertas@ecoair.example"}


# ---------- Envio por la API HTTP ----------

class RespuestaFalsa(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _mailer():
    return notifier.Mailer(False, "h", 587, "u", "p", proveedor="brevo", brevo_api_key=CLAVE,
                           brevo_url="https://api.brevo.com/v3/smtp/email")


def test_envio_brevo_correcto_y_sin_clave_en_el_log(monkeypatch, caplog):
    peticiones = []

    def urlopen_falso(req, timeout=None):
        peticiones.append((req, timeout))
        return RespuestaFalsa(b'{"messageId": "<abc@smtp-relay.brevo.com>"}')

    monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen_falso)
    monkeypatch.setattr(notifier.smtplib, "SMTP", lambda *a, **k: pytest.fail("no debe usar SMTP"))
    with caplog.at_level(logging.INFO, logger="notifier"):
        assert _mailer().enviar(_correo()) == "sent"
    req, timeout = peticiones[0]
    assert req.full_url == "https://api.brevo.com/v3/smtp/email" and req.get_method() == "POST"
    assert req.get_header("Api-key") == CLAVE and req.get_header("Content-type") == "application/json"
    assert timeout == 20
    assert json.loads(req.data)["to"] == [{"email": EMAIL}]
    assert CLAVE not in caplog.text and EMAIL not in caplog.text and "a***@gmail.com" in caplog.text


def test_brevo_error_http_lanza_error_envio_sin_clave(monkeypatch):
    def urlopen_falso(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b'{"code":"unauthorized","message":"Key not found"}'))

    monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen_falso)
    with pytest.raises(notifier.ErrorEnvio) as exc:
        _mailer().enviar(_correo())
    assert "HTTP 401" in str(exc.value) and CLAVE not in str(exc.value)


def test_brevo_sin_red_lanza_error_envio(monkeypatch):
    def urlopen_falso(req, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(notifier.urllib.request, "urlopen", urlopen_falso)
    with pytest.raises(notifier.ErrorEnvio, match="No se pudo contactar a Brevo"):
        _mailer().enviar(_correo())


def test_brevo_en_dry_run_no_llama_a_la_api(monkeypatch):
    monkeypatch.setattr(notifier.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no debe llamar"))
    m = notifier.Mailer(True, "h", 587, "u", "p", proveedor="brevo", brevo_api_key=CLAVE)
    assert m.enviar(_correo()) == "dry_run"


def test_error_de_brevo_cuenta_el_intento_de_confirmacion(monkeypatch):
    """Un ErrorEnvio entra en la misma politica de reintentos que un fallo de SMTP."""
    from tests.test_notifier import ConexionFalsa

    class MailerBrevoQueFalla:
        def enviar(self, msg):
            raise notifier.ErrorEnvio("Brevo respondio HTTP 500: error")

    monkeypatch.setattr(notifier.config, "SUBSCRIPTION_SECRET", "secreto")
    monkeypatch.setattr(notifier.config, "BASE_URL", BASE)
    monkeypatch.setattr(notifier.config, "MAX_INTENTOS", 5)
    conn = ConexionFalsa([{"id": 1, "confirm_attempts": 0, "confirm_last_attempt_at": None}])
    notifier.procesar_confirmaciones(conn, MailerBrevoQueFalla())
    assert any("confirm_attempts = confirm_attempts + 1" in s for s in conn.confirmados)
    assert not any("confirm_token_hash = %s" in s for s in conn.confirmados)


# ---------- Configuracion por proveedor ----------

@pytest.fixture
def entorno(monkeypatch):
    for v in ("EMAIL_PROVIDER", "BREVO_API_KEY", "EMAIL_FROM", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("BASE_URL", BASE)
    monkeypatch.setenv("SUBSCRIPTION_SECRET", "secreto")
    monkeypatch.setenv("NOTIFIER_DRY_RUN", "false")
    import config
    yield lambda: importlib.reload(config)
    monkeypatch.undo()
    importlib.reload(config)


def test_proveedor_por_defecto_es_smtp(entorno):
    c = entorno()
    assert c.EMAIL_PROVIDER == "smtp"
    assert set(c.faltantes()) == {"SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM"}


def test_brevo_exige_clave_y_remitente(entorno, monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "brevo")
    assert set(entorno().faltantes()) == {"BREVO_API_KEY", "EMAIL_FROM"}
    monkeypatch.setenv("BREVO_API_KEY", CLAVE)
    monkeypatch.setenv("SMTP_FROM", "alertas@ecoair.example")   # SMTP_FROM sirve como alias
    assert entorno().faltantes() == []


def test_proveedor_invalido(entorno, monkeypatch):
    monkeypatch.setenv("EMAIL_PROVIDER", "sendgrid")
    assert "EMAIL_PROVIDER (smtp|brevo)" in entorno().faltantes()
