import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import migrate  # noqa: E402


def test_orden_y_exclusion_del_borrador():
    nombres = [n for n, _ in migrate.archivos()]
    assert nombres[0] == "schema.sql"
    assert "003_air_levels_borrador.sql" not in nombres
    assert nombres == ["schema.sql", "002_simulated.sql", "004_alerts.sql", "005_subscriptions.sql"]


def test_archivos_ignora_excluidos_en_cualquier_carpeta(tmp_path):
    (tmp_path / "migrations").mkdir()
    (tmp_path / "schema.sql").write_text("-- base")
    for n in ("010_b.sql", "003_air_levels_borrador.sql", "002_a.sql", "notas.txt"):
        (tmp_path / "migrations" / n).write_text("--")
    assert [n for n, _ in migrate.archivos(tmp_path)] == ["schema.sql", "002_a.sql", "010_b.sql"]


def test_planificar_base_vacia_aplica_todo_en_orden():
    locales = [("schema.sql", "h0"), ("002.sql", "h2"), ("004.sql", "h4")]
    assert migrate.planificar(locales, {}) == (locales, [])


def test_planificar_solo_lo_que_falta():
    locales = [("schema.sql", "h0"), ("002.sql", "h2"), ("004.sql", "h4")]
    pendientes, conflictos = migrate.planificar(locales, {"schema.sql": "h0", "002.sql": "h2"})
    assert pendientes == [("004.sql", "h4")] and conflictos == []


def test_planificar_detecta_archivo_modificado():
    locales = [("schema.sql", "h0"), ("002.sql", "OTRO")]
    _, conflictos = migrate.planificar(locales, {"schema.sql": "h0", "002.sql": "h2"})
    assert conflictos == ["002.sql"]


def test_sha256_estable():
    assert migrate.sha256("abc") == migrate.sha256("abc") != migrate.sha256("abd")
