-- Datos simulados: columna is_simulated y dispositivo ecoair-sim-01.
-- Se puede ejecutar mas de una vez sin error.
ALTER TABLE measurements ADD COLUMN IF NOT EXISTS is_simulated BOOLEAN NOT NULL DEFAULT false;

INSERT INTO devices (device_id, name, location)
VALUES ('ecoair-sim-01', 'Estación simulada', 'Simulado')
ON CONFLICT (device_id) DO NOTHING;
