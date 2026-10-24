"""Tests de tools/importar_ideam.py con filas INVENTADAS (sin red ni base de datos)."""
import io
import json
import os
import sys
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importar_ideam as ii  # noqa: E402


def fila(fin, valor="20", est="29509", nombre="TRÁFICO CENTRO", dur="60", unidad="ug/m3", muni="Medellín"):
    return {"estacion_id": est, "nombre_est": nombre, "municipio": muni, "med_concentracion_estandar": valor,
            "med_fecha_inicio": fin.replace("T01", "T00"), "med_fecha_final": fin, "duraci_n": dur,
            "sigla_unidad": unidad, "tipo_estacion": "Fija"}


def test_hora_de_colombia_a_utc_con_el_fin_de_la_hora():
    filas, _ = ii.convertir([fila("2024-03-01T01:00:00.000")])
    assert filas[0]["measured_at"] == datetime(2024, 3, 1, 6, 0, tzinfo=timezone.utc)
    f = filas[0]
    assert (f["source"], f["license"], f["station"], f["city"]) == (
        "IDEAM datos.gov.co", "CC BY-SA 4.0", "TRÁFICO CENTRO (IDEAM 29509)", "Medellín")
    assert (f["pm10"], f["temperature"], f["humidity"]) == (None, None, None)


def test_descartes_por_motivo():
    filas, res = ii.convertir([
        fila("2024-03-01T01:00:00.000", None), fila("2024-03-01T02:00:00.000", ""),
        fila("2024-03-01T03:00:00.000", "-3"), fila("2024-03-01T04:00:00.000", "1000.5"),
        fila("2024-03-01T05:00:00.000", "abc"), fila("2024-03-01T06:00:00.000", "9", dur="1440"),
        fila("2024-03-01T07:00:00.000", "9", unidad="ppm"), fila("2024-03-01T08:00:00.000", "0"),
        fila("2024-03-01T09:00:00.000", "12.36"),
    ])
    c = res["conteos"]
    assert (c["nulos"], c["negativos"], c["fuera_de_rango"], c["no_numericos"], c["no_horaria"], c["unidad"]) == \
        (2, 1, 1, 1, 1, 1)
    assert [f["pm25"] for f in filas] == [0.0, 12.4] and c["validas"] == 2 and c["leidas"] == 9


def test_duplicados_por_estacion_y_hora_y_estaciones_separadas():
    filas, res = ii.convertir([fila("2024-03-01T01:00:00.000", "10"), fila("2024-03-01T01:00:00.000", "11"),
                               fila("2024-03-01T01:00:00.000", "12", est="29493", nombre="CASA JUSTICIA")])
    assert res["conteos"]["duplicadas"] == 1 and len(filas) == 2
    assert len(res["estaciones"]) == 2


def test_filtro_soql_amva_pm25_y_fechas_inclusive():
    w = ii.filtro_soql(date(2024, 3, 1), date(2024, 3, 31), "29509")
    assert "msfl_code='PM2.5'" in w and "nombre_fgda='AMVA'" in w and "estacion_id='29509'" in w
    # formato de texto del IDEAM desde 2026-10-24: 'AAAA-MM-DD HH:MM:SS'
    assert "med_fecha_inicio >= '2024-03-01 00:00:00'" in w and "med_fecha_inicio < '2024-04-01 00:00:00'" in w
    with pytest.raises(ValueError):
        ii.filtro_soql(date(2024, 3, 1), date(2024, 3, 31), "1' OR '1'='1")


def test_paginacion_hasta_la_ultima_pagina():
    datos = [fila(f"2024-03-01T{h:02d}:00:00.000") for h in range(1, 6)]
    pedidas = []

    def abrir(url):
        q = parse_qs(urlparse(url).query)
        pedidas.append((int(q["$limit"][0]), int(q["$offset"][0]), q["$order"][0]))
        o, n = int(q["$offset"][0]), int(q["$limit"][0])
        return io.BytesIO(json.dumps(datos[o:o + n]).encode())

    assert len(list(ii.descargar("x", limite=2, abrir=abrir))) == 5
    assert [(n, o) for n, o, _ in pedidas] == [(2, 0), (2, 2), (2, 4)]
    assert pedidas[0][2] == "estacion_id, med_fecha_inicio, :id"


class ConexionFalsa:
    """Imita la tabla con su UNIQUE (source, station, measured_at)."""
    def __init__(self):
        self.claves = set()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a):
        return type("R", (), {"fetchone": lambda s: (True,)})()

    def transaction(self):
        return self

    def cursor(self):
        con = self

        class Cur:
            rowcount = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def executemany(self, sql, filas):
                antes = len(con.claves)
                con.claves |= {(f[0], f[1], f[3]) for f in filas}
                self.rowcount = len(con.claves) - antes
        return Cur()


def test_importacion_idempotente(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:Secreta@h:1/db")
    datos = [fila(f"2024-03-01T{h:02d}:00:00.000", str(h)) for h in range(1, 4)]
    abrir = lambda url: io.BytesIO(json.dumps(datos if "%24offset=0" in url else []).encode())  # noqa: E731
    con = ConexionFalsa()
    argv = ["--estacion", "29509", "--desde", "2024-03-01", "--hasta", "2024-03-01"]
    assert ii.main(argv, conectar=lambda: con, abrir=abrir) == 0
    assert "Insertadas: 3 · ya existian (omitidas): 0" in capsys.readouterr().out
    assert ii.main(argv, conectar=lambda: con, abrir=abrir) == 0
    assert "Insertadas: 0 · ya existian (omitidas): 3" in capsys.readouterr().out
    assert len(con.claves) == 3


# ---------- --listar, --estaciones y --min-horas ----------

CONTEOS = [
    {"estacion_id": "29493", "nombre_est": "CASA JUSTICIA", "municipio": "Itagüí", "tipo_estacion": "Fija",
     "horas": "40000", "primera": "2020-01-01T00:00:00.000", "ultima": "2024-12-31T23:00:00.000"},
    {"estacion_id": "29509", "nombre_est": "TRÁFICO CENTRO", "municipio": "Medellín", "tipo_estacion": "Fija",
     "horas": "39000", "primera": "2020-01-01T00:00:00.000", "ultima": "2024-12-31T23:00:00.000"},
    {"estacion_id": "9060", "nombre_est": "POLITÉCNICO JIC (S)", "municipio": "Medellín", "tipo_estacion": "Fija",
     "horas": "483", "primera": "2020-01-04T00:00:00.000", "ultima": "2024-12-28T00:00:00.000"},
]


def _abrir_falso(datos_por_estacion, pedidas=None):
    def abrir(url):
        q = parse_qs(urlparse(url).query)
        if pedidas is not None:
            pedidas.append(q)
        if "$group" in q:                     # consulta de conteos (--listar / --min-horas)
            return io.BytesIO(json.dumps(CONTEOS).encode())
        if q["$offset"][0] != "0":
            return io.BytesIO(b"[]")
        w = q["$where"][0]
        filas = [f for est, fs in datos_por_estacion.items() if f"'{est}'" in w for f in fs]
        return io.BytesIO(json.dumps(filas).encode())
    return abrir


def test_codigos_y_filtro_con_varias_estaciones():
    assert ii.codigos_estacion(" 29509, 29493 ,") == ["29509", "29493"]
    w = ii.filtro_soql(date(2020, 1, 1), date(2024, 12, 31), ["29509", "29493"])
    assert "estacion_id in ('29509', '29493')" in w
    assert "duraci_n=60" in ii.filtro_soql(date(2020, 1, 1), date(2024, 12, 31), solo_horarias=True)
    with pytest.raises(ValueError, match="no numericos"):
        ii.codigos_estacion("29509,abc")


def test_seleccionar_por_lista_y_minimo_de_horas():
    lst = ii.contar_por_estacion(date(2020, 1, 1), date(2024, 12, 31), abrir=_abrir_falso({}))
    assert [e["estacion_id"] for e in lst] == ["29493", "29509", "9060"] and lst[0]["horas"] == 40000
    assert [e["estacion_id"] for e in ii.seleccionar(lst, min_horas=30000)] == ["29493", "29509"]
    assert [e["estacion_id"] for e in ii.seleccionar(lst, ["9060", "29509"], 30000)] == ["29509"]
    with pytest.raises(ValueError, match="99999"):
        ii.seleccionar(lst, ["99999"])


def test_tamano_estimado_razonable_e_incluye_los_indices():
    b = ii.bytes_por_fila("TRÁFICO CENTRO (IDEAM 29509)", "Medellín")
    assert 100 < b["heap"] < 200 and b["unico"] > b["pk"] > 0 and b["otros"] > 0
    assert b["total"] == pytest.approx(b["heap"] + b["pk"] + b["unico"] + b["otros"])


def test_listar_no_descarga_mediciones(capsys):
    pedidas = []
    assert ii.main(["--listar"], abrir=_abrir_falso({}, pedidas)) == 0
    out = capsys.readouterr().out
    assert len(pedidas) == 1 and "$group" in pedidas[0]
    assert "CASA JUSTICIA" in out and "MB acum." in out and "Total: 3 estaciones, 79483 horas" in out


def test_min_horas_importa_solo_las_estaciones_que_cumplen(capsys):
    datos = {"29493": [fila("2024-03-01T01:00:00.000", "10", est="29493", nombre="CASA JUSTICIA")],
             "29509": [fila("2024-03-01T01:00:00.000", "12")],
             "9060": [fila("2024-03-01T01:00:00.000", "14", est="9060", nombre="POLITÉCNICO JIC (S)")]}
    pedidas = []
    assert ii.main(["--min-horas", "30000", "--dry-run"], abrir=_abrir_falso(datos, pedidas)) == 0
    out = capsys.readouterr().out
    assert "Estaciones con >= 30000 horas: 2 (29493, 29509)" in out and "Filas leidas: 2" in out
    assert "POLITÉCNICO" not in out.split("Estaciones:")[-1]
    assert "estacion_id in ('29493', '29509')" in pedidas[-1]["$where"][0]


# ---------- Limpieza (tools/limpieza_pm25.py) conectada al importador ----------

def _serie_ideam(valores):
    return [fila(f"2024-03-01T{h + 1:02d}:00:00.000", str(v)) for h, v in enumerate(valores)]


def test_importador_descarta_ceros_y_rachas_antes_de_insertar(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:Secreta@h:1/db")
    # 0 exacto, racha de 6 horas con 20 y valores normales
    datos = _serie_ideam([0, 15, 20, 20, 20, 20, 20, 20, 18, 0.0, 22])
    abrir = lambda url: io.BytesIO(json.dumps(datos if "%24offset=0" in url else []).encode())  # noqa: E731
    con = ConexionFalsa()
    argv = ["--estacion", "29509", "--desde", "2024-03-01", "--hasta", "2024-03-01"]
    assert ii.main(argv, conectar=lambda: con, abrir=abrir) == 0
    out = capsys.readouterr().out
    assert "Filas leidas: 11 · validas: 3" in out
    assert "descartadas por cero exacto (falla del equipo): 2" in out
    assert "rachas de 6+ horas con el mismo valor: 1 (6 horas descartadas)" in out
    assert "TRÁFICO CENTRO (IDEAM 29509) · Medellín · Fija · 3 horas" in out
    assert "Insertadas: 3 · ya existian (omitidas): 0" in out
    assert len(con.claves) == 3


def test_aplicar_limpieza_recalcula_el_resumen_por_estacion():
    filas, res = ii.convertir(_serie_ideam([5, 5, 5, 5, 5, 5, 7, 0]))
    assert res["conteos"]["validas"] == 8      # convertir solo valida formato y rango
    limpias, res = ii.aplicar_limpieza(filas, res)
    assert [f["pm25"] for f in limpias] == [7.0] and res["conteos"]["validas"] == 1
    e = res["estaciones"]["TRÁFICO CENTRO (IDEAM 29509)"]
    assert e["n"] == 1 and e["primera"] == e["ultima"] and e["tipo"] == "Fija"
    assert res["limpieza"]["conteos"]["ceros"] == 1 and res["limpieza"]["conteos"]["rachas"] == 1


def test_estacion_que_queda_vacia_desaparece_del_resumen():
    filas, res = ii.convertir(_serie_ideam([0, 0, 0]))
    limpias, res = ii.aplicar_limpieza(filas, res)
    assert limpias == [] and res["estaciones"] == {}



# ---------- Formato nuevo del IDEAM (2026-10-24): duracion '60.0' y fechas de texto ----------

def test_duracion_60_punto_0_es_horaria():
    assert ii.es_horaria("60") and ii.es_horaria("60.0") and ii.es_horaria(60) and ii.es_horaria(60.0)
    assert not ii.es_horaria("1440") and not ii.es_horaria(None) and not ii.es_horaria("x")
    filas, res = ii.convertir([fila("2024-01-01 01:00:00", "83.0", dur="60.0")])
    assert len(filas) == 1 and res["conteos"].get("no_horaria", 0) == 0
    assert filas[0]["measured_at"] == datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)


def test_filas_fuera_del_periodo_se_descartan_aunque_las_devuelva_la_api():
    # el texto '2025-01-01 00:00:00' < '2025-01-01T00:00:00': con el filtro viejo se colaba el 1 de enero
    datos = [fila("2024-12-31 23:00:00", "10"), fila("2025-01-01 01:00:00", "11"), fila("2019-12-31 23:00:00", "12")]
    datos[0]["med_fecha_inicio"], datos[1]["med_fecha_inicio"], datos[2]["med_fecha_inicio"] = \
        "2024-12-31 22:00:00", "2025-01-01 00:00:00", "2019-12-31 22:00:00"
    filas, res = ii.convertir(datos, date(2020, 1, 1), date(2024, 12, 31))
    assert [f["pm25"] for f in filas] == [10.0] and res["conteos"]["fuera_de_periodo"] == 2



# ---------- Protecciones (iguales a importar_sisaire.py) ----------

def _abrir_tres():
    datos = [fila(f"2024-03-01T{h:02d}:00:00.000", str(h)) for h in range(1, 4)]
    return lambda url: io.BytesIO(json.dumps(datos if "%24offset=0" in url else []).encode())


ARGV = ["--estacion", "29509", "--desde", "2024-03-01", "--hasta", "2024-03-01"]


def test_sin_database_url_se_detiene_sin_conectar(capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    llamado = []
    assert ii.main(ARGV, conectar=lambda: llamado.append(1), abrir=_abrir_tres()) == 2
    assert "falta DATABASE_URL" in capsys.readouterr().out and llamado == []
    # --dry-run no necesita base
    assert ii.main(ARGV + ["--dry-run"], abrir=_abrir_tres()) == 0


def test_muestra_el_destino_antes_de_escribir_sin_credenciales(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:Secreta@shinkansen.proxy.rlwy.net:41234/railway")
    assert ii.main(ARGV, conectar=lambda: ConexionFalsa(), abrir=_abrir_tres()) == 0
    out = capsys.readouterr().out
    assert "Base de destino: shinkansen.proxy.rlwy.net:41234/railway (DATABASE_URL)" in out
    assert out.index("Base de destino") < out.index("Insertadas") and "Secreta" not in out


def test_local_ignora_database_url(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:Secreta@railway.example:1/db")
    monkeypatch.setenv("DB_PORT", "5433")
    assert ii.main(ARGV + ["--local"], conectar=lambda: ConexionFalsa(), abrir=_abrir_tres()) == 0
    out = capsys.readouterr().out
    assert "Base de destino: localhost:5433/ecoair (LOCAL, DB_*)" in out and "railway" not in out


def test_error_de_conexion_seguro(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:Secreta@h:1/db")

    def falla():
        raise OSError("could not connect: postgresql://u:Secreta@h:1/db password=Secreta")
    assert ii.main(ARGV, conectar=falla, abrir=_abrir_tres()) == 2
    out = capsys.readouterr().out
    assert "ERROR de base de datos: OSError" in out and "Secreta" not in out and "postgresql://" not in out
