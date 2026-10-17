-- Datos EXTERNOS de calidad del aire (estaciones de terceros: p. ej. redes publicas de monitoreo).
-- Tabla separada de measurements: nunca se mezclan con los datos propios (ecoair-node-*)
-- ni con los simulados (ecoair-sim-01). Se cargan con db/load_external.py.
-- Se puede ejecutar mas de una vez sin error.

CREATE TABLE IF NOT EXISTS external_measurements (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT NOT NULL CHECK (length(trim(source)) > 0),    -- quien publica los datos
    station      TEXT NOT NULL CHECK (length(trim(station)) > 0),   -- identificador de la estacion
    city         TEXT NOT NULL CHECK (length(trim(city)) > 0),
    measured_at  TIMESTAMPTZ NOT NULL,                              -- instante de la medicion (UTC)
    pm25         NUMERIC(6, 1) NOT NULL CHECK (pm25 BETWEEN 0 AND 1000),         -- obligatorio
    pm10         NUMERIC(6, 1) CHECK (pm10 IS NULL OR pm10 BETWEEN 0 AND 1000),  -- opcional
    temperature  NUMERIC(4, 1) CHECK (temperature BETWEEN -40 AND 80),   -- puede ser NULL
    humidity     NUMERIC(4, 1) CHECK (humidity BETWEEN 0 AND 100),       -- puede ser NULL
    license      TEXT NOT NULL CHECK (length(trim(license)) > 0),   -- licencia de uso de la fuente
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),                -- fecha de carga
    UNIQUE (source, station, measured_at)                           -- evita duplicados al recargar
);

CREATE INDEX IF NOT EXISTS external_measurements_ciudad_tiempo
    ON external_measurements (city, measured_at);
CREATE INDEX IF NOT EXISTS external_measurements_fuente_carga
    ON external_measurements (source, loaded_at);
