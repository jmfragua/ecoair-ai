-- Suscripcion a alertas por correo (API /subscriptions y servicio notifier/).
-- Se puede ejecutar mas de una vez sin error. NO borra ni altera filas existentes.
-- Datos personales: solo el correo y la fecha/version del consentimiento (Ley 1581 de 2012).
-- No se guarda la IP. Los tokens se guardan solo como hash SHA-256.

CREATE TABLE IF NOT EXISTS subscribers (
    id                     BIGSERIAL PRIMARY KEY,
    email                  TEXT NOT NULL CHECK (email = lower(email) AND length(email) <= 254),
    device_id              TEXT NOT NULL REFERENCES devices(device_id),
    min_severity           TEXT NOT NULL DEFAULT 'aviso' CHECK (min_severity IN ('aviso', 'critica')),
    consent_at             TIMESTAMPTZ NOT NULL,
    consent_text_version   TEXT NOT NULL,
    confirm_requested_at   TIMESTAMPTZ,          -- la API pide (re)enviar la confirmacion
    confirmed_at           TIMESTAMPTZ,
    confirm_token_hash     TEXT,                 -- SHA-256 del token de un solo uso
    confirm_expires_at     TIMESTAMPTZ,
    confirm_sent_count     INT NOT NULL DEFAULT 0,
    last_confirm_sent_at   TIMESTAMPTZ,
    confirm_attempts       INT NOT NULL DEFAULT 0,   -- envios de confirmacion fallidos seguidos
    confirm_last_attempt_at TIMESTAMPTZ,             -- para la espera creciente (1, 2, 4, 8 min)
    unsubscribe_token_hash TEXT,                 -- SHA-256 del token HMAC de baja
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    unsubscribed_at        TIMESTAMPTZ,
    UNIQUE (email, device_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS subscribers_confirm_token
    ON subscribers (confirm_token_hash) WHERE confirm_token_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS subscribers_unsubscribe_token ON subscribers (unsubscribe_token_hash);
CREATE INDEX IF NOT EXISTS subscribers_email ON subscribers (email);

-- Cola de envios: una fila por (alerta, suscriptor, tipo) = un correo por episodio.
CREATE TABLE IF NOT EXISTS notifications_sent (
    id              BIGSERIAL PRIMARY KEY,
    alert_id        BIGINT NOT NULL REFERENCES alerts(id),
    subscriber_id   BIGINT NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('open', 'close')),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'sent', 'failed', 'dry_run', 'skipped')),
    attempts        INT NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    UNIQUE (alert_id, subscriber_id, kind)
);

CREATE INDEX IF NOT EXISTS notifications_pendientes
    ON notifications_sent (status) WHERE status IN ('pending', 'failed');
