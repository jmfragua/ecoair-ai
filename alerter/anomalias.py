"""Capa EXPERIMENTAL de anomalias con Isolation Forest dentro del alerter.

- Desactivada por defecto (ANOMALIAS_ACTIVAS=false). scikit-learn y joblib solo se importan si se
  activa, para no gastar memoria cuando esta apagada.
- Modelo: modelos/anomalias_sim_if_v2_A.joblib + .json (procedencia). Se comprueba el SHA-256.
- Alcance: el modelo se entreno SOLO con datos simulados, asi que solo se aplica a dispositivos
  simulados (lo dice su procedencia: solo_dispositivos_simulados). Para el nodo real hay que
  reentrenar con datos reales.
- Resultado: alerta kind='anomaly', rule_code='isolation_forest', severidad 'info', con la variable
  que mas contribuyo y la procedencia del modelo. No es una regla de la norma.
"""

import hashlib
import json
import logging
from pathlib import Path

import pandas as pd

from reglas_alertas import Condicion

log = logging.getLogger("alerter")

RULE_CODE = "isolation_forest"
NOMBRES = {"temperature": "temperatura", "humidity": "humedad", "pm25": "PM2.5", "pm10": "PM10"}


def ruta_por_defecto():
    """modelos/ en la raiz del repo (desarrollo) o junto al codigo (/app/modelos en Docker)."""
    aqui = Path(__file__).resolve().parent
    for base in (aqui.parent, aqui):
        p = base / "modelos" / "anomalias_sim_if_v2_A.joblib"
        if p.is_file():
            return p
    return aqui.parent / "modelos" / "anomalias_sim_if_v2_A.joblib"


def variable_principal(row, modelo):
    """(feature, desviacion) de la feature mas alejada de su mediana (MAD con piso)."""
    from scoring import desviaciones

    z = desviaciones(row, modelo)
    top = z.abs().idxmax()
    return top, float(z[top])


def texto_variable(feature):
    for v, nombre in NOMBRES.items():
        if feature == v or feature.startswith(v + "_"):
            return f"{nombre} ({feature})"
    return feature


def condicion_anomalia(row, modelo, procedencia):
    """Condicion de alerta para una fila marcada como anomala por el modelo."""
    from scoring import explicar

    feature, z = variable_principal(row, modelo)
    fuentes = ", ".join(f["tipo"] for f in procedencia.get("entrenado_con", [])) or "desconocidas"
    msg = (f"Lectura inusual según el modelo (Isolation Forest, EXPERIMENTAL; no es una regla de la norma). "
           f"{explicar(row, modelo)} Variable que más contribuyó: {texto_variable(feature)}. "
           f"Modelo {procedencia.get('nombre')}, entrenado con datos {fuentes}.")
    return Condicion(
        rule_code=RULE_CODE, kind="anomaly", severity="info", message=msg,
        details={"experimental": True, "score": round(float(row["score"]), 4),
                 "variable_principal": feature, "desviacion": round(z, 2),
                 "modelo": procedencia.get("nombre"), "sha256": procedencia.get("sha256"),
                 "entrenado_con": procedencia.get("entrenado_con"),
                 "limites": procedencia.get("limites")},
    )


class DetectorAnomalias:
    """Carga el modelo una vez y evalua la ultima medicion de un dispositivo."""

    def __init__(self, ruta_modelo=None):
        import joblib  # solo si la capa esta activa

        self.ruta = Path(ruta_modelo or ruta_por_defecto())
        self.procedencia = json.loads(self.ruta.with_suffix(".json").read_text(encoding="utf-8"))
        real = hashlib.sha256(self.ruta.read_bytes()).hexdigest()
        if real != self.procedencia["sha256"]:
            raise ValueError(f"SHA-256 de {self.ruta.name} no coincide con su procedencia")
        self.modelo = joblib.load(self.ruta)
        self.solo_simulados = bool(self.procedencia.get("solo_dispositivos_simulados", True))

    def aplica_a(self, es_simulado):
        return es_simulado or not self.solo_simulados

    def evaluar(self, filas, ahora, max_antiguedad_min=5):
        """filas: dicts con timestamp, temperature, humidity, pm25, pm10 (ultimos ~60 min).

        Devuelve una Condicion si el minuto mas reciente es anomalo, o None.
        """
        from features import construir_features
        from scoring import puntuar_features

        if not filas:
            return None
        df = pd.DataFrame(filas)
        for col in ("temperature", "humidity", "pm25", "pm10"):
            df[col] = df[col].astype(float)
        feats, _ = construir_features(df)
        if feats.empty:
            return None
        scored = puntuar_features(feats, self.modelo)
        ultima = scored.iloc[-1]
        if (pd.Timestamp(ahora) - ultima["timestamp"]).total_seconds() > max_antiguedad_min * 60:
            return None
        if not bool(ultima["es_anomalia"]):
            return None
        return condicion_anomalia(ultima, self.modelo, self.procedencia)
