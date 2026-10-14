CREATE TABLE devices (
  device_id   TEXT PRIMARY KEY,
  name        TEXT,
  location    TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE measurements (
  id           BIGSERIAL PRIMARY KEY,
  device_id    TEXT NOT NULL REFERENCES devices(device_id),
  received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  temperature  NUMERIC(4,1) CHECK (temperature BETWEEN -40 AND 80),
  humidity     NUMERIC(4,1) CHECK (humidity BETWEEN 0 AND 100),
  pm25         NUMERIC(5,1) CHECK (pm25 >= 0),
  pm10         NUMERIC(5,1) CHECK (pm10 >= 0)
);
CREATE INDEX idx_measurements_device_time ON measurements (device_id, received_at DESC);

CREATE TABLE alerts (
  id              BIGSERIAL PRIMARY KEY,
  device_id       TEXT NOT NULL REFERENCES devices(device_id),
  measurement_id  BIGINT REFERENCES measurements(id),
  detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind            TEXT NOT NULL CHECK (kind IN ('threshold','anomaly')),
  score           NUMERIC,
  message         TEXT NOT NULL
);

INSERT INTO devices (device_id, name) VALUES ('ecoair-node-01', 'Nodo 01');
