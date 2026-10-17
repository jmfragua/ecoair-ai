"""Servicio de alertas EcoAir: cada INTERVALO_S revisa los dispositivos y abre/cierra alertas.

Solo reglas deterministas (umbral/norma y salud del sensor); el Isolation Forest no esta aqui.
Requiere las migraciones 002 (measurements.is_simulated) y 004 (columnas nuevas de alerts).
Si faltan, lo registra y reintenta en la siguiente revision sin morirse.

Los contadores de histeresis viven en memoria. Al arrancar se cargan las alertas abiertas de
la base para poder cerrarlas; tras un reinicio, abrir una alerta nueva tarda como mucho
REVISIONES_ABRIR revisiones mas.
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import config
from reglas_alertas import (
    REGLAS,
    EstadoRegla,
    actualizar,
    condicion_caido,
    condiciones_sensor,
    condiciones_umbral,
    etiquetar_simulacion,
    max_severidad,
)
from calidad_aire import SQL_ULTIMA, SQL_VENTANAS, construir_respuesta, ventanas_desde_fila  # noqa: E402
from features import promediar_por_minuto  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("alerter")

estados = defaultdict(EstadoRegla)  # (device_id, rule_code) -> EstadoRegla
db = None


def get_db():
    global db
    if db is None or db.closed:
        db = psycopg.connect(config.DB_DSN, autocommit=True, row_factory=dict_row)
        log.info("Conectado a PostgreSQL")
        cargar_abiertas(db)
    return db


def reset_db():
    global db
    if db is not None:
        try:
            db.close()
        except psycopg.Error:
            pass
    db = None


def migraciones_ok(conn):
    rows = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE (table_name = 'measurements' AND column_name = 'is_simulated') "
        "   OR (table_name = 'alerts' AND column_name IN ('rule_code', 'ended_at', 'last_seen_at'))"
    ).fetchall()
    return len(rows) == 4


def cargar_abiertas(conn):
    """Recupera las alertas abiertas para poder mantenerlas o cerrarlas tras un reinicio."""
    if not migraciones_ok(conn):
        return
    for r in conn.execute(
        "SELECT id, device_id, rule_code, severity FROM alerts "
        "WHERE ended_at IS NULL AND rule_code IS NOT NULL"
    ).fetchall():
        estados[(r["device_id"], r["rule_code"])] = EstadoRegla(
            abierta=True, alert_id=r["id"], severity=r["severity"])
    log.info("Alertas abiertas recuperadas: %d", sum(e.abierta for e in estados.values()))


def dispositivos(conn):
    """Dispositivos con datos en las ultimas VENTANA_DISPOSITIVOS_H horas, mas los que tienen
    alertas abiertas (para poder cerrarlas o mantener 'dispositivo_caido' aunque ya no envien)."""
    return conn.execute(
        "SELECT device_id, bool_or(is_simulated) AS is_simulated FROM ("
        "  SELECT device_id, is_simulated FROM measurements "
        "  WHERE received_at > now() - make_interval(hours => %(horas)s) "
        "  AND (%(simulados)s OR NOT is_simulated) "
        "  UNION ALL "
        "  SELECT device_id, is_simulated FROM alerts "
        "  WHERE ended_at IS NULL AND rule_code IS NOT NULL AND (%(simulados)s OR NOT is_simulated)"
        ") d GROUP BY device_id ORDER BY device_id",
        {"horas": config.VENTANA_DISPOSITIVOS_H, "simulados": config.INCLUIR_SIMULADOS},
    ).fetchall()


_detector = None
_detector_fallo = False


def obtener_detector():
    """Detector de anomalias si ANOMALIAS_ACTIVAS; None si esta apagado o no se pudo cargar."""
    global _detector, _detector_fallo
    if not config.ANOMALIAS_ACTIVAS or _detector_fallo:
        return None
    if _detector is None:
        try:
            from anomalias import DetectorAnomalias
            _detector = DetectorAnomalias(config.ANOMALIAS_MODELO_PATH)
            log.info("Anomalias (EXPERIMENTAL) activas: modelo %s, solo simulados=%s",
                     _detector.procedencia.get("nombre"), _detector.solo_simulados)
        except Exception as e:  # noqa: BLE001 - sin modelo, las reglas siguen funcionando
            _detector_fallo = True
            log.error("No se pudo cargar el modelo de anomalias (%s): %s. Se desactiva la capa.",
                      type(e).__name__, e)
            return None
    return _detector


def condicion_anomalia_de(conn, device_id, ahora, es_simulado, detector):
    """Evalua el modelo sobre los ultimos minutos. Nunca rompe la evaluacion de las reglas."""
    if detector is None or not detector.aplica_a(es_simulado):
        return None
    try:
        rows = conn.execute(
            "SELECT received_at AS timestamp, temperature, humidity, pm25, pm10 FROM measurements "
            "WHERE device_id = %s AND received_at > %s - make_interval(mins => %s) AND received_at <= %s "
            "ORDER BY received_at",
            (device_id, ahora, config.ANOMALIAS_VENTANA_MIN, ahora),
        ).fetchall()
        return detector.evaluar(rows, ahora)
    except psycopg.Error:
        raise
    except Exception as e:  # noqa: BLE001
        log.error("Error del modelo de anomalias en %s: %s", device_id, e)
        return None


def evaluar_dispositivo(conn, device_id, ahora, es_simulado=False):
    params = {"device_id": device_id, "ahora": ahora}
    ventanas = conn.execute(SQL_VENTANAS, params).fetchone()
    ultima = conn.execute(SQL_ULTIMA, params).fetchone()
    v1, v24 = ventanas_desde_fila(ventanas)
    resp = construir_respuesta(device_id, v1, v24, ultima, ahora)

    condiciones = condiciones_umbral(resp, ahora)
    minutos = max(config.VENTANA_SENSOR_MIN.values())
    rows = conn.execute(
        "SELECT received_at AS timestamp, temperature, humidity, pm25, pm10 FROM measurements "
        "WHERE device_id = %s AND received_at > %s - make_interval(mins => %s) AND received_at <= %s",
        (device_id, ahora, minutos, ahora),
    ).fetchall()
    if rows:
        df_min = promediar_por_minuto(_dataframe(rows))
        condiciones += condiciones_sensor(df_min, ahora, config.VENTANA_SENSOR_MIN)
    caido = condicion_caido(ultima["received_at"] if ultima else None, ahora)
    if caido:
        condiciones.append(caido)
    anomalia = condicion_anomalia_de(conn, device_id, ahora, es_simulado, obtener_detector())
    if anomalia:
        condiciones.append(anomalia)
    return {c.rule_code: c for c in condiciones}


def _dataframe(rows):
    import pandas as pd

    df = pd.DataFrame(rows)
    for col in ("temperature", "humidity", "pm25", "pm10"):
        df[col] = df[col].astype(float)
    return df


def aplicar(conn, device_id, is_simulated, condiciones, ahora):
    for regla in REGLAS:
        estado = estados[(device_id, regla)]
        cond = condiciones.get(regla)
        if cond is not None:
            cond = etiquetar_simulacion(cond, is_simulated)
        accion = actualizar(estado, cond is not None, ahora,
                            config.REVISIONES_ABRIR, config.REVISIONES_CERRAR)
        if accion == "abrir":
            row = conn.execute(
                "INSERT INTO alerts (device_id, kind, rule_code, severity, message, details, "
                "started_at, last_seen_at, is_simulated) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (device_id, rule_code) WHERE ended_at IS NULL DO NOTHING RETURNING id",
                (device_id, cond.kind, regla, cond.severity, cond.message, Jsonb(cond.details),
                 estado.primera_vez or ahora, ahora, is_simulated),
            ).fetchone()
            if row is None:  # ya habia una abierta (p. ej. de otra instancia): se reutiliza
                row = conn.execute(
                    "SELECT id FROM alerts WHERE device_id = %s AND rule_code = %s AND ended_at IS NULL",
                    (device_id, regla),
                ).fetchone()
            estado.alert_id, estado.severity = row["id"], cond.severity
            log.info("ABIERTA %s %s [%s] %s", device_id, regla, cond.severity, cond.message)
        elif accion == "mantener" and estado.alert_id is not None:
            estado.severity = max_severidad(estado.severity, cond.severity)
            conn.execute(
                "UPDATE alerts SET last_seen_at = %s, severity = %s, message = %s, details = %s "
                "WHERE id = %s",
                (ahora, estado.severity, cond.message, Jsonb(cond.details), estado.alert_id),
            )
        elif accion == "cerrar" and estado.alert_id is not None:
            conn.execute("UPDATE alerts SET ended_at = %s WHERE id = %s", (ahora, estado.alert_id))
            log.info("CERRADA %s %s (id %s)", device_id, regla, estado.alert_id)
            estado.alert_id, estado.severity = None, None


def revisar():
    conn = get_db()
    if not migraciones_ok(conn):
        log.error("Faltan migraciones (002 y/o 004): no se evalúan alertas en esta revisión")
        return
    ahora = datetime.now(timezone.utc)
    for d in dispositivos(conn):
        try:
            condiciones = evaluar_dispositivo(conn, d["device_id"], ahora, bool(d["is_simulated"]))
            aplicar(conn, d["device_id"], d["is_simulated"], condiciones, ahora)
        except psycopg.OperationalError:
            raise  # conexion rota: se reconecta en la siguiente revision
        except Exception:
            log.exception("Error evaluando %s; sigo con el siguiente", d["device_id"])


def main():
    log.info("Alerter iniciado: intervalo %ss, abrir tras %d, cerrar tras %d, simulados=%s, anomalias=%s",
             config.INTERVALO_S, config.REVISIONES_ABRIR, config.REVISIONES_CERRAR,
             config.INCLUIR_SIMULADOS, config.ANOMALIAS_ACTIVAS)
    while True:
        inicio = time.monotonic()
        try:
            revisar()
        except psycopg.Error as e:
            log.error("Error de base de datos: %s; reconecto en la siguiente revisión", e)
            reset_db()
        except Exception:
            log.exception("Error inesperado en la revisión; sigo")
        time.sleep(max(1.0, config.INTERVALO_S - (time.monotonic() - inicio)))


if __name__ == "__main__":
    main()
