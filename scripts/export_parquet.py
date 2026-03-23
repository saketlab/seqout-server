#!/usr/bin/env python3
"""Export PostgreSQL tables to Parquet files for DuckDB remote access.

Usage:
    python scripts/export_parquet.py [--output-dir /path/to/parquet] [--tables table1,table2]

Reads DATABASE_URL from .env. Exports each table as a single Parquet file
with snappy compression. Designed to run as a weekly cron job.

Keep EXPORT_TABLES in sync with .seqout_tables() in the seqout R package
(~/github/seqout/R/R/connection.R).
"""

import argparse
import os
import sys
import time

import pyarrow as pa
import pyarrow.parquet as pq
import psycopg
from dotenv import load_dotenv

load_dotenv()

EXPORT_TABLES = [
    "geo_series",
    "geo_samples",
    "sra_studies",
    "sra_experiments",
    "sra_samples",
    "ena_studies",
    "ena_experiments",
    "arrayexpress_experiments",
    "arrayexpress_samples",
    "pubmed_metadata",
    "unified_metadata",
    "enriched_samples",
    "enriched_studies_v3",
    "ontology_samples_v3",
    "cross_ref_geo",
    "cross_ref_ae",
    "cross_ref_ena",
    "cross_ref_pubmed",
    "common_names",
    "organism_growth_monthly",
    "study_publications",
]

BATCH_SIZE = 50_000


def get_columns(conn, table: str) -> list[str] | None:
    """Get column names for a table/matview, excluding tsvector columns.

    Returns None if the table does not exist.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            JOIN pg_class c ON a.attrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            LEFT JOIN pg_type t ON a.atttypid = t.oid
            WHERE c.relname = %s AND n.nspname = 'public'
              AND c.relkind IN ('r', 'v', 'm')
              AND a.attnum > 0
              AND NOT a.attisdropped
              AND t.typname != 'tsvector'
            ORDER BY a.attnum
            """,
            (table,),
        )
        cols = [row[0] for row in cur.fetchall()]
        return cols if cols else None


def export_table(conn, table: str, output_dir: str) -> None:
    """Export a single table to Parquet using batched server-side cursor."""
    columns = get_columns(conn, table)
    if columns is None:
        print(f"  SKIP {table} (does not exist)")
        return

    col_list = ", ".join(f'"{c}"' for c in columns)
    query = f'SELECT {col_list} FROM "{table}"'

    output_path = os.path.join(output_dir, f"{table}.parquet")
    tmp_path = output_path + ".tmp"

    start = time.time()
    total_rows = 0
    writer = None

    try:
        with conn.cursor(name=f"export_{table}") as cur:
            cur.execute(query)

            while True:
                rows = cur.fetchmany(BATCH_SIZE)
                if not rows:
                    break

                cols_data = list(zip(*rows))
                arrays = {col: list(cols_data[i]) for i, col in enumerate(columns)}
                batch_table = pa.table(arrays)

                if writer is None:
                    writer = pq.ParquetWriter(
                        tmp_path,
                        batch_table.schema,
                        compression="snappy",
                    )

                writer.write_table(batch_table)
                total_rows += len(rows)

    except Exception as e:
        print(f"  FAIL {table}: {e}")
        raise

    finally:
        if writer:
            writer.close()
            writer = None
        if total_rows == 0 and os.path.exists(tmp_path):
            os.remove(tmp_path)

    if total_rows == 0:
        print(f"  WARN {table}: 0 rows (no file written)")
        return

    os.replace(tmp_path, output_path)
    elapsed = time.time() - start
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  OK   {table}: {total_rows:,} rows, {size_mb:.1f} MB, {elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="Export tables to Parquet")
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("PARQUET_OUTPUT_DIR", "/var/www/seqout/data"),
        help="Directory to write Parquet files (default: /var/www/seqout/data)",
    )
    parser.add_argument(
        "--tables",
        default=None,
        help="Comma-separated list of tables to export (default: all)",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    tables = args.tables.split(",") if args.tables else EXPORT_TABLES

    print(f"Exporting {len(tables)} tables to {args.output_dir}")
    print()

    # Server-side cursors require a transaction (no autocommit)
    with psycopg.connect(database_url) as conn:
        for table in tables:
            export_table(conn, table, args.output_dir)

    print()
    print("Done.")


if __name__ == "__main__":
    main()
