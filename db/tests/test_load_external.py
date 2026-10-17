import io
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import load_external as le  # noqa: E402

EJEMPLO = Path(__file__).parent / "data" / "external_ejemplo.csv"
ARGS = ["--source", "Fuente de prueba", "--license", "CC BY 4.0", "--col-station", "estacion",
        "--city", "Medellin", "--col-time", "fecha", "--col-pm25", "pm25", "--col-pm10", "pm10",
        "--col-temperature", "temp", "--col-humidity", "hum", "--utc-offset", "-05:00"]


def _cfg(**extra):
    cfg = {"source": "Fuente de prueba", "license": "CC BY 4.0", "station": None, "city": "Medellin",
           "cols": {"time": "fecha", "pm25": "pm25", "pm10": "pm10", "temperature": "temp",
                    "humidity": "hum", "station": "estacion", "city": None},
           "time_format": None, "offset": le.parsear_offset("-05:00"), "decimal": ".", "delimiter": ","}
    cfg.update(extra)
    return cfg


# ---------- Conversiones ----------

def test_parsear_offset():
    assert le.parsear_offset("-05:00").utcoffset(None).total_seconds() == -5 * 3600
    assert le.parsear_offset("+0130").utcoffset(None).total_seconds() == 5400
    assert le.parsear_offset("Z") is timezone.utc
    with pytest.raises(ValueError):
        le.parsear_offset("Bogota")


def test_parsear_numero():
    assert le.parsear_numero("12.5") == 12.5
    assert le.parsear_numero("12,5", decimal=",") == 12.5
    assert le.parsear_numero("1.234,5", decimal=",") == 1234.5
    assert le.parsear_numero("  ") is None and le.parsear_numero(None) is None
    for malo in ("abc", "nan", "inf"):
        with pytest.raises(le.FilaInvalida):
            le.parsear_numero(malo)


def test_parsear_hora_a_utc():
    off = le.parsear_offset("-05:00")
    assert le.parsear_hora("2026-10-01 07:00", offset=off) == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    assert le.parsear_hora("2026-10-01T12:00:00Z") == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    assert le.parsear_hora("01/10/2026 07:00", "%d/%m/%Y %H:%M", off).hour == 12
    with pytest.raises(le.FilaInvalida, match="--utc-offset"):
        le.parsear_hora("2026-10-01 07:00")  # sin zona y sin offset
    with pytest.raises(le.FilaInvalida):
        le.parsear_hora("fecha-mala", offset=off)


# ---------- CSV de ejemplo ----------

def test_procesar_csv_de_ejemplo():
    with EJEMPLO.open(encoding="utf-8", newline="") as f:
        filas, r = le.procesar_csv(f, _cfg())
    assert r["leidas"] == 11
    assert r["validas"] == 3 and r["duplicadas_en_csv"] == 1 and r["invalidas"] == 7
    lineas = dict(r["errores"])
    assert "pm25=1200 fuera de rango" in lineas[6]
    assert lineas[7] == "pm25 vacio"
    assert "no numerico" in lineas[8]
    assert "hora no valida" in lineas[9]
    assert "temperature=95 fuera de rango" in lineas[10]
    assert "humidity=101 fuera de rango" in lineas[11]
    assert lineas[12] == "estacion vacia"
    primera = filas[0]
    assert primera == {"source": "Fuente de prueba", "station": "EST-01", "city": "Medellin",
                       "measured_at": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
                       "pm25": 12.4, "pm10": 20.1, "temperature": 18.5, "humidity": 80.0,
                       "license": "CC BY 4.0"}
    assert filas[1]["temperature"] is None and filas[1]["humidity"] is None  # vacias -> NULL


def test_columna_inexistente_da_error_claro():
    with EJEMPLO.open(encoding="utf-8", newline="") as f:
        with pytest.raises(ValueError, match="no tiene las columnas indicadas: pm_2_5"):
            le.procesar_csv(f, _cfg(cols={**_cfg()["cols"], "pm25": "pm_2_5"}))


def test_decimal_con_coma_y_punto_y_coma():
    texto = "estacion;fecha;pm25;pm10\nE1;2026-10-01T07:00:00-05:00;12,5;20,0\n"
    cfg = _cfg(decimal=",", delimiter=";", cols={**_cfg()["cols"], "temperature": None, "humidity": None})
    filas, r = le.procesar_csv(io.StringIO(texto), cfg)
    assert r["validas"] == 1 and filas[0]["pm25"] == 12.5 and filas[0]["temperature"] is None


def test_lotes():
    assert [len(x) for x in le.lotes(list(range(2500)), 1000)] == [1000, 1000, 500]


# ---------- Carga (con conexion falsa) ----------

class CursorFalso:
    def __init__(self, existentes):
        self.existentes, self.rowcount, self.llamadas = existentes, 0, []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def executemany(self, sql, params):
        self.llamadas.append(len(params))
        nuevas = [p for p in params if (p[0], p[1], p[3]) not in self.existentes]
        self.existentes |= {(p[0], p[1], p[3]) for p in nuevas}
        self.rowcount = len(nuevas)


class ConexionFalsa:
    def __init__(self, existentes=(), tabla=True):
        self.cur, self.tabla, self.transacciones = CursorFalso(set(existentes)), tabla, 0

    def __enter__(self): return self
    def __exit__(self, *a): return False

    @contextmanager
    def transaction(self):
        self.transacciones += 1
        yield

    def cursor(self): return self.cur

    def execute(self, sql, params=()):
        assert "to_regclass('external_measurements')" in sql
        return type("R", (), {"fetchone": lambda s: (self.tabla,)})()


def test_insertar_por_lotes_y_omitir_existentes():
    with EJEMPLO.open(encoding="utf-8", newline="") as f:
        filas, _ = le.procesar_csv(f, _cfg())
    ya = {("Fuente de prueba", "EST-01", datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc))}
    conn = ConexionFalsa(existentes=ya)
    insertadas, existian = le.insertar(conn, filas, tamano_lote=2)
    assert (insertadas, existian) == (2, 1)
    assert conn.cur.llamadas == [2, 1] and conn.transacciones == 2  # 3 filas en lotes de 2


def test_main_dry_run_no_conecta(capsys):
    def conectar():
        raise AssertionError("no debe conectar en --dry-run")

    assert le.main([str(EJEMPLO), *ARGS, "--dry-run"], conectar=conectar) == 0
    salida = capsys.readouterr().out
    assert "Filas leidas: 11 · validas: 3 · invalidas: 7 · duplicadas en el CSV: 1" in salida
    assert "no se escribio nada" in salida


def test_main_carga_y_recarga_sin_duplicar(capsys):
    conn = ConexionFalsa()
    assert le.main([str(EJEMPLO), *ARGS], conectar=lambda: conn) == 0
    assert "Insertadas: 3 · ya existian en la base (omitidas): 0" in capsys.readouterr().out
    assert le.main([str(EJEMPLO), *ARGS], conectar=lambda: conn) == 0
    assert "Insertadas: 0 · ya existian en la base (omitidas): 3" in capsys.readouterr().out


def test_main_sin_migracion_006(capsys):
    assert le.main([str(EJEMPLO), *ARGS], conectar=lambda: ConexionFalsa(tabla=False)) == 2
    assert "006_external_measurements.sql" in capsys.readouterr().out


def test_main_exige_estacion_y_ciudad(capsys):
    args = [a for a in ARGS]
    i = args.index("--city"); del args[i:i + 2]
    assert le.main([str(EJEMPLO), *args, "--dry-run"]) == 2
    assert "--col-city o --city" in capsys.readouterr().out


# ---------- PM10 opcional ----------

SOLO_PM25 = "estacion,fecha,pm25\nE1,2026-10-01T07:00:00-05:00,12.0\nE1,2026-10-01T08:00:00-05:00,13.5\n"


def _cfg_sin_pm10():
    return _cfg(cols={**_cfg()["cols"], "pm10": None, "temperature": None, "humidity": None})


def test_csv_sin_columna_pm10_es_valido():
    filas, r = le.procesar_csv(io.StringIO(SOLO_PM25), _cfg_sin_pm10())
    assert r["validas"] == 2 and r["invalidas"] == 0
    assert all(f["pm10"] is None for f in filas) and filas[0]["pm25"] == 12.0


def test_celda_pm10_vacia_es_null_y_fuera_de_rango_se_rechaza():
    texto = ("estacion,fecha,pm25,pm10\n"
             "E1,2026-10-01T07:00:00-05:00,12.0,\n"        # pm10 vacio -> NULL
             "E1,2026-10-01T08:00:00-05:00,12.0,5000\n")   # pm10 fuera de rango -> invalida
    cfg = _cfg(cols={**_cfg()["cols"], "temperature": None, "humidity": None})
    filas, r = le.procesar_csv(io.StringIO(texto), cfg)
    assert r["validas"] == 1 and filas[0]["pm10"] is None
    assert "pm10=5000 fuera de rango" in dict(r["errores"])[3]


def test_main_sin_col_pm10(tmp_path, capsys):
    csv_ = tmp_path / "solo_pm25.csv"
    csv_.write_text(SOLO_PM25)
    args = ["--source", "F", "--license", "L", "--col-station", "estacion", "--city", "Medellin",
            "--col-time", "fecha", "--col-pm25", "pm25"]
    assert le.main([str(csv_), *args, "--dry-run"]) == 0
    assert "validas: 2" in capsys.readouterr().out
