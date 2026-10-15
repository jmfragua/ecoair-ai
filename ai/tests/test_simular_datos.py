import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simular_datos import EVENT_TYPES, RANGES, generar, main  # noqa: E402

END = "2026-10-14T12:00:00Z"


@pytest.fixture(scope="module")
def datos():
    return generar(seed=42, end=END)


def test_misma_semilla_mismo_resultado(datos):
    df2, ev2 = generar(seed=42, end=END)
    pd.testing.assert_frame_equal(datos[0], df2)
    pd.testing.assert_frame_equal(datos[1], ev2)


def test_valores_en_rango(datos):
    df, _ = datos
    for col, (lo, hi) in RANGES.items():
        assert df[col].between(lo, hi).all(), col
        assert df[col].notna().all(), col


def test_pm10_mayor_o_igual_pm25(datos):
    df, _ = datos
    assert (df.pm10 >= df.pm25).all()


def test_todos_los_tipos_de_evento(datos):
    _, ev = datos
    for t in EVENT_TYPES:
        assert (ev.event_type == t).sum() >= 1, t


def test_csv_existen(tmp_path):
    main(["--seed", "42", "--end", END, "--out-dir", str(tmp_path)])
    assert (tmp_path / "sim_measurements.csv").is_file()
    assert (tmp_path / "sim_events.csv").is_file()


def test_anomalia_contextual(datos):
    df, ev = datos
    ctx = ev[ev.event_type == "anomalia_contextual"]
    assert 4 <= len(ctx) <= 6
    assert ctx.description.str.contains("caida de humedad").sum() == 2
    for e in ctx.itertuples():
        assert 30 <= (e.end_utc - e.start_utc).total_seconds() / 60 <= 90
        w = df[(df.received_at >= e.start_utc) & (df.received_at < e.end_utc)]
        assert len(w) > 0
        assert w.pm25.between(20, 35).all()  # siempre bajo el umbral de 37
        local = w.received_at - pd.Timedelta(hours=5)
        assert (local.dt.hour < 5).all()  # madrugada, hora Colombia


def test_huecos_sin_filas(datos):
    df, ev = datos
    gaps = ev[ev.event_type == "hueco_de_datos"]
    assert len(gaps) >= 1
    for g in gaps.itertuples():
        inside = (df.received_at >= g.start_utc) & (df.received_at < g.end_utc)
        assert not inside.any()
