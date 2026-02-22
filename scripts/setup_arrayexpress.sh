#!/bin/bash
set -e

# Load configuration from .env file
if [ ! -f .env ]; then
    echo "Error: .env file not found"
    echo "Create one first: cp .env.example .env && nano .env"
    exit 1
fi

# Source the .env file
set -a
source .env
set +a

# Check required variables
if [ -z "$DB_HOST" ] || [ -z "$DB_NAME" ] || [ -z "$DB_USER" ] || [ -z "$DB_PASSWORD" ]; then
    echo "Error: Missing required database variables in .env"
    echo "Required: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PORT"
    exit 1
fi

# Set default port if not specified
DB_PORT=${DB_PORT:-5432}

echo "=== ArrayExpress Tables Setup ==="
echo "Database: ${DB_NAME}"
echo "User: ${DB_USER}"
echo "Host: ${DB_HOST}:${DB_PORT}"
echo ""

echo "Creating ArrayExpress tables..."
PGPASSWORD="${DB_PASSWORD}" psql -h ${DB_HOST} -p ${DB_PORT} -U ${DB_USER} -d ${DB_NAME} <<'EOF'

-- ArrayExpress Experiments
CREATE TABLE IF NOT EXISTS arrayexpress_experiments (
    accession                   TEXT PRIMARY KEY,
    title                       TEXT,
    organism                    TEXT,
    description                 TEXT,
    study_type                  TEXT,
    release_date                DATE,
    submission_date             DATE,
    experimental_factors        TEXT[],
    experimental_factor_types   TEXT[],
    contact_name                TEXT,
    contact_email               TEXT,
    contact_affiliation         TEXT,
    pubmed_id                   TEXT,
    protocol_names              TEXT[],
    protocol_types              TEXT[],
    attributes_json             JSONB,
    organisms                   TEXT[]
);

-- Add new metadata columns if they don't exist
ALTER TABLE arrayexpress_experiments
ADD COLUMN IF NOT EXISTS publication_title TEXT,
ADD COLUMN IF NOT EXISTS publication_authors TEXT,
ADD COLUMN IF NOT EXISTS publication_doi TEXT,
ADD COLUMN IF NOT EXISTS external_links JSONB,
ADD COLUMN IF NOT EXISTS processed_data_files JSONB,
ADD COLUMN IF NOT EXISTS experimental_factors_detail JSONB,
ADD COLUMN IF NOT EXISTS authors TEXT[],
ADD COLUMN IF NOT EXISTS organizations JSONB,
ADD COLUMN IF NOT EXISTS protocols_description TEXT,
ADD COLUMN IF NOT EXISTS assay_count INTEGER,
ADD COLUMN IF NOT EXISTS technology TEXT,
ADD COLUMN IF NOT EXISTS assay_by_molecule TEXT,
ADD COLUMN IF NOT EXISTS minseqe_score JSONB;

-- ArrayExpress Samples
CREATE TABLE IF NOT EXISTS arrayexpress_samples (
    id                      SERIAL PRIMARY KEY,
    experiment_accession    TEXT NOT NULL,
    source_name             TEXT,
    organism                TEXT,
    organism_part           TEXT,
    cell_type               TEXT,
    genotype                TEXT,
    sample_source_name      TEXT,
    sample_title            TEXT,
    sample_description      TEXT,
    library_strategy        TEXT,
    library_source          TEXT,
    library_selection       TEXT,
    ena_experiment          TEXT,
    ena_run                 TEXT,
    attributes_json         JSONB
);

-- Add full-text search vector column to experiments
ALTER TABLE arrayexpress_experiments
ADD COLUMN IF NOT EXISTS tsv tsvector
GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
    setweight(to_tsvector('english', coalesce(description,'')), 'B') ||
    setweight(to_tsvector('english', coalesce(protocols_description,'')), 'C') ||
    setweight(to_tsvector('english', coalesce(publication_title,'')), 'D')
) STORED;

-- Create ArrayExpress indexes
CREATE INDEX IF NOT EXISTS ae_experiments_fts_idx ON arrayexpress_experiments USING GIN(tsv);
CREATE INDEX IF NOT EXISTS ae_experiments_accession_upper_idx ON arrayexpress_experiments (UPPER(accession));
CREATE INDEX IF NOT EXISTS ae_experiments_organisms_idx ON arrayexpress_experiments USING GIN(organisms);
CREATE INDEX IF NOT EXISTS ae_samples_experiment_idx ON arrayexpress_samples (experiment_accession);
CREATE INDEX IF NOT EXISTS ae_samples_ena_experiment_idx ON arrayexpress_samples (UPPER(ena_experiment));
CREATE INDEX IF NOT EXISTS ae_samples_ena_run_idx ON arrayexpress_samples (UPPER(ena_run));

-- Indexes for new metadata fields
CREATE INDEX IF NOT EXISTS ae_experiments_pubmed_idx ON arrayexpress_experiments (pubmed_id);
CREATE INDEX IF NOT EXISTS ae_experiments_external_links_idx ON arrayexpress_experiments USING GIN (external_links);

EOF

echo ""
echo "=== Setup Complete ==="
echo "ArrayExpress tables created successfully."
echo ""
