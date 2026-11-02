import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import simular_continuo as sc  # noqa: E402

AHORA = datetime(2026, 11, 2, 15, 7, 31, tzinfo=timezone.utc)   # 10:07 en Colombia
HASTA = datetime(2026, 11, 6, 22, 0, tzinfo=timezone.utc)      # viernes 17:00 en Colombia
LOCAL = {"DB_HOST": "localhost"}
RAILWAY = {"DATABASE_URL": "postgresql://usuario:CLAVE-SECRETA@mainline.proxy.rlwy.net:41234/railway"}


# ---------- Generacion ----------

def test_nunca_genera_horas_futuras():
    filas = sc.generar(AHORA - timedelta(hours=48), HASTA, AHORA)
    assert filas and all(f["received_at"] <= AHORA for f in filas)
    assert filas[-1]["received_at"] == AHORA.replace(second=0, microsecond=0)
    assert len(filas) == 48 * 60                                  # una por minuto, la del minuto en curso incluida
    assert sc.generar(AHORA - timedelta(hours=1), AHORA - timedelta(hours=2), AHORA) == []   # --hasta ya paso
    assert sc.generar(HASTA - timedelta(minutes=5), HASTA, HASTA + timedelta(days=1))[-1]["received_at"] == HASTA


def test_reproducible_y_sin_depender_de_los_pasos():
    a = sc.generar(AHORA - timedelta(hours=6), HASTA, AHORA, semilla=7)
    assert a == sc.generar(AHORA - timedelta(hours=6), HASTA, AHORA, semilla=7)
    # relleno de 6 h en una vez == tres pasadas sucesivas (como el modo continuo)
    trozos = []
    for h in (6, 4, 2):
        trozos += sc.generar(AHORA - timedelta(hours=h), HASTA, AHORA - timedelta(hours=h - 2, seconds=1), semilla=7)
    assert trozos == a[:-1] or trozos == a[:len(trozos)]
    assert a != sc.generar(AHORA - timedelta(hours=6), HASTA, AHORA, semilla=8)


def test_rangos_fisicos_y_patron_diario():
    dia = sc.generar(datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc), HASTA, datetime(2026, 11, 4, 4, 59, tzinfo=timezone.utc))
    assert len(dia) == 24 * 60
    for f in dia:
        assert 0 <= f["pm25"] <= f["pm10"] <= 1000
        assert -40 <= f["temperature"] <= 80 and 0 <= f["humidity"] <= 100
    media = lambda hs, k: sum(f[k] for f in dia if f["received_at"].astimezone(sc.COLOMBIA).hour in hs) / (60 * len(hs))  # noqa: E731
    assert media((7, 8), "pm25") > media((2, 3), "pm25")          # hora pico de la manana > madrugada
    assert media((18, 19), "pm25") > media((2, 3), "pm25")        # hora pico de la tarde > madrugada
    assert media((13, 14), "temperature") > media((4, 5), "temperature") + 5   # ciclo diario de temperatura
    assert media((13, 14), "humidity") < media((4, 5), "humidity")             # humedad inversa a la temperatura
    pasos = [abs(b["pm25"] - a["pm25"]) for a, b in zip(dia, dia[1:])]
    assert max(pasos) < 5                                         # ruido pequeno, sin saltos


def test_hay_episodios_ocasionales_de_pm_alto():
    eps = sc.episodios_entre(datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 12, 1, tzinfo=timezone.utc), 2026)
    assert 10 <= len(eps) <= 35                                   # ~1 de cada 3 dias en 2 meses
    ini, dur, pico = eps[0]
    v = sc.valores(ini + dur / 2)
    assert v["pm25"] == pytest.approx(pico, abs=3) and pico >= 45


def test_faltantes_no_duplica():
    filas = sc.generar(AHORA - timedelta(minutes=9), HASTA, AHORA)
    ya = [filas[0]["received_at"], filas[3]["received_at"] + timedelta(seconds=20)]
    assert [f["received_at"] for f in sc.faltantes(filas, ya)] == [f["received_at"] for f in filas if f not in (filas[0], filas[3])]


# ---------- Seguridad ----------

def test_nunca_en_ecoair_node_01_ni_otro_dispositivo_real():
    sc.validar_device("ecoair-sim-01")
    for d in ("ecoair-node-01", "ecoair-sim-01 ", "ecoair-sim-", "ecoair-node-01;ecoair-sim-01", "", None):
        with pytest.raises(ValueError, match="simulación"):
            sc.validar_device(d)


def test_destino_remoto_pide_el_host_y_nunca_imprime_la_clave(capsys):
    host = sc.host_destino(RAILWAY)
    assert host == "mainline.proxy.rlwy.net"
    assert sc.confirmar_destino(True, "localhost:5433/ecoair", "", None, lambda _: pytest.fail("no pregunta"))
    assert sc.confirmar_destino(False, "d", host, None, lambda _: host) is True
    assert sc.confirmar_destino(False, "d", host, None, lambda _: "si") is False
    assert sc.confirmar_destino(False, "d", host, "otro.host", None) is False
    assert sc.confirmar_destino(False, "d", host, host, None) is True

    def sin_entrada(_):
        raise EOFError
    assert sc.confirmar_destino(False, "d", host, None, sin_entrada) is False   # sin terminal: no escribe


# ---------- main con una base falsa ----------

class BaseFalsa:
    """Imita measurements para un dispositivo: guarda (device, received_at, is_simulated)."""

    def __init__(self, reales=0):
        self.reales, self.filas, self.sql = reales, [], []
        base = self

        class Cur:
            def __enter__(s): return s
            def __exit__(s, *a): return False

            def executemany(s, sql, params):
                assert "true)" in sql                               # is_simulated literal true en el INSERT
                base.filas += [(p[0], p[1], True) for p in params]
        self._cur = Cur()

    @contextmanager
    def transaction(self): yield

    def cursor(self): return self._cur
    def close(self): pass

    def execute(self, sql, params=()):
        self.sql.append(sql)
        filas = self.filas
        if sql is sc.SQL_EXISTENTES:
            res = [(t,) for d, t, _ in filas if d == params[0] and params[1] <= t <= params[2]]
        elif sql is sc.SQL_REALES:
            res = [(self.reales,)]
        elif sql is sc.SQL_FUTURAS:
            res = [(0,)]
        else:
            res = []
        return type("R", (), {"fetchone": lambda s: res[0], "fetchall": lambda s: res})()


def test_main_dry_run_no_conecta(capsys):
    def conectar():
        raise AssertionError("no debe conectar")
    assert sc.main(["--backfill-horas", "48", "--dry-run"], conectar=conectar, reloj=lambda: AHORA, env=RAILWAY) == 0
    out = capsys.readouterr().out
    assert "DATOS SIMULADOS" in out and "2880 filas" in out and "ninguna futura" in out
    assert "CLAVE-SECRETA" not in out


def test_main_local_backfill_y_continuo_solo_simulados_sin_futuro(capsys):
    base, relojes = BaseFalsa(), iter([AHORA, AHORA + timedelta(minutes=5), HASTA + timedelta(minutes=3)])
    actual = {}

    def reloj():
        actual["t"] = next(relojes)
        return actual["t"]
    esperas = []
    rc = sc.main(["--local", "--backfill-horas", "48", "--continuo"], conectar=lambda: base, reloj=reloj,
                 dormir=esperas.append, preguntar=lambda _: pytest.fail("local no pregunta"), env={**LOCAL, **RAILWAY})
    # reloj: AHORA al arrancar; pasadas a AHORA + 5 min y despues de --hasta (la ultima termina el bucle)
    assert rc == 0 and esperas == [300]
    devices = {d for d, _, _ in base.filas}
    assert devices == {"ecoair-sim-01"}                          # nunca ecoair-node-01
    assert all(sim for _, _, sim in base.filas)                  # is_simulated siempre true
    assert max(t for _, t, _ in base.filas) == HASTA             # se detiene en --hasta, nada futuro
    ts = [t for _, t, _ in base.filas]
    assert len(ts) == len(set(ts))                               # sin duplicados entre pasadas
    out = capsys.readouterr().out
    assert "localhost:5433/ecoair" in out and "CLAVE-SECRETA" not in out


def test_main_remoto_sin_confirmar_no_escribe(capsys):
    base = BaseFalsa()
    rc = sc.main(["--backfill-horas", "1"], conectar=lambda: base, reloj=lambda: AHORA, preguntar=lambda _: "", env=RAILWAY)
    out = capsys.readouterr().out
    assert rc == 3 and base.filas == [] and base.sql == []
    assert "mainline.proxy.rlwy.net:41234/railway" in out and "CLAVE-SECRETA" not in out and "usuario" not in out
    rc = sc.main(["--backfill-horas", "1", "--confirmar-destino", "mainline.proxy.rlwy.net"], conectar=lambda: base,
                 reloj=lambda: AHORA, env=RAILWAY)
    assert rc == 0 and len(base.filas) == 60


def test_main_sin_database_url_ni_local_falla():
    assert sc.main(["--backfill-horas", "1"], conectar=lambda: pytest.fail("no conecta"), reloj=lambda: AHORA, env={}) == 2


def test_main_rechaza_node_01_y_dispositivo_con_datos_reales(capsys):
    assert sc.main(["--device", "ecoair-node-01", "--local"], conectar=lambda: pytest.fail("no conecta"),
                   reloj=lambda: AHORA, env=LOCAL) == 2
    base = BaseFalsa(reales=3)
    assert sc.main(["--device", "ecoair-sim-02", "--local", "--backfill-horas", "1"], conectar=lambda: base,
                   reloj=lambda: AHORA, env=LOCAL) == 2
    assert base.filas == [] and "REALES" in capsys.readouterr().out


# ---------- Episodio programado (--episodio-en) ----------

def test_episodio_en_programado_reproducible_y_sin_filas_futuras():
    ep = sc.parsear_episodio_en(sc.EPISODIO_EN_POR_DEFECTO)
    inicio = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)                # viernes 15:00 en Colombia
    assert ep == (inicio, timedelta(minutes=90), sc.EPISODIO_PICO)
    assert sc.parsear_episodio_en("ninguno") is None and sc.parsear_episodio_en("") is None
    for malo in ("2026-11-06T15:00", "6 nov 15h", "2026-11-06 25:00"):
        with pytest.raises(ValueError, match="AAAA-MM-DD HH:MM"):
            sc.parsear_episodio_en(malo)
    # pico ~95 en la meseta, subida y bajada suaves, nada fuera de los 90 min
    dia = sc.generar(inicio - timedelta(hours=2), HASTA, HASTA, programados=(ep,))
    pm = {f["received_at"]: f["pm25"] for f in dia}
    assert pm[inicio + timedelta(minutes=45)] == pytest.approx(95, abs=3)
    assert pm[inicio - timedelta(minutes=30)] < 40 and pm[inicio + timedelta(minutes=120)] < 40
    sin = {f["received_at"]: f["pm25"] for f in sc.generar(inicio - timedelta(hours=2), HASTA, HASTA)}
    assert all(pm[t] == sin[t] for t in pm if not inicio <= t <= inicio + timedelta(minutes=90))
    assert max(abs(b["pm25"] - a["pm25"]) for a, b in zip(dia, dia[1:])) < 10   # sin saltos bruscos
    # reproducible
    assert dia == sc.generar(inicio - timedelta(hours=2), HASTA, HASTA, programados=(ep,))
    # a mitad del episodio solo existen los minutos ya pasados: el resto no se inserta antes de tiempo
    a_mitad = inicio + timedelta(minutes=30, seconds=10)
    parcial = sc.generar(inicio - timedelta(hours=2), HASTA, a_mitad, programados=(ep,))
    assert parcial[-1]["received_at"] == inicio + timedelta(minutes=30)
    assert parcial == dia[:len(parcial)]                                       # mismos valores que luego


def test_main_episodio_por_defecto_y_servicio(capsys):
    base, relojes = BaseFalsa(), iter([AHORA, AHORA, HASTA + timedelta(hours=1)])
    rc = sc.main(["--local", "--continuo"], conectar=lambda: base, reloj=lambda: next(relojes), dormir=lambda s: None, env=LOCAL)
    out = capsys.readouterr().out
    assert rc == 0 and "programado con --episodio-en): 2026-11-06 15:00 -> 2026-11-06 16:30 · pico PM2.5 95" in out
    assert all(sim for _, _, sim in base.filas) and max(t for _, t, _ in base.filas) == HASTA   # simulados, nada futuro
    assert sc.main(["--episodio-en", "ninguno", "--dry-run"], reloj=lambda: AHORA, env=LOCAL) == 0
    assert "programado" not in capsys.readouterr().out
    assert sc.main(["--episodio-en", "mañana", "--dry-run"], reloj=lambda: AHORA, env=LOCAL) == 2
    argv = sc.argumentos_servicio({"SIM_ACTIVO": "true", "SIM_EPISODIO_EN": "2026-11-05 10:00"})
    assert argv[argv.index("--episodio-en") + 1] == "2026-11-05 10:00"
    argv = sc.argumentos_servicio({"SIM_ACTIVO": "true"})
    assert argv[argv.index("--episodio-en") + 1] == "2026-11-06 15:00"


# ---------- Modo servicio (Railway) ----------

def test_servicio_sin_sim_activo_no_hace_nada(capsys):
    for env in ({}, RAILWAY, {**RAILWAY, "SIM_ACTIVO": "false"}, {**RAILWAY, "SIM_ACTIVO": ""}):
        assert sc.main(["--servicio"], conectar=lambda: pytest.fail("no conecta"), reloj=lambda: AHORA, env=env) == 0
    assert "SIM_ACTIVO no es true: el simulador no hace nada." in capsys.readouterr().out


def test_servicio_relleno_48h_y_bucle_sin_preguntar(capsys):
    base, relojes = BaseFalsa(), iter([AHORA, AHORA, AHORA + timedelta(minutes=5), HASTA + timedelta(hours=1)])
    esperas = []
    rc = sc.main(["--servicio"], conectar=lambda: base, reloj=lambda: next(relojes), dormir=esperas.append,
                 preguntar=lambda _: pytest.fail("como servicio no pregunta"), env={**RAILWAY, "SIM_ACTIVO": "true"})
    assert rc == 0 and esperas == [300, 300]
    ts = [t for _, t, _ in base.filas]
    assert {d for d, _, _ in base.filas} == {"ecoair-sim-01"} and all(s for _, _, s in base.filas)
    assert min(ts) <= AHORA - timedelta(hours=47, minutes=59)     # relleno de 48 h al arrancar
    assert max(ts) == HASTA and len(ts) == len(set(ts))           # hasta el viernes 17:00, nada futuro ni repetido
    out = capsys.readouterr().out
    assert "mainline.proxy.rlwy.net:41234/railway" in out and "CLAVE-SECRETA" not in out and "usuario" not in out
    assert "Modo servicio (SIM_ACTIVO=true)" in out


def test_servicio_conserva_las_protecciones(capsys):
    activo = {**RAILWAY, "SIM_ACTIVO": "true"}
    no = lambda: pytest.fail("no conecta")  # noqa: E731
    assert sc.main(["--servicio"], conectar=no, reloj=lambda: AHORA, env={**activo, "SIM_DEVICE": "ecoair-node-01"}) == 2
    assert sc.main(["--servicio"], conectar=no, reloj=lambda: AHORA, env={"SIM_ACTIVO": "true"}) == 2   # sin DATABASE_URL
    assert sc.main(["--servicio", "--local"], conectar=no, reloj=lambda: AHORA, env=activo) == 2
    base = BaseFalsa(reales=5)
    assert sc.main(["--servicio"], conectar=lambda: base, reloj=lambda: AHORA, env=activo) == 2       # datos reales
    assert base.filas == [] and "REALES" in capsys.readouterr().out


def test_dockerfile_y_railway_toml_del_simulador():
    raiz = os.path.join(os.path.dirname(__file__), "..", "..")
    df = open(os.path.join(raiz, "simulador", "Dockerfile")).read()
    assert 'CMD ["python", "tools/simular_continuo.py", "--servicio"]' in df
    codigo = "\n".join(l for l in df.splitlines() if not l.lstrip().startswith("#"))
    assert "DATABASE_URL" not in codigo and "SIM_ACTIVO" not in codigo     # nada activado ni secretos en la imagen
    toml = open(os.path.join(raiz, "simulador", "railway.toml")).read()
    assert 'dockerfilePath = "simulador/Dockerfile"' in toml and 'restartPolicyType = "ON_FAILURE"' in toml
