import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reglas_alertas import (  # noqa: E402
    EstadoRegla,
    actualizar,
    condicion_caido,
    condiciones_sensor,
    condiciones_umbral,
    max_severidad,
    severidad_ica,
    severidad_tabla4,
)
from calidad_aire import construir_respuesta  # noqa: E402

AHORA = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
VENTANAS = {"sensor_congelado": 45, "salto_temperatura": 15, "fuera_de_rango_fisico": 15}


def _serie(estado, cumplimientos, abrir=2, cerrar=3):
    return [actualizar(estado, c, AHORA + timedelta(minutes=i), abrir, cerrar)
            for i, c in enumerate(cumplimientos)]


# ---- Histeresis ----

def test_abre_en_la_segunda_revision():
    assert _serie(EstadoRegla(), [True, True, True]) == [None, "abrir", "mantener"]


def test_un_si_suelto_no_abre():
    assert _serie(EstadoRegla(), [True, False, True, False]) == [None, None, None, None]


def test_no_parpadea_y_cierra_en_la_tercera():
    acciones = _serie(EstadoRegla(), [True, True, False, True, False, False, False])
    assert acciones == [None, "abrir", None, "mantener", None, None, "cerrar"]


def test_started_at_es_la_primera_revision_que_se_cumplio():
    e = EstadoRegla()
    _serie(e, [False, True, True])
    assert e.primera_vez == AHORA + timedelta(minutes=1)


def test_al_cerrar_conserva_el_id_para_el_llamador():
    e = EstadoRegla(abierta=True, alert_id=7)
    assert _serie(e, [False, False, False]) == [None, None, "cerrar"]
    assert e.alert_id == 7 and not e.abierta


# ---- Severidad ----

@pytest.mark.parametrize("cat, sev", [
    ("buena", None), ("aceptable", None), ("danina_sensibles", "aviso"),
    ("danina", "critica"), ("muy_danina", "critica"), ("peligrosa", "critica"),
])
def test_severidad_ica(cat, sev):
    assert severidad_ica(cat) == sev


@pytest.mark.parametrize("nivel, sev", [
    (None, None), ("prevencion", "aviso"), ("alerta", "critica"), ("emergencia", "critica"),
])
def test_severidad_tabla4(nivel, sev):
    assert severidad_tabla4(nivel) == sev


def test_max_severidad():
    assert max_severidad("aviso", "critica") == "critica"
    assert max_severidad("critica", "aviso") == "critica"
    assert max_severidad(None, "aviso") == "aviso"


# ---- Umbral / norma ----

def _resp(pm25_1h, pm10_1h, pm25_24h, pm10_24h, minutos_24h=24 * 60):
    v1 = {"pm25_media": pm25_1h, "pm10_media": pm10_1h, "muestras": 720, "minutos_con_datos": 60}
    v24 = {"pm25_media": pm25_24h, "pm10_media": pm10_24h, "muestras": 17280,
           "minutos_con_datos": minutos_24h}
    ultima = {"received_at": AHORA - timedelta(seconds=5), "temperature": 22.0, "humidity": 70.0}
    return construir_respuesta("ecoair-node-01", v1, v24, ultima, AHORA)


def test_ica_danina_sensibles_es_aviso_con_recomendacion_y_fuente():
    conds = {c.rule_code: c for c in condiciones_umbral(_resp(45, 40, 20, 30), AHORA)}
    c = conds["ica_1h"]
    assert c.kind == "threshold" and c.severity == "aviso"
    assert "indicativo" in c.message and "Res. 2254/2017" in c.message
    assert "Recomendación:" in c.message
    assert "2026-10-15 12:00 UTC (07:00 hora Colombia)" in c.message  # UTC-5
    assert c.details["indicativo"] is True


def test_ica_aceptable_no_alerta():
    assert condiciones_umbral(_resp(25, 40, 20, 30), AHORA) == []


def test_tabla4_solo_si_la_ventana_no_es_parcial():
    completa = {c.rule_code: c for c in condiciones_umbral(_resp(10, 20, 60, 30), AHORA)}
    assert completa["tabla4_pm25"].severity == "critica"  # 60 -> alerta
    parcial = condiciones_umbral(_resp(10, 20, 60, 30, minutos_24h=17 * 60), AHORA)
    assert not any(c.rule_code.startswith("tabla4") for c in parcial)


def test_sin_datos_no_genera_umbral():
    v = {"pm25_media": 50, "pm10_media": 50, "muestras": 1, "minutos_con_datos": 1}
    ultima = {"received_at": AHORA - timedelta(minutes=10), "temperature": 22.0, "humidity": 70.0}
    resp = construir_respuesta("x", v, v, ultima, AHORA)
    assert condiciones_umbral(resp, AHORA) == []


# ---- Dispositivo caido ----

@pytest.mark.parametrize("ultima_hace, caido", [
    (timedelta(minutes=4), False),
    (timedelta(minutes=5), False),   # exactamente 5 min: no es "mas de 5" (igual que la API)
    (timedelta(minutes=5, seconds=1), True),
    (timedelta(minutes=6), True),
    (timedelta(hours=3), True),
])
def test_dispositivo_caido(ultima_hace, caido):
    c = condicion_caido(AHORA - ultima_hace, AHORA)
    assert (c is not None) == caido
    if caido:
        assert c.kind == "device_down" and c.severity == "critica"
        assert "Sin datos" in c.message


def test_sin_ninguna_medicion_no_es_caido():
    assert condicion_caido(None, AHORA) is None


# ---- Sensor, con ventana propia por regla ----

def _df_min(minutos, temperatura=None):
    ts = [AHORA - timedelta(minutes=m) for m in sorted(minutos, reverse=True)]
    n = len(ts)
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(ts, utc=True),
        "temperature": [22 + 0.1 * (i % 3) for i in range(n)],
        "humidity": [70 + 0.2 * (i % 4) for i in range(n)],
        "pm25": [10 + 0.3 * (i % 5) for i in range(n)],
        "pm10": [18 + 0.3 * (i % 7) for i in range(n)],
    })
    if temperatura:
        for hace, valor in temperatura.items():
            df.loc[df.timestamp == pd.Timestamp(AHORA - timedelta(minutes=hace)), "temperature"] = valor
    return df


def test_salto_reciente_alerta_y_salto_viejo_no():
    reciente = condiciones_sensor(_df_min(range(60), {5: 60.0}), AHORA, VENTANAS)
    assert [c.rule_code for c in reciente] == ["salto_temperatura"]
    viejo = condiciones_sensor(_df_min(range(60), {30: 60.0}), AHORA, VENTANAS)  # fuera de 15 min
    assert viejo == []


def test_ventana_de_congelado_debe_cubrir_30_min():
    with pytest.raises(ValueError):
        condiciones_sensor(_df_min(range(60)), AHORA, {**VENTANAS, "sensor_congelado": 20})
