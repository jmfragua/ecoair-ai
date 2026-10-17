import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI.parent.parent / "tools"))

import alerter  # noqa: E402
import anomalias  # noqa: E402
from simular_estacion import generar  # noqa: E402

AHORA = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


def _filas(ultimos_pm25=None):
    """60 min de datos simulados normales; opcionalmente los ultimos minutos con otro PM2.5."""
    filas = generar(AHORA - timedelta(minutes=59), AHORA, semilla=11)
    out = [{"timestamp": f["received_at"], **{k: f[k] for k in ("temperature", "humidity", "pm25", "pm10")}}
           for f in filas]
    if ultimos_pm25:
        for f, v in zip(out[-len(ultimos_pm25):], ultimos_pm25):
            f["pm25"], f["pm10"] = v, v * 1.6
    return out


@pytest.fixture(scope="module")
def detector():
    return anomalias.DetectorAnomalias()


def test_modelo_versionado_con_procedencia_y_hash(detector):
    p = detector.procedencia
    assert p["experimental"] is True and p["solo_dispositivos_simulados"] is True
    assert [f["tipo"] for f in p["entrenado_con"]] == ["simulados"]
    assert any("NO mejoro a las reglas" in lim for lim in p["limites"])
    assert detector.modelo["features"] == p["features"]


def test_solo_se_aplica_a_dispositivos_simulados(detector):
    assert detector.aplica_a(True) is True
    assert detector.aplica_a(False) is False


def test_datos_normales_no_dan_anomalia(detector):
    assert detector.evaluar(_filas(), AHORA) is None


def test_lectura_absurda_da_anomalia_interpretable(detector):
    c = detector.evaluar(_filas(ultimos_pm25=[600.0, 650.0, 700.0]), AHORA)
    assert c is not None
    assert (c.kind, c.severity, c.rule_code) == ("anomaly", "info", "isolation_forest")
    assert "EXPERIMENTAL" in c.message and "no es una regla de la norma" in c.message
    assert "entrenado con datos simulados" in c.message
    assert c.details["variable_principal"].startswith("pm") and c.details["experimental"] is True
    assert c.details["modelo"] == "anomalias_sim_if_v2_A" and len(c.details["sha256"]) == 64


def test_datos_viejos_no_se_evaluan(detector):
    assert detector.evaluar(_filas(ultimos_pm25=[700.0] * 3), AHORA + timedelta(minutes=30)) is None
    assert detector.evaluar([], AHORA) is None


def test_hash_distinto_rechaza_el_modelo(tmp_path):
    origen = anomalias.ruta_por_defecto()
    copia = tmp_path / origen.name
    shutil.copyfile(origen, copia)
    meta = json.loads(origen.with_suffix(".json").read_text())
    meta["sha256"] = "0" * 64
    copia.with_suffix(".json").write_text(json.dumps(meta))
    with pytest.raises(ValueError, match="SHA-256"):
        anomalias.DetectorAnomalias(copia)


# ---------- Integracion con el alerter ----------

def test_apagada_por_defecto_no_importa_scikit_learn():
    codigo = ("import os, sys; os.environ.pop('ANOMALIAS_ACTIVAS', None); import alerter; "
              "print(alerter.obtener_detector() is None, 'sklearn' in sys.modules)")
    r = subprocess.run([sys.executable, "-c", codigo], cwd=AQUI.parent, capture_output=True, text=True, check=True)
    assert r.stdout.split() == ["True", "False"]


def test_modelo_ausente_desactiva_la_capa_sin_romper(monkeypatch, caplog):
    monkeypatch.setattr(alerter.config, "ANOMALIAS_ACTIVAS", True)
    monkeypatch.setattr(alerter.config, "ANOMALIAS_MODELO_PATH", "/no/existe.joblib")
    monkeypatch.setattr(alerter, "_detector", None)
    monkeypatch.setattr(alerter, "_detector_fallo", False)
    with caplog.at_level(logging.ERROR, logger="alerter"):
        assert alerter.obtener_detector() is None
    assert "Se desactiva la capa" in caplog.text


def test_dispositivo_real_no_se_evalua(detector):
    class ConexionQueNoDebeUsarse:
        def execute(self, *a, **k):
            raise AssertionError("no debe consultar datos de un dispositivo real")
    assert alerter.condicion_anomalia_de(ConexionQueNoDebeUsarse(), "ecoair-node-01", AHORA, False, detector) is None
