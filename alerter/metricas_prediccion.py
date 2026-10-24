"""Metricas diarias de las predicciones EXPERIMENTALES (tabla prediction_metrics), dentro del alerter.

- fecha = dia de target_time en hora de Colombia. Una vez al dia, a partir de las 00:30 de Colombia,
  se calculan AYER y ANTEAYER (el segundo recoge datos que llegaron tarde); se escriben con
  ON CONFLICT DO UPDATE, asi que repetir el calculo no duplica.
- Valor observado: NO se guarda en predictions; se calcula con un join contra measurements: media de
  PM2.5 de la hora que termina en target_time (received_at en (target_time - 1 h, target_time]),
  solo datos reales, con >= 40 minutos con datos. Si no llega a 40 min, la prediccion cuenta en
  n_sin_observacion y no entra en el error.
- Linea base: baseline_pm25 guardado al emitir (persistencia). Se compara el valor YA recortado a 0.
- Un grupo (dia, horizonte, version) sin ninguna prediccion evaluable no se escribe (CHECK n > 0).
"""

import logging
import math
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

log = logging.getLogger("alerter.metricas")

COLOMBIA = timezone(timedelta(hours=-5))
MIN_MINUTOS_OBSERVACION = 40
HORA_CALCULO = time(0, 30)        # hora de Colombia

SQL_FILAS = (
    "SELECT p.horizon_hours, p.model_version, p.predicted_pm25, p.baseline_pm25, obs.media, obs.minutos "
    "FROM predictions p LEFT JOIN LATERAL ("
    "  SELECT avg(m.pm25) AS media, count(DISTINCT date_trunc('minute', m.received_at)) AS minutos "
    "  FROM measurements m WHERE m.device_id = p.device_id AND NOT m.is_simulated "
    "  AND m.pm25 IS NOT NULL AND m.received_at > p.target_time - interval '1 hour' "
    "  AND m.received_at <= p.target_time) obs ON true "
    "WHERE p.device_id = %(device_id)s AND NOT p.is_simulated "
    "AND p.target_time > %(desde)s AND p.target_time <= %(hasta)s AND p.target_time <= now()"
)
SQL_UPSERT = (
    "INSERT INTO prediction_metrics (fecha, device_id, horizon_hours, model_version, n, n_sin_observacion, "
    "mae_model, rmse_model, mae_baseline, rmse_baseline, is_simulated, computed_at) "
    "VALUES (%(fecha)s, %(device_id)s, %(horizon_hours)s, %(model_version)s, %(n)s, %(n_sin_observacion)s, "
    "%(mae_model)s, %(rmse_model)s, %(mae_baseline)s, %(rmse_baseline)s, false, now()) "
    "ON CONFLICT (fecha, device_id, horizon_hours, model_version) DO UPDATE SET n = EXCLUDED.n, "
    "n_sin_observacion = EXCLUDED.n_sin_observacion, mae_model = EXCLUDED.mae_model, "
    "rmse_model = EXCLUDED.rmse_model, mae_baseline = EXCLUDED.mae_baseline, "
    "rmse_baseline = EXCLUDED.rmse_baseline, computed_at = now()"
)


def limites_del_dia(fecha):
    """(desde, hasta] en UTC para las target_time del dia de Colombia 'fecha' (target_time en hora en punto)."""
    ini = datetime.combine(fecha, time(0), tzinfo=COLOMBIA)
    # target_time = 00:00 de 'fecha' pertenece a ese dia: intervalo (ini - 1 s, ini + 1 dia - 1 s]
    return (ini - timedelta(seconds=1)).astimezone(timezone.utc), (ini + timedelta(days=1) - timedelta(seconds=1)).astimezone(timezone.utc)


def metricas_de(filas, fecha, device_id):
    """filas: dicts de SQL_FILAS. -> lista de dicts para SQL_UPSERT (solo grupos con n > 0). Puro."""
    grupos = defaultdict(list)
    for f in filas:
        grupos[(int(f["horizon_hours"]), f["model_version"])].append(f)
    out = []
    for (h, version), fs in sorted(grupos.items()):
        ok = [f for f in fs if f["media"] is not None and (f["minutos"] or 0) >= MIN_MINUTOS_OBSERVACION]
        if not ok:
            continue
        em = [float(f["predicted_pm25"]) - float(f["media"]) for f in ok]
        eb = [float(f["baseline_pm25"]) - float(f["media"]) for f in ok]
        out.append({"fecha": fecha, "device_id": device_id, "horizon_hours": h, "model_version": version,
                    "n": len(ok), "n_sin_observacion": len(fs) - len(ok),
                    "mae_model": round(sum(map(abs, em)) / len(ok), 3),
                    "rmse_model": round(math.sqrt(sum(e * e for e in em) / len(ok)), 3),
                    "mae_baseline": round(sum(map(abs, eb)) / len(ok), 3),
                    "rmse_baseline": round(math.sqrt(sum(e * e for e in eb) / len(ok)), 3)})
    return out


def dias_a_calcular(ahora):
    """Ayer y anteayer (hora de Colombia) si ya pasaron las 00:30 de Colombia; si no, ninguno."""
    local = ahora.astimezone(COLOMBIA)
    if local.time() < HORA_CALCULO:
        return []
    hoy = local.date()
    return [hoy - timedelta(days=2), hoy - timedelta(days=1)]


class CalculadorMetricas:
    def __init__(self, dispositivos):
        self.dispositivos = list(dispositivos)
        self.ultimo_dia = None          # dia local en que ya se calculo

    def revisar(self, conn, ahora):
        """Una vez por dia de Colombia. Devuelve cuantas filas de metricas se escribieron."""
        dias = dias_a_calcular(ahora)
        hoy = ahora.astimezone(COLOMBIA).date()
        if not dias or self.ultimo_dia == hoy:
            return 0
        if not conn.execute("SELECT to_regclass('prediction_metrics') IS NOT NULL AS ok").fetchone()["ok"]:
            return 0
        escritas = 0
        for device_id in self.dispositivos:
            for fecha in dias:
                desde, hasta = limites_del_dia(fecha)
                filas = conn.execute(SQL_FILAS, {"device_id": device_id, "desde": desde, "hasta": hasta}).fetchall()
                metricas = metricas_de(filas, fecha, device_id)
                if metricas:
                    with conn.cursor() as cur:
                        cur.executemany(SQL_UPSERT, metricas)
                escritas += len(metricas)
                log.info("Metricas %s %s: %d predicciones, %d filas (%s)", device_id, fecha, len(filas), len(metricas),
                         ", ".join(f"{m['horizon_hours']} h n={m['n']} MAE {m['mae_model']} vs {m['mae_baseline']}"
                                   for m in metricas) or "sin observaciones suficientes")
        self.ultimo_dia = hoy
        return escritas
