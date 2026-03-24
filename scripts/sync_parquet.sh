#!/usr/bin/env bash
# Sync Parquet files to the production server.
#
# Usage:
#   bash scripts/sync_parquet.sh [user@host]
#
# Defaults to SRAMAP_SSH_HOST env var, or prompts if unset.

set -euo pipefail

LOCAL_DIR="/NAS/saket/github/pysradb-server/parquet_export"
REMOTE_DIR="/var/www/sramap/data"
SSH_HOST="${1:-sramap@31.97.203.121}"

echo "Syncing Parquet files to ${SSH_HOST}:${REMOTE_DIR}"
echo

# Create remote directory if needed
ssh "$SSH_HOST" "mkdir -p ${REMOTE_DIR}"

# rsync with compression and overall progress bar — only transfers changed files
rsync -ahz --info=progress2 --no-inc-recursive \
    --include='*.parquet' \
    --exclude='*' \
    "${LOCAL_DIR}/" \
    "${SSH_HOST}:${REMOTE_DIR}/"

echo
echo "Done. Files on server:"
ssh "$SSH_HOST" "ls -lhS ${REMOTE_DIR}/*.parquet"
