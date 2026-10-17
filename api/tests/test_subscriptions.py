import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("POSTGRES_PASSWORD", "solo-para-tests")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from subscriptions import (  # noqa: E402
    CONTACTO_NO_CONFIGURADO,
    MENSAJE_GENERICO,
    LimitadorVentana,
    decidir_registro,
    enmascarar_email,
    hash_token,
    ip_cliente,
    normalizar_email,
    nuevo_conteo_confirmaciones,
    puede_enviar_confirmacion,
    severidad_cumple,
    texto_consentimiento,
    token_baja,
)

AHORA = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


# ---------- Logica pura ----------

@pytest.mark.parametrize("entrada, esperado", [
    ("Ana.Perez@Gmail.com", "ana.perez@gmail.com"),
    ("  a+alertas@uni.edu.co ", "a+alertas@uni.edu.co"),
    ("sin-arroba.com", None),
    ("a@b", None),
    ("a..b@gmail.com", None),
    (".a@gmail.com", None),
    ("a@-gmail.com", None),
    ("a b@gmail.com", None),
    ("x" * 65 + "@gmail.com", None),
    ("a@" + "b" * 250 + ".com", None),
    (None, None),
])
def test_normalizar_email(entrada, esperado):
    assert normalizar_email(entrada) == esperado


def test_enmascarar_email():
    assert enmascarar_email("ana.perez@gmail.com") == "a***@gmail.com"
    assert enmascarar_email("") == "***"


def test_hash_y_token_de_baja():
    assert hash_token("abc") == hash_token("abc") != hash_token("abd")
    creado = datetime(2026, 10, 1, tzinfo=timezone.utc)
    t1 = token_baja("secreto", 7, creado)
    assert t1 == token_baja("secreto", 7, creado)          # deterministico
    assert t1 != token_baja("secreto", 8, creado)          # depende del suscriptor
    assert t1 != token_baja("otro", 7, creado)             # depende del secreto
    with pytest.raises(ValueError):
        token_baja("", 7, creado)


def test_limitador_ventana():
    t = [0.0]
    lim = LimitadorVentana(2, 600, reloj=lambda: t[0])
    assert lim.permitir("ip") and lim.permitir("ip")
    assert not lim.permitir("ip")
    assert lim.permitir("otra-ip")
    t[0] = 600.0
    assert lim.permitir("ip")  # la ventana ya paso


def test_ip_cliente_sin_proxy_ignora_cabeceras():
    cab = {"cf-connecting-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"}
    assert ip_cliente("10.0.0.9", cab, confiar_proxy=False) == "10.0.0.9"


def test_ip_cliente_con_proxy_usa_cloudflare_y_luego_xff():
    assert ip_cliente("10.0.0.9", {"cf-connecting-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"}, True) == "1.2.3.4"
    assert ip_cliente("10.0.0.9", {"x-forwarded-for": "5.6.7.8, 172.16.0.1"}, True) == "5.6.7.8"
    assert ip_cliente("10.0.0.9", {"x-forwarded-for": "basura"}, True) == "10.0.0.9"
    assert ip_cliente("10.0.0.9", {}, True) == "10.0.0.9"


def test_limite_de_confirmaciones_por_correo():
    hace = lambda h: AHORA - timedelta(hours=h)  # noqa: E731
    assert puede_enviar_confirmacion([], AHORA)
    assert puede_enviar_confirmacion([(2, hace(1))], AHORA)
    assert not puede_enviar_confirmacion([(3, hace(1))], AHORA)
    assert not puede_enviar_confirmacion([(2, hace(1)), (1, hace(2))], AHORA)  # suma de dispositivos
    assert puede_enviar_confirmacion([(3, hace(25))], AHORA)                   # ya pasaron 24 h
    assert nuevo_conteo_confirmaciones(2, hace(1), AHORA) == 3
    assert nuevo_conteo_confirmaciones(3, hace(25), AHORA) == 1


def test_decidir_registro():
    activa = {"confirmed_at": AHORA, "unsubscribed_at": None}
    baja = {"confirmed_at": AHORA, "unsubscribed_at": AHORA}
    pendiente = {"confirmed_at": None, "unsubscribed_at": None}
    assert decidir_registro(None, [], AHORA) == "insertar"
    assert decidir_registro(activa, [], AHORA) == "nada"
    assert decidir_registro(baja, [], AHORA) == "reactivar"
    assert decidir_registro(pendiente, [], AHORA) == "reactivar"
    assert decidir_registro(pendiente, [(3, AHORA - timedelta(hours=1))], AHORA) == "nada"


def test_severidad_cumple():
    assert severidad_cumple("aviso", "aviso") and severidad_cumple("aviso", "critica")
    assert not severidad_cumple("critica", "aviso") and severidad_cumple("critica", "critica")
    assert not severidad_cumple("aviso", "info") and not severidad_cumple("aviso", None)


def test_consentimiento_con_y_sin_contacto():
    sin = texto_consentimiento(None)
    assert sin["contacto_configurado"] is False and sin["contacto"] == CONTACTO_NO_CONFIGURADO
    assert CONTACTO_NO_CONFIGURADO in sin["texto"]
    assert texto_consentimiento("   ")["contacto_configurado"] is False
    con = texto_consentimiento("responsable@uni.edu.co")
    assert con["contacto_configurado"] is True and "responsable@uni.edu.co" in con["texto"]
    for frase in ("José Mario Fragua Rengifo", "proyecto académico", "darme de baja", "no se comparten", "Ley 1581"):
        assert frase in con["texto"]


# ---------- Endpoints (sin base de datos: se sustituyen las funciones que la usan) ----------

@pytest.fixture
def client(monkeypatch):
    main.limitador_ip.reiniciar()
    llamadas = []

    async def device_ok(device_id):
        if device_id != "ecoair-node-01":
            raise main.HTTPException(status_code=404, detail="no existe")

    async def registrar_falso(email, device_id, min_severity, ahora):
        llamadas.append((email, device_id, min_severity))
        return "nada" if email == "ya@existe.com" else "insertar"

    monkeypatch.setattr(main, "require_device", device_ok)
    monkeypatch.setattr(main, "_registrar", registrar_falso)
    c = TestClient(main.app)
    c.llamadas = llamadas
    return c


def _post(c, email="nuevo@gmail.com", acepta=True, headers=None, **extra):
    return c.post("/subscriptions", json={"email": email, "device_id": "ecoair-node-01",
                                          "accepted_terms": acepta, **extra}, headers=headers or {})


def test_respuesta_identica_para_correo_nuevo_y_existente(client):
    r1, r2 = _post(client, "nuevo@gmail.com"), _post(client, "ya@existe.com")
    assert r1.status_code == r2.status_code == 202
    assert r1.json() == r2.json() == {"mensaje": MENSAJE_GENERICO}
    assert client.llamadas[0] == ("nuevo@gmail.com", "ecoair-node-01", "aviso")


def test_sin_consentimiento_422_y_no_registra(client):
    r = _post(client, acepta=False)
    assert r.status_code == 422 and client.llamadas == []


def test_correo_invalido_422(client):
    assert _post(client, "no-es-correo").status_code == 422


def test_severidad_invalida_422(client):
    assert _post(client, min_severity="info").status_code == 422


def test_dispositivo_inexistente_404(client):
    r = client.post("/subscriptions", json={"email": "a@gmail.com", "device_id": "otro", "accepted_terms": True})
    assert r.status_code == 404


def test_limite_por_ip_sin_proxy(client, monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    for i in range(20):
        assert _post(client, f"u{i}@gmail.com").status_code == 202
    # Sin TRUST_PROXY_HEADERS la cabecera no cambia la IP: sigue limitado
    r = _post(client, "u21@gmail.com", headers={"CF-Connecting-IP": "9.9.9.9"})
    assert r.status_code == 429 and r.json()["detail"]


def test_limite_por_ip_detras_de_proxy(client, monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    for i in range(20):
        assert _post(client, f"a{i}@gmail.com", headers={"CF-Connecting-IP": "1.1.1.1"}).status_code == 202
    assert _post(client, "a21@gmail.com", headers={"CF-Connecting-IP": "1.1.1.1"}).status_code == 429
    # Otro cliente detras del mismo proxy (otra IP real) no queda bloqueado
    assert _post(client, "b@gmail.com", headers={"X-Forwarded-For": "2.2.2.2, 10.0.0.1"}).status_code == 202


def test_consent_endpoint_con_y_sin_contacto(client, monkeypatch):
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    r = client.get("/subscriptions/consent").json()
    assert r["contacto_configurado"] is False and r["contacto"] == CONTACTO_NO_CONFIGURADO
    monkeypatch.setenv("CONTACT_EMAIL", "responsable@uni.edu.co")
    assert client.get("/subscriptions/consent").json()["contacto"] == "responsable@uni.edu.co"


def test_get_de_baja_no_da_de_baja_y_post_si(client, monkeypatch):
    consultas = []

    async def query_falsa(sql, params=()):
        consultas.append(sql)
        return [{"id": 1}]

    monkeypatch.setattr(main, "query", query_falsa)
    r = client.get("/subscriptions/unsubscribe?token=abc")
    assert r.status_code == 200 and 'method="post"' in r.text and consultas == []
    r = client.post("/subscriptions/unsubscribe?token=abc")
    assert r.status_code == 200 and len(consultas) == 1 and "unsubscribed_at = now()" in consultas[0]


def test_confirmar_con_token_vacio_no_consulta(client, monkeypatch):
    async def query_falsa(sql, params=()):
        raise AssertionError("no deberia consultar la base")

    monkeypatch.setattr(main, "query", query_falsa)
    r = client.get("/subscriptions/confirm?token=")
    assert r.status_code == 200 and "no es válido" in r.text


# ---------- /devices con marca de simulacion (paso 2) ----------

def test_devices_indica_si_es_simulado(monkeypatch):
    async def query_falsa(sql, params=()):
        assert sql is main.SQL_DEVICES
        return [{"device_id": "ecoair-node-01", "name": "Nodo 01", "location": None, "created_at": AHORA, "is_simulated": False},
                {"device_id": "ecoair-sim-01", "name": "Estación simulada", "location": "Simulado", "created_at": AHORA, "is_simulated": True}]

    monkeypatch.setattr(main, "query", query_falsa)
    r = TestClient(main.app).get("/devices").json()
    assert [(d["device_id"], d["is_simulated"]) for d in r] == [("ecoair-node-01", False), ("ecoair-sim-01", True)]


def test_lecturas_ignoran_filas_futuras():
    import inspect
    fuente = inspect.getsource(main.latest_measurement) + inspect.getsource(main.list_measurements) + main.SQL_DEVICES
    assert fuente.count("received_at <= now()") == 3
