import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import resumen_datos as rd  # noqa: E402

T1 = datetime(2026, 10, 14, 2, 13, tzinfo=timezone.utc)   # 13 oct 21:13 en Colombia
T2 = datetime(2026, 10, 16, 5, 40, tzinfo=timezone.utc)


def test_solo_consultas_de_lectura():
    for sql in rd.CONSULTAS:
        assert sql.strip().upper().startswith("SELECT")
        for palabra in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"):
            assert palabra not in sql.upper()


@pytest.mark.parametrize("mediciones, todas, alguna, esperado", [
    (0, None, None, "sin datos"),
    (10, False, False, "reales"),
    (10, True, True, "simuladas"),
    (10, False, True, "MIXTAS (revisar)"),
    (10, None, None, "desconocido (falta migracion 002)"),
])
def test_tipo_datos(mediciones, todas, alguna, esperado):
    assert rd.tipo_datos(mediciones, todas, alguna) == esperado


def test_fecha_en_hora_de_colombia():
    assert rd.fecha(T1) == "2026-10-13 21:13"
    assert rd.fecha(None) == "-"


def test_tabla_alineada():
    t = rd.tabla(["a", "bb"], [["xxx", "1"], ["y", "22"]]).splitlines()
    assert t[0] == "a    bb" and t[1] == "---  --" and t[2] == "xxx  1 "


class ConexionFalsa:
    def __init__(self, tablas=("measurements", "external_measurements"), is_simulated=True):
        self.tablas, self.is_simulated, self.sql = set(tablas), is_simulated, []
        self.rollbacks = self.cerrada = 0

    def execute(self, sql, params=()):
        self.sql.append(sql)
        if sql is rd.SQL_EXISTE:
            filas = [(params[0] in self.tablas,)]
        elif sql is rd.SQL_TIENE_IS_SIMULATED:
            filas = [(self.is_simulated,)]
        elif "FROM devices" in sql:
            filas = [("ecoair-node-01", 4222, T1, T2, 6.0, False, False),
                     ("ecoair-nuevo", 0, None, None, 0, None, None),
                     ("ecoair-sim-01", 43100, T1, T2, 718.3, True, True)]
        else:
            filas = [("Fuente X", 3, 2, "Medellin", T1, T2, 2, "CC BY 4.0")]
        return type("R", (), {"fetchone": lambda s: filas[0], "fetchall": lambda s: filas})()

    def rollback(self): self.rollbacks += 1
    def close(self): self.cerrada += 1


def test_resumen_completo():
    texto = rd.resumen(ConexionFalsa())
    assert "ecoair-node-01  reales" in texto and "4222" in texto and "6.0" in texto
    assert "ecoair-sim-01   simuladas" in texto and "718.3" in texto
    assert "ecoair-nuevo    sin datos" in texto
    assert "Fuente X" in texto and "CC BY 4.0" in texto and "2026-10-13 21:13" in texto


def test_resumen_sin_tabla_externa_ni_002():
    conn = ConexionFalsa(tablas=("measurements",), is_simulated=False)
    texto = rd.resumen(conn)
    assert "migracion 006 sin aplicar" in texto
    assert rd.SIMULADO_NO in conn.sql[2]


def test_main_no_imprime_credenciales_y_cierra(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://usuario:CLAVE-SECRETA@db.example:5432/railway")
    conn = ConexionFalsa()
    assert rd.main(conectar=lambda: conn) == 0
    salida = capsys.readouterr().out
    assert "db.example:5432/railway (solo lectura)" in salida
    assert "CLAVE-SECRETA" not in salida and "usuario" not in salida
    assert conn.rollbacks == 1 and conn.cerrada == 1
