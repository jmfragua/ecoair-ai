import logging
import os
import sys

import paho.mqtt.client as mqtt
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ingestor  # noqa: E402


# ---------- Validacion del mensaje (contrato MQTT v0.1) ----------

def _msg(**extra):
    return {"deviceId": "ecoair-node-01", "temperature": 22.5, "humidity": 60, "pm25": 10, "pm10": 20, **extra}


def test_mensaje_valido():
    assert ingestor.validate("ecoair-node-01", _msg()) == (
        "ecoair-node-01", {"temperature": 22.5, "humidity": 60, "pm25": 10, "pm10": 20})


@pytest.mark.parametrize("data", [
    _msg(deviceId="otro"), _msg(pm25=1001), _msg(humidity=True), _msg(temperature="22"), [],
])
def test_mensaje_invalido(data):
    with pytest.raises(ValueError):
        ingestor.validate("ecoair-node-01", data)


# ---------- Conexion al broker ----------

@pytest.mark.parametrize("rc", [4, 5])
def test_credenciales_rechazadas_con_codigos_reales_de_paho(rc):
    code = mqtt.convert_connack_rc_to_reason_code(rc)   # 4 -> 134, 5 -> 135
    ok, msg = ingestor.resultado_conexion(code.value, code.is_failure, "ingestor")
    assert not ok
    assert "rechazo las credenciales" in msg and "usuario 'ingestor'" in msg
    assert "MQTT_USER y MQTT_PASSWORD" in msg


def test_rechazo_sin_usuario_lo_dice():
    ok, msg = ingestor.resultado_conexion(135, True, None)
    assert not ok and "sin usuario (anonimo)" in msg


def test_conexion_aceptada():
    ok, msg = ingestor.resultado_conexion(0, False, "ingestor")
    assert ok and msg.startswith("Conectado al broker MQTT")


class ClienteFalso:
    def __init__(self):
        self.llamadas = []

    def username_pw_set(self, u, p): self.llamadas.append(("auth", u, p))
    def tls_set(self, ca_certs=None): self.llamadas.append(("tls", ca_certs))
    def reconnect_delay_set(self, min_delay, max_delay): self.llamadas.append(("delay", min_delay, max_delay))
    def subscribe(self, topic, qos=0): self.llamadas.append(("subscribe", topic))


def test_configurar_cliente_con_y_sin_credenciales():
    cfg = {"host": "h", "port": 1883, "user": "ingestor", "password": "k", "tls": True, "ca_cert": "/ca.pem"}
    c = ingestor.configurar_cliente(ClienteFalso(), cfg)
    assert ("auth", "ingestor", "k") in c.llamadas and ("tls", "/ca.pem") in c.llamadas
    c = ingestor.configurar_cliente(ClienteFalso(), {**cfg, "user": None, "password": None, "tls": False})
    assert not any(x[0] in ("auth", "tls") for x in c.llamadas)


def test_on_connect_rechazado_no_suscribe_ni_registra_la_clave(monkeypatch, caplog):
    monkeypatch.setitem(ingestor.MQTT, "user", "ingestor")
    monkeypatch.setitem(ingestor.MQTT, "password", "CLAVE-SECRETA")
    c = ClienteFalso()
    with caplog.at_level(logging.INFO, logger="ingestor"):
        ingestor.on_connect(c, None, None, mqtt.convert_connack_rc_to_reason_code(5), None)
    assert ("subscribe", ingestor.TOPIC) not in c.llamadas
    assert "rechazo las credenciales" in caplog.text and "CLAVE-SECRETA" not in caplog.text


def test_on_connect_aceptado_suscribe():
    c = ClienteFalso()
    ingestor.on_connect(c, None, None, mqtt.convert_connack_rc_to_reason_code(0), None)
    assert ("subscribe", "ecoair/+/telemetry") in c.llamadas
