#!/usr/bin/env python3
"""Rebuild sitemap_page_bounds (keyset boundaries for /sitemap/accessions).

Usage: DATABASE_URL=... python scripts/build_sitemap_bounds.py
"""

import logging
import os
import sys

import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from queries import SITEMAP_PAGE_SIZE, SITEMAP_TABLES  # noqa: E402

from _db import dsn  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("build_sitemap_bounds")


def rebuild_source(
    cur, source: str, table: str, col: str, distinct: bool, where: str | None
) -> int:
    where_clause = f" WHERE {where}" if where else ""
    inner = (
        f"SELECT DISTINCT {col} AS accession FROM {table}{where_clause}"
        if distinct
        else f"SELECT {col} AS accession FROM {table}{where_clause}"
    )
    cur.execute("DELETE FROM sitemap_page_bounds WHERE source = %s", (source,))
    cur.execute(
        f"""
        INSERT INTO sitemap_page_bounds (source, page, start_accession)
        SELECT %s, (rn - 1) / %s, accession
        FROM (
            SELECT accession, row_number() OVER (ORDER BY accession) AS rn
            FROM ({inner}) d
        ) t
        WHERE (rn - 1) %% %s = 0
        """,
        (source, SITEMAP_PAGE_SIZE, SITEMAP_PAGE_SIZE),
    )
    return cur.rowcount


def main() -> None:
    with psycopg.connect(dsn()) as conn:
        for source, (table, col, distinct, where) in SITEMAP_TABLES.items():
            with conn.transaction(), conn.cursor() as cur:
                pages = rebuild_source(cur, source, table, col, distinct, where)
            logger.info("%s: %d pages", source, pages)


if __name__ == "__main__":
    main()
