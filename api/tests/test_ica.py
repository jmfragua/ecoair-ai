import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ica import calcular_ica, ica_general, nivel_alerta  # noqa: E402


@pytest.mark.parametrize(
    "pollutant, conc, ica, category",
    [
        ("pm25", 0, 0, "buena"),
        ("pm25", 12, 50, "buena"),
        ("pm25", 12.5, 50, "buena"),  # truncado a 12
        ("pm25", 25, 76, "aceptable"),
        ("pm25", 45, 121, "danina_sensibles"),
        ("pm10", 100, 73, "aceptable"),
        ("pm25", 700, 500, "peligrosa"),
    ],
)
def test_calcular_ica(pollutant, conc, ica, category):
    r = calcular_ica(pollutant, conc)
    assert r["ica"] == ica
    assert r["category_code"] == category


def test_concentracion_negativa():
    with pytest.raises(ValueError):
        calcular_ica("pm25", -1)


@pytest.mark.parametrize(
    "pollutant, conc, nivel",
    [
        ("pm25", 37, None),
        ("pm25", 38, "prevencion"),
        ("pm25", 56, "alerta"),
        ("pm25", 151, "emergencia"),
        ("pm10", 154, None),
        ("pm10", 155, "prevencion"),
        ("pm10", 355, "emergencia"),
    ],
)
def test_nivel_alerta(pollutant, conc, nivel):
    assert nivel_alerta(pollutant, conc) == nivel


def test_ica_general():
    r = ica_general(pm25=45, pm10=100)
    assert r["ica"] == 121
    assert r["pollutant"] == "pm25"
