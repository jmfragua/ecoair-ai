"""Logica pura del alerter: que reglas se cumplen y como evoluciona cada alerta.

Solo capas deterministas (no es IA):
  - Umbral y norma (kind=threshold): ICA de la media de 1 h (indicativo) y nivel de la
    Tabla 4 con la media de 24 h (solo si la ventana de 24 h no es parcial).
  - Salud del sensor (kind=sensor) con ai/reglas_sensor.py, ventana propia por regla.
  - Dispositivo caido (kind=device_down): ultima medicion con mas de CAIDO_MIN (5) minutos.

Sin base de datos: alerter.py hace las consultas y llama a estas funciones.
"""

import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

# En desarrollo los modulos reutilizados estan en api/ y ai/; en Docker se copian a /app.
_RAIZ = Path(__file__).resolve().parent.parent
for _extra in (_RAIZ / "api", _RAIZ / "ai"):
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import pandas as pd  # noqa: E402

from calidad_aire import FUENTE  # noqa: E402
from recomendaciones import recomendacion  # noqa: E402
from reglas_sensor import CAIDO_MIN, CONGELADO_MIN, evaluar_reglas  # noqa: E402

COLOMBIA = timedelta(hours=5)
SEVERIDAD_ICA = {
    "danina_sensibles": "aviso",
    "danina": "critica",
    "muy_danina": "critica",
    "peligrosa": "critica",
}
SEVERIDAD_TABLA4 = {"prevencion": "aviso", "alerta": "critica", "emergencia": "critica"}
RANGO_SEVERIDAD = ["info", "aviso", "critica"]
REGLAS_SENSOR = ["sensor_congelado", "salto_temperatura", "fuera_de_rango_fisico"]
# isolation_forest: capa experimental opcional (alerter/anomalias.py, ANOMALIAS_ACTIVAS)
REGLAS = ["ica_1h", "tabla4_pm25", "tabla4_pm10", *REGLAS_SENSOR, "dispositivo_caido", "isolation_forest"]
NOMBRE_PM = {"pm25": "PM2.5", "pm10": "PM10"}
NOMBRE_NIVEL = {"prevencion": "prevención", "alerta": "alerta", "emergencia": "emergencia"}
FUENTE_SENSOR = "regla de salud del sensor del proyecto EcoAir (no es una norma)"


@dataclass
class Condicion:
    rule_code: str
    kind: str
    severity: str
    message: str
    details: dict = field(default_factory=dict)


def _hora(ts):
    """'2026-10-15 14:03 UTC (09:03 hora Colombia)'."""
    ts = pd.Timestamp(ts)
    return f"{ts:%Y-%m-%d %H:%M} UTC ({ts - COLOMBIA:%H:%M} hora Colombia)"


def severidad_ica(category_code):
    """Categoria del ICA -> severidad, o None si no genera alerta."""
    return SEVERIDAD_ICA.get(category_code)


def severidad_tabla4(nivel):
    """Nivel de la Tabla 4 -> severidad, o None si no genera alerta."""
    return SEVERIDAD_TABLA4.get(nivel)


def max_severidad(a, b):
    return max(a, b, key=RANGO_SEVERIDAD.index) if a and b else (a or b)


def condiciones_umbral(resp, ahora):
    """Condiciones de umbral/norma a partir de calidad_aire.construir_respuesta."""
    if resp.get("estado") != "ok":
        return []
    out = []
    general = resp["ica"]["general"]
    sev = severidad_ica(general["category_code"])
    if sev:
        rec = recomendacion(general["category_code"])
        out.append(Condicion(
            rule_code="ica_1h", kind="threshold", severity=sev,
            message=(
                f"ICA {general['ica']} ({general['category_name']}), definido por "
                f"{NOMBRE_PM[general['definido_por']]}, con la media de la última hora. Es "
                f"indicativo: la {FUENTE} define el ICA con promedios de 24 h. {_hora(ahora)}. "
                f"Fuente: {FUENTE}. Recomendación: {rec['recomendacion']}"
            ),
            details={"ica": general, "ventana_1h": resp["ventana_1h"], "indicativo": True,
                     "recomendacion": rec},
        ))
    norma = resp["norma_24h"]
    if not norma["parcial"]:
        for p, nivel in norma["nivel_alerta_por_contaminante"].items():
            sev = severidad_tabla4(nivel)
            if not sev:
                continue
            media = resp["ventana_24h"][f"{p}_media"]
            out.append(Condicion(
                rule_code=f"tabla4_{p}", kind="threshold", severity=sev,
                message=(
                    f"Nivel de {NOMBRE_NIVEL[nivel]} por {NOMBRE_PM[p]} (Tabla 4): media de 24 h de "
                    f"{media} µg/m³ con {resp['ventana_24h']['horas_con_datos']} h de datos. "
                    f"{_hora(ahora)}. Fuente: {FUENTE}."
                ),
                details={"nivel": nivel, "pollutant": p, "media_24h": media,
                         "ventana_24h": resp["ventana_24h"]},
            ))
    return out


def condiciones_sensor(df_min, ahora, ventanas_min):
    """Reglas de salud del sensor, cada una sobre su propia ventana (minutos hasta `ahora`).

    df_min: salida de features.promediar_por_minuto (timestamp + 4 variables).
    """
    if ventanas_min["sensor_congelado"] < CONGELADO_MIN:
        raise ValueError(f"La ventana de sensor_congelado debe ser >= {CONGELADO_MIN} min")
    ahora = pd.Timestamp(ahora)
    out = []
    for regla in REGLAS_SENSOR:
        desde = ahora - pd.Timedelta(minutes=ventanas_min[regla])
        sub = df_min[(df_min["timestamp"] >= desde) & (df_min["timestamp"] <= ahora)]
        if sub.empty:
            continue
        det = evaluar_reglas(sub)
        det = det[det["tipo"] == regla]
        if det.empty:
            continue
        ultima = det.iloc[-1]
        out.append(Condicion(
            rule_code=regla, kind="sensor", severity="aviso",
            message=f"{ultima['mensaje']} {_hora(ultima['inicio'])}. Fuente: {FUENTE_SENSOR}.",
            details={"inicio": str(ultima["inicio"]), "fin": str(ultima["fin"]),
                     "ventana_min": ventanas_min[regla], "detecciones": int(len(det))},
        ))
    return out


def condicion_caido(ultima_ts, ahora):
    """Dispositivo caido: la ultima medicion tiene mas de CAIDO_MIN (5) minutos.

    Usa el umbral CAIDO_MIN de reglas_sensor, pero mide desde la hora exacta de la ultima
    medicion (no desde el fin de su minuto, como hace reglas_sensor con datos por minuto), para
    coincidir con el estado "sin_datos" del endpoint de calidad del aire (> 5 min).
    Solo cuenta el hueco actual (desde la ultima medicion hasta ahora), no huecos ya recuperados.
    """
    if ultima_ts is None:
        return None
    sin_datos = pd.Timestamp(ahora) - pd.Timestamp(ultima_ts)
    if sin_datos <= pd.Timedelta(minutes=CAIDO_MIN):
        return None
    minutos = int(sin_datos.total_seconds() // 60)
    return Condicion(
        rule_code="dispositivo_caido", kind="device_down", severity="critica",
        message=(f"Sin datos durante {minutos} min. Posible dispositivo caído o sin conexión. "
                 f"Última medición: {_hora(ultima_ts)}. Fuente: {FUENTE_SENSOR}."),
        details={"ultima_medicion": str(pd.Timestamp(ultima_ts)), "minutos_sin_datos": minutos},
    )


ETIQUETA_SIMULACION = "[SIMULACIÓN] "


def etiquetar_simulacion(cond, simulado):
    """Si los datos del dispositivo son simulados, lo dice en el mensaje y en details.

    Nunca se presenta una lectura simulada como si fuera del sensor real.
    """
    if not simulado:
        return cond
    msg = cond.message if cond.message.startswith(ETIQUETA_SIMULACION) else ETIQUETA_SIMULACION + cond.message
    return Condicion(cond.rule_code, cond.kind, cond.severity, msg, {**cond.details, "simulado": True})


@dataclass
class EstadoRegla:
    """Histeresis de una regla de un dispositivo."""
    seguidas_si: int = 0
    seguidas_no: int = 0
    abierta: bool = False
    alert_id: int | None = None
    severity: str | None = None
    primera_vez: object = None  # hora de la primera revision seguida que se cumplio


def actualizar(estado, se_cumple, ahora, abrir_tras, cerrar_tras):
    """Avanza la histeresis una revision. Devuelve "abrir", "mantener", "cerrar" o None.

    - Abre cuando la condicion se cumple `abrir_tras` revisiones seguidas.
    - Mientras esta abierta y se cumple: "mantener" (actualizar last_seen_at).
    - Cierra cuando deja de cumplirse `cerrar_tras` revisiones seguidas. Un "no" suelto
      entre dos "si" no la cierra (sin parpadeo).
    """
    if se_cumple:
        if estado.seguidas_si == 0:
            estado.primera_vez = ahora
        estado.seguidas_si += 1
        estado.seguidas_no = 0
        if estado.abierta:
            return "mantener"
        if estado.seguidas_si >= abrir_tras:
            estado.abierta = True
            return "abrir"
        return None
    estado.seguidas_no += 1
    estado.seguidas_si = 0
    estado.primera_vez = None
    if estado.abierta and estado.seguidas_no >= cerrar_tras:
        estado.abierta = False  # alert_id se conserva para que el llamador cierre esa fila
        return "cerrar"
    return None
