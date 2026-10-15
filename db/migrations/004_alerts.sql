-- Alertas reales (servicio alerter/). Se puede ejecutar mas de una vez sin error.
-- No borra ni altera filas existentes: las filas antiguas quedan con rule_code NULL y
-- started_at NULL (la API no las cuenta como abiertas).

-- kind: se amplia el CHECK original ('threshold','anomaly').
ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_kind_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_kind_check
    CHECK (kind IN ('threshold', 'anomaly', 'sensor', 'device_down'));

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS rule_code    TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS severity     TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS started_at   TIMESTAMPTZ;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS ended_at     TIMESTAMPTZ;  -- NULL mientras esta abierta
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS details      JSONB;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS is_simulated BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_severity_check;
ALTER TABLE alerts ADD CONSTRAINT alerts_severity_check
    CHECK (severity IS NULL OR severity IN ('info', 'aviso', 'critica'));

-- Una sola alerta abierta por (device_id, rule_code). Las filas antiguas (rule_code NULL)
-- no chocan: en un indice unico los NULL son distintos entre si.
CREATE UNIQUE INDEX IF NOT EXISTS alerts_una_abierta_por_regla
    ON alerts (device_id, rule_code) WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS alerts_device_started ON alerts (device_id, started_at DESC);
