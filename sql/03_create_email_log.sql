CREATE TABLE IF NOT EXISTS tnbike.email_log (
    email_log_id BIGSERIAL PRIMARY KEY,
    message_id TEXT UNIQUE,
    from_address TEXT,
    received_at TIMESTAMPTZ,
    attachment_name TEXT,
    processing_status TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);