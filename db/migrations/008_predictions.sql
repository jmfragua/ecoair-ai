-- Predicciones EXPERIMENTALES de PM2.5 a 3, 4 y 5 h (modelo prediccion_pm25_v4) y su evaluacion
-- contra lo medido. Las escribe el alerter (modulo de prediccion, con bandera para desactivarlo).
-- Se puede ejecutar mas de una vez sin error. No altera filas existentes. Horas en UTC.
-- created_at es la hora de EMISION truncada a la hora (no now()): repetir el calculo de la misma
-- hora no duplica gracias a UNIQUE (device_id, created_at, horizon_hours).
-- El valor observado NO se guarda: se calcula con un join contra measurements al evaluar.

CREATE TABLE IF NOT EXISTS predictions (
    id              BIGSERIAL PRIMARY KEY,
    device_id       TEXT NOT NULL REFERENCES devices(device_id),
    created_at      TIMESTAMPTZ NOT NULL CHECK (EXTRACT(EPOCH FROM created_at) % 3600 = 0),
    target_time     TIMESTAMPTZ NOT NULL,
    horizon_hours   SMALLINT NOT NULL CHECK (horizon_hours IN (3, 4, 5)),
    predicted_pm25  NUMERIC(6, 1) NOT NULL CHECK (predicted_pm25 BETWEEN 0 AND 1000),
    baseline_pm25   NUMERIC(6, 1) NOT NULL CHECK (baseline_pm25 BETWEEN 0 AND 1000), -- persistencia: media de la ultima hora
    model_version   TEXT NOT NULL CHECK (length(trim(model_version)) > 0),           -- prediccion_pm25_v4@<sha256 corto>
    inputs          JSONB NOT NULL,          -- variables usadas (medias horarias previas, media 24 h, hora, dia, minutos con datos)
    last_reading_at TIMESTAMPTZ,             -- ultima medicion usada
    is_simulated    BOOLEAN NOT NULL DEFAULT false,
    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (target_time = created_at + make_interval(hours => horizon_hours)),
    UNIQUE (device_id, created_at, horizon_hours)
);
CREATE INDEX IF NOT EXISTS predictions_device_target ON predictions (device_id, target_time);

-- Metricas diarias (dia de target_time en hora de Colombia). Se recalculan con
-- ON CONFLICT DO UPDATE: volver a calcular el mismo dia no duplica.
CREATE TABLE IF NOT EXISTS prediction_metrics (
    id                BIGSERIAL PRIMARY KEY,
    fecha             DATE NOT NULL,
    device_id         TEXT NOT NULL REFERENCES devices(device_id),
    horizon_hours     SMALLINT NOT NULL CHECK (horizon_hours IN (3, 4, 5)),
    model_version     TEXT NOT NULL,
    n                 INT NOT NULL CHECK (n > 0),                                -- predicciones evaluadas
    n_sin_observacion INT NOT NULL DEFAULT 0 CHECK (n_sin_observacion >= 0),     -- objetivo sin datos suficientes
    mae_model         NUMERIC(7, 3) NOT NULL CHECK (mae_model >= 0),
    rmse_model        NUMERIC(7, 3) NOT NULL CHECK (rmse_model >= 0),
    mae_baseline      NUMERIC(7, 3) NOT NULL CHECK (mae_baseline >= 0),
    rmse_baseline     NUMERIC(7, 3) NOT NULL CHECK (rmse_baseline >= 0),
    is_simulated      BOOLEAN NOT NULL DEFAULT false,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (fecha, device_id, horizon_hours, model_version)
);
