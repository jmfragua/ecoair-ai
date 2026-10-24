import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.settings import (descripcion_db, destino_importacion, dsn_desde_entorno,  # noqa: E402
                             error_conexion_seguro, mqtt_desde_entorno, origen_db)


# ---------- Base de datos ----------

def test_dsn_por_defecto_igual_que_antes():
    dsn = dsn_desde_entorno({"POSTGRES_PASSWORD": "clave"})
    assert dsn == "host='localhost' port='5433' dbname='ecoair' user='ecoair' password='clave'"


def test_dsn_con_variables_del_compose():
    dsn = dsn_desde_entorno({"DB_HOST": "postgres", "DB_PORT": "5432", "POSTGRES_PASSWORD": "x"},
                            connect_timeout=3)
    assert "host='postgres'" in dsn and "port='5432'" in dsn and dsn.endswith("connect_timeout='3'")


def test_dsn_escapa_claves_con_espacios_y_comillas():
    dsn = dsn_desde_entorno({"POSTGRES_PASSWORD": "a b'c\\d"})
    assert "password='a b\\'c\\\\d'" in dsn


def test_database_url_tiene_prioridad():
    url = "postgresql://u:p@db.internal:5432/railway"
    env = {"DATABASE_URL": url, "DB_HOST": "postgres", "POSTGRES_PASSWORD": "otra"}
    assert dsn_desde_entorno(env) == url
    assert dsn_desde_entorno(env, connect_timeout=5) == url + "?connect_timeout=5"
    assert dsn_desde_entorno({"DATABASE_URL": url + "?sslmode=require"}, 5) == url + "?sslmode=require&connect_timeout=5"
    assert dsn_desde_entorno({"DATABASE_URL": url + "?connect_timeout=9"}, 5) == url + "?connect_timeout=9"


def test_descripcion_db_no_lleva_credenciales():
    assert descripcion_db({"DATABASE_URL": "postgresql://u:secreta@db.internal:5432/railway?sslmode=require"}) \
        == "db.internal:5432/railway"
    assert descripcion_db({"DB_HOST": "postgres", "DB_PORT": "5432"}) == "postgres:5432/ecoair"


# ---------- MQTT ----------

def test_mqtt_por_defecto_sin_credenciales():
    m = mqtt_desde_entorno({})
    assert m == {"host": "localhost", "port": 1883, "user": None, "password": None, "tls": False, "ca_cert": None}


def test_mqtt_con_credenciales_y_tls():
    m = mqtt_desde_entorno({"MQTT_HOST": "mosquitto", "MQTT_PORT": "12345", "MQTT_USER": "ingestor",
                            "MQTT_PASSWORD": "k", "MQTT_TLS": "true", "MQTT_CA_CERT": "/ca.pem"})
    assert m == {"host": "mosquitto", "port": 12345, "user": "ingestor", "password": "k",
                 "tls": True, "ca_cert": "/ca.pem"}


@pytest.mark.parametrize("env", [
    {"MQTT_USER": "solo-usuario"},
    {"MQTT_PASSWORD": "solo-clave"},
    {"MQTT_PORT": "abc"},
    {"MQTT_PORT": "70000"},
])
def test_mqtt_configuracion_invalida(env):
    with pytest.raises(ValueError):
        mqtt_desde_entorno(env)


# ---------- Errores de conexion sin secretos ----------

URL = "postgresql://postgres:Ab%40cd123@shinkansen.proxy.rlwy.net:41234/railway"


class ErrorPG(Exception):
    sqlstate = "28P01"


def test_error_conexion_muestra_codigo_y_mensaje_sin_url_ni_clave():
    e = ErrorPG('connection failed: connection to server at "shinkansen.proxy.rlwy.net" (66.33.22.1), '
                'port 41234 failed: FATAL:  password authentication failed for user "postgres"\n'
                f"dsn={URL} password=Ab@cd123 x Ab%40cd123")
    txt = error_conexion_seguro(e, {"DATABASE_URL": URL})
    assert txt.startswith("ErrorPG [28P01]: connection failed:")
    assert "password authentication failed" in txt and "shinkansen.proxy.rlwy.net" in txt and "port 41234" in txt
    for secreto in ("Ab@cd123", "Ab%40cd123", "postgresql://", URL):
        assert secreto not in txt
    assert "\n" not in txt


def test_error_conexion_oculta_postgres_password_y_urls_ajenas():
    e = RuntimeError("fallo con clave s3cr3t y postgres://otro:zz@h/db password='a b'")
    txt = error_conexion_seguro(e, {"POSTGRES_PASSWORD": "s3cr3t"})
    assert "s3cr3t" not in txt and "zz@h" not in txt and "'a b'" not in txt
    assert txt.startswith("RuntimeError: fallo con clave ***")
    assert error_conexion_seguro(ValueError(""), {}) == "ValueError"


def test_origen_db():
    assert origen_db({"DATABASE_URL": URL}) == "DATABASE_URL"
    assert origen_db({"DATABASE_URL": "  "}) == "DB_*" and origen_db({}) == "DB_*"


def test_destino_importacion():
    dsn, desc = destino_importacion(False, {"DATABASE_URL": URL})
    assert dsn == URL and desc == "shinkansen.proxy.rlwy.net:41234/railway"
    dsn, desc = destino_importacion(True, {"DATABASE_URL": URL, "DB_PORT": "5433"})
    assert "railway" not in dsn and "Ab" not in dsn and desc == "localhost:5433/ecoair"
    with pytest.raises(ValueError, match="falta DATABASE_URL"):
        destino_importacion(False, {"DB_PORT": "5433"})


def test_alerta_dispositivo_caido_activa():
    from shared.settings import alerta_caido_activa
    assert alerta_caido_activa({}) is True                          # por defecto: comportamiento de siempre
    for v in ("true", "TRUE", "1", "si", " yes "):
        assert alerta_caido_activa({"ALERTA_DISPOSITIVO_CAIDO_ACTIVA": v}) is True
    for v in ("false", "False", "0", "no"):
        assert alerta_caido_activa({"ALERTA_DISPOSITIVO_CAIDO_ACTIVA": v}) is False
    with pytest.raises(ValueError, match="ALERTA_DISPOSITIVO_CAIDO_ACTIVA"):
        alerta_caido_activa({"ALERTA_DISPOSITIVO_CAIDO_ACTIVA": "flase"})
