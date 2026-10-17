-- Correo de bienvenida al confirmar la suscripcion (servicio notifier/).
-- welcome_sent_at marca el envio UNICO: aunque el enlace de confirmacion se abra varias veces,
-- el notifier solo envia si welcome_sent_at es NULL y lo marca en la misma transaccion.
-- Se puede ejecutar mas de una vez sin error. No altera filas existentes.

ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS welcome_sent_at         TIMESTAMPTZ;
ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS welcome_attempts        INT NOT NULL DEFAULT 0;
ALTER TABLE subscribers ADD COLUMN IF NOT EXISTS welcome_last_attempt_at TIMESTAMPTZ;
