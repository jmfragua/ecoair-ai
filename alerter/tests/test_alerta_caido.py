"""ALERTA_DISPOSITIVO_CAIDO_ACTIVA en el alerter (sin base: conexion falsa)."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import alerter  # noqa: E402
from reglas_alertas import Condicion  # noqa: E402

AHORA = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)


class Res:
    def __init__(self, uno=None, todos=None):
        self.uno, self.todos = uno, todos or []

    def fetchone(self):
        return self.uno

    def fetchall(self):
        return self.todos


class ConexionFalsa:
    def __init__(self, cerradas=()):
        self.sql, self.params, self.cerradas = [], [], list(cerradas)

    def execute(self, sql, params=None):
        self.sql.append(sql)
        self.params.append(params)
        if sql.startswith("UPDATE alerts SET ended_at"):
            return Res(todos=self.cerradas)
        if sql.startswith("SELECT received_at AS timestamp"):      # ventana del sensor: sin filas
            return Res(todos=[])
        return Res({"received_at": AHORA - timedelta(hours=3)})   # SQL_VENTANAS / SQL_ULTIMA


def _evaluar(monkeypatch, activa):
    monkeypatch.setattr(alerter.config, "ALERTA_DISPOSITIVO_CAIDO_ACTIVA", activa)
    monkeypatch.setattr(alerter, "ventanas_desde_fila", lambda fila: ({}, {}))
    monkeypatch.setattr(alerter, "construir_respuesta", lambda *a: {})
    pm25 = Condicion(rule_code="ica_1h", kind="threshold", severity="aviso", message="ICA alto", details={})
    monkeypatch.setattr(alerter, "condiciones_umbral", lambda resp, ahora: [pm25])
    monkeypatch.setattr(alerter, "condicion_anomalia_de", lambda *a: None)
    return alerter.evaluar_dispositivo(ConexionFalsa(), "ecoair-node-01", AHORA)


def test_con_true_se_evalua_dispositivo_caido_como_siempre(monkeypatch):
    assert set(_evaluar(monkeypatch, True)) == {"ica_1h", "dispositivo_caido"}


def test_con_false_no_se_abre_dispositivo_caido_y_las_demas_siguen(monkeypatch):
    assert set(_evaluar(monkeypatch, False)) == {"ica_1h"}


def test_cierre_silencioso_marca_details_y_olvida_el_estado():
    alerter.estados[("ecoair-node-01", "dispositivo_caido")].abierta = True
    alerter.estados[("ecoair-node-01", "ica_1h")].abierta = True
    con = ConexionFalsa(cerradas=[{"id": 9, "device_id": "ecoair-node-01"}])
    assert alerter.cerrar_caidas_en_silencio(con, AHORA) == 1
    sql = con.sql[0]
    assert sql.startswith("UPDATE alerts SET ended_at") and "DELETE" not in sql.upper()
    assert "kind = 'device_down' OR rule_code = 'dispositivo_caido'" in sql and "ended_at IS NULL" in sql
    assert con.params[0]["marca"].obj == {"cierre_silencioso": True, "motivo": "ALERTA_DISPOSITIVO_CAIDO_ACTIVA=false"}
    assert ("ecoair-node-01", "dispositivo_caido") not in alerter.estados
    assert alerter.estados[("ecoair-node-01", "ica_1h")].abierta is True        # las demas no se tocan
    alerter.estados.clear()


def test_revisar_cierra_en_silencio_solo_con_false(monkeypatch):
    llamadas = []
    monkeypatch.setattr(alerter, "get_db", lambda: object())
    monkeypatch.setattr(alerter, "migraciones_ok", lambda conn: True)
    monkeypatch.setattr(alerter, "dispositivos", lambda conn: [])
    monkeypatch.setattr(alerter, "cerrar_caidas_en_silencio", lambda conn, ahora: llamadas.append(1))
    monkeypatch.setattr(alerter.config, "PREDICCION_ACTIVA", False)
    monkeypatch.setattr(alerter.config, "ALERTA_DISPOSITIVO_CAIDO_ACTIVA", True)
    alerter.revisar()
    assert llamadas == []
    monkeypatch.setattr(alerter.config, "ALERTA_DISPOSITIVO_CAIDO_ACTIVA", False)
    alerter.revisar()
    assert llamadas == [1]
