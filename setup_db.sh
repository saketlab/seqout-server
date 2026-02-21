#!/bin/bash
set -e

# Load configuration from .env file
if [ ! -f .env ]; then
    echo "Error: .env file not found"
    echo "Create one first: cp .env.example .env && nano .env"
    exit 1
fi

source .env

# Parse DATABASE_URL: postgresql://user:password@host:port/dbname
DB_USER=$(echo $DATABASE_URL | sed -n 's|postgresql://\([^:]*\):.*|\1|p')
DB_PASSWORD=$(echo $DATABASE_URL | sed -n 's|postgresql://[^:]*:\([^@]*\)@.*|\1|p')
DB_HOST=$(echo $DATABASE_URL | sed -n 's|postgresql://[^@]*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo $DATABASE_URL | sed -n 's|postgresql://[^@]*@[^:]*:\([^/]*\)/.*|\1|p')
DB_NAME=$(echo $DATABASE_URL | sed -n 's|postgresql://[^@]*@[^/]*/\(.*\)|\1|p')

echo "=== pysradb Database Setup ==="
echo "Database: ${DB_NAME}"
echo "User: ${DB_USER}"
echo "Host: ${DB_HOST}:${DB_PORT}"

# Create PostgreSQL user and database
echo ""
echo "Creating database user and database..."
sudo -u postgres psql <<EOF
-- Create user if not exists
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';
    END IF;
END
\$\$;

-- Create database if not exists
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
EOF

echo "Creating tables..."
PGPASSWORD="${DB_PASSWORD}" psql -h ${DB_HOST} -p ${DB_PORT} -U ${DB_USER} -d ${DB_NAME} <<'EOF'

-- SRA Studies
CREATE TABLE IF NOT EXISTS sra_studies (
    accession       TEXT PRIMARY KEY,
    published_at    DATE,
    updated_at      DATE,
    abstract        TEXT,
    study_type      TEXT,
    submission      TEXT,
    alias           TEXT,
    title           TEXT
);

-- SRA Samples
CREATE TABLE IF NOT EXISTS sra_samples (
    accession           TEXT PRIMARY KEY,
    alias               TEXT,
    description         TEXT,
    title               TEXT,
    scientific_name     TEXT,
    taxon_id            TEXT,
    external_id         JSONB,
    attributes_json     JSONB,
    submission          TEXT,
    links               JSONB
);

-- SRA Experiments
CREATE TABLE IF NOT EXISTS sra_experiments (
    accession            TEXT PRIMARY KEY,
    design_description   TEXT,
    library_layout       TEXT,
    library_name         TEXT,
    library_selection    TEXT,
    library_source       TEXT,
    library_strategy     TEXT,
    samples              TEXT[],
    platform             TEXT,
    instrument_model     TEXT,
    study                TEXT,
    title                TEXT,
    submission           TEXT
);

-- GEO Series
CREATE TABLE IF NOT EXISTS geo_series (
    accession           TEXT PRIMARY KEY,
    overall_design      TEXT,
    summary             TEXT,
    title               TEXT,
    pubmed_id           TEXT[],
    samples_ref         TEXT[],
    series_type         TEXT[],
    relation            JSONB[],
    supplementary_data  JSONB[],
    published_at        DATE,
    updated_at          DATE
);

-- GEO Samples
CREATE TABLE IF NOT EXISTS geo_samples (
    accession               TEXT PRIMARY KEY,
    channel_count           INTEGER,
    channels                JSONB[],
    description             TEXT,
    platform_ref            TEXT,
    published_at            DATE,
    updated_at              DATE,
    supplementary_data      JSONB[],
    title                   TEXT,
    sample_type             TEXT,
    hybridization_protocol  TEXT,
    scan_protocol           TEXT
);

-- Add full-text search vector columns
ALTER TABLE sra_studies
ADD COLUMN IF NOT EXISTS tsv tsvector
GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(title,'')),    'A') ||
    setweight(to_tsvector('english', coalesce(abstract,'')), 'B') ||
    setweight(to_tsvector('english', coalesce(center_project_name,'')), 'C') ||
    setweight(to_tsvector('english', coalesce(study_type,'')),'D')
) STORED;

ALTER TABLE geo_series
ADD COLUMN IF NOT EXISTS tsv tsvector
GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
    setweight(to_tsvector('english', coalesce(summary,'')), 'B') ||
    setweight(to_tsvector('english', coalesce(overall_design,'')), 'C')
) STORED;

-- Create full-text search indexes
CREATE INDEX IF NOT EXISTS sra_studies_fts_idx ON sra_studies USING GIN(tsv);
CREATE INDEX IF NOT EXISTS geo_series_fts_idx ON geo_series USING GIN(tsv);

-- Create functional indexes for case-insensitive accession lookups
CREATE INDEX IF NOT EXISTS sra_studies_accession_upper_idx ON sra_studies (UPPER(accession));
CREATE INDEX IF NOT EXISTS geo_series_accession_upper_idx ON geo_series (UPPER(accession));
CREATE INDEX IF NOT EXISTS sra_experiments_accession_upper_idx ON sra_experiments (UPPER(accession));

-- Index for SRX to SRP lookups (join on study column)
CREATE INDEX IF NOT EXISTS sra_experiments_study_idx ON sra_experiments (study);

-- Indexes for structured search filters
CREATE INDEX IF NOT EXISTS idx_sra_exp_library_strategy ON sra_experiments (library_strategy);
CREATE INDEX IF NOT EXISTS idx_sra_exp_platform ON sra_experiments (platform);
CREATE INDEX IF NOT EXISTS idx_geo_series_organisms ON geo_series USING GIN (organisms);
CREATE INDEX IF NOT EXISTS idx_sra_studies_organisms ON sra_studies USING GIN (organisms);
CREATE INDEX IF NOT EXISTS idx_geo_contributors_country ON geo_contributors (address_country);

EOF

echo ""
echo "=== Setup Complete ==="
echo "Tables created successfully."
echo ""
echo "To start the server:"
echo "  uv sync && fastapi run main.py --host 0.0.0.0 --port 8000"
