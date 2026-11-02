"""ALERTA_DISPOSITIVO_CAIDO_ACTIVA en el notifier (sin base ni envios reales)."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier  # noqa: E402

AHORA = datetime.now(timezone.utc)


def test_omitir_envio():
    o = notifier.omitir_envio
    assert o("open", "device_down", "dispositivo_caido", {}, caido_activa=False)
    assert o("close", "device_down", "dispositivo_caido", {}, caido_activa=False)
    assert not o("open", "device_down", "dispositivo_caido", {}, caido_activa=True)
    assert not o("open", "threshold", "ica_1h", {}, caido_activa=False)          # las demas siguen igual
    # cierre silencioso: nunca se envia su correo de cierre, aunque la bandera vuelva a true
    assert o("close", "device_down", "dispositivo_caido", {"cierre_silencioso": True}, caido_activa=True)
    assert not o("open", "threshold", "ica_1h", {"cierre_silencioso": True}, caido_activa=True)


class Res:
    def __init__(self, filas):
        self.filas, self.rowcount = filas, 0

    def fetchall(self):
        return self.filas


class Conexion:
    def __init__(self, pendientes=()):
        self.pendientes, self.sql = list(pendientes), []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if sql.startswith("SELECT n.id"):
            return Res(self.pendientes)
        return Res([])


def pendiente(id_, kind, alert_kind, rule, details=None):
    return {"id": id_, "kind": kind, "attempts": 0, "last_attempt_at": None, "device_id": "ecoair-node-01",
            "alert_kind": alert_kind, "rule_code": rule, "severity": "critica", "message": "m",
            "started_at": AHORA - timedelta(hours=1), "ended_at": None, "is_simulated": False, "details": details or {},
            "sid": 1, "email": "a@b.co", "s_created_at": AHORA - timedelta(days=1), "unsubscribed_at": None}


class Mailer:
    def __init__(self):
        self.enviados = []

    def enviar(self, msg):
        self.enviados.append(msg)
        return "dry_run"


def test_encolar_filtra_caido_y_cierres_silenciosos(monkeypatch):
    monkeypatch.setattr(notifier.config, "ALERTA_DISPOSITIVO_CAIDO_ACTIVA", False)
    monkeypatch.setattr(notifier.config, "NOTIFY_ON_CLOSE", True)
    monkeypatch.setattr(notifier.config, "ANOMALIAS_CORREO", False)
    con = Conexion()
    notifier.encolar(con)
    selects = [(s, p) for s, p in con.sql if s.startswith("SELECT a.id AS alert_id")]
    assert len(selects) == 2                                      # open y close
    for s, p in selects:
        assert notifier.FILTRO_CAIDO in s and notifier.FILTRO_CIERRE_SILENCIOSO in s and p["caido"] is False


def test_pendientes_de_caido_se_omiten_y_las_demas_salen(monkeypatch):
    monkeypatch.setattr(notifier.config, "ALERTA_DISPOSITIVO_CAIDO_ACTIVA", False)
    monkeypatch.setattr(notifier.config, "SUBSCRIPTION_SECRET", "secreto")
    monkeypatch.setattr(notifier.config, "BASE_URL", "https://ecoair.example")
    monkeypatch.setattr(notifier.config, "MAX_INTENTOS", 5)
    con = Conexion([pendiente(1, "open", "device_down", "dispositivo_caido"),
                    pendiente(2, "close", "device_down", "dispositivo_caido", {"cierre_silencioso": True}),
                    pendiente(3, "open", "threshold", "ica_1h")])
    m = Mailer()
    notifier.enviar_pendientes(con, m)
    omitidas = [p for s, p in con.sql if "SET status = 'skipped'" in s]
    assert omitidas == [(1,), (2,)] and len(m.enviados) == 1      # solo sale el de ica_1h


def test_alertas_de_estacion_simulada_nunca_salen_a_un_buzon(monkeypatch, caplog):
    """Aunque NOTIFIER_INCLUIR_SIMULADOS=true y DRY_RUN=false: el correo simulado solo queda en el log."""
    caplog.set_level("INFO", logger="notifier")
    monkeypatch.setattr(notifier.config, "ALERTA_DISPOSITIVO_CAIDO_ACTIVA", True)
    monkeypatch.setattr(notifier.config, "SUBSCRIPTION_SECRET", "secreto")
    monkeypatch.setattr(notifier.config, "BASE_URL", "https://ecoair.example")
    monkeypatch.setattr(notifier.config, "MAX_INTENTOS", 5)
    sim = {**pendiente(7, "open", "threshold", "ica_1h"), "device_id": "ecoair-sim-01", "is_simulated": True,
           "email": "ana.perez@gmail.com"}
    con = Conexion([sim, pendiente(8, "open", "threshold", "ica_1h")])
    m = Mailer()
    notifier.enviar_pendientes(con, m)
    assert len(m.enviados) == 1 and m.enviados[0]["To"] == "a@b.co"          # solo sale la real
    estados = [p for s, p in con.sql if s.startswith("UPDATE notifications_sent SET status = %s")]
    assert ("dry_run", 7) in estados                                           # la simulada: registrada, no enviada
    assert "SIMULACIÓN: correo NO enviado (estación simulada) a a***@gmail.com: [SIMULACIÓN]" in caplog.text
    assert "ana.perez@gmail.com" not in caplog.text and "token" not in caplog.text.lower()
