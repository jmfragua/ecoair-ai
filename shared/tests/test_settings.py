import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.settings import descripcion_db, dsn_desde_entorno, mqtt_desde_entorno  # noqa: E402


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
