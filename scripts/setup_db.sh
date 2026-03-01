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

-- Add pysradb-backfilled PubMed ID columns
ALTER TABLE sra_studies ADD COLUMN IF NOT EXISTS pubmed_id_pysradb TEXT;
ALTER TABLE geo_series  ADD COLUMN IF NOT EXISTS pubmed_id_pysradb TEXT;
ALTER TABLE arrayexpress_experiments ADD COLUMN IF NOT EXISTS pubmed_id_pysradb TEXT;
ALTER TABLE ena_studies ADD COLUMN IF NOT EXISTS pubmed_id_pysradb TEXT;

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

-- ENA Studies (created here so structured search queries don't error if no data transferred yet)
CREATE TABLE IF NOT EXISTS ena_studies (
    study_accession             TEXT PRIMARY KEY,
    secondary_study_accession   TEXT,
    secondary_study_alias       TEXT,
    secondary_study_center_name TEXT,
    study_alias                 TEXT,
    study_title                 TEXT,
    study_description           TEXT,
    study_name                  TEXT,
    center_name                 TEXT,
    broker_name                 TEXT,
    project_name                TEXT,
    tax_id                      INTEGER,
    tax_lineage                 TEXT,
    tax_division                TEXT,
    scientific_name             TEXT,
    strain                      TEXT,
    breed                       TEXT,
    cultivar                    TEXT,
    isolate                     TEXT,
    first_public                DATE,
    last_updated                DATE,
    geo_accession               TEXT,
    keywords                    TEXT,
    tag                         TEXT,
    submission_tool             TEXT,
    parent_study_accession      TEXT,
    description                 TEXT,
    status                      TEXT
);

CREATE TABLE IF NOT EXISTS ena_experiments (
    run_accession               TEXT PRIMARY KEY,
    experiment_accession        TEXT NOT NULL,
    sample_accession            TEXT,
    study_accession             TEXT NOT NULL,
    experiment_title            TEXT,
    library_strategy            TEXT,
    library_source              TEXT,
    library_selection           TEXT,
    library_layout              TEXT,
    library_name                TEXT,
    instrument_platform         TEXT,
    instrument_model            TEXT,
    scientific_name             TEXT,
    tax_id                      INTEGER,
    center_name                 TEXT,
    first_public                DATE,
    last_updated                DATE,
    sample_title                TEXT,
    sample_alias                TEXT,
    sample_description          TEXT,
    country                     TEXT,
    host                        TEXT,
    tissue_type                 TEXT,
    cell_type                   TEXT,
    disease                     TEXT,
    dev_stage                   TEXT,
    base_count                  INTEGER,
    read_count                  INTEGER
);

-- Indexes for structured search filters
CREATE INDEX IF NOT EXISTS idx_sra_exp_library_strategy ON sra_experiments (library_strategy);
CREATE INDEX IF NOT EXISTS idx_sra_exp_platform ON sra_experiments (platform);
CREATE INDEX IF NOT EXISTS idx_geo_series_organisms ON geo_series USING GIN (organisms);
CREATE INDEX IF NOT EXISTS idx_sra_studies_organisms ON sra_studies USING GIN (organisms);
CREATE INDEX IF NOT EXISTS idx_geo_contributors_country ON geo_contributors (address_country);

-- ENA indexes
CREATE INDEX IF NOT EXISTS idx_ena_studies_scientific_name ON ena_studies (scientific_name);
CREATE INDEX IF NOT EXISTS idx_ena_studies_first_public ON ena_studies (first_public);
CREATE INDEX IF NOT EXISTS idx_ena_studies_geo_accession ON ena_studies (geo_accession);
CREATE INDEX IF NOT EXISTS idx_ena_studies_center_name ON ena_studies (center_name);
CREATE INDEX IF NOT EXISTS idx_ena_exp_study ON ena_experiments (study_accession);
CREATE INDEX IF NOT EXISTS idx_ena_exp_strategy ON ena_experiments (library_strategy);
CREATE INDEX IF NOT EXISTS idx_ena_exp_platform ON ena_experiments (instrument_platform);
CREATE INDEX IF NOT EXISTS idx_ena_exp_country ON ena_experiments (country);

-- Date-based indexes for year range filters and growth stats
CREATE INDEX IF NOT EXISTS idx_geo_series_published_at ON geo_series (published_at);
CREATE INDEX IF NOT EXISTS idx_sra_studies_published_at ON sra_studies (published_at);
CREATE INDEX IF NOT EXISTS idx_ae_experiments_release_date ON arrayexpress_experiments (release_date);
CREATE INDEX IF NOT EXISTS idx_geo_samples_published_at ON geo_samples (published_at);
CREATE INDEX IF NOT EXISTS idx_ena_experiments_first_public ON ena_experiments (first_public);

-- SRA submissions center_name for center filter
CREATE INDEX IF NOT EXISTS idx_sra_submissions_center ON sra_submissions (center_name);

-- Cross-reference tables (populated by setup_cross_refs.sh, created here to prevent errors)
CREATE TABLE IF NOT EXISTS cross_ref_geo (
    geo_accession    TEXT NOT NULL,
    target_accession TEXT NOT NULL,
    link_type        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cross_ref_ae (
    ae_accession     TEXT NOT NULL,
    target_accession TEXT NOT NULL,
    link_type        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cross_ref_ena (
    ena_accession    TEXT NOT NULL,
    target_accession TEXT NOT NULL,
    link_type        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cross_ref_pubmed (
    accession  TEXT NOT NULL,
    pubmed_id  TEXT NOT NULL,
    source     TEXT NOT NULL
);

-- PubMed metadata (populated by backfill_pubmed_metadata.py + transfers/pubmed_metadata.py)
CREATE TABLE IF NOT EXISTS pubmed_metadata (
    pmid                       TEXT PRIMARY KEY,
    title                      TEXT,
    journal                    TEXT,
    doi                        TEXT,
    pub_date                   TEXT,
    authors                    TEXT,
    issn                       TEXT,
    citation_count             INTEGER,
    journal_h_index            INTEGER,
    journal_i10_index          INTEGER,
    journal_2yr_mean_citedness REAL,
    journal_cited_by_count     INTEGER,
    journal_works_count        INTEGER
);

-- Analytics: privacy-respecting request log
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

CREATE INDEX IF NOT EXISTS idx_request_log_ip_address ON request_log (ip_address);
CREATE INDEX IF NOT EXISTS idx_request_log_ts          ON request_log (ts);
CREATE INDEX IF NOT EXISTS idx_request_log_path        ON request_log (path);
CREATE INDEX IF NOT EXISTS idx_request_log_search_query ON request_log (search_query);
CREATE INDEX IF NOT EXISTS idx_request_log_country     ON request_log (country);
CREATE INDEX IF NOT EXISTS idx_request_log_city        ON request_log (city);
CREATE INDEX IF NOT EXISTS idx_request_log_device_type ON request_log (device_type);

EOF

echo ""
echo "=== Setup Complete ==="
echo "Tables created successfully."
echo ""
echo "To start the server:"
echo "  uv sync && fastapi run main.py --host 0.0.0.0 --port 8000"
