#!/bin/bash
set -e

if [ ! -f .env ]; then
    echo "Error: .env file not found"
    exit 1
fi

source .env

echo "Creating request_log table in ${DB_NAME}..."

PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" <<'EOF'
CREATE TABLE IF NOT EXISTS request_log (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    method          TEXT,
    path            TEXT,
    query_string    TEXT,
    search_query    TEXT,
    status_code     SMALLINT,
    response_ms     REAL,
    ip_address      INET,
    ip_hash         TEXT,
    country         TEXT,
    city            TEXT,
    user_agent_raw  TEXT,
    browser         TEXT,
    browser_version TEXT,
    os              TEXT,
    os_version      TEXT,
    device_type     TEXT,
    accept_language TEXT,
    referer         TEXT
);

CREATE INDEX IF NOT EXISTS idx_request_log_ip_address  ON request_log (ip_address);
CREATE INDEX IF NOT EXISTS idx_request_log_ts          ON request_log (ts);
CREATE INDEX IF NOT EXISTS idx_request_log_path        ON request_log (path);
CREATE INDEX IF NOT EXISTS idx_request_log_search_query ON request_log (search_query);
CREATE INDEX IF NOT EXISTS idx_request_log_country     ON request_log (country);
CREATE INDEX IF NOT EXISTS idx_request_log_city        ON request_log (city);
CREATE INDEX IF NOT EXISTS idx_request_log_device_type ON request_log (device_type);
EOF

echo "Done."
