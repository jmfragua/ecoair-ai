"""Emision horaria de predicciones EXPERIMENTALES de PM2.5 (prediccion_pm25_v4) dentro del alerter.

- Se activa con PREDICCION_ACTIVA=true (apagado por defecto). Solo dispositivos de
  PREDICCION_DISPOSITIVOS (por defecto ecoair-node-01); un dispositivo con datos simulados en la
  ventana se omite: el simulado nunca genera predicciones.
- Una vez por hora, a partir del minuto PREDICCION_MINUTO (5) de la hora: created_at = la hora en
  punto (UTC) y se usan SOLO mediciones con received_at <= created_at (24 h, shared/prediccion.py).
- Guarda los 3 horizontes del modelo (3, 4 y 5 h), tambien los ocultos en el panel, para evaluarlos
  en vivo. Sin relleno hacia atras: solo la hora en curso.
- Idempotente: INSERT ... ON CONFLICT (device_id, created_at, horizon_hours) DO NOTHING.
- Si faltan datos (6 horas previas con >= 40 min cada una o < 18 de 24 h) no inserta nada y lo
  registra en el log.
- Recorte: predicted_pm25 se limita a un minimo de 0 (el CHECK de la tabla rechaza negativos). Cada
  recorte se cuenta y se registra en el log con el valor crudo.
- Si falta la tabla predictions (migracion 008) lo avisa una vez y no hace nada mas.
"""

import hashlib
import logging
from datetime import timedelta
from pathlib import Path

from psycopg.types.json import Jsonb

from shared import prediccion as pr

log = logging.getLogger("alerter.prediccion")

SQL_INSERT = (
    "INSERT INTO predictions (device_id, created_at, target_time, horizon_hours, predicted_pm25, baseline_pm25, "
    "model_version, inputs, last_reading_at, is_simulated) "
    "VALUES (%(device_id)s, %(created_at)s, %(target_time)s, %(horizon_hours)s, %(predicted_pm25)s, "
    "%(baseline_pm25)s, %(model_version)s, %(inputs)s, %(last_reading_at)s, false) "
    "ON CONFLICT (device_id, created_at, horizon_hours) DO NOTHING"
)
SQL_SIMULADO = (
    "SELECT coalesce(bool_or(is_simulated), false) AS sim FROM measurements "
    "WHERE device_id = %(device_id)s AND received_at > %(ahora)s - interval '1445 minutes' AND received_at <= %(ahora)s"
)
CAMPOS_ENTRADA = ("pm25_h1", "media_6h", "media_24h", "horas_24h", *pr.VARIABLES_V4)


def hora_de_emision(ahora, minuto):
    """Hora en punto (UTC) que toca emitir, o None si aun no llega el minuto de emision."""
    if ahora.minute < minuto:
        return None
    return ahora.replace(minute=0, second=0, microsecond=0)


def version_modelo(ruta, modelo):
    """'<nombre>@<sha256 corto del archivo>': cualquier cambio del JSON cambia la version."""
    return f"{modelo.get('nombre', 'modelo')}@{hashlib.sha256(Path(ruta).read_bytes()).hexdigest()[:8]}"


def calcular(modelo, version, device_id, serie, created_at):
    """-> (filas para SQL_INSERT, recortes [(horas, crudo)]) o (None, motivo) si faltan datos. Puro."""
    m = pr.minuto(created_at)
    x = pr.variables_para(modelo, serie, m)
    if x is None:
        return None, "faltan datos (6 horas previas con >= 40 min cada una y >= 18 de 24 h)"
    ultima = max((k for k in serie if k <= m), default=None)
    entradas = {k: round(float(x[k]), 4) for k in CAMPOS_ENTRADA if k in x}
    filas, recortes = [], []
    for h in pr.horizontes_de(modelo):
        crudo = pr.predecir_horizonte_crudo(modelo, x, h)
        if crudo < 0:
            recortes.append((h // 60, crudo))
        filas.append({"device_id": device_id, "created_at": created_at, "target_time": created_at + timedelta(minutes=h),
                      "horizon_hours": h // 60, "predicted_pm25": round(max(0.0, crudo), 1),
                      "baseline_pm25": round(float(x["pm25_h1"]), 1), "model_version": version,
                      "inputs": entradas, "last_reading_at": None if ultima is None else created_at + timedelta(minutes=ultima - m)})
    return filas, recortes


class EmisorHorario:
    """Estado entre revisiones: ultima hora emitida por dispositivo, modelo cargado y conteo de recortes."""

    def __init__(self, ruta_modelo, dispositivos, minuto=5):
        self.ruta, self.dispositivos, self.minuto = Path(ruta_modelo), list(dispositivos), minuto
        self.modelo = self.version = None
        self.ultima = {}            # device_id -> created_at ya procesada
        self.recortes_total = 0
        self.sin_tabla_avisado = False

    def cargar(self):
        if self.modelo is None:
            self.modelo = pr.cargar_modelo(self.ruta)
            if self.modelo is None:
                raise RuntimeError(f"modelo de prediccion no disponible en {self.ruta}")
            self.version = version_modelo(self.ruta, self.modelo)
            log.info("Prediccion horaria: modelo %s (horizontes %s)", self.version, pr.horizontes_de(self.modelo))
        return self.modelo

    def revisar(self, conn, ahora):
        """Llamada en cada revision del alerter. Devuelve el numero de filas insertadas."""
        created_at = hora_de_emision(ahora, self.minuto)
        if created_at is None:
            return 0
        pendientes = [d for d in self.dispositivos if self.ultima.get(d) != created_at]
        if not pendientes:
            return 0
        if not conn.execute("SELECT to_regclass('predictions') IS NOT NULL AS ok").fetchone()["ok"]:
            if not self.sin_tabla_avisado:
                log.warning("Falta db/migrations/008_predictions.sql: no se emiten predicciones")
                self.sin_tabla_avisado = True
            return 0
        modelo = self.cargar()
        total = 0
        for device_id in pendientes:
            self.ultima[device_id] = created_at        # una sola vez por hora, aunque falten datos
            params = {"device_id": device_id, "ahora": created_at}
            if conn.execute(SQL_SIMULADO, params).fetchone()["sim"]:
                log.info("Prediccion %s %s: omitida (datos simulados en la ventana)", device_id, created_at)
                continue
            filas = conn.execute(pr.SQL_SERIE_PM25_24H, params).fetchall()
            serie = pr.serie_por_minuto([(f["t"], f["pm25"]) for f in filas])
            resultado, extra = calcular(modelo, self.version, device_id, serie, created_at)
            if resultado is None:
                log.info("Prediccion %s %s: omitida, %s", device_id, created_at, extra)
                continue
            for horas, crudo in extra:
                self.recortes_total += 1
                log.info("Prediccion %s %s a %d h recortada a 0 (valor crudo %.3f); recortes acumulados: %d",
                         device_id, created_at, horas, crudo, self.recortes_total)
            with conn.cursor() as cur:
                cur.executemany(SQL_INSERT, [{**f, "inputs": Jsonb(f["inputs"])} for f in resultado])
                insertadas = max(cur.rowcount, 0)
            total += insertadas
            log.info("Prediccion %s %s: %d filas nuevas (%s)", device_id, created_at, insertadas,
                     ", ".join(f"{f['horizon_hours']} h = {f['predicted_pm25']}" for f in resultado))
        return total
