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


def get_columns_with_types(conn, table: str) -> list[tuple[str, str]] | None:
    """Get (column_name, type_name) pairs for a table/matview.

    Excludes tsvector columns. Returns None if the table does not exist.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.attname, t.typname
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
        rows = cur.fetchall()
        return rows if rows else None


def build_select(columns_with_types: list[tuple[str, str]]) -> tuple[list[str], str]:
    """Build SELECT clause, casting JSONB/JSONB[] to TEXT to avoid schema mismatches.

    Returns (column_names, select_sql).
    """
    names = []
    exprs = []
    for col, typ in columns_with_types:
        names.append(col)
        # JSONB structs can have inconsistent schemas across rows,
        # which breaks Parquet's fixed schema. Cast to text.
        if typ in ("jsonb", "_jsonb"):
            exprs.append(f'"{col}"::text AS "{col}"')
        else:
            exprs.append(f'"{col}"')
    return names, ", ".join(exprs)


def _promote_null_types(schema: pa.Schema) -> pa.Schema:
    """Replace null-typed fields with string.

    When an entire batch has NULL values for a column, PyArrow infers the
    type as `null`. Later batches may have actual strings, causing a schema
    mismatch. Promote all null types to string upfront.
    """
    fields = []
    for field in schema:
        if pa.types.is_null(field.type):
            fields.append(pa.field(field.name, pa.string()))
        else:
            fields.append(field)
    return pa.schema(fields)


def export_table(conn, table: str, output_dir: str) -> None:
    """Export a single table to Parquet using batched server-side cursor."""
    columns_with_types = get_columns_with_types(conn, table)
    if columns_with_types is None:
        print(f"  SKIP {table} (does not exist)")
        return

    columns, select_sql = build_select(columns_with_types)
    query = f'SELECT {select_sql} FROM "{table}"'

    output_path = os.path.join(output_dir, f"{table}.parquet")
    tmp_path = output_path + ".tmp"

    start = time.time()
    total_rows = 0
    writer = None
    writer_schema = None

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
                    writer_schema = _promote_null_types(batch_table.schema)
                    writer = pq.ParquetWriter(
                        tmp_path,
                        writer_schema,
                        compression="snappy",
                    )

                # Cast batch to writer schema (handles null→string promotion)
                batch_table = batch_table.cast(writer_schema)
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
