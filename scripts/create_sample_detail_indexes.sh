#!/usr/bin/env bash
# Create indexes needed by the /sample-detail/ endpoint.
# Uses CONCURRENTLY to avoid locking tables during creation.
# Safe to re-run (IF NOT EXISTS).

set -euo pipefail

DB_URL=$(grep -oP 'DATABASE_URL=\K.*' "$(dirname "$0")/../.env")

echo "Creating indexes for sample-detail queries..."

echo "1/4: ena_experiments.experiment_accession (40M rows, may take a few minutes)..."
psql "$DB_URL" -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ena_exp_experiment ON ena_experiments (experiment_accession);"
echo "     Done."

echo "2/4: ena_experiments.sample_accession..."
psql "$DB_URL" -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ena_exp_sample ON ena_experiments (sample_accession);"
echo "     Done."

echo "3/4: sra_run_download_links.experiment_accession..."
psql "$DB_URL" -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_sra_run_dl_experiment ON sra_run_download_links (experiment_accession);"
echo "     Done."

echo "4/4: geo_contributors.accession (used by submitter lookup and structured search)..."
psql "$DB_URL" -c "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_geo_contributors_accession ON geo_contributors (accession);"
echo "     Done."

echo "All indexes created."
