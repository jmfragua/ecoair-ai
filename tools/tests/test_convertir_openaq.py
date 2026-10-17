"""Tests de tools/convertir_openaq.py con filas INVENTADAS (no son mediciones reales)."""
import csv
import gzip
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "db"))

import convertir_openaq as co  # noqa: E402
import load_external as le  # noqa: E402

CAMPOS = ["location_id", "sensors_id", "location", "datetime", "lat", "lon", "parameter", "units", "value"]


def fila(dt, value, parameter="pm25", units="µg/m³"):
    return {"location_id": "10641", "sensors_id": "35387", "location": "MED-UNNV - Medellín, X-10641",
            "datetime": dt, "lat": "6.26", "lon": "-75.57", "parameter": parameter, "units": units,
            "value": str(value)}


def test_desfase_horario_se_respeta_y_pasa_a_utc():
    salida, r = co.convertir([fila("2020-02-16T01:00:00-01:00", 17.0),
                              fila("2019-01-01T23:30:00-05:00", 9.0),
                              fila("2019-01-02T10:00:00Z", 8.0)])
    horas = [x["measured_at"] for x in salida]
    assert horas == [datetime(2019, 1, 2, 4, 30, tzinfo=timezone.utc),
                     datetime(2019, 1, 2, 10, 0, tzinfo=timezone.utc),
                     datetime(2020, 2, 16, 2, 0, tzinfo=timezone.utc)]
    assert r["primera"] == horas[0] and r["ultima"] == horas[-1]
    # sin desfase no se adivina la zona: invalida
    assert co.convertir([fila("2020-02-16T01:00:00", 17.0)])[1]["invalidas"] == 1


def test_descarta_menos_9999_y_otros_parametros():
    salida, r = co.convertir([fila("2020-03-16T01:00:00-01:00", -9999.0),
                              fila("2020-03-16T02:00:00-01:00", -999),
                              fila("2020-03-16T03:00:00-01:00", 0.03, parameter="o3", units="ppm"),
                              fila("2020-03-16T04:00:00-01:00", 12.0)])
    assert (r["leidas"], r["leidas_pm25"], r["otros_parametros"], r["sin_dato"], r["finales"]) == (4, 3, 1, 2, 1)
    assert salida[0]["pm25"] == 12.0


def test_duplicados_por_hora_utc_conserva_el_primero():
    # 01:00-01:00 y 02:00+00:00 son el mismo instante
    salida, r = co.convertir([fila("2019-05-01T01:00:00-01:00", 20.0),
                              fila("2019-05-01T02:00:00+00:00", 21.0),
                              fila("2019-05-01T01:00:00-01:00", 20.0),
                              fila("2019-05-01T03:00:00-01:00", 22.0)])
    assert (r["duplicados"], r["duplicados_con_otro_valor"], r["finales"]) == (2, 1, 2)
    assert salida[0]["pm25"] == 20.0


def test_rango_de_la_migracion_006():
    salida, r = co.convertir([fila("2019-05-01T01:00:00-01:00", 0),
                              fila("2019-05-01T02:00:00-01:00", 1000),
                              fila("2019-05-01T03:00:00-01:00", -0.5),
                              fila("2019-05-01T04:00:00-01:00", 1000.1),
                              fila("2019-05-01T05:00:00-01:00", "abc"),
                              fila("2019-05-01T06:00:00-01:00", 5, units="ppm")])
    assert [x["pm25"] for x in salida] == [0.0, 1000.0]
    assert (r["fuera_de_rango"], r["invalidas"]) == (2, 2)


def test_licencia_por_defecto_y_campos_fijos():
    salida, _ = co.convertir([fila("2019-05-01T01:00:00-01:00", 12.34)])
    x = salida[0]
    assert x["license"] == "PENDIENTE DE VERIFICAR" == co.LICENCIA_POR_DEFECTO
    assert (x["source"], x["station"], x["city"]) == ("OpenAQ archive", "MED-UNNV El Volador (OpenAQ 10641)", "Medellín")
    assert x["pm25"] == 12.3
    assert co.convertir([fila("2019-05-01T01:00:00-01:00", 1)], licencia="CC BY 4.0")[0][0]["license"] == "CC BY 4.0"
    with pytest.raises(ValueError, match="licencia"):
        co.convertir([], licencia="  ")


def test_filas_por_mes():
    _, r = co.convertir([fila("2019-01-31T23:00:00-01:00", 1),   # 2019-02-01 00:00 UTC
                         fila("2019-01-15T10:00:00-01:00", 2),
                         fila("2019-02-10T10:00:00-01:00", 3)])
    assert r["por_mes"] == {"2019-01": 1, "2019-02": 2}


def test_salida_no_puede_ir_en_el_repo_fuera_de_datos_externos(tmp_path):
    with pytest.raises(ValueError, match="datos_externos"):
        co.comprobar_salida(co.RAIZ / "tools" / "x.csv")
    assert co.comprobar_salida(co.RAIZ / "datos_externos" / "x.csv")
    assert co.comprobar_salida(tmp_path / "x.csv")


def test_de_punta_a_punta_y_compatible_con_load_external(tmp_path, capsys):
    entrada = tmp_path / "openaq" / "year=2019" / "month=05"
    entrada.mkdir(parents=True)
    with gzip.open(entrada / "location-10641-20190501.csv.gz", "wt", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=CAMPOS)
        w.writeheader()
        for f in [fila("2019-05-01T01:00:00-01:00", 17.0), fila("2019-05-01T02:00:00-01:00", -9999.0),
                  fila("2019-05-01T01:00:00-01:00", 17.0), fila("2019-05-01T03:00:00-01:00", 0.02, "no2", "ppm")]:
            w.writerow(f)
    salida = tmp_path / "salida.csv"
    assert co.main([str(tmp_path / "openaq"), "--salida", str(salida)]) == 0
    texto = capsys.readouterr().out
    for esperado in ("Filas leidas: 4", "Descartadas por -9999 (sin dato): 1", "Duplicados (misma hora UTC): 1",
                     "Filas finales: 1", "2019-05: 1"):
        assert esperado in texto, esperado
    # El CSV de salida lo acepta db/load_external.py tal cual (solo validacion, sin base)
    cfg = {"source": co.SOURCE, "license": co.LICENCIA_POR_DEFECTO, "station": None, "city": None,
           "cols": {"time": "measured_at", "pm25": "pm25", "pm10": None, "temperature": None, "humidity": None,
                    "station": "station", "city": "city"}, "time_format": None, "offset": None}
    with open(salida, encoding="utf-8", newline="") as h:
        filas, r = le.procesar_csv(h, cfg)
    assert (r["validas"], r["invalidas"]) == (1, 0)
    assert filas[0]["measured_at"] == datetime(2019, 5, 1, 2, 0, tzinfo=timezone.utc)
    assert filas[0]["city"] == "Medellín" and filas[0]["pm10"] is None
