import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import simular_estacion as se  # noqa: E402

AHORA = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)   # 10:00 en Colombia


def test_parsear_momento():
    assert se.parsear_momento("+20m", AHORA) == AHORA + timedelta(minutes=20)
    assert se.parsear_momento("-1h", AHORA) == AHORA - timedelta(hours=1)
    assert se.parsear_momento("2026-11-05T10:30-05:00", AHORA) == datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="zona"):
        se.parsear_momento("2026-11-05T10:30", AHORA)


def test_perfil_suave_sin_saltos():
    inicio, dur = AHORA, timedelta(minutes=80)
    vals = [se.perfil_episodio(inicio + timedelta(minutes=m), inicio, dur) for m in range(-5, 86)]
    assert vals[0] == 0 and vals[-1] == 0 and max(vals) == 1.0
    assert max(abs(b - a) for a, b in zip(vals, vals[1:])) < 0.1   # ningun salto brusco minuto a minuto


def test_generar_rangos_reproducible_y_un_dato_por_minuto():
    a = se.generar(AHORA - timedelta(hours=24), AHORA, semilla=3)
    assert a == se.generar(AHORA - timedelta(hours=24), AHORA, semilla=3)
    assert len(a) == 24 * 60 + 1
    for f in a:
        assert 0 <= f["pm25"] <= f["pm10"] <= 1000
        assert -40 <= f["temperature"] <= 80 and 0 <= f["humidity"] <= 100


def test_episodio_llega_al_pico_y_dispara_ica_1h():
    ep = (AHORA + timedelta(minutes=20), timedelta(minutes=90), 120.0)
    filas = se.generar(AHORA - timedelta(hours=2), AHORA + timedelta(hours=3), episodio=ep)
    assert max(f["pm25"] for f in filas) == pytest.approx(120, abs=5)
    media, ica, cat, cruce = se.prever_disparo(filas)
    assert media >= 38 and ica >= 101 and cruce is not None
    assert ep[0] < cruce < ep[0] + ep[1]


def test_episodio_debil_no_dispara():
    ep = (AHORA, timedelta(minutes=60), 30.0)
    filas = se.generar(AHORA - timedelta(hours=2), AHORA + timedelta(hours=2), episodio=ep)
    media, ica, cat, cruce = se.prever_disparo(filas)
    assert media < 38 and cruce is None


def test_nunca_en_un_dispositivo_real():
    se.verificar_dispositivo(0, "ecoair-sim-01")
    with pytest.raises(ValueError, match="REALES"):
        se.verificar_dispositivo(4222, "ecoair-node-01")


# ---------- main (sin base real) ----------

class ConexionFalsa:
    def __init__(self, reales=0):
        self.reales, self.sql, self.insertadas = reales, [], 0
        cur = self

        class Cur:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def executemany(s, sql, params): cur.insertadas += len(params); cur.sql.append(sql)
        self._cur = Cur()

    def __enter__(self): return self
    def __exit__(self, *a): return False

    @contextmanager
    def transaction(self): yield

    def cursor(self): return self._cur

    def execute(self, sql, params=()):
        self.sql.append(sql)
        r = type("R", (), {"rowcount": 5, "fetchone": lambda s: (self.reales,)})()
        return r


def test_main_dry_run_no_conecta(capsys):
    def conectar():
        raise AssertionError("no debe conectar")
    assert se.main(["--horas", "1", "--episodio-inicio", "+10m", "--dry-run"], conectar=conectar, ahora=AHORA) == 0
    out = capsys.readouterr().out
    assert "DATOS SIMULADOS" in out and "filas tienen hora futura" in out and "no se escribio nada" in out


def test_main_carga_solo_simulados(capsys):
    conn = ConexionFalsa()
    assert se.main(["--horas", "2"], conectar=lambda: conn, ahora=AHORA) == 0
    assert conn.insertadas == 2 * 60 + 1
    assert all("true)" in s for s in conn.sql if s.startswith("INSERT INTO measurements"))
    assert any(s.startswith("DELETE FROM measurements") and "is_simulated" in s for s in conn.sql)


def test_main_rechaza_dispositivo_real(capsys):
    conn = ConexionFalsa(reales=10)
    assert se.main(["--device", "ecoair-node-01"], conectar=lambda: conn, ahora=AHORA) == 2
    assert conn.insertadas == 0 and "REALES" in capsys.readouterr().out
