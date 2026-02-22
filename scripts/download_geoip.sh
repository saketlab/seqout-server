#!/bin/bash
set -e

# Load configuration from .env file
if [ ! -f .env ]; then
    echo "Error: .env file not found"
    exit 1
fi

source .env

if [ -z "$MAXMIND_ACCOUNT_ID" ] || [ -z "$MAXMIND_LICENSE_KEY" ]; then
    echo "Error: MAXMIND_ACCOUNT_ID and MAXMIND_LICENSE_KEY must be set in .env"
    exit 1
fi

OUTDIR="${GEOIP_DB_PATH%/*}"
TMPDIR=$(mktemp -d)
AUTH="${MAXMIND_ACCOUNT_ID}:${MAXMIND_LICENSE_KEY}"

DATABASES=("GeoLite2-City" "GeoLite2-Country" "GeoLite2-ASN")

for DB in "${DATABASES[@]}"; do
    echo "Downloading ${DB}..."
    curl -sSL -u "${AUTH}" \
        "https://download.maxmind.com/geoip/databases/${DB}/download?suffix=tar.gz" \
        -o "${TMPDIR}/${DB}.tar.gz"

    echo "Extracting ${DB}..."
    tar -xzf "${TMPDIR}/${DB}.tar.gz" -C "${TMPDIR}"

    sudo mkdir -p "${OUTDIR}"
    sudo cp "${TMPDIR}"/${DB}_*/${DB}.mmdb "${OUTDIR}/"
    echo "Installed: ${OUTDIR}/${DB}.mmdb"
done

rm -rf "${TMPDIR}"

echo ""
echo "Done. All databases installed to ${OUTDIR}/"
