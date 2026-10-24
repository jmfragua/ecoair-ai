"""Tests de tools/importar_sisaire.py con un CSV INVENTADO (tools/tests/data/sisaire_ejemplo.csv)."""
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import importar_sisaire as isa  # noqa: E402
from test_importar_ideam import ConexionFalsa  # noqa: E402

EJEMPLO = Path(__file__).parent / "data" / "sisaire_ejemplo.csv"
TC = "ESTACIÓN TRÁFICO CENTRO - MEDELLÍN"
FC = "FISCALÍA GENERAL DE LA NACIÓN - SECTOR CARIBE, MEDELLÍN"


def test_hora_de_colombia_a_utc_fin_de_hora_y_formato():
    with open(EJEMPLO, encoding="utf-8-sig", newline="") as f:
        filas, c = isa.leer_csv(f)
    assert c["leidas"] == 15 and c["hora_inconsistente"] == 1 and c["no_numericos"] == 1
    primera = filas[0]
    # 2026-01-01 23:00-23:59 local -> fin de hora 2026-01-02 00:00 local = 05:00 UTC
    assert primera["measured_at"] == datetime(2026, 1, 2, 5, 0, tzinfo=timezone.utc)
    assert (primera["station"], primera["city"], primera["pm25"], primera["anio"]) == (TC, "Medellín", 8.0, 2026)
    assert any(f["pm25"] is None for f in filas)        # celda vacia -> nulo (se descarta en la limpieza)


def test_columnas_obligatorias():
    with pytest.raises(ValueError, match="faltan columnas"):
        isa.leer_csv(io.StringIO('"Estacion","Fecha inicial","PM2.5"\n'))


def test_dry_run_limpieza_y_resumen_por_estacion(capsys):
    assert isa.main(["--archivo", str(EJEMPLO), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Fuente: IDEAM SISAIRE · licencia: IDEAM SISAIRE – citar la fuente, sin alterar, sin uso comercial" in out
    assert "CC BY" not in out
    assert "validas tras limpieza: 3" in out
    for txt in ("vacios: 1", "negativos: 1", "cero exacto (falla del equipo): 1", "> 1000 ug/m3: 1",
                "fecha final != inicial + 59 min: 1", "valor no numerico: 1",
                "rachas de 6+ horas con el mismo valor: 1 (6 horas descartadas)"):
        assert txt in out, txt
    assert f"  {FC}\n    validas 1 · ceros 0 · rachas 1 (6 h)" in out
    assert "% de ceros por año (hora local): 2026: 33.3 %" in out      # TC: 1 cero de 3 horas con valor
    assert "no se conecto a ninguna base" in out


def test_mismo_dia_en_dos_archivos_cuenta_duplicados_y_no_duplica(capsys):
    assert isa.main(["--archivo", str(EJEMPLO), "--archivo", str(EJEMPLO), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "validas tras limpieza: 3" in out and "duplicadas (misma estacion y hora" in out
    # 13 filas legibles por archivo (15 - hora inconsistente - no numerico) -> 13 duplicadas, contadas una vez
    assert "descartadas por duplicadas (misma estacion y hora; p. ej. el mismo dia en dos archivos): 13" in out
    assert "cero exacto (falla del equipo): 1" in out          # el cero repetido no se cuenta dos veces


def test_importacion_idempotente_en_base_falsa(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:Secreta@h:1/db")
    con = ConexionFalsa()
    assert isa.main(["--archivo", str(EJEMPLO)], conectar=lambda: con) == 0
    out = capsys.readouterr().out
    assert "Base de destino: h:1/db (DATABASE_URL)" in out and "Secreta" not in out
    assert "Insertadas: 3 · ya existian (omitidas): 0" in out
    assert isa.main(["--archivo", str(EJEMPLO)], conectar=lambda: con) == 0
    assert "Insertadas: 0 · ya existian (omitidas): 3" in capsys.readouterr().out
    assert len(con.claves) == 3 and {k[0] for k in con.claves} == {"IDEAM SISAIRE"}


def test_sin_database_url_se_detiene_sin_conectar(capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    llamado = []
    assert isa.main(["--archivo", str(EJEMPLO)], conectar=lambda: llamado.append(1)) == 2
    assert "falta DATABASE_URL" in capsys.readouterr().out and llamado == []


def test_local_ignora_database_url():
    dsn, destino = isa.conectar_destino(True, {"DATABASE_URL": "postgresql://u:Secreta@railway:1/db", "DB_PORT": "5433"})
    assert "Secreta" not in dsn and "railway" not in dsn and destino == "localhost:5433/ecoair"
    with pytest.raises(ValueError, match="falta DATABASE_URL"):
        isa.conectar_destino(False, {})


def test_error_de_conexion_seguro(capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:Secreta@h:1/db")

    def falla():
        raise OSError("could not connect: postgresql://u:Secreta@h:1/db password=Secreta")
    assert isa.main(["--archivo", str(EJEMPLO)], conectar=falla) == 2
    out = capsys.readouterr().out
    assert "ERROR de base de datos: OSError" in out and "Secreta" not in out and "postgresql://" not in out



# ---------- --desde / --hasta ----------

def test_desde_hasta_por_dia_local_inclusive(capsys):
    # el ejemplo trae filas del 2025-12-31 (Tomas Carrasquilla) y del 2026-01-01 (las otras dos)
    assert isa.main(["--archivo", str(EJEMPLO), "--dry-run", "--desde", "2026-01-01"]) == 0
    out = capsys.readouterr().out
    assert "Periodo: 2026-01-01 -> sin limite" in out and "fuera de --desde/--hasta: 2" in out
    assert "TOMÁS CARRASQUILLA" not in out.split("Por estacion")[1]
    assert isa.main(["--archivo", str(EJEMPLO), "--dry-run", "--hasta", "2025-12-31"]) == 0
    out = capsys.readouterr().out
    assert "fuera de --desde/--hasta: 11" in out and TC not in out.split("Por estacion")[1]
    assert isa.main(["--archivo", str(EJEMPLO), "--dry-run", "--desde", "2026-01-02", "--hasta", "2026-01-01"]) == 2


def test_sin_limites_no_filtra(capsys):
    assert isa.main(["--archivo", str(EJEMPLO), "--dry-run"]) == 0
    assert "fuera de --desde/--hasta: 0" in capsys.readouterr().out
