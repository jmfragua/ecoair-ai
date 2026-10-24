"""Tests de tools/reporte_limpieza_external.py con filas INVENTADAS y una conexion falsa (sin base)."""
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import reporte_limpieza_external as rep  # noqa: E402

T0 = datetime(2024, 3, 1, 6, 0, tzinfo=timezone.utc)


def filas(source, station, valores):
    return [(source, station, T0 + timedelta(hours=i), Decimal(str(v))) for i, v in enumerate(valores)]


DATOS = (filas("IDEAM datos.gov.co", "A (IDEAM 1)", [0, 12, 9, 9, 9, 9, 9, 9, 15])
         + filas("IDEAM datos.gov.co", "B (IDEAM 2)", [10, 11, 12])
         + filas("OpenAQ archive", "MED-UNNV", [0, 0, 5, 5, 5, 5, 5, 5, 5, 6]))


def test_resumir_por_fuente_y_estacion():
    grupos, rachas = rep.resumir(DATOS)
    g = {(x["source"], x["station"]): x for x in grupos}
    a = g[("IDEAM datos.gov.co", "A (IDEAM 1)")]
    assert (a["filas"], a["ceros"], a["rachas"], a["horas_en_rachas"]) == (9, 1, 1, 6)
    b = g[("IDEAM datos.gov.co", "B (IDEAM 2)")]
    assert (b["ceros"], b["rachas"], b["horas_en_rachas"]) == (0, 0, 0)
    o = g[("OpenAQ archive", "MED-UNNV")]
    assert (o["ceros"], o["rachas"], o["horas_en_rachas"]) == (2, 1, 7)
    assert len(rachas) == 2 and {r["source"] for r in rachas} == {"IDEAM datos.gov.co", "OpenAQ archive"}


class ConexionFalsa:
    def __init__(self, existe=True):
        self.existe, self.read_only, self.cerrada, self.rollbacks, self.sql = existe, None, False, 0, []

    def execute(self, sql, *a):
        self.sql.append(sql)
        return type("R", (), {"fetchone": lambda s: (self.existe,)})()

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.cerrada = True


def test_main_es_solo_lectura_y_no_escribe(capsys):
    con = ConexionFalsa()
    assert rep.main(["--detalle", "5"], conectar=lambda: con, leer=lambda c: iter(DATOS)) == 0
    out = capsys.readouterr().out
    assert con.read_only is True and con.rollbacks == 1 and con.cerrada
    assert not any(k in s.upper() for s in con.sql for k in ("DELETE", "UPDATE", "INSERT", "ALTER", "DROP"))
    assert "| IDEAM datos.gov.co | A (IDEAM 1) | 9 |" in out and "| 1 | 1 | 6 |" in out
    assert "OpenAQ archive: 1 estaciones · 10 filas · 2 ceros · 1 rachas (7 horas) · afectado 90.00 %" in out
    assert "Primeras 2 rachas (de 2):" in out and "Solo lectura" in out


def test_sin_tabla_o_sin_conexion(capsys):
    assert rep.main([], conectar=lambda: ConexionFalsa(existe=False), leer=lambda c: iter([])) == 0
    assert "migracion 006 sin aplicar" in capsys.readouterr().out

    def falla():
        raise OSError("postgresql://usuario:CLAVE@host/db")
    assert rep.main([], conectar=falla) == 2
    out = capsys.readouterr().out
    assert "CLAVE" not in out and "postgresql://" not in out and "OSError" in out


def test_sql_solo_lee_y_ordena():
    assert rep.SQL.upper().startswith("SELECT") and "ORDER BY SOURCE, STATION, MEASURED_AT" in rep.SQL.upper()


# ---------- Errores de conexion: codigo y mensaje, sin URL ni clave ----------

def test_operational_error_muestra_codigo_y_mensaje_sin_secretos(capsys, monkeypatch):
    import psycopg
    url = "postgresql://postgres:Cl4ve%21@shinkansen.proxy.rlwy.net:41234/railway"
    monkeypatch.setenv("DATABASE_URL", url)

    class Auth(psycopg.OperationalError):
        sqlstate = "28P01"

    def falla():
        raise Auth('connection failed: connection to server at "shinkansen.proxy.rlwy.net" (1.2.3.4), port 41234 '
                   f'failed: FATAL: password authentication failed for user "postgres" ({url})')
    assert rep.main([], conectar=falla) == 2
    out = capsys.readouterr().out
    assert "origen: DATABASE_URL" in out and "shinkansen.proxy.rlwy.net:41234/railway" in out
    assert "No se pudo conectar: Auth [28P01]: connection failed" in out
    assert "password authentication failed" in out
    for secreto in ("Cl4ve", "postgresql://", "postgres:Cl4ve"):
        assert secreto not in out


def test_sin_database_url_avisa_que_usa_localhost(capsys, monkeypatch):
    import psycopg
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def falla():
        raise psycopg.OperationalError('connection failed: connection to server at "127.0.0.1", port 5433 failed: '
                                       "Connection refused")
    assert rep.main([], conectar=falla) == 2
    out = capsys.readouterr().out
    assert "origen: DB_*" in out and "DATABASE_URL no esta definida en este proceso" in out
    assert "OperationalError: connection failed" in out and "Connection refused" in out


def test_error_durante_la_consulta_tambien_es_seguro(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:Secreta@h:1/db")
    con = ConexionFalsa()

    def leer(_):
        raise RuntimeError("canceling statement due to statement timeout (postgresql://u:Secreta@h:1/db)")
    assert rep.main([], conectar=lambda: con, leer=leer) == 2
    out = capsys.readouterr().out
    assert "Error durante la consulta: RuntimeError: canceling statement" in out and "Secreta" not in out
    assert con.rollbacks == 1 and con.cerrada
