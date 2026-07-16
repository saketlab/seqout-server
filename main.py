import asyncio
import csv
import gzip
import hashlib
import io
import json
import logging
import os
import pathlib
import re
import shutil
import threading
import time
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

import geoip2.database
import geoip2.errors
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastmcp import FastMCP
from neo4j import GraphDatabase
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from symspellpy import SymSpell, Verbosity
from user_agents import parse as parse_ua

from deep_dive import fetch_deep_dive_children, fetch_deep_dive_terms
from expansions import expand_terms
from queries import (
    AE_EXPERIMENT_SAMPLES_SQL,
    AE_QUERY_ACCESSIONS_SQL,
    AE_SEARCH_SQL,
    ALL_QUERY_ACCESSIONS_SQL,
    BATCH_DRA_SAMPLES_SQL,
    BATCH_ENA_SAMPLES_SQL,
    BATCH_GSA_SAMPLES_SQL,
    BATCH_SAMPLES_SQL,
    BIO_PROJECT_GEO_SERIES_SQL,
    BULK_PROJECT_METADATA_SQL,
    COMMON_NAMES_BY_SCIENTIFIC_NAMES_SQL,
    COMMON_NAME_BY_SCIENTIFIC_NAME_SQL,
    DATABASE_GROWTH_MONTHLY_SQL,
    DATABASE_VOLUME_GROWTH_MONTHLY_SQL,
    DRA_EXPERIMENT_SQL,
    DRA_EXPERIMENT_TO_STUDY_SQL,
    DRA_SAMPLE_SQL,
    DRA_SAMPLE_TO_STUDY_SQL,
    DRA_SEARCH_SQL,
    DRA_STUDY_EXPERIMENTS_SQL,
    ENA_EXPERIMENT_SQL,
    ENA_SAMPLE_SQL,
    ENA_SAMPLE_TO_EXPERIMENT_SQL,
    ENA_SEARCH_SQL,
    ENA_STUDY_EXPERIMENTS_SQL,
    ENRICHED_COLUMN_VALUES_SQL,
    ENRICHED_CROSSTAB_COLUMNS,
    ENRICHED_CROSSTAB_SQL,
    ENRICHED_SAMPLES_SQL,
    ENRICHED_SAMPLES_V3_SQL,
    ENRICHED_STAT_COLUMNS,
    ENRICHED_STUDY_SQL,
    ENRICHED_STUDY_V3_SQL,
    ENRICHED_SUMMARY_SQL,
    ENRICHED_TOP_VALUES_PER_COLUMN,
    EXPERIMENT_RUNS_SQL,
    GEA_EXPERIMENT_SAMPLES_SQL,
    GEA_SEARCH_SQL,
    GEO_QUERY_ACCESSIONS_SQL,
    GEO_SAMPLE_SQL,
    GEO_SAMPLE_TO_SERIES_SQL,
    GEO_SEARCH_SQL,
    GEO_SERIES_CONTRIBUTORS_BATCH_SQL,
    GEO_SERIES_NEIGHBORS_COORDS_SQL,
    GEO_SERIES_SAMPLES_SQL,
    GLOBAL_CONTRIBUTIONS_FILTERS_SQL,
    GROWTH_BASES_ENA_SQL,
    GROWTH_BYTES_SRA_FASTQ_SQL,
    GROWTH_BYTES_SRA_SRA_SQL,
    GROWTH_EXPERIMENTS_AE_SQL,
    GROWTH_EXPERIMENTS_DRA_SQL,
    GROWTH_EXPERIMENTS_ENA_SQL,
    GROWTH_EXPERIMENTS_GEA_SQL,
    GROWTH_EXPERIMENTS_GEO_SQL,
    GROWTH_EXPERIMENTS_GSA_SQL,
    GROWTH_EXPERIMENTS_SRA_SQL,
    GROWTH_ORGANISM_SQL,
    GROWTH_PLATFORM_INSTRUMENT_SQL,
    GROWTH_PLATFORM_PROJECTS_SQL,
    GROWTH_PLATFORM_SQL,
    GROWTH_PROJECTS_AE_SQL,
    GROWTH_PROJECTS_DRA_SQL,
    GROWTH_PROJECTS_ENA_SQL,
    GROWTH_PROJECTS_GEA_SQL,
    GROWTH_PROJECTS_GEO_SQL,
    GROWTH_PROJECTS_GSA_SQL,
    GROWTH_PROJECTS_SRA_SQL,
    GSA_EXPERIMENT_SQL,
    GSA_SAMPLE_SQL,
    GSA_SAMPLE_TO_EXPERIMENT_SQL,
    GSA_SEARCH_SQL,
    GSA_STUDY_EXPERIMENTS_SQL,
    GSA_STUDY_RUNS_PREVIEW_SQL,
    GSA_STUDY_RUNS_SUMMARY_SQL,
    GSA_SUBMITTER_BATCH_SQL,
    LAST_UPDATED_SQL,
    LIST_CENTERS_SQL,
    LIST_INSTRUMENT_MODELS_SQL,
    LIST_JOURNALS_SQL,
    LIST_LIBRARY_STRATEGIES_SQL,
    LIST_ORGANISMS_SQL,
    LIST_ORGANISMS_WITH_COMMON_NAMES_SQL,
    ONTOLOGY_SAMPLES_V3_SQL,
    ORGANISM_SEARCH_SQL,
    ORGANISM_TOTALS_SQL,
    ORGANISM_TOTALS_YEAR_SQL,
    PLATFORM_FILTERS_SQL,
    PLATFORM_INSTRUMENTS_SQL,
    PLATFORM_TOTALS_SQL,
    PMID_VARIANT_BATCH_SQL,
    PRJNA_TO_GEO_SERIES_SQL,
    PRJ_TO_ENA_STUDY_SQL,
    PRJ_TO_GSA_STUDY_SQL,
    PRJ_TO_SRA_STUDY_SQL,
    PROJECT_AE_SQL,
    PROJECT_DRA_SQL,
    PROJECT_ENA_SQL,
    PROJECT_GEA_SQL,
    PROJECT_GEO_SQL,
    PROJECT_GSA_SQL,
    PROJECT_SRA_SQL,
    PUBLICATION_BY_DOI_SQL,
    PUBLICATION_BY_PMID_SQL,
    PUBMED_BY_PMIDS_BATCH_SQL,
    RELATED_SQL,
    RUN_DOWNLOAD_SQL,
    SAMPLE_SQL,
    SEARCH_AUTHORS_SQL,
    SEARCH_AUTHOR_PROJECTS_SQL,
    SEARCH_BY_ACCESSION_SQL_BY_KIND,
    SEARCH_SQL,
    SIMILAR_STUDIES_SQL,
    SITEMAP_ACCESSIONS_SQL,
    SITEMAP_BOUNDS_COUNTS_SQL,
    SITEMAP_BOUND_LOOKUP_SQL,
    SITEMAP_COUNTS_SQL,
    SITEMAP_KEYSET_SQL,
    SITEMAP_PAGE_SIZE,
    SITEMAP_SOURCE_BUILT_SQL,
    SOURCE_TOTALS_SQL,
    SRA_EXPERIMENT_SQL,
    SRA_EXPERIMENT_TO_STUDY_SQL,
    SRA_QUERY_ACCESSIONS_SQL,
    SRA_SAMPLE_TO_EXPERIMENT_SQL,
    SRA_SAMPLE_TO_STUDY_SQL,
    SRA_SEARCH_SQL,
    PROJECTS_BY_PMID_SQL,
    STUDY_BAMS_SQL,
    STUDY_EXPERIMENTS_SQL,
    STUDY_PUBLICATIONS_BATCH_SQL,
    STUDY_RUNS_PREVIEW_SQL,
    STUDY_RUNS_SQL,
    STUDY_RUNS_SUMMARY_SQL,
    STUDY_SAMPLES_DRA_SQL,
    STUDY_SAMPLES_SRA_SQL,
    SUBMITTER_BATCH_SQL,
    build_country_accessions_sql,
    build_country_filters_sql,
    build_enriched_samples_sql,
    build_global_contributions_sql,
    build_platform_growth_filtered_sql,
    build_search_facets_over_matched,
    build_search_match_temp,
    build_search_results,
    build_search_total_over_matched,
    build_sorted_search_sql,
    build_structured_search,
    init_global_contributions_filters_sql,
    init_global_contributions_schema,
)

load_dotenv()


ENRICHMENT_ENABLED = False


_STUDY_PREFIX = r"[SED]RP"
_EXPERIMENT_PREFIX = r"[SED]RX"
_SAMPLE_PREFIX = r"[SED]RS"
_RUN_PREFIX = r"[SED]RR"

STUDY_PATTERN = re.compile(rf"^({_STUDY_PREFIX})\d+$", re.IGNORECASE)
SAMPLE_PATTERN = re.compile(rf"^({_SAMPLE_PREFIX})\d+$", re.IGNORECASE)
EXPERIMENT_PATTERN = re.compile(rf"^({_EXPERIMENT_PREFIX})\d+$", re.IGNORECASE)
RUN_PATTERN = re.compile(rf"^({_RUN_PREFIX})\d+$", re.IGNORECASE)
GEO_SERIES_PATTERN = re.compile(r"^GSE\d+$", re.IGNORECASE)
GEO_SAMPLE_PATTERN = re.compile(r"^GSM\d+$", re.IGNORECASE)
BIOSAMPLE_PATTERN = re.compile(r"^SAM[A-Z]+\d+$", re.IGNORECASE)
AE_PATTERN = re.compile(r"^E-[A-Z]{4}-\d+$", re.IGNORECASE)
# "GEAD" is four letters, so E-GEAD-N also matches AE_PATTERN: always test GEA first.
_GEA_PREFIX = r"[Ee]-[Gg][Ee][Aa][Dd]-"
PRJ_PATTERN = re.compile(r"^PRJ[A-Z]+\d+$", re.IGNORECASE)
# GSA (CNCB-NGDC) accessions; PRJCA/SAMC already match PRJ/BIOSAMPLE above.
GSA_STUDY_PATTERN = re.compile(r"^(CRA|HRA)\d+$", re.IGNORECASE)
GSA_RUN_PATTERN = re.compile(r"^(CRR|HRR)\d+$", re.IGNORECASE)
GSA_EXPERIMENT_PATTERN = re.compile(r"^(CRX|HRX)\d+$", re.IGNORECASE)
GSA_SAMPLE_PATTERN = re.compile(r"^HRS\d+$", re.IGNORECASE)
PROJECT_PATTERN = re.compile(
    rf"^(GSE|{_STUDY_PREFIX}|[Ee]-[A-Za-z]{{4}}-)\d+$", re.IGNORECASE
)
BULK_PATTERN = re.compile(
    rf"^(GSE|{_STUDY_PREFIX}|PRJNA|[Ee]-[A-Za-z]{{4}}-)\d+$", re.IGNORECASE
)

STUDY_REGEX = rf"^{_STUDY_PREFIX}\d+$"
RUN_REGEX = rf"^{_RUN_PREFIX}\d+$"
SAMPLE_REGEX = rf"^{_SAMPLE_PREFIX}\d+$"
EXPERIMENT_REGEX = rf"^({_EXPERIMENT_PREFIX}|CRX|HRX)\d+$"
GEO_SAMPLE_REGEX = r"^GSM\d+$"
PRJ_REGEX = r"^PRJ[A-Z]+\d+$"
PROJECT_REGEX = rf"^(GSE|{_STUDY_PREFIX}|PRJ[A-Z]+|CRA|HRA|[Ee]-[A-Za-z]{{4}}-)\d+$"
STUDY_EXPERIMENTS_REGEX = rf"^({_STUDY_PREFIX}|PRJ[A-Z]+|CRA|HRA|{_GEA_PREFIX})\d+$"
SAMPLE_OR_ENA_REGEX = rf"^({_SAMPLE_PREFIX}|SAM[A-Z]+|HRS)\d+$"
GEO_SERIES_REGEX = r"^GSE\d+$"
AE_REGEX = r"^[Ee]-[A-Za-z]{4}-\d+$"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_QUERY_DOWNLOAD = 100

MAX_BULK_ACCESSIONS = 100
MAX_COUNTRY_ACCESSIONS = 50_000
MAX_METADATA_ROWS = 200_000
_MAX_CROSSTAB_CACHE_ENTRIES = 200
_MAX_GLOBAL_CONTRIBUTION_QUERY_CACHE_ENTRIES = 200
_MAX_COUNTRY_FILTERS_CACHE_ENTRIES = 500
_MAX_COUNTRY_ACCESSIONS_CACHE_ENTRIES = 100
_STATS_CACHE_CONTROL = "public, s-maxage=604800, stale-while-revalidate=2592000"


def _require_bulk_limit(accessions: list) -> None:
    if len(accessions) > MAX_BULK_ACCESSIONS:
        raise HTTPException(
            413, f"Too many accessions: {len(accessions)} (max {MAX_BULK_ACCESSIONS})"
        )


ATLAS_PROJECTS_ROWS_SQL = """
WITH atlas_base AS (
    SELECT
        accession,
        'geo'::text AS source,
        'GEO'::text AS archive,
        coords_2d[1]::double precision AS raw_x,
        coords_2d[2]::double precision AS raw_y,
        COALESCE(countries_iso, ARRAY[]::text[]) AS raw_country_codes,
        ARRAY[]::text[] AS raw_country_labels
    FROM geo_series
    WHERE coords_2d IS NOT NULL
      AND array_length(coords_2d, 1) >= 2
      AND coords_2d[1] IS NOT NULL
      AND coords_2d[2] IS NOT NULL
      AND accession ~ '^[A-Za-z]+\\d+$'

    UNION ALL

    SELECT
        accession,
        'sra'::text AS source,
        'SRA'::text AS archive,
        coords_2d[1]::double precision AS raw_x,
        coords_2d[2]::double precision AS raw_y,
        ARRAY[]::text[] AS raw_country_codes,
        COALESCE(countries, ARRAY[]::text[]) AS raw_country_labels
    FROM sra_studies
    WHERE coords_2d IS NOT NULL
      AND array_length(coords_2d, 1) >= 2
      AND coords_2d[1] IS NOT NULL
      AND coords_2d[2] IS NOT NULL
      AND accession ~ '^[A-Za-z]+\\d+$'
),
country_ranked AS (
    SELECT
        canonical_accession AS accession,
        source,
        UPPER(NULLIF(BTRIM(country_code), '')) AS country_code,
        NULLIF(BTRIM(country), '') AS country_label,
        ROW_NUMBER() OVER (
            PARTITION BY canonical_accession,
                         source,
                         UPPER(NULLIF(BTRIM(country_code), '')),
                         NULLIF(BTRIM(country), '')
            ORDER BY canonical_accession
        ) AS rn
    FROM unified_metadata
    WHERE source IN ('geo', 'sra')
      AND (country IS NOT NULL OR country_code IS NOT NULL)
),
country_agg AS (
    SELECT
        accession,
        source,
        COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'code', country_code,
                    'label', country_label
                )
                ORDER BY country_label NULLS LAST, country_code NULLS LAST
            ) FILTER (WHERE country_code IS NOT NULL OR country_label IS NOT NULL),
            '[]'::jsonb
        ) AS country_pairs
    FROM country_ranked
    WHERE rn = 1
    GROUP BY accession, source
)
SELECT
    b.accession,
    b.archive,
    b.raw_x,
    b.raw_y,
    COALESCE(c.country_pairs, '[]'::jsonb) AS country_pairs,
    b.raw_country_codes,
    b.raw_country_labels
FROM atlas_base b
LEFT JOIN country_agg c
  ON c.accession = b.accession
 AND c.source = b.source
ORDER BY b.accession;
"""
MAP_FILTER_COLUMNS = (
    ["organism", "tissue", "disease", "assay"] if ENRICHMENT_ENABLED else []
)
MAP_FILTER_LABELS = {
    "organism": "Organism",
    "tissue": "Tissue",
    "disease": "Disease",
    "assay": "Assay",
}

if MAP_FILTER_COLUMNS:
    _MAP_ENRICHED_AGG = ",\n        ".join(
        f"mode() WITHIN GROUP (ORDER BY BTRIM({c}::text)) "
        f"FILTER (WHERE {c} IS NOT NULL AND BTRIM({c}::text) <> '') AS {c}"
        for c in MAP_FILTER_COLUMNS
    )
    _MAP_ENRICHED_COLS = "".join(f",\n    e.{c}" for c in MAP_FILTER_COLUMNS)

    MAP_POINTS_SQL = f"""
WITH combined AS (
    SELECT accession, coords_2d, countries_iso AS countries, clusters
    FROM geo_series WHERE coords_2d IS NOT NULL
    UNION ALL
    SELECT accession, coords_2d, countries, clusters
    FROM sra_studies WHERE coords_2d IS NOT NULL
    UNION ALL
    SELECT accession, coords_2d, countries, clusters
    FROM arrayexpress_experiments WHERE coords_2d IS NOT NULL
),
enriched_agg AS (
    SELECT study_accession AS accession,
        {_MAP_ENRICHED_AGG}
    FROM ontology_samples_v4
    GROUP BY study_accession
)
SELECT
    combined.accession,
    coords_2d[1]::real AS x,
    coords_2d[2]::real AS y,
    countries,
    clusters{_MAP_ENRICHED_COLS}
FROM combined
LEFT JOIN enriched_agg e ON e.accession = combined.accession
WHERE array_length(coords_2d, 1) >= 2
  AND coords_2d[1] IS NOT NULL
  AND coords_2d[2] IS NOT NULL
  AND combined.accession ~ '^[A-Za-z]+\\d+$'
ORDER BY combined.accession;
"""
else:
    _MAP_ENRICHED_AGG = ""
    _MAP_ENRICHED_COLS = ""
    MAP_POINTS_SQL = """
WITH combined AS (
    SELECT accession, coords_2d, countries_iso AS countries, clusters
    FROM geo_series WHERE coords_2d IS NOT NULL
    UNION ALL
    SELECT accession, coords_2d, countries, clusters
    FROM sra_studies WHERE coords_2d IS NOT NULL
    UNION ALL
    SELECT accession, coords_2d, countries, clusters
    FROM arrayexpress_experiments WHERE coords_2d IS NOT NULL
)
SELECT
    combined.accession,
    coords_2d[1]::real AS x,
    coords_2d[2]::real AS y,
    countries,
    clusters
FROM combined
WHERE array_length(coords_2d, 1) >= 2
  AND coords_2d[1] IS NOT NULL
  AND coords_2d[2] IS NOT NULL
  AND combined.accession ~ '^[A-Za-z]+\\d+$'
ORDER BY combined.accession;
"""

CLUSTER_LABELS_SQL = """
SELECT level, cluster_id, label, size, centroid_x, centroid_y
FROM cluster_labels
ORDER BY level, cluster_id;
"""

CLUSTER_LABELS_BBOX_SQL = """
SELECT cluster_id, label, size, centroid_x, centroid_y
FROM cluster_labels
WHERE level = %(level)s
  AND centroid_x BETWEEN %(minx)s AND %(maxx)s
  AND centroid_y BETWEEN %(miny)s AND %(maxy)s
ORDER BY size DESC NULLS LAST
LIMIT %(limit)s;
"""
_clusters_cache_lock = threading.Lock()
_clusters_cache = {"labels": None, "levels": None}
_map_data_cache_lock = threading.Lock()
_map_data_cache = {"ready": False, "rows": None, "labels": None, "levels": None}
_ATLAS_SCHEMA_VERSION = "v2"
_ATLAS_EXTENT = 2200.0
_ATLAS_HALF_EXTENT = _ATLAS_EXTENT / 2
_ATLAS_CACHE_CONTROL = "public, max-age=2592000, immutable"
_atlas_cache_lock = threading.Lock()
_atlas_cache = {
    "ready": False,
    "raw": b"",
    "gzip": b"",
    "etag_raw": "",
    "etag_gzip": "",
    "version": "",
    "labels": b"",
    "labels_etag": "",
}
_growth_cache_lock = threading.Lock()
_growth_cache: dict[tuple[str, str | None], dict] = {}
_MAX_ORGANISM_CACHE_ENTRIES = 500
_organism_growth_cache_lock = threading.Lock()
_organism_growth_cache: dict[str, dict] = {}  # key -> {data, built_at}
_organisms_list_cache_lock = threading.Lock()
_organisms_list_cache: dict[str, dict] = {}  # "plain"/"common" -> {data, built_at}
_organism_totals_cache_lock = threading.Lock()
_organism_totals_cache: dict[str, dict] = {}  # cache_key -> {data, built_at}
_global_contributions_cache_lock = threading.Lock()
_global_contributions_cache = {"ready": False, "data": [], "built_at": 0.0}
_global_contributions_query_cache_lock = threading.Lock()
_global_contributions_query_cache: dict[tuple, dict] = {}
_country_filters_cache_lock = threading.Lock()
_country_filters_cache: dict[tuple, dict] = {}
_country_accessions_cache_lock = threading.Lock()
_country_accessions_cache: dict[tuple, dict] = {}
_spelling_cache_lock = threading.Lock()
_spelling_cache: dict = {"ready": False, "symspell": None}
_source_totals_cache_lock = threading.Lock()
_source_totals_cache = {"ready": False, "data": {}, "built_at": 0.0}
_last_updated_cache_lock = threading.Lock()
_last_updated_cache = {"ready": False, "data": {}, "built_at": 0.0}
_sitemap_counts_cache_lock = threading.Lock()
_sitemap_counts_cache = {"ready": False, "data": {}, "built_at": 0.0}
_enriched_crosstab_cache_lock = threading.Lock()
_enriched_crosstab_cache: dict = {}
# Broad-query facet cache: materializing the FTS match set is the cold-start cost
# (up to ~68s for "cancer"). Once a broad query (total >= threshold) is computed we
# keep its facets in-process with no expiry until /cache/purge clears it. Narrow
# queries are never stored, so memory stays bounded to the few broad terms.
_BROAD_SEARCH_MIN_TOTAL = 5000
_MAX_BROAD_FACETS_CACHE_ENTRIES = 500
_broad_facets_cache_lock = threading.Lock()
_broad_facets_cache: dict[tuple, dict] = {}
# First-page unfiltered results
_search_results_cache_lock = threading.Lock()
_search_results_cache: dict[tuple, dict] = {}
_MAX_SEARCH_RESULTS_CACHE_ENTRIES = 300
_SEARCH_RESULTS_PAGE = 200  # full page ⇒ broad query worth caching
# Author-projects: deterministic per normalized name, and pages get revisited.
_author_projects_cache_lock = threading.Lock()
_author_projects_cache: dict[tuple, dict] = {}
_AUTHOR_PROJECTS_CACHE_TTL = 24 * 3600
_MAX_AUTHOR_PROJECTS_CACHE_ENTRIES = 500
# Fetch cap for faceting: the full deduped set is tiny for almost everyone.
# total stays exact (the window runs pre-LIMIT); only facets undercount past the cap.
_AUTHOR_PROJECTS_FETCH_CAP = 2000
_STATS_SOURCES = ("geo", "sra", "arrayexpress", "ena", "gsa", "dra", "gea")


# Per-query ceiling
_DEFAULT_STATEMENT_TIMEOUT = "30s"


def _configure_connection(conn) -> None:
    conn.execute(f"SET statement_timeout = '{_DEFAULT_STATEMENT_TIMEOUT}'")


pool = ConnectionPool(
    os.environ["DATABASE_URL"],
    min_size=4,
    max_size=20,
    timeout=10,
    kwargs={"autocommit": True, "prepare_threshold": 2},
    configure=_configure_connection,
)

with pool.connection() as _conn:
    ENRICHED_SAMPLES_SQL = build_enriched_samples_sql(_conn)
    init_global_contributions_schema(_conn)
    GLOBAL_CONTRIBUTIONS_FILTERS_SQL = init_global_contributions_filters_sql()

neo4j_driver = GraphDatabase.driver(
    os.environ.get("NEO4J_URI", "neo4j://localhost:7687"),
    auth=(
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ.get("NEO4J_PASSWORD", ""),
    ),
    # Fail fast when Neo4j is down
    connection_timeout=3.0,
    connection_acquisition_timeout=5.0,
    max_transaction_retry_time=3.0,
)

limiter = Limiter(key_func=get_remote_address)
api_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
api_app.state.limiter = limiter


# ---------
# Analytics
# ---------

_geoip_reader = None
_geoip_lock = threading.Lock()
_GEOIP_DB_PATH = os.environ.get("GEOIP_DB_PATH", "/usr/share/GeoIP/GeoLite2-City.mmdb")
_IST = ZoneInfo("Asia/Kolkata")


def _get_daily_salt() -> str:
    return f"pysradb-{date.today().isoformat()}"


def hash_ip(ip: str) -> str:
    salted = f"{ip}:{_get_daily_salt()}"
    return hashlib.sha256(salted.encode()).hexdigest()[:16]


def get_location_from_ip(ip: str) -> tuple[str | None, str | None]:
    """Return (country, city) from IP using GeoLite2-City database."""
    global _geoip_reader
    with _geoip_lock:
        if _geoip_reader is None:
            try:
                _geoip_reader = geoip2.database.Reader(_GEOIP_DB_PATH)
            except Exception:
                logger.debug("GeoIP database not found at %s", _GEOIP_DB_PATH)
                return None, None
    try:
        resp = _geoip_reader.city(ip)
        return resp.country.name, resp.city.name
    except (geoip2.errors.AddressNotFoundError, ValueError):
        return None, None
    except Exception:
        return None, None


def log_request(
    method: str,
    path: str,
    query_string: str | None,
    client_ip: str,
    user_agent: str,
    accept_language: str,
    referer: str,
    status_code: int,
    response_ms: float,
    search_query: str | None,
) -> None:
    try:
        client_ip_val = client_ip if client_ip else ""
        ip_hashed = hash_ip(client_ip_val) if client_ip_val else None
        country, city = (
            get_location_from_ip(client_ip_val) if client_ip_val else (None, None)
        )

        now_ist = datetime.now(_IST)

        ua = parse_ua(user_agent)
        browser = ua.browser.family if ua.browser.family != "Other" else None
        browser_version = ua.browser.version_string or None
        os_name = ua.os.family if ua.os.family != "Other" else None
        os_version = ua.os.version_string or None

        if ua.is_mobile:
            device_type = "mobile"
        elif ua.is_tablet:
            device_type = "tablet"
        elif ua.is_bot:
            device_type = "bot"
        elif ua.is_pc:
            device_type = "desktop"
        else:
            device_type = "other"

        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET timezone = 'Asia/Kolkata'")
                cur.execute(
                    """
                    INSERT INTO request_log
                        (ts, method, path, query_string, search_query,
                         status_code, response_ms, ip_address, ip_hash,
                         country, city,
                         user_agent_raw, browser, browser_version,
                         os, os_version, device_type,
                         accept_language, referer)
                    VALUES
                        (%s, %s, %s, %s, %s,
                         %s, %s, %s, %s,
                         %s, %s,
                         %s, %s, %s,
                         %s, %s, %s,
                         %s, %s)
                    """,
                    (
                        now_ist,
                        method,
                        path,
                        query_string,
                        search_query,
                        status_code,
                        round(response_ms, 2),
                        client_ip_val or None,
                        ip_hashed,
                        country,
                        city,
                        user_agent[:512] if user_agent else None,
                        browser,
                        browser_version,
                        os_name,
                        os_version,
                        device_type,
                        accept_language[:255] or None,
                        referer[:512] or None,
                    ),
                )
    except Exception:
        logger.debug("Failed to log request", exc_info=True)


class AnalyticsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        response_ms = (time.perf_counter() - start) * 1000

        # Extract all needed properties synchronously on the main thread
        search_query = request.query_params.get("q")
        client_ip = request.client.host if request.client else ""
        user_agent = request.headers.get("user-agent", "")
        accept_language = request.headers.get("accept-language", "")
        referer = request.headers.get("referer", "")
        method = request.method
        path = request.url.path
        query_string = str(request.url.query) or None

        # Schedule the blocking database write and GeoIP lookup to run asynchronously
        # in asyncio's built-in thread pool executor.
        asyncio.create_task(
            asyncio.to_thread(
                log_request,
                method,
                path,
                query_string,
                client_ip,
                user_agent,
                accept_language,
                referer,
                response.status_code,
                response_ms,
                search_query,
            )
        )

        return response


# Middleware is registered on combined_app (below) so it covers
# both REST and MCP routes.


@api_app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded. Please try again later."},
    )


def _accession_source(acc: str) -> str | None:
    """Classify accession into database source."""
    if acc.startswith("GSE"):
        return "geo"
    if acc.upper().startswith("E-GEAD-"):
        return "gea"
    if acc.startswith("E-"):
        return "arrayexpress"
    if STUDY_PATTERN.match(acc):
        return "sra"
    return None


def _normalize_aliases(alias) -> list[str]:
    """Normalise a project's `alias` (string, list, or Postgres/JSON array
    text) into a deduped, upper-cased list of accessions."""
    if not alias:
        return []
    if isinstance(alias, (list, tuple)):
        items = list(alias)
    elif isinstance(alias, str):
        s = alias.strip()
        if s.startswith("{") and s.endswith("}"):
            items = [x.strip().strip('"') for x in s[1:-1].split(",")]
        elif s.startswith("["):
            try:
                items = json.loads(s)
            except Exception:
                items = [s]
        else:
            items = [s]
    else:
        return []
    out: list[str] = []
    for it in items:
        if isinstance(it, str) and it.strip():
            out.append(it.strip().upper())
    return list(dict.fromkeys(out))


def _external_id_accessions(external_id) -> list[str]:
    """Accession-shaped values from a project's external_id cross-links
    ({"GEO": "GSE..", "SRA": "SRP.."}); tolerates a dict or a JSON string."""
    if not external_id:
        return []
    if isinstance(external_id, str):
        try:
            external_id = json.loads(external_id)
        except (ValueError, TypeError):
            return []
    if not isinstance(external_id, dict):
        return []
    out: list[str] = []
    for value in external_id.values():
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if item:
                out.append(str(item).strip().upper())
    return out


def _rich_pub(pmid: str, m: dict) -> dict:
    """A full publication entry from a pubmed_metadata row."""
    pub_date = m.get("pub_date")
    if hasattr(pub_date, "isoformat"):
        pub_date = pub_date.isoformat()
    elif pub_date is not None:
        pub_date = str(pub_date)
    return {
        "pmid": pmid,
        "title": m.get("title"),
        "journal": m.get("journal"),
        "doi": m.get("doi"),
        "pub_date": pub_date,
        "authors": m.get("authors"),
        "issn": m.get("issn"),
        "citation_count": m.get("citation_count"),
        "journal_h_index": m.get("journal_h_index"),
        "journal_i10_index": m.get("journal_i10_index"),
        "journal_2yr_mean_citedness": m.get("journal_2yr_mean_citedness"),
        "journal_cited_by_count": m.get("journal_cited_by_count"),
        "journal_works_count": m.get("journal_works_count"),
    }


def _flat_pub(pmid: str | None, flat_row: dict | None) -> dict | None:
    """A publication entry from the project's own flat publication_* columns
    (ArrayExpress) and/or a bare pmid the frontend enriches from NCBI. None
    when there's nothing to show."""
    title = doi = authors = None
    if flat_row is not None:
        title = flat_row.get("publication_title")
        doi = flat_row.get("publication_doi")
        if isinstance(doi, str):
            doi = (
                re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip(), flags=re.I)
                or None
            )
        authors = flat_row.get("publication_authors")
    if pmid is None and not title and not doi:
        return None
    return {
        "pmid": pmid,
        "title": title,
        "journal": None,
        "doi": doi,
        "pub_date": None,
        "authors": authors,
        "citation_count": None,
    }


def _build_publications(
    pmids: list[str], pubmed_map: dict[str, dict], flat_row: dict | None
) -> list[dict] | None:
    """One entry per resolved pmid (all of them, so multi-publication studies
    show every paper). Each pmid: rich details from pubmed_metadata → flat
    columns / pmid-only stub. When there are no pmids, fall back to a
    flat-columns-only entry. Returns None when there's nothing to show.

    flat_row (the project's own row) is only applied when there's a single
    pmid, so its one publication's title never bleeds onto sibling pmids.
    """
    out: list[dict] = []
    single = len(pmids) <= 1
    for pmid in pmids:
        pmid = str(pmid)
        m = pubmed_map.get(pmid)
        pub = _rich_pub(pmid, m) if m else _flat_pub(pmid, flat_row if single else None)
        if pub:
            out.append(pub)
    if not out:
        stub = _flat_pub(None, flat_row)
        if stub:
            out.append(stub)
    return out or None


def _enrich_with_publications(rows: list[dict], cur) -> list[dict]:
    """Batch-lookup publications and center names, merge into result rows.

    One uniform resolver for every project type. Per row, the linked
    publication is resolved over candidate accessions — the project's own
    accession first, then its aliases (so a project inherits its linked
    archive's publication when it has none of its own). For each candidate:
    study_publications → pmid (the repo's own pubmed_id / pubmed_id_pysradb
    columns) → pubmed_metadata details / flat columns / pmid-only stub.
    Always sets `center_name` from unified_metadata (own accession).
    """
    if not rows:
        return rows
    accessions = [r["accession"] for r in rows if r.get("accession")]
    if not accessions:
        return rows

    # Candidate accessions per row: own accession first, then aliases.
    row_candidates: dict[str, list[str]] = {}
    all_cands: set[str] = set()
    for r in rows:
        acc = r.get("accession")
        if not acc:
            continue
        cands = [acc] + [
            a for a in _normalize_aliases(r.get("alias")) if a and a != acc
        ]
        # Linked archives named in external_id ({"GEO": "GSE..", "SRA": "SRP.."})
        # lend their publication too, so a PRJ* wrapper — or any project holding a
        # same-study cross-archive link — inherits the linked study's pub when it
        # has none of its own. Own accession stays first, so a native pub wins.
        for linked in _external_id_accessions(r.get("external_id")):
            if linked and linked != acc and linked not in cands:
                cands.append(linked)
        row_candidates[acc] = cands
        all_cands.update(cands)
    candidate_list = list(all_cands)

    cur.execute(STUDY_PUBLICATIONS_BATCH_SQL, (candidate_list,))
    pub_map = {r["accession"]: r["publications"] for r in cur.fetchall()}

    # geo_series.contributors (author names, in order) for the GEO rows — the
    # author fallback when a series has no linked publication.
    geo_accs = [a for a in accessions if a.startswith("GSE")]
    contrib_map: dict[str, list] = {}
    if geo_accs:
        cur.execute(GEO_SERIES_CONTRIBUTORS_BATCH_SQL, {"acc": geo_accs})
        contrib_map = {r["accession"]: r["contributors"] for r in cur.fetchall()}

    # GSA rows: unified_metadata has none, so their submitter/org come off the
    # linked BioProject — the same place the project page reads them.
    gsa_accs = [a for a in accessions if a[:3] in ("CRA", "HRA")]
    gsa_map: dict[str, dict] = {}
    if gsa_accs:
        cur.execute(GSA_SUBMITTER_BATCH_SQL, {"acc": gsa_accs})
        gsa_map = {r["accession"]: r for r in cur.fetchall()}

    # center_name / submitter stay keyed on the row's own accession.
    cur.execute(SUBMITTER_BATCH_SQL, {"acc": accessions})
    sub_map = {}
    for r in cur.fetchall():
        sub_map[r["accession"]] = {
            "submitter": r["submitter"],
            "country_code": r["country_code"],
            "is_single_cell": r["is_single_cell"],
            "single_cell_modality": r["single_cell_modality"],
        }

    # pmid per candidate from each repo's own project table (pubmed_id /
    # pubmed_id_pysradb) — the authoritative source across all repos.
    cur.execute(PMID_VARIANT_BATCH_SQL, {"acc": candidate_list})
    cand_pmids: dict[str, list[str]] = {}
    for r in cur.fetchall():
        cand_pmids.setdefault(r["accession"], []).append(str(r["pmid"]))

    all_pmids = sorted({p for lst in cand_pmids.values() for p in lst})
    pubmed_map: dict[str, dict] = {}
    if all_pmids:
        cur.execute(PUBMED_BY_PMIDS_BATCH_SQL, {"pmids": all_pmids})
        pubmed_map = {r["pmid"]: r for r in cur.fetchall()}

    for row in rows:
        acc = row.get("accession")
        pubs = None
        for i, cand in enumerate(row_candidates.get(acc, [acc] if acc else [])):
            sp = pub_map.get(cand)
            if sp:
                pubs = sp
                break
            # flat publication_* columns only exist on the project's own row.
            built = _build_publications(
                cand_pmids.get(cand, []), pubmed_map, row if i == 0 else None
            )
            if built:
                pubs = built
                break
        row["publications"] = pubs
        if pubs and len(pubs) > 0:
            primary = pubs[0]
            row["pmid"] = primary.get("pmid")
            row["publication_title"] = primary.get("title")
            row["journal"] = primary.get("journal")
            row["doi"] = primary.get("doi")
            row["authors"] = primary.get("authors")
            row["citation_count"] = primary.get("citation_count")
        meta = sub_map.get(acc, {})
        gsa = gsa_map.get(acc, {})
        # unified_metadata has no GSA rows, so don't let a missing submitter erase
        # a center_name the per-source project query already supplied.
        row["center_name"] = (
            meta.get("submitter") or gsa.get("center_name") or row.get("center_name")
        )
        if not row.get("authors") and gsa.get("submitter_person"):
            row["authors"] = gsa["submitter_person"]
        row["country_code"] = meta.get("country_code")
        row["is_single_cell"] = meta.get("is_single_cell")
        row["single_cell_modality"] = meta.get("single_cell_modality")
        # Prefer real publication authors; otherwise use geo_series.contributors
        # (dedup preserving order — ingest may repeat names) over the center_name
        # fallback. Orgs are unchanged: center_name already carries them.
        names = list(
            dict.fromkeys(n for n in (contrib_map.get(acc) or []) if n and n.strip())
        )
        if names and (
            not row.get("authors") or row.get("authors") == row.get("center_name")
        ):
            row["authors"] = ", ".join(names)
        elif not row.get("authors") and row.get("center_name"):
            row["authors"] = row["center_name"]
    return rows


def _project_sql(accession: str) -> str:
    """Pick the per-source project query."""
    if accession.startswith("E-GEAD-"):
        return PROJECT_GEA_SQL
    if accession.startswith("E-"):
        return PROJECT_AE_SQL
    if accession.startswith("G"):
        return PROJECT_GEO_SQL
    if accession.startswith(("CRA", "HRA")):
        return PROJECT_GSA_SQL
    if accession.startswith("PRJ"):
        return PROJECT_ENA_SQL
    return PROJECT_SRA_SQL


def _fetch_project_row(cur, accession: str):
    """Project row for any source; a DDBJ-exclusive DRP has no sra_studies twin."""
    cur.execute(_project_sql(accession), (accession,))
    row = cur.fetchone()
    if not row and accession.startswith("DRP"):
        cur.execute(PROJECT_DRA_SQL, (accession,))
        row = cur.fetchone()
    return row


def _project_publications(accession: str, cur) -> list[dict]:
    """Linked publications for one accession via the uniform resolver, so
    server-side consumers (e.g. /cite) match what the project page shows."""
    row = _fetch_project_row(cur, accession.upper())
    if not row:
        return []
    _enrich_with_publications([row], cur)
    return row.get("publications") or []


def _pub_to_bibtex(pub: dict, accession: str) -> str:
    """Convert a publication dict from study_publications JSONB into a BibTeX @article string."""
    pmid = pub.get("pmid") or "unknown"
    fields = []

    if pub.get("title"):
        fields.append(f"  title   = {{{pub['title']}}}")

    authors = pub.get("authors")
    if authors:
        fields.append(
            f"  author  = {{{' and '.join(a.strip() for a in authors.split(','))}}}"
        )

    if pub.get("journal"):
        fields.append(f"  journal = {{{pub['journal']}}}")

    pub_date = pub.get("pub_date") or ""
    year = ""
    if pub_date:
        year = pub_date.strip().split()[0] if pub_date.strip() else ""
        if year.isdigit():
            fields.append(f"  year    = {{{year}}}")
        else:
            year = ""

    if pub.get("doi"):
        fields.append(f"  doi     = {{{pub['doi']}}}")

    fields.append(f"  pmid    = {{{pmid}}}")

    cite_key = f"PMID{pmid}"
    if authors:
        first_author = authors.split(",")[0].strip()
        last_name = first_author.split()[-1] if first_author else ""
        if last_name:
            cite_key = f"{last_name}{year}"

    body = ",\n".join(fields)
    return f"@article{{{cite_key},\n{body},\n}}"


def _transform_ae_sample_to_geo(sample: dict) -> dict:
    """Transform ArrayExpress sample to GEO-compatible format for frontend."""
    attrs = sample.get("attributes_json") or {}
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except:
            attrs = {}

    characteristics = [
        {"@tag": k, "#text": v}
        for k, v in attrs.items()
        if not k.startswith("comment[")
    ]

    return {
        "id": sample.get("id"),
        "accession": sample.get("ena_run")
        or sample.get("ena_experiment")
        or sample.get("source_name"),
        "title": sample.get("sample_title") or sample.get("source_name"),
        "description": sample.get("sample_description"),
        "channel_count": 1,
        "sample_type": sample.get("library_strategy"),
        "platform_ref": None,
        "organism": sample.get("organism"),
        "channels": [
            {
                "Characteristics": characteristics,
                "Source": sample.get("sample_source_name") or sample.get("source_name"),
                "Organism": {"#text": sample.get("organism"), "@taxid": ""},
                "Label": sample.get("library_strategy"),
                "@position": "1",
            }
        ],
        "hybridization_protocol": None,
        "scan_protocol": None,
    }


def _transform_gea_sample_to_geo(sample: dict) -> dict:
    """Transform a DDBJ GEA sample to GEO-compatible format for the frontend."""
    attrs = sample.get("attributes_json") or {}
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except json.JSONDecodeError:
            attrs = {}

    characteristics = [{"@tag": k, "#text": v} for k, v in attrs.items()]

    return {
        "id": sample.get("id"),
        # the DRR is the sample's handle on its FASTQ, so prefer it as the accession
        "accession": sample.get("dra_run")
        or sample.get("dra_experiment")
        or sample.get("biosample")
        or sample.get("source_name"),
        "title": sample.get("sample_title") or sample.get("source_name"),
        "description": sample.get("sample_description"),
        "channel_count": 1,
        "sample_type": sample.get("library_strategy"),
        "platform_ref": sample.get("instrument_model"),
        "organism": sample.get("organism"),
        "channels": [
            {
                "Characteristics": characteristics,
                "Source": sample.get("source_name"),
                "Organism": {
                    "#text": sample.get("organism"),
                    "@taxid": sample.get("taxon_id") or "",
                },
                "Label": sample.get("library_strategy"),
                "@position": "1",
            }
        ],
        "hybridization_protocol": None,
        "scan_protocol": None,
    }


def _fetch_geo_series_samples(
    cur, accession: str, source: str, limit: int | None = None, offset: int = 0
) -> list[dict]:
    if source in ("arrayexpress", "gea"):
        gea = source == "gea"
        base = GEA_EXPERIMENT_SAMPLES_SQL if gea else AE_EXPERIMENT_SAMPLES_SQL
        sql = base.rstrip().rstrip(";")
        if not gea:
            sql += "\nORDER BY COALESCE(ena_run, ena_experiment, source_name)"
        params: list = [accession]
        if limit is not None:
            sql += "\nLIMIT %s OFFSET %s"
            params += [limit, offset]
        cur.execute(sql, tuple(params))
        transform = _transform_gea_sample_to_geo if gea else _transform_ae_sample_to_geo
        return [transform(s) for s in cur.fetchall()]
    sql = GEO_SERIES_SAMPLES_SQL.rstrip().rstrip(";") + "\nORDER BY s.accession"
    params = [accession]
    if limit is not None:
        sql += "\nLIMIT %s OFFSET %s"
        params += [limit, offset]
    cur.execute(sql, tuple(params))
    return cur.fetchall()


def _count_geo_series_samples(cur, accession: str, source: str) -> int:
    if source == "gea":
        cur.execute(
            "SELECT COUNT(*) AS count FROM gea_samples "
            "WHERE experiment_accession = %s",
            (accession,),
        )
    elif source == "arrayexpress":
        cur.execute(
            "SELECT COUNT(*) AS count FROM arrayexpress_samples "
            "WHERE experiment_accession = %s",
            (accession,),
        )
    else:
        cur.execute(
            "SELECT COUNT(*) AS count FROM geo_samples s "
            "JOIN geo_series g ON s.accession = ANY (g.samples_ref) "
            "WHERE g.accession = %s",
            (accession,),
        )
    return cur.fetchone()["count"]


def rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _is_ena_study(study: str) -> bool:
    return study.upper().startswith("PRJ")


def _is_gsa_study(study: str) -> bool:
    return study.upper().startswith(("CRA", "HRA"))


def _is_gea_study(study: str) -> bool:
    return study.upper().startswith("E-GEAD-")


def _is_dra_only_study(cur, study: str) -> bool:
    """DDBJ-exclusive per the ingest's is_only_ddbj, which checks SRA *and* ENA, by
    DRP *and* BioProject."""
    if not study.upper().startswith("DRP"):
        return False
    cur.execute(
        "SELECT is_only_ddbj = 1 AS only_dra FROM dra_studies WHERE accession = %s",
        (study,),
    )
    row = cur.fetchone()
    return bool(row and row["only_dra"])


def _fetch_study_experiments(
    cur, study: str, limit: int | None = None, offset: int = 0
) -> list[dict]:
    if _is_gsa_study(study):
        sql = GSA_STUDY_EXPERIMENTS_SQL
        ena_style = True
    elif _is_ena_study(study):
        sql = ENA_STUDY_EXPERIMENTS_SQL
        ena_style = True
    elif _is_dra_only_study(cur, study):
        sql = DRA_STUDY_EXPERIMENTS_SQL
        ena_style = False
    else:
        sql = STUDY_EXPERIMENTS_SQL
        ena_style = False
    if limit is None:
        cur.execute(sql, (study,))
        return cur.fetchall()
    base = sql.rstrip().rstrip(";")
    paged = base + (
        "\nLIMIT %s OFFSET %s"
        if ena_style
        else "\nORDER BY accession\nLIMIT %s OFFSET %s"
    )
    cur.execute(paged, (study, limit, offset))
    return cur.fetchall()


def _count_study_experiments(cur, study: str) -> int:
    if _is_gsa_study(study):
        cur.execute(
            "SELECT COUNT(DISTINCT experiment_accession) AS count "
            "FROM gsa_experiments WHERE study_accession = UPPER(%s)",
            (study,),
        )
    elif _is_ena_study(study):
        cur.execute(
            "SELECT COUNT(DISTINCT experiment_accession) AS count "
            "FROM ena_experiments WHERE study_accession = UPPER(%s)",
            (study,),
        )
    elif _is_dra_only_study(cur, study):
        cur.execute(
            "SELECT COUNT(*) AS count FROM dra_experiments WHERE study = %s",
            (study,),
        )
    else:
        cur.execute(
            "SELECT COUNT(*) AS count FROM sra_experiments WHERE study = %s",
            (study,),
        )
    return cur.fetchone()["count"]


def _fetch_sample(cur, accession: str) -> dict | None:
    cur.execute(SAMPLE_SQL, (accession,))
    sample = cur.fetchone()
    if sample:
        return sample

    cur.execute(ENA_SAMPLE_SQL, (accession,))
    sample = cur.fetchone()
    if sample:
        return sample

    cur.execute(GSA_SAMPLE_SQL, (accession,))
    sample = cur.fetchone()
    if sample:
        return sample

    cur.execute(DRA_SAMPLE_SQL, (accession,))
    return cur.fetchone()


def _fetch_samples_batch(cur, accessions) -> dict[str, dict]:
    """Batch equivalent of _fetch_sample: SRA, then ENA, GSA, DDBJ fallbacks."""
    accs = list(dict.fromkeys(a for a in accessions if a))
    if not accs:
        return {}

    out: dict[str, dict] = {}
    cur.execute(BATCH_SAMPLES_SQL, (accs,))
    for s in cur.fetchall():
        out[s["accession"]] = s

    missing = [a for a in accs if a not in out]
    if missing:
        cur.execute(BATCH_ENA_SAMPLES_SQL, ([a.upper() for a in missing],))
        ena_by_upper = {s["accession"].upper(): s for s in cur.fetchall()}
        for a in missing:
            s = ena_by_upper.get(a.upper())
            if s:
                out[a] = s

    missing = [a for a in accs if a not in out]
    if missing:
        cur.execute(BATCH_GSA_SAMPLES_SQL, ([a.upper() for a in missing],))
        gsa_by_upper = {s["accession"].upper(): s for s in cur.fetchall()}
        for a in missing:
            s = gsa_by_upper.get(a.upper())
            if s:
                out[a] = s

    missing = [a for a in accs if a not in out]
    if missing:
        cur.execute(BATCH_DRA_SAMPLES_SQL, ([a.upper() for a in missing],))
        dra_by_upper = {s["accession"].upper(): s for s in cur.fetchall()}
        for a in missing:
            s = dra_by_upper.get(a.upper())
            if s:
                out[a] = s
    return out


def flatten_sra_study(cur, study: str) -> list[dict]:
    # 1. Fetch experiments (like /project/{study}/experiments endpoint)
    experiments = _fetch_study_experiments(cur, study)
    if not experiments:
        return []

    # 2. Fetch all unique samples (like frontend's fetchSamplesForExperiments)
    sample_accessions = []
    for e in experiments:
        if e.get("samples") and len(e["samples"]) > 0:
            sample_accessions.append(e["samples"][0])

    # Remove duplicates while preserving order
    unique_sample_accs = list(dict.fromkeys(sample_accessions))

    # Fetch all samples and parse attributes_json
    samples_map = {}
    for sample_acc, sample in _fetch_samples_batch(cur, unique_sample_accs).items():
        # Parse attributes_json if it's a string (like frontend does)
        attrs = sample.get("attributes_json")
        if attrs and isinstance(attrs, str):
            try:
                sample["attributes_json"] = json.loads(attrs)
            except (json.JSONDecodeError, ValueError):
                sample["attributes_json"] = None
        samples_map[sample_acc] = sample

    # 3. Collect all unique attribute keys (like frontend's useMemo)
    attribute_keys = set()
    for sample in samples_map.values():
        attrs = sample.get("attributes_json")
        if attrs and isinstance(attrs, dict):
            attribute_keys.update(attrs.keys())
        elif attrs and isinstance(attrs, list):
            for attr in attrs:
                if isinstance(attr, dict):
                    key = attr.get("tag") or attr.get("key")
                    if key:
                        attribute_keys.add(key)

    attribute_keys = sorted(attribute_keys)

    # 4. Build rows matching the CSV structure
    rows = []
    for e in experiments:
        if not e.get("samples") or len(e["samples"]) == 0:
            continue

        sample_acc = e["samples"][0]
        sample = samples_map.get(sample_acc)

        # Base row matching frontend's baseRow
        row = {
            "Accession": e.get("accession") or "-",
            "Title": e.get("title") or "-",
            "Library": e.get("library_name") or e.get("library_strategy") or "-",
            "Layout": e.get("library_layout") or "-",
            "Platform": e.get("platform") or "-",
            "Instrument": e.get("instrument_model") or "-",
            "Sample": sample_acc or "-",
            "Sample Alias": (sample.get("alias") if sample else None) or "-",
            "Sample Title": (sample.get("title") if sample else None) or "-",
            "Description": (sample.get("description") if sample else None) or "-",
            "Scientific Name": (sample.get("scientific_name") if sample else None)
            or "-",
            "Taxon ID": (sample.get("taxon_id") if sample else None) or "-",
        }

        # Add attribute columns matching frontend's attrRow
        attrs = sample.get("attributes_json") if sample else None
        attrs_map = None
        if isinstance(attrs, list):
            attrs_map = {
                (attr.get("tag") or attr.get("key")): attr.get("value")
                for attr in attrs
                if isinstance(attr, dict) and (attr.get("tag") or attr.get("key"))
            }
        for key in attribute_keys:
            if isinstance(attrs, dict):
                row[key] = attrs.get(key, "-")
            elif isinstance(attrs_map, dict):
                row[key] = attrs_map.get(key, "-")
            else:
                row[key] = "-"

        rows.append(row)

    return rows


def flatten_geo_series(cur, series: str) -> list[dict]:
    cur.execute(GEO_SERIES_SAMPLES_SQL, (series,))
    samples = cur.fetchall()
    if not samples:
        return []

    tags = set()
    for s in samples:
        for ch in s.get("channels") or []:
            chars = ch.get("Characteristics")
            if isinstance(chars, list):
                for c in chars:
                    if isinstance(c, dict):
                        tags.add(c.get("@tag"))
            elif isinstance(chars, dict):
                tags.add(chars.get("@tag"))

    rows = []
    for s in samples:
        channels = s.get("channels") or [None]

        for idx, ch in enumerate(channels):
            char_map = {}

            if ch:
                chars = ch.get("Characteristics")
                if isinstance(chars, list):
                    for c in chars:
                        if isinstance(c, dict):
                            char_map[c.get("@tag")] = c.get("#text")
                elif isinstance(chars, dict):
                    char_map[chars.get("@tag")] = chars.get("#text")

            organism_val = None
            if ch:
                org = ch.get("Organism")
                if isinstance(org, dict):
                    organism_val = org.get("#text")
                elif isinstance(org, list):
                    first = org[0] if org else None
                    if isinstance(first, dict):
                        organism_val = first.get("#text")
                    else:
                        organism_val = first
                else:
                    organism_val = org

            row = {
                "Sample": s["accession"],
                "Title": s["title"],
                "Description": s["description"],
                "Channel Count": s["channel_count"],
                "Sample Type": s["sample_type"],
                "Platform": s["platform_ref"],
                "Channel Position": ch.get("@position") if ch else idx + 1,
                "Label": ch.get("Label") if ch else None,
                "Source": ch.get("Source") if ch else None,
                "Molecule": ch.get("Molecule") if ch else None,
                "Organism": organism_val,
                "Label Protocol": ch.get("Label-Protocol") if ch else None,
                "Extract Protocol": ch.get("Extract-Protocol") if ch else None,
                "Hybridization Protocol": s["hybridization_protocol"],
                "Scan Protocol": s["scan_protocol"],
            }

            for tag in tags:
                row[tag] = char_map.get(tag)

            rows.append(row)

    return rows


def flatten_ae_experiment(cur, accession: str) -> list[dict]:
    cur.execute(AE_EXPERIMENT_SAMPLES_SQL, (accession,))
    samples = cur.fetchall()
    if not samples:
        return []

    # Collect all attribute keys across samples
    attribute_keys = set()
    for s in samples:
        attrs = s.get("attributes_json")
        if attrs and isinstance(attrs, dict):
            attribute_keys.update(attrs.keys())
    attribute_keys = sorted(attribute_keys)

    rows = []
    for s in samples:
        row = {
            "Experiment": s.get("experiment_accession") or "-",
            "Source Name": s.get("source_name") or "-",
            "Organism": s.get("organism") or "-",
            "Organism Part": s.get("organism_part") or "-",
            "Cell Type": s.get("cell_type") or "-",
            "Genotype": s.get("genotype") or "-",
            "Sample Source Name": s.get("sample_source_name") or "-",
            "Sample Title": s.get("sample_title") or "-",
            "Sample Description": s.get("sample_description") or "-",
            "Library Strategy": s.get("library_strategy") or "-",
            "Library Source": s.get("library_source") or "-",
            "Library Selection": s.get("library_selection") or "-",
            "ENA Experiment": s.get("ena_experiment") or "-",
            "ENA Run": s.get("ena_run") or "-",
        }

        attrs = s.get("attributes_json")
        for key in attribute_keys:
            if isinstance(attrs, dict):
                row[key] = attrs.get(key, "-")
            else:
                row[key] = "-"

        rows.append(row)

    return rows


def flatten_gea_experiment(cur, accession: str) -> list[dict]:
    cur.execute(GEA_EXPERIMENT_SAMPLES_SQL, (accession,))
    samples = cur.fetchall()
    if not samples:
        return []

    attribute_keys = set()
    for s in samples:
        attrs = s.get("attributes_json")
        if attrs and isinstance(attrs, dict):
            attribute_keys.update(attrs.keys())
    attribute_keys = sorted(attribute_keys)

    rows = []
    for s in samples:
        row = {
            "Experiment": s.get("experiment_accession") or "-",
            "Source Name": s.get("source_name") or "-",
            "Organism": s.get("organism") or "-",
            "Taxon ID": s.get("taxon_id") or "-",
            "Organism Part": s.get("organism_part") or "-",
            "Tissue": s.get("tissue") or "-",
            "Cell Type": s.get("cell_type") or "-",
            "Genotype": s.get("genotype") or "-",
            "Strain": s.get("strain") or "-",
            "Sex": s.get("sex") or "-",
            "Age": s.get("age") or "-",
            "BioSample": s.get("biosample") or "-",
            "Sample Title": s.get("sample_title") or "-",
            "Sample Description": s.get("sample_description") or "-",
            "Library Strategy": s.get("library_strategy") or "-",
            "Library Source": s.get("library_source") or "-",
            "Library Selection": s.get("library_selection") or "-",
            "Library Layout": s.get("library_layout") or "-",
            "Instrument Model": s.get("instrument_model") or "-",
            "DRA Experiment": s.get("dra_experiment") or "-",
            "DRA Run": s.get("dra_run") or "-",
        }

        attrs = s.get("attributes_json")
        for key in attribute_keys:
            row[key] = attrs.get(key, "-") if isinstance(attrs, dict) else "-"

        rows.append(row)

    return rows


def format_df(df: pd.DataFrame | None):
    if df is not None:
        grouped_by_srp = (
            df.groupby("study_accession")
            .apply(
                lambda g: g.drop(columns="study_accession").to_dict(orient="records")
            )
            .to_dict()
        )
        return grouped_by_srp
    else:
        return {"error": "No dataframe returned"}


def year_params(year_from: int | None, year_to: int | None) -> list:
    return [year_from, year_from, year_to, year_to]


def _normalize_to_extent(
    value: float, min_value: float, max_value: float, half_extent: float
) -> float:
    if max_value == min_value:
        return 0.0
    scaled = ((value - min_value) / (max_value - min_value)) * (half_extent * 2)
    return round(scaled - half_extent, 2)


def _normalize_country_metadata(
    country_pairs: list[dict] | None,
    raw_country_codes: list[str] | None,
    raw_country_labels: list[str] | None,
) -> tuple[list[str], list[str]]:
    normalized_pairs: list[tuple[str, str]] = []

    for pair in country_pairs or []:
        if not isinstance(pair, dict):
            continue
        code = pair.get("code")
        label = pair.get("label")
        code = code.strip().upper() if isinstance(code, str) and code.strip() else None
        label = label.strip() if isinstance(label, str) and label.strip() else None
        if code or label:
            normalized_pairs.append((code or label, label or code))

    if not normalized_pairs:
        for code in raw_country_codes or []:
            if isinstance(code, str) and code.strip():
                clean_code = code.strip().upper()
                normalized_pairs.append((clean_code, clean_code))
        for label in raw_country_labels or []:
            if isinstance(label, str) and label.strip():
                clean_label = label.strip()
                normalized_pairs.append((clean_label, clean_label))

    seen: set[tuple[str, str]] = set()
    country_codes: list[str] = []
    country_labels: list[str] = []
    for code, label in normalized_pairs:
        pair = (code, label)
        if pair in seen:
            continue
        seen.add(pair)
        country_codes.append(code)
        country_labels.append(label)

    return country_codes, country_labels


def _build_atlas_row(
    accession: str,
    archive: str,
    x: float,
    y: float,
    country_pairs: list[dict] | None,
    raw_country_codes: list[str] | None,
    raw_country_labels: list[str] | None,
) -> dict:
    country_codes, country_labels = _normalize_country_metadata(
        country_pairs, raw_country_codes, raw_country_labels
    )
    countries_display = ", ".join(country_labels)
    return {
        "accession": accession,
        "archive": archive,
        "country_codes": country_codes,
        "country_labels": country_labels,
        "x": x,
        "y": y,
    }


def _build_atlas_labels(
    min_x: float, max_x: float, min_y: float, max_y: float
) -> list[dict]:
    """Normalize cluster centroids into the atlas coordinate space."""
    clusters = _get_clusters_rows()
    if not clusters:
        return []
    sorted_clusters = sorted(clusters, key=lambda c: c["num_points"], reverse=True)
    n = len(sorted_clusters)
    labels: list[dict] = []
    for rank, cluster in enumerate(sorted_clusters):
        centroid = cluster["centroid"]
        if not centroid or len(centroid) < 2:
            continue
        pct = rank / n if n > 1 else 0.0
        if pct < 0.1:
            level = 0
        elif pct < 0.35:
            level = 1
        else:
            level = 2
        labels.append(
            {
                "text": cluster["title"],
                "x": _normalize_to_extent(
                    float(centroid[0]), min_x, max_x, _ATLAS_HALF_EXTENT
                ),
                "y": _normalize_to_extent(
                    float(centroid[1]), min_y, max_y, _ATLAS_HALF_EXTENT
                ),
                "level": level,
                "priority": cluster["num_points"],
            }
        )
    return labels


def _build_atlas_payloads() -> tuple[bytes, bytes, str, str, str, list[dict]]:
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(ATLAS_PROJECTS_ROWS_SQL)
            rows = cur.fetchall()

    if not rows:
        raw_bytes = b""
        gzip_bytes = gzip.compress(raw_bytes, compresslevel=6)
        raw_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]
        gzip_hash = hashlib.sha256(gzip_bytes).hexdigest()[:16]
        return (
            raw_bytes,
            gzip_bytes,
            f'"atlas-{_ATLAS_SCHEMA_VERSION}-{raw_hash}"',
            f'"atlas-{_ATLAS_SCHEMA_VERSION}-{gzip_hash}"',
            f"{_ATLAS_SCHEMA_VERSION}-{raw_hash}",
            [],
        )

    min_x = min(row["raw_x"] for row in rows)
    max_x = max(row["raw_x"] for row in rows)
    min_y = min(row["raw_y"] for row in rows)
    max_y = max(row["raw_y"] for row in rows)

    encoded_rows: list[str] = []
    for row in rows:
        atlas_row = _build_atlas_row(
            accession=row["accession"],
            archive=row["archive"],
            x=_normalize_to_extent(row["raw_x"], min_x, max_x, _ATLAS_HALF_EXTENT),
            y=_normalize_to_extent(row["raw_y"], min_y, max_y, _ATLAS_HALF_EXTENT),
            country_pairs=row.get("country_pairs"),
            raw_country_codes=row.get("raw_country_codes"),
            raw_country_labels=row.get("raw_country_labels"),
        )
        encoded_rows.append(json.dumps(atlas_row, separators=(",", ":")))

    raw_bytes = ("\n".join(encoded_rows) + "\n").encode("utf-8")
    gzip_bytes = gzip.compress(raw_bytes, compresslevel=6)
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()[:16]
    gzip_hash = hashlib.sha256(gzip_bytes).hexdigest()[:16]

    labels = _build_atlas_labels(min_x, max_x, min_y, max_y)

    return (
        raw_bytes,
        gzip_bytes,
        f'"atlas-{_ATLAS_SCHEMA_VERSION}-{raw_hash}"',
        f'"atlas-{_ATLAS_SCHEMA_VERSION}-{gzip_hash}"',
        f"{_ATLAS_SCHEMA_VERSION}-{raw_hash}",
        labels,
    )


def _get_atlas_payloads() -> tuple[bytes, bytes, str, str, str]:
    with _atlas_cache_lock:
        if _atlas_cache["ready"]:
            return (
                _atlas_cache["raw"],
                _atlas_cache["gzip"],
                _atlas_cache["etag_raw"],
                _atlas_cache["etag_gzip"],
                _atlas_cache["version"],
            )

        raw_bytes, gzip_bytes, etag_raw, etag_gzip, version, labels = (
            _build_atlas_payloads()
        )
        labels_json = json.dumps(labels, separators=(",", ":")).encode("utf-8")
        labels_hash = hashlib.sha256(labels_json).hexdigest()[:16]
        labels_etag = f'"atlas-labels-{_ATLAS_SCHEMA_VERSION}-{labels_hash}"'
        _atlas_cache["ready"] = True
        _atlas_cache["raw"] = raw_bytes
        _atlas_cache["gzip"] = gzip_bytes
        _atlas_cache["etag_raw"] = etag_raw
        _atlas_cache["etag_gzip"] = etag_gzip
        _atlas_cache["version"] = version
        _atlas_cache["labels"] = labels_json
        _atlas_cache["labels_etag"] = labels_etag
        return raw_bytes, gzip_bytes, etag_raw, etag_gzip, version


def _get_atlas_labels() -> tuple[bytes, str]:
    _get_atlas_payloads()  # ensure atlas cache (including labels) is populated
    with _atlas_cache_lock:
        return _atlas_cache["labels"], _atlas_cache["labels_etag"]


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    candidates = [value.strip() for value in if_none_match.split(",")]
    return "*" in candidates or etag in candidates


def _level_num(level: str) -> int:
    """Numeric suffix of a 'cluster_lN' layer name (for ordering coarse↔fine)."""
    digits = "".join(ch for ch in level if ch.isdigit())
    return int(digits) if digits else 0


def _get_cluster_labels() -> tuple[list[dict], list[str]]:
    """Load cluster_labels once, cached (cheap — a few thousand rows). Returns
    (labels, levels) with levels sorted finest→coarsest (cluster_l0 … cluster_lN,
    discovered at runtime — the layer count changes when re-clustered)."""
    with _clusters_cache_lock:
        if _clusters_cache.get("labels") is not None:
            return _clusters_cache["labels"], _clusters_cache["levels"]

        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(CLUSTER_LABELS_SQL)
                labels = cur.fetchall()

        levels = sorted({lbl["level"] for lbl in labels}, key=_level_num)
        _clusters_cache["labels"] = labels
        _clusters_cache["levels"] = levels
        return labels, levels


def _get_map_data() -> tuple[list[dict], list[dict], list[str]]:
    """Load the full map point set (heavy — ~1M rows) plus cluster labels, cached.
    Returns (rows, labels, levels). Only the tile builder needs the points; the
    label-only consumers use _get_cluster_labels."""
    with _map_data_cache_lock:
        if _map_data_cache["ready"]:
            return (
                _map_data_cache["rows"],
                _map_data_cache["labels"],
                _map_data_cache["levels"],
            )

        labels, levels = _get_cluster_labels()
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # One-time build query: the per-project enriched mode() aggregation
                # over all of ontology_samples_v4 can exceed the default 30s
                # statement_timeout. Raise it just for this query (explicit
                # transaction so SET LOCAL actually applies under autocommit, and
                # auto-resets when the block ends).
                with conn.transaction():
                    cur.execute("SET LOCAL statement_timeout = '600s'")
                    cur.execute(MAP_POINTS_SQL)
                    rows = cur.fetchall()

        _map_data_cache.update(ready=True, rows=rows, labels=labels, levels=levels)
        return rows, labels, levels


def _get_clusters_rows() -> list[dict]:
    # Back-compat shape (title/centroid/num_points) for /clusters.json and the
    # atlas labels, sourced from the coarsest cluster layer of cluster_labels.
    # Cheap filter over the cached labels — no need to cache the derived rows.
    labels, levels = _get_cluster_labels()
    coarsest = levels[-1] if levels else None
    return [
        {
            "title": lbl["label"],
            "centroid": [lbl["centroid_x"], lbl["centroid_y"]],
            "num_points": lbl["size"],
        }
        for lbl in labels
        if lbl["level"] == coarsest
    ]


# Label font scales by layer coarseness, not cluster size: coarser layers get
# smaller, more minimal labels; finer (more localized) layers a touch larger.
# Uniform within a layer, so no single big-cluster label dominates the view.
_LABEL_FINEST_PT = 15.0  # finest layer (cluster_l0)
_LABEL_COARSEST_PT = 10.0  # coarsest layer


def _label_font_pt(level: str, levels: list[str]) -> float:
    if len(levels) <= 1:
        return _LABEL_FINEST_PT
    rank = levels.index(level) / (len(levels) - 1)  # 0 finest .. 1 coarsest
    return round(_LABEL_FINEST_PT - rank * (_LABEL_FINEST_PT - _LABEL_COARSEST_PT), 2)


def _get_labels_config() -> dict:
    """Bootstrap for the dynamic label system: the layer list (finest→coarsest),
    the coordinate extent (so the frontend can map zoom depth → layer), and the
    max cluster id + cluster count per layer (so point coloring can size its color
    domain and avoid layers too large for deepscatter's color texture). Derived
    from the cached label set."""
    labels, levels = _get_cluster_labels()
    xs = [lbl["centroid_x"] for lbl in labels if lbl["centroid_x"] is not None]
    ys = [lbl["centroid_y"] for lbl in labels if lbl["centroid_y"] is not None]
    cluster_max: dict[str, int] = {}
    cluster_count: dict[str, int] = {}
    for lbl in labels:
        cid = int(lbl["cluster_id"])
        lvl = lbl["level"]
        if cid > cluster_max.get(lvl, -1):
            cluster_max[lvl] = cid
        cluster_count[lvl] = cluster_count.get(lvl, 0) + 1
    return {
        "levels": levels,
        "cluster_max": cluster_max,
        "cluster_count": cluster_count,
        "extent": {
            "minx": min(xs) if xs else 0.0,
            "maxx": max(xs) if xs else 0.0,
            "miny": min(ys) if ys else 0.0,
            "maxy": max(ys) if ys else 0.0,
        },
    }


# ---------------------------------------------------------------------------
# Map assets (deepscatter tiles + countries.json; labels served live via /map/labels)
#
# The frontend map is powered by deepscatter, which needs a quadtree of Arrow
# tiles and a country list (labels are served live via /map/labels). These are
# derived from the map points loaded by _get_map_data. We generate them once
# on the first request and cache them on disk under a content-hash version dir,
# so a restart with unchanged data reuses the existing tiles instead of
# rebuilding. Generation is heavy (pyarrow + quadfeather), so the builder module
# is imported lazily inside _ensure_map_assets.
# ---------------------------------------------------------------------------
_MAP_ASSETS_DIR = pathlib.Path(
    os.environ.get(
        "MAP_ASSETS_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "map_assets"),
    )
)
_MAP_ASSETS_CACHE_CONTROL = "public, max-age=31536000, immutable"
_PROJECT_CACHE_CONTROL = "public, s-maxage=2592000, stale-while-revalidate=2592000"
# Facets only change on reindex; cache hard at the edge so repeat/bot hits on a
# query are free and the slow first computation is paid at most once per query.
_SEARCH_FACETS_CACHE_CONTROL = "public, s-maxage=604800, stale-while-revalidate=2592000"
_MAP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAP_TILE_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_map_assets_lock = threading.Lock()
_map_assets_state: dict = {"ready": False, "version": None, "dir": None}
# Pointer to the last-built version, so a cold start can serve /map/meta without
# rescanning the ~800k-row point set. Cleared by /cache/purge to force a rebuild.
_MAP_CURRENT_FILE = _MAP_ASSETS_DIR / "CURRENT"


# Bump when the asset-generation logic (tile columns) changes, so the version hash
# moves and clients rebuild without a manual /cache/purge.
_MAP_ASSET_REV = "10-enrichment" if ENRICHMENT_ENABLED else "10-no-enrichment"

# How many of the finest layers (cluster_l0 …) to bake as categorical columns, used
# by the frontend for point coloring and for the cluster picker's filter. The picker
# lists the COARSEST baked layer (seqout-map.tsx picks the last level present in the
# tile columns), so this must cover every level or the coarse ones are advertised in
# /map/meta but silently unusable. Leiden ships 8: cluster_l0 (70348) … cluster_l7 (23).
_COLOR_LAYER_COUNT = 8


def _compute_map_version(rows: list[dict]) -> str:
    # Tiles depend on the points and their per-layer cluster ids (baked as sidecar
    # columns). Labels are served live, so they're intentionally not in this hash.
    h = hashlib.sha256()
    h.update(_MAP_ASSET_REV.encode("utf-8"))
    for r in rows:
        h.update(f"{r['accession']}|{r['x']}|{r['y']}|".encode("utf-8"))
        h.update(json.dumps(r["clusters"], sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()[:16]


def _assets_complete(version_dir: pathlib.Path) -> bool:
    """Every file build_assets produces, present. The manifest alone isn't enough:
    a build that dies between the JSON sidecars and the tiles (or the other way
    round) leaves a dir that looks built, and the map then 404s on countries.json
    forever — the version hash is stable, so nothing ever triggers a retry."""
    if not (version_dir / "tiles" / "manifest.feather").exists():
        return False
    if not (version_dir / "countries.json").exists():
        return False
    return all(
        (version_dir / "filter" / f"{col}.json").exists() for col in MAP_FILTER_COLUMNS
    )


def _prune_old_map_versions(keep: str) -> None:
    """Remove stale map-asset version dirs, keeping only `keep`. Best-effort, so
    disk holds at most one version. Leaves the CURRENT pointer and any in-progress
    `*.tmp` dirs alone (their names don't match the version pattern)."""
    try:
        entries = list(_MAP_ASSETS_DIR.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name != keep and entry.is_dir() and _MAP_ID_RE.match(entry.name):
            shutil.rmtree(entry, ignore_errors=True)


def _ensure_map_assets() -> tuple[str, pathlib.Path]:
    """Build the map assets if needed and return (version, version_dir).

    Thread-safe and idempotent: only the first caller builds; the build is
    written to a temp dir and atomically renamed into place so partial builds
    are never served.
    """
    with _map_assets_lock:
        if _map_assets_state["ready"]:
            return _map_assets_state["version"], _map_assets_state["dir"]

        # Fast path: a prior build (same asset rev) is recorded and its tiles are
        # on disk — serve it without loading/hashing the ~800k-row point set. The
        # pointer is "<rev>:<version>"; a rev bump (generation-logic change) or
        # /cache/purge ("Refresh data") invalidates it and forces a rebuild.
        try:
            cached_rev, _, cached = _MAP_CURRENT_FILE.read_text().strip().partition(":")
        except OSError:
            cached_rev, cached = "", ""
        if (
            cached_rev == _MAP_ASSET_REV
            and _MAP_ID_RE.match(cached)
            and _assets_complete(_MAP_ASSETS_DIR / cached)
        ):
            version_dir = _MAP_ASSETS_DIR / cached
            _prune_old_map_versions(cached)
            _map_assets_state.update(ready=True, version=cached, dir=version_dir)
            return cached, version_dir

        # Slow path: derive the content version and build the tiles if missing.
        rows, _labels, levels = _get_map_data()
        bake_levels = levels[:_COLOR_LAYER_COUNT]
        version = _compute_map_version(rows)
        version_dir = _MAP_ASSETS_DIR / version

        if not _assets_complete(version_dir):
            import map_assets  # lazy: pulls in pyarrow / quadfeather

            _MAP_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
            # Per-process temp dir: _map_assets_lock is in-process only, so with
            # several workers a shared "{version}.tmp" races — one worker renames it
            # into place while another is still writing to that path, and the loser
            # then rebuilds only the tail of the build (tiles, no countries.json)
            # and rmtrees the winner's complete dir on top of it.
            tmp_dir = _MAP_ASSETS_DIR / f"{version}.{os.getpid()}.tmp"
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.info(
                "Building map assets (version=%s, color layers=%s)...",
                version,
                bake_levels,
            )
            map_assets.build_assets(
                rows, bake_levels, tmp_dir, filter_columns=MAP_FILTER_COLUMNS
            )
            # Another worker may have finished the same build meanwhile — keep theirs.
            if _assets_complete(version_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)
            else:
                shutil.rmtree(version_dir, ignore_errors=True)
                os.replace(tmp_dir, version_dir)
                logger.info("Built map assets at %s", version_dir)

        _MAP_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        _MAP_CURRENT_FILE.write_text(f"{_MAP_ASSET_REV}:{version}")
        _prune_old_map_versions(version)
        _map_assets_state.update(ready=True, version=version, dir=version_dir)
        return version, version_dir


def _current_map_version() -> str | None:
    try:
        cached_rev, _, cached = _MAP_CURRENT_FILE.read_text().strip().partition(":")
    except OSError:
        return None
    if cached_rev != _MAP_ASSET_REV or not _MAP_ID_RE.match(cached):
        return None
    return cached


def _serve_map_file(version: str, rel_path: str) -> FileResponse:
    """Serve a file from the current map version dir, guarding against stale URLs."""
    if not _MAP_ID_RE.match(version) or _current_map_version() != version:
        raise HTTPException(404, "Not found")
    base = (_MAP_ASSETS_DIR / version).resolve()
    target = (base / rel_path).resolve()
    if target != base and not str(target).startswith(str(base) + os.sep):
        raise HTTPException(404, "Not found")
    if not target.is_file():
        raise HTTPException(404, "Not found")
    media_type = (
        "application/json" if target.suffix == ".json" else "application/octet-stream"
    )
    return FileResponse(
        target,
        media_type=media_type,
        headers={"Cache-Control": _MAP_ASSETS_CACHE_CONTROL},
    )


_GROWTH_SQL_BY_MODE = {
    "projects": {
        "geo": GROWTH_PROJECTS_GEO_SQL,
        "sra": GROWTH_PROJECTS_SRA_SQL,
        "arrayexpress": GROWTH_PROJECTS_AE_SQL,
        "ena": GROWTH_PROJECTS_ENA_SQL,
        "gsa": GROWTH_PROJECTS_GSA_SQL,
        "dra": GROWTH_PROJECTS_DRA_SQL,
        "gea": GROWTH_PROJECTS_GEA_SQL,
    },
    "experiments": {
        "geo": GROWTH_EXPERIMENTS_GEO_SQL,
        "sra": GROWTH_EXPERIMENTS_SRA_SQL,
        "arrayexpress": GROWTH_EXPERIMENTS_AE_SQL,
        "ena": GROWTH_EXPERIMENTS_ENA_SQL,
        "gsa": GROWTH_EXPERIMENTS_GSA_SQL,
        "dra": GROWTH_EXPERIMENTS_DRA_SQL,
        "gea": GROWTH_EXPERIMENTS_GEA_SQL,
    },
    "bases": {
        "ena": GROWTH_BASES_ENA_SQL,
        "sra_fastq_bytes": GROWTH_BYTES_SRA_FASTQ_SQL,
        "sra_sra_bytes": GROWTH_BYTES_SRA_SRA_SQL,
    },
}


def _growth_relation_name(mode: str) -> str | None:
    if mode in {"projects", "experiments"}:
        return "database_growth_monthly"
    if mode == "bases":
        return "database_volume_growth_monthly"
    return None


def _growth_relation_exists(conn, mode: str) -> bool:
    relation_name = _growth_relation_name(mode)
    if relation_name is None:
        return False
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT to_regclass(%s) IS NOT NULL AS available",
            (f"public.{relation_name}",),
        )
        row = cur.fetchone()
    return bool(row and row["available"])


def _fetch_growth_relation(conn, mode: str, db: str | None = None) -> dict[str, list]:
    data = _empty_growth_series(mode, db)
    if mode == "bases":
        sql = DATABASE_VOLUME_GROWTH_MONTHLY_SQL
        params = (db, db)
    else:
        sql = DATABASE_GROWTH_MONTHLY_SQL
        params = (mode, db, db)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        for row in cur.fetchall():
            data.setdefault(row["db"], []).append(
                {"month": row["month"], "count": row["count"]}
            )
    return data


def _empty_growth_series(mode: str, db: str | None = None) -> dict[str, list]:
    if db:
        return {db: []}
    return {db_name: [] for db_name in _GROWTH_SQL_BY_MODE.get(mode, {})}


def _build_growth_data(mode: str, db: str | None = None) -> dict:
    """Run only the requested growth query and return {db: rows}."""
    db_queries = _GROWTH_SQL_BY_MODE.get(mode)
    if db_queries is None:
        return {}
    if db and db not in db_queries:
        return {db: []}

    with pool.connection() as conn:
        if _growth_relation_exists(conn, mode):
            return _fetch_growth_relation(conn, mode, db)

        if mode == "bases":
            raise HTTPException(
                503, "Volume growth data is being prepared; retry shortly."
            )

        selected_queries = {db: db_queries[db]} if db else db_queries
        data = {}
        with conn.cursor(row_factory=dict_row) as cur:
            for db_name, sql in selected_queries.items():
                cur.execute(sql)
                data[db_name] = cur.fetchall()
        return data


_GROWTH_CACHE_TTL = 7 * 24 * 3600  # 7 days in seconds


def _normalize_stat_filter(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _get_bounded_cache_entry(cache: dict, key: tuple, ttl: float):
    entry = cache.get(key)
    if entry and (time.time() - entry["built_at"]) < ttl:
        return entry["data"]
    return None


def _set_bounded_cache_entry(
    cache: dict, key: tuple, data, max_entries: int, ttl: float
) -> None:
    now = time.time()
    stale = [k for k, v in cache.items() if now - v["built_at"] >= ttl]
    for k in stale:
        del cache[k]
    if len(cache) >= max_entries and key not in cache:
        oldest = min(cache, key=lambda k: cache[k]["built_at"])
        del cache[oldest]
    cache[key] = {"data": data, "built_at": now}


def _get_growth_data(mode: str, db: str | None = None) -> dict:
    cache_key = (mode, db)
    full_mode_key = (mode, None)
    now = time.time()
    with _growth_cache_lock:
        if db is not None:
            full_entry = _growth_cache.get(full_mode_key)
            if full_entry and (now - full_entry["built_at"]) < _GROWTH_CACHE_TTL:
                return {db: full_entry["data"].get(db, [])}

        entry = _growth_cache.get(cache_key)
        if entry and (now - entry["built_at"]) < _GROWTH_CACHE_TTL:
            return entry["data"]

    data = _build_growth_data(mode, db)

    with _growth_cache_lock:
        now = time.time()
        stale = [
            key
            for key, entry in _growth_cache.items()
            if now - entry["built_at"] >= _GROWTH_CACHE_TTL
        ]
        for key in stale:
            del _growth_cache[key]
        _growth_cache[cache_key] = {"data": data, "built_at": now}
    return data


def _replace_query_hyphens(q: str) -> str:
    """Treat hyphens as word separators in search text."""
    return q.replace("-", " ")


_ACCESSION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (GEO_SERIES_PATTERN, "gse"),
    (GEO_SAMPLE_PATTERN, "gsm"),
    (STUDY_PATTERN, "srp"),
    (EXPERIMENT_PATTERN, "srx"),
    (SAMPLE_PATTERN, "srs"),
    (RUN_PATTERN, "srr"),
    (BIOSAMPLE_PATTERN, "sam"),
    (AE_PATTERN, "ae"),
    (PRJ_PATTERN, "prj"),
    # GSA accessions reuse the shared kinds: CRA/HRA → prj, CRR/HRR → srr,
    # CRX/HRX → srx, HRS/SAMC → sam.
    (GSA_STUDY_PATTERN, "prj"),
    (GSA_RUN_PATTERN, "srr"),
    (GSA_EXPERIMENT_PATTERN, "srx"),
    (GSA_SAMPLE_PATTERN, "sam"),
)


def _classify_accession_query(q: str) -> tuple[str, str] | None:
    token = q.strip()
    if not token or any(ch.isspace() for ch in token):
        return None
    acc = token.upper()
    for pattern, kind in _ACCESSION_PATTERNS:
        if pattern.match(acc):
            return acc, kind
    return None


def _try_accession_search(
    q: str, cursor_rank, cursor_acc, db_filter: str | None = None
) -> dict | None:
    """Returns an empty result set (not None) when q matches an accession pattern
    but resolves to nothing. Irrelevant FTS matches are worse than zero results
    when the user clearly typed an accession.
    """
    if cursor_rank is not None or cursor_acc is not None:
        return None
    classified = _classify_accession_query(q)
    if classified is None:
        return None
    acc, kind = classified
    start = time.perf_counter()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SEARCH_BY_ACCESSION_SQL_BY_KIND[kind], {"acc": acc})
                rows = cur.fetchall()
                if rows:
                    _enrich_with_publications(rows, cur)
    except Exception:
        logger.exception("Database error in accession search")
        raise HTTPException(500, "Internal server error")
    if db_filter:
        rows = [r for r in rows if r.get("source") == db_filter]
    took_ms = (time.perf_counter() - start) * 1000
    return {
        "results": rows,
        "total": len(rows),
        "took_ms": round(took_ms, 2),
        "next_cursor": None,
    }


# after a Neo4j failure, skip expansion for the cooldown so an
# outage costs one timed-out request per window, not one per search.
_neo4j_expand_circuit = {"open_until": 0.0}
_NEO4J_EXPAND_COOLDOWN = 30.0


def _search_query_variants(q: str) -> list[str]:
    # Best-effort synonym expansion via Neo4j; degrade to the raw query on failure.
    if time.time() < _neo4j_expand_circuit["open_until"]:
        return [q]
    try:
        return list(dict.fromkeys(expand_terms(neo4j_driver, q)))
    except Exception:
        _neo4j_expand_circuit["open_until"] = time.time() + _NEO4J_EXPAND_COOLDOWN
        logger.warning(
            "Neo4j term expansion failed; skipping synonyms for %.0fs",
            _NEO4J_EXPAND_COOLDOWN,
        )
        return [q]


_SUGGESTION_THRESHOLD = 5


def _ensure_spelling_loaded() -> SymSpell:
    """Load vocabulary into SymSpell on first call."""
    if _spelling_cache["ready"]:
        return _spelling_cache["symspell"]
    with _spelling_cache_lock:
        if _spelling_cache["ready"]:
            return _spelling_cache["symspell"]
        sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        try:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT normalized, frequency FROM search_vocabulary")
                    for row in cur:
                        sym.create_dictionary_entry(row["normalized"], row["frequency"])
        except Exception:
            logger.warning(
                "search_vocabulary table not found; spelling suggestions disabled"
            )
        count = len(sym.words)
        logger.info("Loaded %d terms into SymSpell spelling dictionary", count)
        _spelling_cache["symspell"] = sym
        _spelling_cache["ready"] = True
        return sym


_VOCAB_KNOWN_SQL = """
SELECT w.word
FROM unnest(%s::text[]) AS w(word)
WHERE EXISTS (
    SELECT 1 FROM search_vocabulary v
    WHERE v.normalized = lower(w.word)
       OR v.normalized = btrim(strip(to_tsvector('english', lower(w.word)))::text, '''')
)
"""


def _known_words(words: list[str]) -> set[str]:
    """The words the corpus already contains, directly or via their English stem.

    search_vocabulary stores FTS lexemes, so an ordinary word ("single") is absent
    while its stem ("singl") is present. Matching the stem too is what makes a
    correctly-spelled word recognisable as real. Without it SymSpell finds the stem
    one edit away and "corrects" single -> singl, identifies -> identifi,
    genetic -> generic, rewriting valid queries into nonsense.

    On failure every word is reported known, so a lookup outage suppresses spelling
    correction rather than mangling the query.
    """
    if not words:
        return set()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(_VOCAB_KNOWN_SQL, (words,))
                return {r["word"].lower() for r in cur.fetchall()}
    except Exception:
        logger.warning("vocabulary lookup failed; skipping spelling correction")
        return {w.lower() for w in words}


def _get_suggestions(q: str) -> list[dict]:
    """Return spelling suggestions for a query with few/no FTS results.

    Uses the original query so corrections target what the user actually typed.

    Only genuinely misspelled words are corrected: a word the corpus already knows
    is left alone, however few results the query returned. Suggestions are always
    single tokens — substituting a multi-word vocabulary term ("Mappin's moa") for
    one word grows the query, and since a correction differs from its input by
    definition, the caller's auto-rerun would never reach a fixed point.
    """
    sym = _ensure_spelling_loaded()
    if not sym.words:
        return []
    words = q.split()
    corrections: list[dict] = []
    correction_map: dict[int, str] = {}  # word index -> suggested term

    checkable = [w for w in words if 3 <= len(w) <= 50]
    known = _known_words(checkable)

    for i, word in enumerate(words):
        if len(word) < 3 or len(word) > 50:
            continue
        lookup_word = word.lower()
        if lookup_word in known:
            continue
        results = sym.lookup(lookup_word, Verbosity.CLOSEST, max_edit_distance=2)
        if not results:
            continue
        suggested = results[0].term
        if suggested == lookup_word or " " in suggested:
            continue
        corrections.append(
            {
                "original": word,
                "suggested": suggested,
                "distance": results[0].distance,
            }
        )
        correction_map[i] = suggested

    if not corrections:
        return []

    corrected_words = list(words)
    for i, suggested in correction_map.items():
        corrected_words[i] = suggested
    return [{"corrected_query": " ".join(corrected_words), "corrections": corrections}]


@api_app.get(
    "/",
    summary="Health check",
    description="Return a simple status message to confirm the API is running.",
    operation_id="health_check",
)
def hello():
    return {"message": "pysradb-server is up and running!", "errors": None}


@api_app.post(
    "/cache/purge",
    summary="Purge all in-memory caches",
    description="Reset every server-side cache so the next request rebuilds from the database.",
    operation_id="purge_cache",
)
@limiter.limit("5/minute")
def purge_cache(request: Request):
    with _atlas_cache_lock:
        _atlas_cache["ready"] = False
    with _clusters_cache_lock:
        _clusters_cache["labels"] = None
        _clusters_cache["levels"] = None
    with _map_data_cache_lock:
        _map_data_cache["ready"] = False
    with _map_assets_lock:
        _map_assets_state["ready"] = False
        _MAP_CURRENT_FILE.unlink(missing_ok=True)
    with _growth_cache_lock:
        _growth_cache.clear()
    with _spelling_cache_lock:
        _spelling_cache["ready"] = False
    with _global_contributions_cache_lock:
        _global_contributions_cache["ready"] = False
    with _global_contributions_filters_cache_lock:
        _global_contributions_filters_cache["ready"] = False
    with _global_contributions_query_cache_lock:
        _global_contributions_query_cache.clear()
    with _country_filters_cache_lock:
        _country_filters_cache.clear()
    with _country_accessions_cache_lock:
        _country_accessions_cache.clear()
    with _organism_growth_cache_lock:
        _organism_growth_cache.clear()
    with _organisms_list_cache_lock:
        _organisms_list_cache.clear()
    with _organism_totals_cache_lock:
        _organism_totals_cache.clear()
    with _platform_growth_cache_lock:
        _platform_growth_cache.clear()
    with _platform_filters_cache_lock:
        _platform_filters_cache.clear()
    with _source_totals_cache_lock:
        _source_totals_cache["ready"] = False
    with _last_updated_cache_lock:
        _last_updated_cache["ready"] = False
    with _enriched_crosstab_cache_lock:
        _enriched_crosstab_cache.clear()
    with _broad_facets_cache_lock:
        _broad_facets_cache.clear()
    with _search_results_cache_lock:
        _search_results_cache.clear()
    with _author_projects_cache_lock:
        _author_projects_cache.clear()
    return {"message": "All caches purged."}


@api_app.get(
    "/atlas/projects.v1.ndjson",
    summary="Download atlas-ready project dataset",
    description=(
        "Return one precomputed atlas row per GEO/SRA project as newline-delimited "
        "JSON with final archive/country fields, prebuilt atlas text, and "
        "server-normalized x/y coordinates scaled to the frontend extent."
    ),
    operation_id="get_projects_atlas_v1",
)
@limiter.limit("20/minute")
def get_projects_atlas_v1(request: Request):
    try:
        raw_bytes, gzip_bytes, etag_raw, etag_gzip, version = _get_atlas_payloads()
    except Exception:
        logger.exception("Database error in get_projects_atlas_v1")
        raise HTTPException(500, "Internal server error")

    accepts_gzip = "gzip" in request.headers.get("accept-encoding", "").lower()
    content = gzip_bytes if accepts_gzip else raw_bytes
    etag = etag_gzip if accepts_gzip else etag_raw
    headers = {
        "Cache-Control": _ATLAS_CACHE_CONTROL,
        "ETag": etag,
        "Vary": "Accept-Encoding",
        "X-Atlas-Version": version,
    }
    if accepts_gzip:
        headers["Content-Encoding"] = "gzip"

    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers=headers,
    )


@api_app.get(
    "/atlas/labels.v1.json",
    summary="Atlas cluster labels",
    description=(
        "Return cluster labels normalized into the [-1100, 1100] atlas coordinate "
        "space used by /atlas/projects.v1.ndjson. Each label has text, x, y, level "
        "(zoom tier 0-2 derived from cluster rank), and priority (num_points). "
        "Cached in memory alongside the atlas project payloads."
    ),
    operation_id="get_atlas_labels_v1",
)
@limiter.limit("20/minute")
def get_atlas_labels_v1(request: Request):
    try:
        labels_json, etag = _get_atlas_labels()
    except Exception:
        logger.exception("Database error in get_atlas_labels_v1")
        raise HTTPException(500, "Internal server error")

    headers = {
        "Cache-Control": _ATLAS_CACHE_CONTROL,
        "ETag": etag,
    }
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    return Response(
        content=labels_json,
        media_type="application/json",
        headers=headers,
    )


@api_app.get(
    "/clusters.json",
    summary="Get clusters",
    description=(
        "Return static cluster rows with title, centroid, and num_points. "
        "Results are loaded once and cached in memory."
    ),
    operation_id="get_clusters",
)
@limiter.limit("20/minute")
def get_clusters(request: Request):
    try:
        rows = _get_clusters_rows()
    except Exception:
        logger.exception("Database error in get_clusters")
        raise HTTPException(500, "Internal server error")

    return JSONResponse(
        content=rows,
        headers={"Cache-Control": "public, max-age=2592000"},
    )


@api_app.get(
    "/map/meta",
    summary="Map asset version + label config",
    description=(
        "Return the current map-asset version plus the dynamic-label bootstrap: "
        "the cluster layers (finest→coarsest), the coordinate extent, the color "
        "layer and its max cluster id, and the global cluster-size range. Building "
        "the deepscatter tiles and country list happens lazily on the first call "
        "(this request may take a while the very first time); afterwards they are "
        "served from a disk cache. Labels themselves are fetched on demand from "
        "/map/labels based on the current zoom and viewport."
    ),
    operation_id="get_map_meta",
)
@limiter.limit("60/minute")
def get_map_meta(request: Request):
    try:
        version, _ = _ensure_map_assets()
        config = _get_labels_config()
    except Exception:
        logger.exception("Failed to build map assets")
        raise HTTPException(500, "Internal server error")

    filters = [
        {"key": c, "label": MAP_FILTER_LABELS.get(c, c), "file": f"filter/{c}.json"}
        for c in MAP_FILTER_COLUMNS
    ]
    return JSONResponse(
        content={"version": version, "filters": filters, **config},
        headers={"Cache-Control": "no-cache"},
    )


# The {rev} segment is an opaque, frontend-controlled cache-busting token (it is
# not used to locate files — assets live under {version} only). Bumping it gives
# the browser a fresh URL so it re-downloads instead of using its HTTP cache,
# which is how the frontend "purge and refetch" works for the tiles that
# deepscatter loads internally.
@api_app.get(
    "/map/labels",
    summary="Cluster labels for a layer within a viewport",
    description=(
        "Return cluster labels for one layer (matching the current zoom) within the "
        "given coordinate bbox, biggest cluster first, as a GeoJSON FeatureCollection "
        "ready for deepscatter add_labels. The frontend calls this on pan/zoom so only "
        "the on-screen, zoom-appropriate labels are ever loaded. Omit the bbox to get "
        "the whole layer (used once to map cluster ids to names)."
    ),
    operation_id="get_map_labels",
)
@limiter.limit("300/minute")
def get_map_labels(
    request: Request,
    level: str = Query(..., min_length=1, max_length=64),
    minx: float = Query(-1e18),
    miny: float = Query(-1e18),
    maxx: float = Query(1e18),
    maxy: float = Query(1e18),
    limit: int = Query(300, ge=1, le=2000),
):
    try:
        _, levels = _get_cluster_labels()
        if level not in levels:
            raise HTTPException(404, "Unknown level")
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    CLUSTER_LABELS_BBOX_SQL,
                    {
                        "level": level,
                        "minx": minx,
                        "maxx": maxx,
                        "miny": miny,
                        "maxy": maxy,
                        "limit": limit,
                    },
                )
                rows = cur.fetchall()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_map_labels")
        raise HTTPException(500, "Internal server error")

    font_pt = _label_font_pt(level, levels)  # uniform within a layer
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [r["centroid_x"], r["centroid_y"]],
            },
            "properties": {
                "label": r["label"],
                "size": font_pt,
                "level": level,
                "cluster_id": r["cluster_id"],
            },
        }
        for r in rows
    ]
    return JSONResponse(
        content={"type": "FeatureCollection", "features": features},
        headers={"Cache-Control": "public, max-age=300"},
    )


@api_app.get(
    "/map/{version}/{rev}/countries.json",
    summary="Map country list",
    operation_id="get_map_countries",
)
def get_map_countries(
    request: Request,
    version: str = Path(..., pattern=r"^[A-Za-z0-9_-]{1,64}$"),
    rev: str = Path(..., pattern=r"^[A-Za-z0-9_-]{1,64}$"),
):
    return _serve_map_file(version, "countries.json")


@api_app.get(
    "/map/{version}/{rev}/filter/{name}.json",
    summary="Map facet filter value list",
    operation_id="get_map_filter",
)
def get_map_filter(
    request: Request,
    name: str = Path(..., pattern=r"^[a-z_]{1,40}$"),
    version: str = Path(..., pattern=r"^[A-Za-z0-9_-]{1,64}$"),
    rev: str = Path(..., pattern=r"^[A-Za-z0-9_-]{1,64}$"),
):
    if name not in MAP_FILTER_COLUMNS:
        raise HTTPException(404, "Not found")
    return _serve_map_file(version, f"filter/{name}.json")


@api_app.get(
    "/map/{version}/{rev}/tiles/{tile_path:path}",
    summary="Map deepscatter tile",
    operation_id="get_map_tile",
)
def get_map_tile(
    request: Request,
    tile_path: str,
    version: str = Path(..., pattern=r"^[A-Za-z0-9_-]{1,64}$"),
    rev: str = Path(..., pattern=r"^[A-Za-z0-9_-]{1,64}$"),
):
    if ".." in tile_path or not _MAP_TILE_RE.match(tile_path):
        raise HTTPException(404, "Not found")
    return _serve_map_file(version, f"tiles/{tile_path}")


@api_app.get(
    "/stats/growth",
    summary="Database growth over time",
    description=(
        "Return monthly counts of projects, experiments, or data volume per database. "
        "modes: projects (studies/series count), experiments (samples count), "
        "bases (nucleotide bases for ENA, FASTQ/SRA bytes for SRA). "
        "Counts are per-month (not cumulative). "
        "Returns {mode, series: {db: [{month, count}]}, took_ms}."
    ),
    operation_id="get_growth_stats",
)
@limiter.limit("20/minute")
def get_growth_stats(
    request: Request,
    mode: str = Query(
        ...,
        pattern="^(projects|experiments|bases)$",
        description="Count projects, experiments, or data volume (bases/bytes)",
    ),
    db: str | None = Query(
        None,
        pattern="^(geo|sra|arrayexpress|ena|gsa|dra|gea|sra_fastq_bytes|sra_sra_bytes)$",
        description="Filter to a single series",
    ),
):
    t0 = time.perf_counter()
    try:
        series = _get_growth_data(mode, db)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_growth_stats")
        raise HTTPException(500, "Internal server error")

    return {
        "mode": mode,
        "series": series,
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


@api_app.get(
    "/stats/organism-growth",
    summary="Organism growth over time",
    description=(
        "Return monthly and cumulative experiment/sample counts for a given organism "
        "across GEO, SRA, ArrayExpress, and ENA databases. "
        "The organism parameter should be a scientific name (e.g. 'Homo sapiens'). "
        "mode=absolute returns raw counts; mode=percentage returns the organism's share "
        "of total experiments per month per database. "
        "Returns {organism, mode, series: {db: [{month, count, cumulative}]}, took_ms}."
    ),
    operation_id="get_organism_growth",
)
@limiter.limit("20/minute")
def get_organism_growth(
    request: Request,
    organism: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="Scientific name of the organism (e.g. 'Homo sapiens')",
    ),
    mode: str = Query(
        "absolute",
        pattern="^(absolute|percentage)$",
        description="absolute counts or percentage of total experiments",
    ),
    db: str | None = Query(
        None,
        pattern="^(geo|sra|arrayexpress|ena|gsa|dra|gea)$",
        description="Filter to a single database",
    ),
):
    t0 = time.perf_counter()
    cache_key = organism.lower()

    # Always fetch/cache the absolute organism data first
    with _organism_growth_cache_lock:
        now = time.time()
        cached = _organism_growth_cache.get(cache_key)
        if cached and (now - cached["built_at"]) < _GROWTH_CACHE_TTL:
            all_series = cached["data"]
        else:
            all_series = None

    if all_series is None:
        try:
            all_series = {db_name: [] for db_name in _STATS_SOURCES}
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(GROWTH_ORGANISM_SQL, (organism,))
                    for row in cur.fetchall():
                        all_series.setdefault(row["db"], []).append(
                            {"month": row["month"], "count": row["count"]}
                        )
            for db_rows in all_series.values():
                cumulative = 0
                for row in db_rows:
                    cumulative += row["count"]
                    row["cumulative"] = cumulative
        except Exception:
            logger.exception("Database error in get_organism_growth")
            raise HTTPException(500, "Internal server error")

        with _organism_growth_cache_lock:
            now = time.time()
            # Evict expired entries and cap size
            expired = [
                k
                for k, v in _organism_growth_cache.items()
                if now - v["built_at"] >= _GROWTH_CACHE_TTL
            ]
            for k in expired:
                del _organism_growth_cache[k]
            if len(_organism_growth_cache) >= _MAX_ORGANISM_CACHE_ENTRIES:
                oldest = min(
                    _organism_growth_cache,
                    key=lambda k: _organism_growth_cache[k]["built_at"],
                )
                del _organism_growth_cache[oldest]
            _organism_growth_cache[cache_key] = {
                "data": all_series,
                "built_at": now,
            }

    # For percentage mode, compute organism / total * 100 per month per db
    if mode == "percentage":
        try:
            growth_data = _get_growth_data("experiments")
        except Exception:
            logger.exception("Database error fetching total growth for percentage")
            raise HTTPException(500, "Internal server error")
        total_exp = growth_data

        result_series = {}
        dbs = [db] if db else list(_STATS_SOURCES)
        for db_name in dbs:
            org_rows = all_series.get(db_name, [])
            # Build lookup: month -> total count
            total_lookup = {r["month"]: r["count"] for r in total_exp.get(db_name, [])}
            pct_rows = []
            cum_org = 0
            cum_total = 0
            for row in org_rows:
                total = total_lookup.get(row["month"], 0)
                pct = round(row["count"] / total * 100, 4) if total > 0 else 0.0
                cum_org += row["count"]
                cum_total += total
                cum_pct = round(cum_org / cum_total * 100, 4) if cum_total > 0 else 0.0
                pct_rows.append(
                    {
                        "month": row["month"],
                        "count": pct,
                        "cumulative": cum_pct,
                    }
                )
            result_series[db_name] = pct_rows
        series = result_series
    else:
        if db:
            series = {db: all_series.get(db, [])}
        else:
            series = all_series

    return {
        "organism": organism,
        "mode": mode,
        "series": series,
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


@api_app.get(
    "/stats/organism-totals",
    summary="Total experiments per organism",
    description=(
        "Return total experiment/sample counts per organism across GEO, SRA, "
        "ArrayExpress, and ENA. Optionally filter by publication year range. "
        "Returns {organisms: [{organism, geo, sra, arrayexpress, ena, total}], "
        "year_from, year_to, total_organisms, took_ms}."
    ),
    operation_id="get_organism_totals",
)
@limiter.limit("20/minute")
def get_organism_totals(
    request: Request,
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
    limit: int | None = Query(
        None, ge=1, le=10000, description="Max organisms to return"
    ),
):
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(400, "year_from must be <= year_to")

    t0 = time.perf_counter()
    cache_key = f"{year_from}:{year_to}"

    with _organism_totals_cache_lock:
        now = time.time()
        cached = _organism_totals_cache.get(cache_key)
        if cached and (now - cached["built_at"]) < _GROWTH_CACHE_TTL:
            data = cached["data"][:limit] if limit else cached["data"]
            return {
                "organisms": data,
                "year_from": year_from,
                "year_to": year_to,
                "total_organisms": len(data),
                "took_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

    if year_from is not None or year_to is not None:
        sql = ORGANISM_TOTALS_YEAR_SQL
        params = (year_from or 1900, year_to or 2100)
    else:
        sql = ORGANISM_TOTALS_SQL
        params = ()

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_organism_totals")
        raise HTTPException(500, "Internal server error")

    # Pivot rows into {organism -> {geo, sra, arrayexpress, ena, total, common_name}}
    org_map: dict[str, dict] = {}
    for row in rows:
        name = row["organism"]
        if name not in org_map:
            org_map[name] = {
                "organism": name,
                "common_name": row.get("common_name"),
                **{db_name: 0 for db_name in _STATS_SOURCES},
                "total": 0,
            }
        org_map[name][row["db"]] = row["count"]
        org_map[name]["total"] += row["count"]

    organisms = sorted(org_map.values(), key=lambda x: x["total"], reverse=True)

    with _organism_totals_cache_lock:
        # Evict stale entries (>24h)
        now = time.time()
        stale = [
            k for k, v in _organism_totals_cache.items() if now - v["built_at"] > 86400
        ]
        for k in stale:
            del _organism_totals_cache[k]
        if len(_organism_totals_cache) >= _MAX_ORGANISM_CACHE_ENTRIES:
            oldest = min(
                _organism_totals_cache,
                key=lambda k: _organism_totals_cache[k]["built_at"],
            )
            del _organism_totals_cache[oldest]
        _organism_totals_cache[cache_key] = {
            "data": organisms,
            "built_at": now,
        }

    data = organisms[:limit] if limit else organisms
    return {
        "organisms": data,
        "year_from": year_from,
        "year_to": year_to,
        "total_organisms": len(data),
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


@api_app.get(
    "/stats/organism-search",
    summary="Search organisms by name",
    description=(
        "Search for organisms by partial name match (case-insensitive). "
        "Returns up to `limit` organisms sorted by total experiment count. "
        "Use this for typeahead/autocomplete in organism selectors."
    ),
    operation_id="search_organisms",
)
@limiter.limit("100/minute")
def search_organisms(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200, description="Search term"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
):
    t0 = time.perf_counter()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(ORGANISM_SEARCH_SQL, (f"%{q}%", f"%{q}%", limit))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in search_organisms")
        raise HTTPException(500, "Internal server error")

    return {
        "organisms": [
            {
                "organism": r["organism"],
                "total": r["total"],
                "common_name": r.get("common_name"),
            }
            for r in rows
        ],
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def _query_global_contributions(
    organism: str | None = None,
    assay_l1: str | None = None,
    assay_l2: str | None = None,
    place_type: str | None = None,
    address_type: str | None = None,
) -> list[dict]:
    """Aggregate unified_metadata by rounded lat/lng with optional filters."""
    sql, params = build_global_contributions_sql(
        organism, assay_l1, assay_l2, place_type, address_type
    )
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _get_global_contributions_cached() -> list[dict]:
    """Return unfiltered contributions from cache (7-day TTL)."""
    with _global_contributions_cache_lock:
        now = time.time()
        if (
            _global_contributions_cache["ready"]
            and (now - _global_contributions_cache["built_at"]) < _GROWTH_CACHE_TTL
        ):
            return _global_contributions_cache["data"]

        data = _query_global_contributions()
        _global_contributions_cache["ready"] = True
        _global_contributions_cache["data"] = data
        _global_contributions_cache["built_at"] = now
        return data


def _get_filtered_global_contributions_cached(
    organism: str | None = None,
    assay_l1: str | None = None,
    assay_l2: str | None = None,
    place_type: str | None = None,
    address_type: str | None = None,
) -> list[dict]:
    key = (organism, assay_l1, assay_l2, place_type, address_type)
    with _global_contributions_query_cache_lock:
        cached = _get_bounded_cache_entry(
            _global_contributions_query_cache, key, _GROWTH_CACHE_TTL
        )
        if cached is not None:
            return cached

    data = _query_global_contributions(
        organism, assay_l1, assay_l2, place_type, address_type
    )
    with _global_contributions_query_cache_lock:
        _set_bounded_cache_entry(
            _global_contributions_query_cache,
            key,
            data,
            _MAX_GLOBAL_CONTRIBUTION_QUERY_CACHE_ENTRIES,
            _GROWTH_CACHE_TTL,
        )
    return data


_global_contributions_filters_cache_lock = threading.Lock()
_global_contributions_filters_cache: dict = {
    "ready": False,
    "data": {},
    "built_at": 0.0,
}


def _get_global_contributions_filters() -> dict:
    """Return cached filter options (organisms, assay_l1, assay_l2)."""
    with _global_contributions_filters_cache_lock:
        now = time.time()
        if (
            _global_contributions_filters_cache["ready"]
            and (now - _global_contributions_filters_cache["built_at"])
            < _GROWTH_CACHE_TTL
        ):
            return _global_contributions_filters_cache["data"]

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(GLOBAL_CONTRIBUTIONS_FILTERS_SQL)
            rows = cur.fetchall()

    filters: dict[str, list] = {
        "organisms": [],
        "assay_l1": [],
        "assay_l2": [],
        "place_type": [],
        "address_type": [],
    }
    for r in rows:
        kind = r["kind"]
        entry = {"value": r["value"], "count": r["count"]}
        if kind == "organism":
            filters["organisms"].append(entry)
        elif kind == "assay_l1":
            filters["assay_l1"].append(entry)
        elif kind == "assay_l2":
            filters["assay_l2"].append(entry)
        elif kind == "place_type":
            filters["place_type"].append(entry)
        elif kind == "address_type":
            filters["address_type"].append(entry)

    with _global_contributions_filters_cache_lock:
        _global_contributions_filters_cache["ready"] = True
        _global_contributions_filters_cache["data"] = filters
        _global_contributions_filters_cache["built_at"] = time.time()

    return filters


def _get_country_scoped_filters(country: str, organism: str | None = None) -> dict:
    """Return filter options scoped to a specific country (and optionally organism)."""
    key = (country, organism)
    with _country_filters_cache_lock:
        cached = _get_bounded_cache_entry(
            _country_filters_cache, key, _GROWTH_CACHE_TTL
        )
        if cached is not None:
            return cached

    sql, params = build_country_filters_sql(country, organism=organism)
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    filters: dict[str, object] = {
        "organisms": [],
        "assay_l1": [],
        "assay_l2": [],
        "place_type": [],
        "address_type": [],
        "total": 0,
    }
    for r in rows:
        kind = r["kind"]
        if kind == "total":
            filters["total"] = r["count"]
            continue
        entry = {"value": r["value"], "count": r["count"]}
        if kind == "organism":
            filters["organisms"].append(entry)
        elif kind == "assay_l1":
            filters["assay_l1"].append(entry)
        elif kind == "assay_l2":
            filters["assay_l2"].append(entry)
        elif kind == "place_type":
            filters["place_type"].append(entry)
        elif kind == "address_type":
            filters["address_type"].append(entry)

    with _country_filters_cache_lock:
        _set_bounded_cache_entry(
            _country_filters_cache,
            key,
            filters,
            _MAX_COUNTRY_FILTERS_CACHE_ENTRIES,
            _GROWTH_CACHE_TTL,
        )
    return filters


@api_app.get(
    "/stats/global-contributions",
    summary="Geographic contributions across all databases",
    description=(
        "Return aggregated project/experiment/sample counts per geographic location "
        "(latitude/longitude rounded to 2 decimal places) from the unified_metadata table. "
        "Includes source breakdown (GEO/SRA/ArrayExpress/ENA), city, country, and top 5 organisms "
        "per location. Unfiltered and filtered results are cached for 7 days."
    ),
    operation_id="get_global_contributions",
)
@limiter.limit("20/minute")
def get_global_contributions(
    request: Request,
    response: Response,
    organism: str | None = Query(
        None, description="Filter by dominant scientific name"
    ),
    assay_l1: str | None = Query(None, description="Filter by dominant assay L1"),
    assay_l2: str | None = Query(None, description="Filter by dominant assay L2"),
    place_type: str | None = Query(
        None, description="Filter by place type (e.g. university, hospital)"
    ),
    address_type: str | None = Query(
        None, description="Filter by address type (e.g. building, amenity)"
    ),
):
    t0 = time.perf_counter()
    organism = _normalize_stat_filter(organism)
    assay_l1 = _normalize_stat_filter(assay_l1)
    assay_l2 = _normalize_stat_filter(assay_l2)
    place_type = _normalize_stat_filter(place_type)
    address_type = _normalize_stat_filter(address_type)
    has_filters = organism or assay_l1 or assay_l2 or place_type or address_type
    try:
        if has_filters:
            data = _get_filtered_global_contributions_cached(
                organism, assay_l1, assay_l2, place_type, address_type
            )
        else:
            data = _get_global_contributions_cached()
            response.headers["Cache-Control"] = _SEARCH_FACETS_CACHE_CONTROL
    except Exception:
        logger.exception("Database error in get_global_contributions")
        raise HTTPException(500, "Internal server error")

    response.headers["Cache-Control"] = _STATS_CACHE_CONTROL
    return {
        "locations": data,
        "total": len(data),
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


@api_app.get(
    "/stats/global-contribution-filters",
    summary="Filter options for global contributions map",
    description=(
        "Return distinct values for organism, assay_l1, and assay_l2 filters, "
        "ordered by count descending. When country is provided, counts are scoped "
        "to that country. Global and country-scoped results are cached for 7 days."
    ),
    operation_id="get_global_contribution_filters",
)
@limiter.limit("20/minute")
def get_global_contribution_filters(
    request: Request,
    response: Response,
    country: str | None = Query(
        None, description="Scope filter counts to this country"
    ),
    organism: str | None = Query(
        None,
        description="Scope assay counts to this organism (requires country)",
    ),
):
    t0 = time.perf_counter()
    country = _normalize_stat_filter(country)
    organism = _normalize_stat_filter(organism)
    try:
        if country:
            filters = _get_country_scoped_filters(country, organism=organism)
        else:
            filters = _get_global_contributions_filters()
    except Exception:
        logger.exception("Database error in get_global_contribution_filters")
        raise HTTPException(500, "Internal server error")

    response.headers["Cache-Control"] = _STATS_CACHE_CONTROL
    return {
        **filters,
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def _get_country_accessions_text(
    country: str, organism: str | None = None, assay_l2: str | None = None
) -> str:
    key = (country, organism, assay_l2, MAX_COUNTRY_ACCESSIONS)
    with _country_accessions_cache_lock:
        cached = _get_bounded_cache_entry(
            _country_accessions_cache, key, _GROWTH_CACHE_TTL
        )
        if cached is not None:
            return cached

    sql, params = build_country_accessions_sql(
        country, organism=organism, assay_l2=assay_l2, limit=MAX_COUNTRY_ACCESSIONS
    )
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    text = "\n".join(r[0] for r in rows if r[0])

    with _country_accessions_cache_lock:
        _set_bounded_cache_entry(
            _country_accessions_cache,
            key,
            text,
            _MAX_COUNTRY_ACCESSIONS_CACHE_ENTRIES,
            _GROWTH_CACHE_TTL,
        )
    return text


@api_app.get(
    "/stats/global-contributions/accessions",
    summary="Project accessions for a country",
    description=(
        "Return distinct project accessions from unified_metadata for a given country, "
        "optionally filtered by organism and assay. Returns a plain newline-separated "
        "text list of accessions."
    ),
    operation_id="get_country_accessions",
    response_class=PlainTextResponse,
)
@limiter.limit("10/minute")
def get_country_accessions(
    request: Request,
    country: str = Query(..., description="Country name"),
    organism: str | None = Query(None, description="Filter by organism"),
    assay_l2: str | None = Query(None, description="Filter by assay type"),
):
    country = _normalize_stat_filter(country)
    organism = _normalize_stat_filter(organism)
    assay_l2 = _normalize_stat_filter(assay_l2)
    if not country:
        raise HTTPException(400, "country is required")

    try:
        text = _get_country_accessions_text(
            country, organism=organism, assay_l2=assay_l2
        )
    except Exception:
        logger.exception("Database error in get_country_accessions")
        raise HTTPException(500, "Internal server error")
    return PlainTextResponse(text, headers={"Cache-Control": _STATS_CACHE_CONTROL})


@api_app.get(
    "/organisms",
    summary="List all organisms",
    description=(
        "Return all distinct organism names (scientific names) found across "
        "GEO, SRA, ArrayExpress, and ENA databases. "
        "Set common_names=true to include common name mappings. "
        "Returns {organisms: [...], total: int, took_ms: float}."
    ),
    operation_id="list_organisms",
)
@limiter.limit("20/minute")
def list_organisms(
    request: Request,
    common_names: bool = Query(
        False,
        description="Include common name for each organism",
    ),
):
    t0 = time.perf_counter()
    variant = "common" if common_names else "plain"

    with _organisms_list_cache_lock:
        now = time.time()
        cached = _organisms_list_cache.get(variant)
        if cached and (now - cached["built_at"]) < _GROWTH_CACHE_TTL:
            return {
                "organisms": cached["data"],
                "total": len(cached["data"]),
                "took_ms": round((time.perf_counter() - t0) * 1000, 2),
            }

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if common_names:
                    cur.execute(LIST_ORGANISMS_WITH_COMMON_NAMES_SQL)
                    rows = cur.fetchall()
                    organisms = [
                        {
                            "scientific_name": r["scientific_name"],
                            "common_name": r["common_name"],
                        }
                        for r in rows
                    ]
                else:
                    cur.execute(LIST_ORGANISMS_SQL)
                    rows = cur.fetchall()
                    organisms = [r["organism"] for r in rows]
    except Exception:
        logger.exception("Database error in list_organisms")
        raise HTTPException(500, "Internal server error")

    with _organisms_list_cache_lock:
        _organisms_list_cache[variant] = {
            "data": organisms,
            "built_at": time.time(),
        }

    return {
        "organisms": organisms,
        "total": len(organisms),
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


# ---
# Platform display names
# ---
_PLATFORM_DISPLAY_NAMES: dict[str, str] = {}
_platform_csv = os.path.join(os.path.dirname(__file__), "data", "platforms.csv")
if os.path.exists(_platform_csv):
    with open(_platform_csv) as _f:
        for _row in csv.DictReader(_f):
            _PLATFORM_DISPLAY_NAMES[_row["platform"]] = _row["display_name"]


@api_app.get("/platforms", include_in_schema=False)
@api_app.get(
    "/stats/platform-totals",
    summary="Total experiments per platform",
    description=(
        "Return total experiment counts per sequencing platform across databases. "
        "Also available at /platforms."
    ),
    operation_id="get_platform_totals",
)
@limiter.limit("20/minute")
def get_platform_totals(request: Request):
    t0 = time.perf_counter()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(PLATFORM_TOTALS_SQL)
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_platform_totals")
        raise HTTPException(500, "Internal server error")

    platforms = [
        {
            "name": r["platform"],  # alias for backwards compat
            "platform": r["platform"],
            "display_name": _PLATFORM_DISPLAY_NAMES.get(r["platform"], r["platform"]),
            "total": r["total"],
            "geo": r["geo"],
            "sra": r["sra"],
            "ena": r["ena"],
            "gsa": r["gsa"],
            "dra": r["dra"],
            "gea": r["gea"],
        }
        for r in rows
    ]
    return {
        "platforms": platforms,
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


_platform_growth_cache: dict[str, dict] = {}
_platform_growth_cache_lock = threading.Lock()


@api_app.get(
    "/stats/platform-growth",
    summary="Platform growth over time",
    description=(
        "Return monthly experiment or project counts for a sequencing platform. "
        "Optionally filter by instrument_model. "
        "Returns {platform, mode, series: {db: [{month, count, cumulative}]}, took_ms}."
    ),
    operation_id="get_platform_growth",
)
@limiter.limit("20/minute")
def get_platform_growth(
    request: Request,
    platform: str = Query(..., description="Platform code, e.g. ILLUMINA"),
    mode: str = Query("experiments", pattern="^(experiments|projects)$"),
    instrument_model: str | None = Query(
        None, description="Drill down to specific instrument"
    ),
    organism: str | None = Query(
        None, description="Filter by organism (dominant_scientific_name)"
    ),
    assay: str | None = Query(None, description="Filter by assay (dominant_assay_l2)"),
    country: str | None = Query(None, description="Filter by country"),
):
    has_cross_filter = any([organism, assay, country])
    cache_key = f"{platform}:{mode}:{instrument_model or ''}:{organism or ''}:{assay or ''}:{country or ''}"
    with _platform_growth_cache_lock:
        now = time.time()
        cached = _platform_growth_cache.get(cache_key)
        if cached and (now - cached["built_at"]) < _GROWTH_CACHE_TTL:
            return {
                "platform": platform,
                "display_name": _PLATFORM_DISPLAY_NAMES.get(platform, platform),
                "mode": mode,
                "instrument_model": instrument_model,
                "filters": {"organism": organism, "assay": assay, "country": country},
                "series": cached["series"],
                "took_ms": 0,
            }

    t0 = time.perf_counter()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if has_cross_filter:
                    # Live query when cross-filters are active
                    sql, params = build_platform_growth_filtered_sql(
                        platform,
                        mode,
                        instrument_model,
                        organism,
                        assay,
                        country,
                    )
                    cur.execute(sql, params)
                elif mode == "projects":
                    cur.execute(GROWTH_PLATFORM_PROJECTS_SQL, (platform,))
                elif instrument_model:
                    cur.execute(
                        GROWTH_PLATFORM_INSTRUMENT_SQL, (platform, instrument_model)
                    )
                else:
                    cur.execute(GROWTH_PLATFORM_SQL, (platform,))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_platform_growth")
        raise HTTPException(500, "Internal server error")

    series: dict[str, list] = {}
    for r in rows:
        db_name = r["db"]
        if db_name not in series:
            series[db_name] = []
        series[db_name].append({"month": r["month"], "count": r["count"]})

    for db_name, points in series.items():
        cumulative = 0
        for p in points:
            cumulative += p["count"]
            p["cumulative"] = cumulative

    with _platform_growth_cache_lock:
        # Evict stale entries and cap at 500
        now_ts = time.time()
        stale = [
            k
            for k, v in _platform_growth_cache.items()
            if now_ts - v["built_at"] >= _GROWTH_CACHE_TTL
        ]
        for k in stale:
            del _platform_growth_cache[k]
        if len(_platform_growth_cache) >= 500:
            oldest = min(
                _platform_growth_cache,
                key=lambda k: _platform_growth_cache[k]["built_at"],
            )
            del _platform_growth_cache[oldest]
        _platform_growth_cache[cache_key] = {
            "series": series,
            "built_at": now_ts,
        }

    return {
        "platform": platform,
        "display_name": _PLATFORM_DISPLAY_NAMES.get(platform, platform),
        "mode": mode,
        "instrument_model": instrument_model,
        "filters": {"organism": organism, "assay": assay, "country": country},
        "series": series,
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


@api_app.get(
    "/stats/platform-instruments",
    summary="Instruments for a platform",
    description="Return instrument models for a platform with experiment counts.",
    operation_id="get_platform_instruments",
)
@limiter.limit("20/minute")
def get_platform_instruments(
    request: Request,
    platform: str = Query(..., description="Platform code, e.g. ILLUMINA"),
):
    t0 = time.perf_counter()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(PLATFORM_INSTRUMENTS_SQL, (platform,))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_platform_instruments")
        raise HTTPException(500, "Internal server error")

    return {
        "platform": platform,
        "instruments": [
            {"instrument_model": r["instrument_model"], "total": r["total"]}
            for r in rows
        ],
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


_platform_filters_cache: dict[str, dict] = {}
_platform_filters_cache_lock = threading.Lock()


@api_app.get(
    "/stats/platform-filters",
    summary="Filter options for a platform",
    description=(
        "Return available organisms, assays, and countries for a platform. "
        "Used to populate filter dropdowns in the sequencing technology stats card."
    ),
    operation_id="get_platform_filters",
)
@limiter.limit("20/minute")
def get_platform_filters(
    request: Request,
    platform: str = Query(..., description="Platform code, e.g. ILLUMINA"),
):
    with _platform_filters_cache_lock:
        cached = _platform_filters_cache.get(platform)
        if cached and (time.time() - cached["built_at"]) < _GROWTH_CACHE_TTL:
            return {
                "platform": platform,
                "organisms": cached["organisms"],
                "assays": cached["assays"],
                "countries": cached["countries"],
                "took_ms": 0,
            }

    t0 = time.perf_counter()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SET LOCAL statement_timeout = '30s'")
                cur.execute(PLATFORM_FILTERS_SQL, (platform,))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_platform_filters")
        raise HTTPException(500, "Internal server error")

    organisms = []
    assays = []
    countries = []
    for r in rows:
        item = {"value": r["value"], "count": r["count"]}
        if r["filter_type"] == "organism":
            organisms.append(item)
        elif r["filter_type"] == "assay":
            assays.append(item)
        elif r["filter_type"] == "country":
            countries.append(item)

    with _platform_filters_cache_lock:
        _platform_filters_cache[platform] = {
            "organisms": organisms,
            "assays": assays,
            "countries": countries,
            "built_at": time.time(),
        }

    return {
        "platform": platform,
        "organisms": organisms,
        "assays": assays,
        "countries": countries,
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


@api_app.get(
    "/search/geo",
    summary="Search GEO",
    description=(
        "Full-text search over GEO series and related metadata. "
        "Use cursors (rank + accession) for pagination. "
        "Results objects: {accession: string, title: string, summary: string | null, "
        "updated_at: string | null, organisms: string[] | null, rank: float, source: 'geo'}. "
        "Returns {results: object[], total: int, took_ms: float, "
        "next_cursor: {rank: float, accession: string} | null}."
    ),
    operation_id="search_geo",
)
@limiter.limit("100/minute")
def search_geo(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    cursor_rank: float | None = Query(None),
    cursor_acc: str | None = Query(None),
):
    acc_result = _try_accession_search(q, cursor_rank, cursor_acc, db_filter="geo")
    if acc_result is not None:
        return acc_result
    return _search_geo_impl(request, _replace_query_hyphens(q), cursor_rank, cursor_acc)


def _search_geo_impl(request, q, cursor_rank, cursor_acc, offset=None):
    start = time.perf_counter()

    try:
        with pool.connection() as conn:
            q_variants = _search_query_variants(q)
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SET LOCAL statement_timeout = '60s'")
                cur.execute(
                    GEO_SEARCH_SQL,
                    (q, q_variants, cursor_rank, cursor_rank, cursor_acc, offset),
                )
                rows = cur.fetchall()
                _enrich_with_publications(rows, cur)
    except Exception as e:
        logger.exception("Database error in search_geo")
        raise HTTPException(500, "Internal server error")

    took_ms = (time.perf_counter() - start) * 1000
    total = rows[0]["total_count"] if rows else 0

    return {
        "results": rows,
        "total": total,
        "took_ms": round(took_ms, 2),
        "next_cursor": (
            None
            if not rows
            else {
                "rank": rows[-1]["rank"],
                "accession": rows[-1]["accession"],
            }
        ),
    }


@api_app.get(
    "/search/sra",
    summary="Search SRA",
    description=(
        "Full-text search over SRA studies/experiments and related metadata. "
        "Use cursors (rank + accession) for pagination. "
        "Results objects: {accession: string, title: string, summary: string | null, "
        "updated_at: string | null, organisms: string[] | null, rank: float, source: 'sra'}. "
        "Returns {results: object[], total: int, took_ms: float, "
        "next_cursor: {rank: float, accession: string} | null}."
    ),
    operation_id="search_sra",
)
@limiter.limit("100/minute")
def search_sra(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    cursor_rank: float | None = Query(None),
    cursor_acc: str | None = Query(None),
):
    acc_result = _try_accession_search(q, cursor_rank, cursor_acc, db_filter="sra")
    if acc_result is not None:
        return acc_result
    return _search_sra_impl(request, _replace_query_hyphens(q), cursor_rank, cursor_acc)


def _search_sra_impl(request, q, cursor_rank, cursor_acc, offset=None):
    start = time.perf_counter()

    try:
        with pool.connection() as conn:
            q_variants = _search_query_variants(q)
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SET LOCAL statement_timeout = '60s'")
                cur.execute(
                    SRA_SEARCH_SQL,
                    (q, q_variants, cursor_rank, cursor_rank, cursor_acc, offset),
                )
                rows = cur.fetchall()
                _enrich_with_publications(rows, cur)
    except Exception as e:
        logger.exception("Database error in search_sra")
        raise HTTPException(500, "Internal server error")

    took_ms = (time.perf_counter() - start) * 1000
    total = rows[0]["total_count"] if rows else 0

    return {
        "results": rows,
        "total": total,
        "took_ms": round(took_ms, 2),
        "next_cursor": (
            None
            if not rows
            else {
                "rank": rows[-1]["rank"],
                "accession": rows[-1]["accession"],
            }
        ),
    }


@api_app.get(
    "/search/suggest",
    summary="Spelling suggestions",
    description=(
        "Return spelling correction suggestions for a query. "
        "Useful when a search returns 0 results due to typos."
    ),
    operation_id="search_suggest",
)
@limiter.limit("100/minute")
def search_suggest(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
):
    start = time.perf_counter()
    search_q = _replace_query_hyphens(q)
    suggestions = _get_suggestions(search_q)
    took_ms = (time.perf_counter() - start) * 1000
    result: dict = {"suggestions": suggestions, "took_ms": round(took_ms, 2)}
    return result


# Deep dive: ontology-hierarchy exploration of query terms


@api_app.get(
    "/search/deep-dive/terms",
    summary="Deep-dive candidate terms",
    description=(
        "For a search query, return the sub-phrases that exist in the ontology "
        "graph AND have hierarchy children (SUBCLASS_OF / PART_OF), traversing "
        "MAPS_TO synonyms transparently. Powers the search page 'Deep dive for' picker. "
        "Returns {terms: [{term, name, child_count}], took_ms}."
    ),
    operation_id="deep_dive_terms",
)
@limiter.limit("100/minute")
def deep_dive_terms(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
):
    start = time.perf_counter()
    try:
        terms = fetch_deep_dive_terms(neo4j_driver, q)
    except Exception:
        logger.exception("deep_dive_terms failed for q=%r", q)
        raise HTTPException(500, "Internal server error")
    took_ms = (time.perf_counter() - start) * 1000
    return {"terms": terms, "took_ms": round(took_ms, 2)}


@api_app.get(
    "/search/deep-dive/children",
    summary="Deep-dive term children",
    description=(
        "Return the direct hierarchy children of a graph term (SUBCLASS_OF and "
        "PART_OF merged), traversing MAPS_TO synonyms transparently. Each child "
        "carries has_children so the graph knows if it can expand further. "
        "Capped at 300 children. Returns {term, children: [{name, has_children}], took_ms}."
    ),
    operation_id="deep_dive_children",
)
@limiter.limit("100/minute")
def deep_dive_children(
    request: Request,
    term: str = Query(..., min_length=1, max_length=200),
):
    start = time.perf_counter()
    try:
        children = fetch_deep_dive_children(neo4j_driver, term)
    except Exception:
        logger.exception("deep_dive_children failed for term=%r", term)
        raise HTTPException(500, "Internal server error")
    took_ms = (time.perf_counter() - start) * 1000
    return {"term": term.lower(), "children": children, "took_ms": round(took_ms, 2)}


# db= picks a per-source query instead of filtering the unified one; every source
# with its own search index belongs here.
_SEARCH_SQL_BY_DB = {
    "arrayexpress": AE_SEARCH_SQL,
    "ena": ENA_SEARCH_SQL,
    "gsa": GSA_SEARCH_SQL,
    "dra": DRA_SEARCH_SQL,
    "gea": GEA_SEARCH_SQL,
}


@api_app.get(
    "/search",
    summary="Search all databases",
    description=(
        "Unified search across GEO, SRA, ENA, ArrayExpress, GSA, DDBJ DRA and "
        "DDBJ GEA. Optionally restrict by "
        "db=geo|sra|arrayexpress|ena|gsa|dra|gea. "
        "Optionally sort by sortby=citations|journal|year with order=desc(default)|asc. "
        "For default relevance sort, use cursor_rank + cursor_acc for pagination. "
        "For custom sortby, use cursor_sort + cursor_acc for pagination. "
        "Returns {results: object[], total: int, took_ms: float, "
        "next_cursor: {rank, accession} or {sort_value, accession} | null}."
    ),
    operation_id="search_all",
)
@limiter.limit("100/minute")
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    cursor_rank: float | None = Query(None),
    cursor_acc: str | None = Query(None),
    cursor_sort: str | None = Query(None),
    offset: int | None = Query(None, ge=0),
    db: str | None = Query(None, pattern="^(geo|sra|arrayexpress|ena|gsa|dra|gea)$"),
    sortby: str | None = Query(None, pattern="^(citations|journal|year)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    organism: str | None = Query(None),
    country: list[str] | None = Query(None),
    library_strategy: list[str] | None = Query(None),
    instrument_model: list[str] | None = Query(None),
    platform: list[str] | None = Query(None),
    journal: list[str] | None = Query(None),
    multi_platform: bool = Query(False),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
):
    sidebar_filters = {
        "organism": organism,
        "country": country,
        "library_strategy": library_strategy,
        "instrument_model": instrument_model,
        "platform": platform,
        "journal": journal,
        "multi_platform": multi_platform,
        "year_from": year_from,
        "year_to": year_to,
    }
    has_filter = any(v not in (None, False, []) for v in sidebar_filters.values())

    # Sidebar-filtered search: server-side filters, paginated over the same match
    # set as the facets so list and counts agree. Honors sortby (default
    # relevance) so filtered results order the same as unfiltered.
    if has_filter:
        return _search_filtered(
            q, db, cursor_rank, cursor_acc, sidebar_filters, offset, sortby, order
        )

    # Skip accession shortcut when paging (offset > 0): it returns a fixed small
    # set, so it only makes sense on the first page.
    if not sortby and not offset:
        acc_result = _try_accession_search(q, cursor_rank, cursor_acc, db_filter=db)
        if acc_result is not None:
            return acc_result

    original_q = _replace_query_hyphens(q)
    q = original_q

    # First-page unfiltered searches are cacheable (filtered/accession returned above).
    results_cache_key = None
    if (
        cursor_rank is None
        and cursor_acc is None
        and cursor_sort is None
        and not offset
    ):
        results_cache_key = (q, db, sortby, order)
        with _search_results_cache_lock:
            _cached_results = _get_bounded_cache_entry(
                _search_results_cache, results_cache_key, float("inf")
            )
        if _cached_results is not None:
            return _cached_results

    if sortby:
        start = time.perf_counter()
        try:
            with pool.connection() as conn:
                q_variants = _search_query_variants(q)
                sql, params = build_sorted_search_sql(
                    q=q,
                    q_variants=q_variants,
                    sortby=sortby,
                    order=order,
                    db=db,
                    cursor_sort=cursor_sort,
                    cursor_acc=cursor_acc,
                    offset=offset,
                )
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SET LOCAL statement_timeout = '60s'")
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    _enrich_with_publications(rows, cur)
        except Exception:
            logger.exception("Database error in search (sorted)")
            raise HTTPException(500, "Internal server error")

        took_ms = (time.perf_counter() - start) * 1000
        total = rows[0]["total_count"] if rows else 0

        result = {
            "results": rows,
            "total": total,
            "took_ms": round(took_ms, 2),
            "next_cursor": (
                None
                if not rows
                else {
                    "sort_value": rows[-1].get("sort_val"),
                    "accession": rows[-1]["accession"],
                }
            ),
        }
    elif db == "geo":
        result = _search_geo_impl(request, q, cursor_rank, cursor_acc, offset)
    elif db == "sra":
        result = _search_sra_impl(request, q, cursor_rank, cursor_acc, offset)
    elif db in _SEARCH_SQL_BY_DB:
        start = time.perf_counter()
        try:
            with pool.connection() as conn:
                q_variants = _search_query_variants(q)
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SET LOCAL statement_timeout = '60s'")
                    cur.execute(
                        _SEARCH_SQL_BY_DB[db],
                        (q, q_variants, cursor_rank, cursor_rank, cursor_acc, offset),
                    )
                    rows = cur.fetchall()
                    _enrich_with_publications(rows, cur)
        except Exception:
            logger.exception("Database error in search_%s", db)
            raise HTTPException(500, "Internal server error")

        took_ms = (time.perf_counter() - start) * 1000
        total = rows[0]["total_count"] if rows else 0

        result = {
            "results": rows,
            "total": total,
            "took_ms": round(took_ms, 2),
            "next_cursor": (
                None
                if not rows
                else {
                    "rank": rows[-1]["rank"],
                    "accession": rows[-1]["accession"],
                }
            ),
        }
    else:
        start = time.perf_counter()

        try:
            with pool.connection() as conn:
                q_variants = _search_query_variants(q)
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SET LOCAL statement_timeout = '60s'")
                    cur.execute(
                        SEARCH_SQL,
                        (
                            q,
                            q_variants,
                            cursor_rank,
                            cursor_rank,
                            cursor_acc,
                            offset,
                        ),
                    )
                    rows = cur.fetchall()
                    _enrich_with_publications(rows, cur)
        except Exception as e:
            logger.exception("Database error in search")
            raise HTTPException(500, "Internal server error")

        took_ms = (time.perf_counter() - start) * 1000
        total = rows[0]["total_count"] if rows else 0

        result = {
            "results": rows,
            "total": total,
            "took_ms": round(took_ms, 2),
            "next_cursor": (
                None
                if not rows
                else {
                    "rank": rows[-1]["rank"],
                    "accession": rows[-1]["accession"],
                }
            ),
        }

    # total is deferred (None) on the unified path now, so fall back to the
    # first-page row count as the low-result proxy for "did you mean".
    total_for_suggest = result.get("total")
    if total_for_suggest is None:
        total_for_suggest = len(result.get("results", []))
    if total_for_suggest <= _SUGGESTION_THRESHOLD:
        suggestions = _get_suggestions(original_q)
        corrected_q = suggestions[0]["corrected_query"] if suggestions else None
        if corrected_q and corrected_q != original_q:
            # Auto-run the corrected query instead of only suggesting it. Call the
            # undecorated function (search.__wrapped__) so this internal re-run
            # doesn't re-enter the rate limiter. Recursion terminates: the
            # corrected term is in-vocab so it doesn't re-correct.
            corrected = search.__wrapped__(
                request,
                q=corrected_q,
                cursor_rank=cursor_rank,
                cursor_acc=cursor_acc,
                cursor_sort=cursor_sort,
                offset=offset,
                db=db,
                sortby=sortby,
                order=order,
                organism=organism,
                country=country,
                library_strategy=library_strategy,
                instrument_model=instrument_model,
                platform=platform,
                journal=journal,
                multi_platform=multi_platform,
                year_from=year_from,
                year_to=year_to,
            )
            orig_results = result.get("results", [])
            if corrected.get("results"):
                # Corrected results become the paginated stream so offset paging
                # stays clean; the few literal-typo matches (orig_results) ride in
                # the correction payload and the client renders them as a separate
                # page-1 block instead of shifting the 200-row server windows.
                result["results"] = corrected["results"]
                result["total"] = corrected.get("total")
                result["next_cursor"] = corrected.get("next_cursor")
                # Banner + extras only on the first page; deeper pages are just
                # the paginated corrected stream. Use explicit None checks so a
                # legitimate cursor_rank/offset of 0 isn't read as "no cursor".
                first_page = (
                    offset is None
                    and cursor_rank is None
                    and cursor_acc is None
                    and cursor_sort is None
                )
                if first_page:
                    # Drop literal-typo matches that also appear in the corrected
                    # page so they don't render twice (extras block + main list).
                    corrected_accs = {r["accession"] for r in corrected["results"]}
                    extras = [
                        r for r in orig_results if r["accession"] not in corrected_accs
                    ]
                    result["correction"] = {
                        "original_query": original_q,
                        "corrected_query": corrected_q,
                        "original_total": total_for_suggest,
                        # Based on orig_results, not extras: the original DID
                        # match (augmented) even if every match also appears in
                        # the corrected page and dedups out of the extras block.
                        "mode": "augmented" if orig_results else "replaced",
                        "extra_results": extras or None,
                    }
            else:
                # Correction didn't help either; fall back to the suggestion link.
                result["suggestions"] = suggestions
        elif suggestions:
            result["suggestions"] = suggestions

    # Pin broad (full-page) results until /cache/purge.
    if results_cache_key and len(result.get("results", [])) >= _SEARCH_RESULTS_PAGE:
        with _search_results_cache_lock:
            _set_bounded_cache_entry(
                _search_results_cache,
                results_cache_key,
                result,
                _MAX_SEARCH_RESULTS_CACHE_ENTRIES,
                float("inf"),
            )
    return result


def _search_filtered(
    q, db, cursor_rank, cursor_acc, filters, offset=None, sortby=None, order="desc"
):
    """Run a sidebar-filtered search via build_search_results and shape the
    response like the unfiltered /search path (results, total, took_ms,
    next_cursor)."""
    q = _replace_query_hyphens(q)
    start = time.perf_counter()
    try:
        with pool.connection() as conn:
            q_variants = _search_query_variants(q)
            sql, params = build_search_results(
                q=q,
                q_variants=q_variants,
                db=db,
                cursor_rank=cursor_rank,
                cursor_acc=cursor_acc,
                offset=offset,
                sortby=sortby,
                order=order,
                **filters,
            )
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                _enrich_with_publications(rows, cur)
    except Exception:
        logger.exception("Database error in filtered search")
        raise HTTPException(500, "Internal server error")

    return {
        "results": rows,
        "total": rows[0]["total_count"] if rows else 0,
        "took_ms": round((time.perf_counter() - start) * 1000, 2),
        "next_cursor": (
            None
            if not rows
            else {"rank": rows[-1]["rank"], "accession": rows[-1]["accession"]}
        ),
    }


def _compute_search_facets(
    q: str, db: str | None, **filters
) -> tuple[dict[str, list[dict]], int | None]:
    """Exact result total and per-filter facet counts for a query.

    The match set is materialized ONCE into a temp table (the single FTS pass);
    the total counts it (reliable) and the facets aggregate it (best-effort).
    `filters` narrow each facet by every *other* active filter (exclude-self);
    the total applies all filters.

    Returns ({facet_name: [{value, count}, ...]}, total). The facets degrade to
    {} on a facet-only timeout while the total survives; a hard failure (the FTS
    itself, etc.) yields ({}, None) so the caller can avoid caching it.
    """
    out: dict[str, list[dict]] = {}
    total: int | None = None
    try:
        with pool.connection() as conn:
            q_variants = _search_query_variants(q)
            with conn.transaction():
                with conn.cursor(row_factory=dict_row) as cur:
                    # Match /search's ceiling so broad-query totals materialize
                    # instead of dying on the 30s pool default (e.g. "mouse brain").
                    cur.execute("SET LOCAL statement_timeout = '60s'")
                    # Single FTS pass: materialize the match set, then reuse it.
                    msql, mparams = build_search_match_temp(
                        q=q, q_variants=q_variants, db=db
                    )
                    cur.execute(msql, mparams)
                    # Total: cheap count over the materialized set. Computed before
                    # the facet pass so it survives even if facets time out.
                    tsql, tparams = build_search_total_over_matched(**filters)
                    cur.execute(tsql, tparams)
                    total = cur.fetchone()["total"]
                    try:
                        with conn.transaction():
                            cur.execute("SET LOCAL statement_timeout = 60000")
                            fsql, fparams = build_search_facets_over_matched(**filters)
                            cur.execute(fsql, fparams)
                            for r in cur.fetchall():
                                out.setdefault(r["facet"], []).append(
                                    {"value": r["value"], "count": r["count"]}
                                )
                    except Exception:
                        logger.warning(
                            "Search facets degraded (timeout?)", exc_info=True
                        )
    except Exception:
        logger.exception("Database error computing search facets")
        return {}, None
    return out, total


@api_app.get(
    "/search/facets",
    summary="Exact sidebar facet counts for a search",
    description=(
        "Exact counts per sidebar filter (organism, country, library_strategy, "
        "instrument_model, journal) over the FULL match set for a query, so the "
        "UI can show real totals without paging through every result. Computed "
        "independently of /search so it doesn't block result rendering. "
        "Returns {facets: {name: [{value, count}], ...}, took_ms}."
    ),
    operation_id="search_facets",
)
@limiter.limit("100/minute")
def search_facets(
    request: Request,
    response: Response,
    q: str = Query(..., min_length=1, max_length=500),
    db: str | None = Query(None, pattern="^(geo|sra|arrayexpress|ena|gsa|dra|gea)$"),
    organism: str | None = Query(None),
    country: list[str] | None = Query(None),
    library_strategy: list[str] | None = Query(None),
    instrument_model: list[str] | None = Query(None),
    platform: list[str] | None = Query(None),
    journal: list[str] | None = Query(None),
    multi_platform: bool = Query(False),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
):
    t0 = time.perf_counter()
    nq = _replace_query_hyphens(q)
    cache_key = (
        nq,
        db,
        organism,
        tuple(sorted(country or [])),
        tuple(sorted(library_strategy or [])),
        tuple(sorted(instrument_model or [])),
        tuple(sorted(platform or [])),
        tuple(sorted(journal or [])),
        multi_platform,
        year_from,
        year_to,
    )
    with _broad_facets_cache_lock:
        cached = _get_bounded_cache_entry(_broad_facets_cache, cache_key, float("inf"))
    if cached is not None:
        response.headers["Cache-Control"] = _SEARCH_FACETS_CACHE_CONTROL
        return {
            "facets": cached["facets"],
            "total": cached["total"],
            "took_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    facets, total = _compute_search_facets(
        nq,
        db,
        organism=organism,
        country=country,
        library_strategy=library_strategy,
        instrument_model=instrument_model,
        platform=platform,
        journal=journal,
        multi_platform=multi_platform,
        year_from=year_from,
        year_to=year_to,
    )
    # Mirror /search's auto-correction: when the original query is a low-result
    # typo, swap in the corrected query's facets + total so the header count and
    # the sidebar agree with the auto-corrected result list (which /search
    # already returns for the corrected term). Cached below under the original
    # cache_key so repeat typo searches stay fast. Only when unfiltered: /search's
    # filtered path (_search_filtered) doesn't auto-correct, so correcting facets
    # under an active filter would desync the count/rail from the result list.
    has_filter = bool(
        organism
        or country
        or library_strategy
        or instrument_model
        or platform
        or journal
        or multi_platform
        or year_from is not None
        or year_to is not None
    )
    if not has_filter and total is not None and total <= _SUGGESTION_THRESHOLD:
        _suggestions = _get_suggestions(nq)
        _corrected_q = _suggestions[0]["corrected_query"] if _suggestions else None
        if _corrected_q and _corrected_q != nq:
            c_facets, c_total = _compute_search_facets(
                _corrected_q,
                db,
                organism=organism,
                country=country,
                library_strategy=library_strategy,
                instrument_model=instrument_model,
                platform=platform,
                journal=journal,
                multi_platform=multi_platform,
                year_from=year_from,
                year_to=year_to,
            )
            if c_total:
                facets, total = c_facets, c_total
    # Only cache a complete response. A timeout degrades to {facets:{}, total:null};
    # caching that for a week would pin the wrong total (e.g. the loaded-page count)
    # on every later search of this query, so leave it uncached to allow a retry.
    if total is not None:
        # Filter-specific counts still cache (URL includes the filters), just with
        # a lower hit rate than the bare query.
        response.headers["Cache-Control"] = _SEARCH_FACETS_CACHE_CONTROL
        # Broad queries are the expensive cold-start ones — pin them in-process
        # until /cache/purge so the first slow hit is the only slow hit.
        if total >= _BROAD_SEARCH_MIN_TOTAL:
            with _broad_facets_cache_lock:
                _set_bounded_cache_entry(
                    _broad_facets_cache,
                    cache_key,
                    {"facets": facets, "total": total},
                    _MAX_BROAD_FACETS_CACHE_ENTRIES,
                    float("inf"),
                )
    else:
        response.headers["Cache-Control"] = "no-store"
    return {
        "facets": facets,
        "total": total,
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


def _graft_linked_project(cur, row: dict, srp: str | None, gse: str | None) -> None:
    """Fill an ENA project row with data it lacks natively by inheriting it from
    the SRA study / GEO series it wraps. The similarity graph (coords_3d +
    neighbors) is built only for SRA/GEO/AE, and supplementary_data is a GEO
    concept — an ENA (PRJ*) row has neither. Existing values are never
    overwritten. Cheap: two PK lookups, only for PRJ* pages."""
    if srp and not (row.get("coords_3d") and row.get("neighbors")):
        cur.execute(
            "SELECT coords_3d, neighbors, links FROM sra_studies WHERE accession = %s",
            (srp,),
        )
        if g := cur.fetchone():
            row["coords_3d"] = row.get("coords_3d") or g["coords_3d"]
            row["neighbors"] = row.get("neighbors") or g["neighbors"]
            row["links"] = row.get("links") or g["links"]
    if gse:
        cur.execute(
            "SELECT supplementary_data, coords_3d, neighbors"
            " FROM geo_series WHERE accession = %s",
            (gse,),
        )
        if g := cur.fetchone():
            row["supplementary_data"] = (
                row.get("supplementary_data") or g["supplementary_data"]
            )
            row["coords_3d"] = row.get("coords_3d") or g["coords_3d"]
            row["neighbors"] = row.get("neighbors") or g["neighbors"]


@api_app.get(
    "/project/{accession}",
    summary="Get project",
    description=(
        "Return a GEO series (GSE), SRA study (SRP/DRP/ERP), ENA project (PRJ*), "
        "or ArrayExpress project (E-*) record by accession."
    ),
    operation_id="get_project",
)
@limiter.limit("100/minute")
def get_project(
    request: Request,
    response: Response,
    accession: str = Path(..., pattern=PROJECT_REGEX),
):
    accession = accession.upper()

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                row = _fetch_project_row(cur, accession)
                if row:
                    # PRJ* wrappers: enrichment + cross-links key on the GSE/SRP,
                    # not the PRJ. Resolve FIRST so the linked accession is set in
                    # external_id before enrichment (below) reads it as a
                    # publication candidate.
                    enrich_acc = accession
                    if accession.startswith("PRJ"):
                        gse, srp = (r := _resolve_prj(cur, accession))["gse"], r["srp"]
                        if gse and not row.get("alias"):
                            row["alias"] = gse
                        xref = {k: v for k, v in (("GEO", gse), ("SRA", srp)) if v}
                        if xref and not row.get("external_id"):
                            row["external_id"] = xref
                        enrich_acc = gse or srp or accession
                        # ENA rows carry no embedding/supplementary of their own;
                        # inherit the similarity graph + supplementary from the
                        # linked SRA study / GEO series.
                        _graft_linked_project(cur, row, srp, gse)
                    # _enrich_with_publications resolves authors (publication →
                    # geo_series.contributors → center_name) uniformly for every
                    # project type.
                    _enrich_with_publications([row], cur)
                    if ENRICHMENT_ENABLED:
                        # Cheap existence probe so the UI can show/hide the Enriched
                        # tab without eagerly loading the heavy enriched payload.
                        cur.execute(
                            "SELECT EXISTS(SELECT 1 FROM ontology_samples_v4 WHERE study_accession = %s)"
                            " OR EXISTS(SELECT 1 FROM enriched_samples_v4 WHERE study_accession = %s)"
                            " OR EXISTS(SELECT 1 FROM enriched_samples WHERE study_accession = %s)"
                            " AS has_enriched",
                            (enrich_acc, enrich_acc, enrich_acc),
                        )
                        row["has_enriched"] = bool(cur.fetchone()["has_enriched"])
                    else:
                        row["has_enriched"] = False
    except Exception as e:
        logger.exception("Database error in get_project")
        raise HTTPException(500, "Internal server error")

    if row:
        response.headers["Cache-Control"] = (
            _PROJECT_CACHE_CONTROL if ENRICHMENT_ENABLED else "no-store"
        )
    return row


@api_app.get(
    "/project/{accession}/metadata",
    summary="Get project title and description",
    description=(
        "Return only title and description for a GEO series (GSE), SRA study "
        "(SRP/DRP/ERP), ENA project (PRJ*), or ArrayExpress experiment (E-*) accession."
    ),
    operation_id="get_project_metadata",
)
@limiter.limit("100/minute")
def get_project_metadata(
    request: Request,
    accession: str = Path(..., pattern=PROJECT_REGEX),
):
    accession = accession.upper()
    if accession.startswith("G"):
        sql = """
        SELECT accession, title, summary AS description
        FROM geo_series
        WHERE accession = %s;
        """
    elif accession.startswith("E-GEAD-"):
        sql = """
        SELECT accession, title, description
        FROM gea_experiments
        WHERE accession = %s;
        """
    elif accession.startswith("E-"):
        sql = """
        SELECT accession, title, description
        FROM arrayexpress_experiments
        WHERE accession = %s;
        """
    elif accession.startswith(("CRA", "HRA")):
        sql = """
        SELECT study_accession AS accession, study_title AS title,
               study_description AS description
        FROM gsa_studies
        WHERE UPPER(study_accession) = UPPER(%s)
        LIMIT 1;
        """
    elif accession.startswith("PRJ"):
        sql = """
        SELECT study_accession AS accession, study_title AS title,
               study_description AS description
        FROM ena_studies
        WHERE UPPER(study_accession) = UPPER(%s)
        LIMIT 1;
        """
    else:
        sql = """
        SELECT accession, title, abstract AS description
        FROM sra_studies
        WHERE accession = %s;
        """

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (accession,))
                row = cur.fetchone()
                if not row and accession.startswith("DRP"):
                    cur.execute(
                        "SELECT accession, title, abstract AS description "
                        "FROM dra_studies WHERE accession = %s",
                        (accession,),
                    )
                    row = cur.fetchone()
    except Exception:
        logger.exception("Database error in get_project_metadata")
        raise HTTPException(500, "Internal server error")

    if not row:
        raise HTTPException(404, f"Project not found: {accession}")

    return row


@api_app.get(
    "/project/{study}/experiments",
    summary="List study experiments",
    description=(
        "List all experiments for a study accession (SRP/DRP/ERP/PRJ*). "
        "For ENA (PRJ*), returns one row per experiment_accession (first run only)."
    ),
    operation_id="get_study_experiments",
)
@limiter.limit("100/minute")
def get_study_experiments(
    request: Request,
    response: Response,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
    limit: int | None = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    study = study.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # Count is a cheap indexed query; the page fetch uses SQL
                # LIMIT/OFFSET so 100k-experiment studies don't time out.
                total = _count_study_experiments(cur, study)
                result = _fetch_study_experiments(
                    cur, study, limit=limit, offset=offset
                )
    except Exception as e:
        logger.exception("Database error in get_study_experiments")
        raise HTTPException(500, "Internal server error")
    response.headers["X-Total-Count"] = str(total)
    response.headers["Cache-Control"] = _PROJECT_CACHE_CONTROL
    return result


@api_app.get(
    "/project/{study}/samples",
    summary="List samples for an SRA study",
    description=(
        "Return distinct samples referenced by any experiment in an SRA study "
        "(SRP/DRP/ERP). PRJ accessions are resolved to their SRA study first."
    ),
    operation_id="get_study_samples",
)
@limiter.limit("1000/minute")
def get_study_samples(
    request: Request,
    response: Response,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
):
    study = study.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if study.startswith("PRJ"):
                    cur.execute(PRJ_TO_SRA_STUDY_SQL, (study,))
                    resolved = cur.fetchone()
                    if not resolved:
                        raise HTTPException(404, f"No SRA study found for {study}")
                    study = resolved["accession"]
                if _is_gea_study(study):
                    cur.execute(GEA_EXPERIMENT_SAMPLES_SQL, (study,))
                elif _is_dra_only_study(cur, study):
                    cur.execute(STUDY_SAMPLES_DRA_SQL, (study,))
                else:
                    cur.execute(STUDY_SAMPLES_SRA_SQL, (study,))
                rows = cur.fetchall()
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_study_samples")
        raise HTTPException(500, "Internal server error")
    response.headers["Cache-Control"] = _PROJECT_CACHE_CONTROL
    return {"study_accession": study, "total": len(rows), "samples": rows}


_URL_SCHEMES = ("http://", "https://", "ftp://", "s3://", "gs://")


def _split_semicolon_values(value: object) -> list[str]:
    return [v.strip() for v in str(value or "").split(";") if v.strip()]


def _normalize_download_url(url: str) -> str:
    url = (url or "").strip()
    if not url or url.startswith(_URL_SCHEMES):
        return url
    return f"ftp://{url}"


def _fastq_file_count(row: dict) -> int:
    return len(_split_semicolon_values(row.get("fastq_ftp")))


def _is_interleaved_paired_fastq(row: dict) -> bool:
    return (row.get("library_layout") or "").upper() == "PAIRED" and _fastq_file_count(
        row
    ) == 1


def _preferred_fastq_conversion_source_from_row(row: dict) -> dict | None:
    candidates = [
        (
            "sra",
            row.get("ncbi_sra_normalized_url"),
            row.get("ncbi_sra_normalized_bytes"),
            row.get("sra_md5"),
        ),
        (
            "sra_lite",
            row.get("ncbi_sra_lite_url"),
            row.get("ncbi_sra_lite_bytes"),
            row.get("sra_md5"),
        ),
        ("sra", row.get("sra_ftp"), row.get("sra_bytes"), row.get("sra_md5")),
        ("sra", row.get("ncbi_sra_url"), row.get("sra_bytes"), row.get("sra_md5")),
    ]
    for source, url, bytes_, md5 in candidates:
        url = _normalize_download_url(url or "")
        if url:
            return {
                "source": source,
                "url": url,
                "bytes": bytes_ or "",
                "md5": md5 or "",
            }
    return None


def _annotate_run_download(row: dict) -> dict:
    count = _fastq_file_count(row)
    interleaved = _is_interleaved_paired_fastq(row)
    source = _preferred_fastq_conversion_source_from_row(row) if interleaved else None
    row["fastq_file_count"] = count
    row["fastq_is_interleaved"] = interleaved
    row["fastq_conversion_required"] = interleaved
    row["fastq_conversion_available"] = bool(source)
    row["recommended_fastq_source"] = source["source"] if source else "fastq"
    return row


def _annotate_run_downloads(rows: list[dict]) -> list[dict]:
    return [_annotate_run_download(row) for row in rows]


def _preferred_fastq_conversion_source_from_entry(entry: dict) -> dict | None:
    candidates = [
        ("sra", entry.get("sra_url"), entry.get("sra_bytes"), entry.get("sra_md5")),
        (
            "sra_lite",
            entry.get("sra_lite_url"),
            entry.get("sra_lite_bytes"),
            entry.get("sra_md5"),
        ),
    ]
    for source, url, bytes_, md5 in candidates:
        url = _normalize_download_url(url or "")
        if url:
            return {
                "source": source,
                "url": url,
                "bytes": bytes_ or "",
                "md5": md5 or "",
            }
    return None


def _source_filename(url: str, run: str, source: str) -> str:
    filename = url.rstrip("/").rsplit("/", 1)[-1]
    if filename and "." in filename:
        return filename
    suffix = "sra_lite.sra" if source == "sra_lite" else "sra"
    return f"{run}.{suffix}"


def _expected_split_fastq_paths(entry: dict) -> list[str]:
    run = entry["run_accession"]
    directory = entry["directory_path"]
    if (entry.get("library_layout") or "").upper() == "PAIRED":
        return [f"{directory}/{run}_1.fastq.gz", f"{directory}/{run}_2.fastq.gz"]
    return [f"{directory}/{run}.fastq.gz"]


def _sracha_format(source_mode: str) -> str:
    return "sralite" if source_mode == "sra_lite" else "sra"


def _sracha_fastq_shell_call(run: str, directory_path: str, source_mode: str) -> str:
    return (
        f"sracha_interleaved_fastq {_shell_escape(run)} "
        f"{_shell_escape(directory_path)} {_shell_escape(_sracha_format(source_mode))}"
    )


def _download_manifest_entry(
    study: str,
    mode: str,
    method: str,
    url_key: str,
    bytes_key: str | None,
    md5_key: str,
    entry: dict,
) -> dict | None:
    url = entry.get(url_key) or ""
    if not url:
        return None
    filename = entry["filename"]
    path = f"{entry['directory_path']}/{filename}"
    bytes_value = entry.get(bytes_key) if bytes_key else ""
    checksum = entry.get(md5_key) or ""
    interleaved = bool(entry.get("fastq_is_interleaved"))
    return {
        "study_accession": study,
        "mode": mode,
        "method": method,
        "run_accession": entry["run_accession"],
        "run_alias": entry.get("run_alias") or "",
        "experiment_accession": entry["experiment_accession"],
        "library_layout": entry.get("library_layout") or "",
        "fastq_file_count": entry.get("fastq_file_count", 0),
        "fastq_is_interleaved": interleaved,
        "fastq_conversion_required": interleaved and mode == "fastq",
        "fastq_conversion_available": False,
        "recommended_fastq_source": entry.get("recommended_fastq_source") or "fastq",
        "url": url,
        "path": path,
        "filename": filename,
        "bytes": _coerce_int(bytes_value),
        "md5": checksum,
        "directory_path": entry["directory_path"],
        "download_command": _download_command(method, url, path),
    }


def _fastq_conversion_manifest_entry(study: str, entry: dict) -> dict | None:
    source = _preferred_fastq_conversion_source_from_entry(entry)
    if not source:
        return None
    run = entry["run_accession"]
    filename = _source_filename(source["url"], run, source["source"])
    path = f"{entry['directory_path']}/{filename}"
    sracha_command = _sracha_fastq_shell_call(
        run, entry["directory_path"], source["source"]
    )
    source_download_command = _download_command("wget", source["url"], path)
    return {
        "study_accession": study,
        "mode": "fastq",
        "method": "sra_to_fastq",
        "source_mode": source["source"],
        "run_accession": run,
        "run_alias": entry.get("run_alias") or "",
        "experiment_accession": entry["experiment_accession"],
        "library_layout": entry.get("library_layout") or "",
        "fastq_file_count": entry.get("fastq_file_count", 0),
        "fastq_is_interleaved": True,
        "fastq_conversion_required": True,
        "fastq_conversion_available": True,
        "recommended_fastq_source": source["source"],
        "original_fastq_url": entry.get("fastq_url") or "",
        "url": source["url"],
        "path": path,
        "filename": filename,
        "bytes": _coerce_int(source.get("bytes")),
        "md5": source.get("md5") or "",
        "directory_path": entry["directory_path"],
        "expected_fastq_paths": _expected_split_fastq_paths(entry),
        "download_command": sracha_command,
        "conversion_command": sracha_command,
        "source_download_command": source_download_command,
        "download_commands": [sracha_command],
    }


def _split_fastq_fields(row: dict, study: str) -> list[dict]:
    """Expand a single run row into one dict per downloadable file.

    Paired-end runs usually have semicolon-separated fastq_ftp/bytes/md5 fields.
    If a paired-layout run has exactly one ENA FASTQ, mark it as interleaved so
    callers can use SRA/SRA Lite conversion instead of treating it as R1/R2 data.
    """
    ftp = row.get("fastq_ftp") or ""
    bytes_ = row.get("fastq_bytes") or ""
    md5s = row.get("fastq_md5") or ""
    urls = _split_semicolon_values(ftp)
    sizes = _split_semicolon_values(bytes_)
    checksums = _split_semicolon_values(md5s)

    run = row["run_accession"]
    exp = row.get("experiment_accession") or "unknown"
    dir_path = f"{study}/{exp}/{run}"
    fastq_is_interleaved = _is_interleaved_paired_fastq(row)
    conversion_source = (
        _preferred_fastq_conversion_source_from_row(row)
        if fastq_is_interleaved
        else None
    )

    sra_url = _normalize_download_url(
        row.get("ncbi_sra_normalized_url")
        or row.get("sra_ftp")
        or row.get("ncbi_sra_url")
        or row.get("ncbi_sra_url_aws")
        or ""
    )
    sra_lite_url = _normalize_download_url(row.get("ncbi_sra_lite_url") or "")
    sra_bytes = row.get("ncbi_sra_normalized_bytes") or row.get("sra_bytes") or ""
    sra_lite_bytes = row.get("ncbi_sra_lite_bytes") or ""
    s3_url = row.get("ncbi_sra_lite_s3_url") or ""
    gs_url = row.get("ncbi_sra_lite_gs_url") or ""
    sra_md5 = row.get("sra_md5") or ""

    common = {
        "run_accession": run,
        "run_alias": row.get("run_alias") or "",
        "experiment_accession": exp,
        "library_layout": row.get("library_layout") or "",
        "fastq_file_count": len(urls),
        "fastq_is_interleaved": fastq_is_interleaved,
        "fastq_conversion_required": fastq_is_interleaved,
        "fastq_conversion_available": bool(conversion_source),
        "recommended_fastq_source": (
            conversion_source["source"] if conversion_source else "fastq"
        ),
        "sra_lite_url": sra_lite_url,
        "sra_lite_bytes": sra_lite_bytes,
        "sra_url": sra_url,
        "sra_bytes": sra_bytes,
        "sra_md5": sra_md5,
        "s3_url": s3_url,
        "gs_url": gs_url,
        "directory_path": dir_path,
    }

    if urls:
        results = []
        for i, url in enumerate(urls):
            filename = url.rsplit("/", 1)[-1] if "/" in url else url
            normalized_url = _normalize_download_url(url)
            results.append(
                {
                    **common,
                    "fastq_url": normalized_url,
                    "fastq_bytes": sizes[i] if i < len(sizes) else "",
                    "fastq_md5": checksums[i] if i < len(checksums) else "",
                    "filename": filename,
                }
            )
        return results

    best_url = sra_url or sra_lite_url or s3_url or gs_url
    if not best_url:
        return []

    filename = best_url.rsplit("/", 1)[-1] if "/" in best_url else run
    return [
        {
            **common,
            "fastq_url": "",
            "fastq_bytes": "",
            "fastq_md5": "",
            "filename": filename,
        }
    ]


@api_app.get(
    "/project/{study}/runs",
    summary="List download links for a study",
    description=(
        "Return FASTQ/SRA download links for runs in an SRA/ENA study. "
        "Returns summary statistics plus a preview of up to 500 runs; FASTQ "
        "fields are semicolon-joined for paired-end runs. If a paired-layout "
        "run has one ENA FASTQ, fastq_is_interleaved is true and SRA/SRA Lite "
        "conversion should be used. For complete paginated per-file entries, "
        "use get_study_download_manifest."
    ),
    operation_id="get_study_runs",
)
@limiter.limit("100/minute")
def get_study_runs(
    request: Request,
    response: Response,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
):
    study = study.upper()
    response.headers["Cache-Control"] = _PROJECT_CACHE_CONTROL
    is_gsa = _is_gsa_study(study)
    summary_sql = GSA_STUDY_RUNS_SUMMARY_SQL if is_gsa else STUDY_RUNS_SUMMARY_SQL
    preview_sql = GSA_STUDY_RUNS_PREVIEW_SQL if is_gsa else STUDY_RUNS_PREVIEW_SQL
    # Both the GSA SQL and the (single-%s) standard resolve-CTE take one param.
    params = (study,)
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(summary_sql, params)
                summary = cur.fetchone()
                if not summary or summary["total_runs"] == 0:
                    return {
                        "total_runs": 0,
                        "paired_runs": 0,
                        "single_runs": 0,
                        "total_fastq_bytes": 0,
                        "runs": [],
                    }

                cur.execute(preview_sql, params)
                rows = _annotate_run_downloads(cur.fetchall())
    except Exception:
        logger.exception("Database error in get_study_runs")
        raise HTTPException(500, "Internal server error")

    return {
        "total_runs": summary["total_runs"],
        "paired_runs": summary["paired_runs"],
        "single_runs": summary["single_runs"],
        "total_fastq_bytes": summary["total_fastq_bytes"],
        "runs": rows,
    }


@api_app.get(
    "/run/{run}",
    summary="Download links for a single run",
    description=(
        "Return FASTQ/SRA download links and layout for one run accession "
        "(SRR/ERR/DRR). fastq_ftp, fastq_bytes, and fastq_md5 are "
        "semicolon-joined for paired-end FASTQs. If a paired-layout run has "
        "one ENA FASTQ, fastq_is_interleaved is true and SRA/SRA Lite conversion "
        "should be used. Use get_run_checksums when you need parsed per-file "
        "FASTQ URLs, byte sizes, and MD5 checksums. "
        "Returns 404 if the run is unknown."
    ),
    operation_id="get_run",
)
@limiter.limit("1000/minute")
def get_run(
    request: Request,
    run: str = Path(..., pattern=RUN_REGEX),
):
    return _fetch_run_row(run)


def _fetch_run_row(run: str) -> dict:
    run = run.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(RUN_DOWNLOAD_SQL, (run,))
                row = cur.fetchone()
    except Exception:
        logger.exception("Database error fetching run %s", run)
        raise HTTPException(500, "Internal server error")
    if not row:
        raise HTTPException(404, f"No run found for accession {run}")
    return _annotate_run_download(row)


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_url(value: str) -> str:
    value = value or ""
    if not value or value.startswith(_URL_SCHEMES):
        return value
    return f"https://{value}"


def _file_record(url: str, size, md5) -> dict:
    return {
        "url": _as_url(url),
        "filename": url.rstrip("/").rsplit("/", 1)[-1],
        "bytes": _to_int(size),
        "md5": md5 or None,
    }


def _zip_run_files(ftp, sizes, hashes) -> list[dict]:
    urls = [u.strip() for u in (ftp or "").split(";") if u.strip()]
    size_parts = (sizes or "").split(";")
    md5_parts = (hashes or "").split(";")
    return [
        _file_record(
            url,
            size_parts[i] if i < len(size_parts) else "",
            md5_parts[i] if i < len(md5_parts) else "",
        )
        for i, url in enumerate(urls)
    ]


@api_app.get(
    "/run/{run}/checksums",
    summary="MD5 checksums and file sizes for a run",
    description=(
        "Return per-file FASTQ and SRA download URLs with their byte size and MD5 "
        "checksum for a run accession (SRR/ERR/DRR), parsed into structured "
        "records so callers do not need to split semicolon-joined FASTQ fields. "
        "For paired-layout runs with one ENA FASTQ, flags indicate that SRA/SRA "
        "Lite conversion is required and which source should be used."
    ),
    operation_id="get_run_checksums",
)
@limiter.limit("1000/minute")
def get_run_checksums(
    request: Request,
    run: str = Path(..., pattern=RUN_REGEX),
):
    row = _fetch_run_row(run)
    conversion_source = (
        _preferred_fastq_conversion_source_from_row(row)
        if row.get("fastq_is_interleaved")
        else None
    )
    sra = (
        _file_record(row["sra_ftp"], row.get("sra_bytes"), row.get("sra_md5"))
        if row.get("sra_ftp")
        else None
    )
    conversion_record = None
    if conversion_source:
        conversion_record = {
            "source": conversion_source["source"],
            **_file_record(
                conversion_source["url"],
                conversion_source.get("bytes"),
                conversion_source.get("md5"),
            ),
        }
    return {
        "run_accession": run.upper(),
        "fastq_file_count": row.get("fastq_file_count", 0),
        "fastq_is_interleaved": row.get("fastq_is_interleaved", False),
        "fastq_conversion_required": row.get("fastq_conversion_required", False),
        "fastq_conversion_available": row.get("fastq_conversion_available", False),
        "recommended_fastq_source": row.get("recommended_fastq_source", "fastq"),
        "fastq": _zip_run_files(
            row.get("fastq_ftp"), row.get("fastq_bytes"), row.get("fastq_md5")
        ),
        "sra": sra,
        "conversion_source": conversion_record,
    }


@api_app.get(
    "/experiment/{accession}",
    summary="Get a single SRA/ENA experiment",
    description=(
        "Return experiment metadata (library, platform, instrument, parent study, "
        "linked samples) plus runs for an SRA or ENA experiment accession "
        "(SRX/ERX/DRX). Use /experiment/{accession}/runs to fetch just the runs."
    ),
    operation_id="get_experiment",
)
@limiter.limit("1000/minute")
def get_experiment(
    request: Request,
    accession: str = Path(..., pattern=EXPERIMENT_REGEX),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SRA_EXPERIMENT_SQL, (accession,))
                experiment = cur.fetchone()
                if not experiment:
                    cur.execute(ENA_EXPERIMENT_SQL, (accession,))
                    experiment = cur.fetchone()
                if not experiment:
                    cur.execute(GSA_EXPERIMENT_SQL, (accession,))
                    experiment = cur.fetchone()
                if not experiment:
                    cur.execute(DRA_EXPERIMENT_SQL, (accession,))
                    experiment = cur.fetchone()
                if experiment:
                    cur.execute(EXPERIMENT_RUNS_SQL, (accession,))
                    experiment["runs"] = _annotate_run_downloads(cur.fetchall())
    except Exception:
        logger.exception("Database error in get_experiment")
        raise HTTPException(500, "Internal server error")
    if not experiment:
        raise HTTPException(404, f"No experiment found for accession {accession}")
    return experiment


@api_app.get(
    "/experiment/{accession}/runs",
    summary="List runs for an experiment",
    description="Return run download links and layout for an SRX/ERX/DRX experiment.",
    operation_id="get_experiment_runs",
)
@limiter.limit("1000/minute")
def get_experiment_runs(
    request: Request,
    accession: str = Path(..., pattern=EXPERIMENT_REGEX),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(EXPERIMENT_RUNS_SQL, (accession,))
                runs = _annotate_run_downloads(cur.fetchall())
    except Exception:
        logger.exception("Database error in get_experiment_runs")
        raise HTTPException(500, "Internal server error")
    return {"experiment_accession": accession, "total": len(runs), "runs": runs}


@api_app.get(
    "/project/{study}/bams",
    summary="List original BAM files for a study",
    description=(
        "Return direct download links for original submitted BAM files in an SRA study. "
        "BAM files are extracted from the ncbi_files JSONB column where supertype is 'Original'. "
        "Not all studies have BAM files — returns total_bams=0 when none exist."
    ),
    operation_id="get_study_bams",
)
@limiter.limit("100/minute")
def get_study_bams(
    request: Request,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
):
    study = study.upper()
    empty = {"total_bams": 0, "total_bam_bytes": "0", "bams": []}
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(STUDY_BAMS_SQL, (study,))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_study_bams")
        raise HTTPException(500, "Internal server error")

    if not rows:
        return empty

    total_bams = rows[0]["total_bams"]
    total_bam_bytes = rows[0]["total_bam_bytes"]
    for row in rows:
        del row["total_bams"]
        del row["total_bam_bytes"]

    return {
        "total_bams": total_bams,
        "total_bam_bytes": total_bam_bytes,
        "bams": rows,
    }


@api_app.get(
    "/project/{study}/bams/download",
    summary="Download BAM links as TSV",
    description=(
        "Stream a TSV file containing original submitted BAM download links for a study, "
        "with one row per BAM file."
    ),
    operation_id="download_study_bams_tsv",
)
@limiter.limit("100/minute")
def download_study_bams_tsv(
    request: Request,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
):
    study = study.upper()

    def generate():
        header = (
            "run_accession\texperiment_accession\t"
            "filename\turl\thttps_url\ts3_url\t"
            "size\tmd5\tsemantic_name\tdirectory_path\n"
        )
        yield header
        try:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(STUDY_BAMS_SQL, (study,))
                    while True:
                        rows = cur.fetchmany(500)
                        if not rows:
                            break
                        for row in rows:
                            dirpath = f"{study}/{row['experiment_accession'] or 'unknown'}/{row['run_accession']}"
                            yield (
                                f"{row['run_accession']}\t"
                                f"{row['experiment_accession'] or ''}\t"
                                f"{row['filename']}\t"
                                f"{row['url']}\t"
                                f"{row['https_url'] or ''}\t"
                                f"{row['s3_url'] or ''}\t"
                                f"{row['size'] or ''}\t"
                                f"{row['md5'] or ''}\t"
                                f"{row['semantic_name'] or ''}\t"
                                f"{dirpath}\n"
                            )
        except Exception:
            logger.exception("Error streaming BAMs TSV for %s", study)

    return StreamingResponse(
        generate(),
        media_type="text/tab-separated-values",
        headers={
            "Content-Disposition": f'attachment; filename="{study}_bam_links.tsv"'
        },
    )


@api_app.get(
    "/project/{study}/runs/download",
    summary="Download run links as TSV",
    description=(
        "Stream a TSV file containing all FASTQ download links for a study, "
        "with one row per file. Paired-end runs are split into separate rows. "
        "Includes a directory_path column for organizing files as STUDY/EXPERIMENT/RUN/."
    ),
    operation_id="download_study_runs_tsv",
)
@limiter.limit("100/minute")
def download_study_runs_tsv(
    request: Request,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
):
    study = study.upper()

    def generate():
        header = (
            "run_accession\trun_alias\texperiment_accession\tlibrary_layout\t"
            "fastq_url\tfastq_bytes\tfastq_md5\t"
            "sra_lite_url\tsra_lite_bytes\t"
            "sra_url\tsra_bytes\t"
            "s3_url\tgs_url\t"
            "filename\tdirectory_path\n"
        )
        yield header
        try:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(STUDY_RUNS_SQL, (study,))
                    while True:
                        rows = cur.fetchmany(500)
                        if not rows:
                            break
                        for row in rows:
                            for entry in _split_fastq_fields(row, study):
                                yield (
                                    f"{entry['run_accession']}\t"
                                    f"{entry['run_alias']}\t"
                                    f"{entry['experiment_accession']}\t"
                                    f"{entry['library_layout']}\t"
                                    f"{entry['fastq_url']}\t"
                                    f"{entry['fastq_bytes']}\t"
                                    f"{entry['fastq_md5']}\t"
                                    f"{entry['sra_lite_url']}\t"
                                    f"{entry['sra_lite_bytes']}\t"
                                    f"{entry['sra_url']}\t"
                                    f"{entry['sra_bytes']}\t"
                                    f"{entry['s3_url']}\t"
                                    f"{entry['gs_url']}\t"
                                    f"{entry['filename']}\t"
                                    f"{entry['directory_path']}\n"
                                )
        except Exception:
            logger.exception("Error streaming runs TSV for %s", study)

    return StreamingResponse(
        generate(),
        media_type="text/tab-separated-values",
        headers={
            "Content-Disposition": f'attachment; filename="{study}_download_links.tsv"'
        },
    )


@api_app.get(
    "/project/{study}/metadata/download",
    summary="Download merged metadata as CSV",
    description=(
        "Return a CSV file merging experiment metadata, sample attributes, "
        "and run download links for an SRA study. One row per run."
    ),
    operation_id="download_study_metadata_csv",
)
@limiter.limit("100/minute")
def download_study_metadata_csv(
    request: Request,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
):
    study = study.upper()

    # Fetch experiments and samples eagerly (bounded by experiment count),
    # then stream runs to avoid buffering the full TSV in memory.
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # 1. Fetch experiments and build lookup in a single pass
                experiments = _fetch_study_experiments(cur, study)
                if not experiments:
                    raise HTTPException(404, f"No experiments found for {study}")

                exp_map: dict[str, dict] = {}
                sample_accs: list[str] = []
                for exp in experiments:
                    # samples is TEXT[] — psycopg3 returns a Python list
                    samples_list = exp.get("samples") or []
                    sample_acc = samples_list[0] if samples_list else ""
                    exp["_sample_acc"] = sample_acc
                    exp_map[exp["accession"]] = exp
                    if sample_acc:
                        sample_accs.append(sample_acc)

                # 2. Batch-fetch samples (SRA first, ENA fallback)
                sample_map = _fetch_samples_batch(cur, sample_accs)

                # 3. Collect dynamic attribute keys (order-preserving)
                all_attr_keys: list[str] = []
                seen_keys: set[str] = set()
                for s in sample_map.values():
                    attrs_raw = s.get("attributes_json")
                    if isinstance(attrs_raw, str):
                        try:
                            attrs_raw = json.loads(attrs_raw)
                        except (json.JSONDecodeError, TypeError):
                            attrs_raw = None
                        s["attributes_json"] = attrs_raw
                    if isinstance(attrs_raw, list):
                        for attr in attrs_raw:
                            key = attr.get("tag") or attr.get("key") or ""
                            if key and key not in seen_keys:
                                seen_keys.add(key)
                                all_attr_keys.append(key)

                # 4. Verify runs exist before streaming
                cur.execute(STUDY_RUNS_SUMMARY_SQL, (study,))
                summary = cur.fetchone()
                if not summary or summary["total_runs"] == 0:
                    raise HTTPException(404, f"No runs found for {study}")

    except HTTPException:
        raise
    except Exception:
        logger.exception("Error fetching metadata for %s", study)
        raise HTTPException(500, "Internal error generating metadata TSV")

    # Pre-compute per-sample attribute dicts (already parsed above)
    sample_attrs: dict[str, dict[str, str]] = {}
    for acc, s in sample_map.items():
        attrs_raw = s.get("attributes_json")
        if isinstance(attrs_raw, list):
            sample_attrs[acc] = {
                f"sample_attribute:{attr.get('tag') or attr.get('key') or ''}": attr.get(
                    "value"
                )
                or ""
                for attr in attrs_raw
                if attr.get("tag") or attr.get("key")
            }

    # Define columns
    exp_cols = [
        "experiment_accession",
        "experiment_title",
        "design_description",
        "library_layout",
        "library_name",
        "library_selection",
        "library_source",
        "library_strategy",
        "platform",
        "instrument_model",
    ]
    sample_cols = [
        "sample_accession",
        "sample_alias",
        "sample_description",
        "sample_title",
        "scientific_name",
        "taxon_id",
    ]
    attr_cols = [f"sample_attribute:{k}" for k in all_attr_keys]
    run_cols = [
        "run_accession",
        "run_alias",
        "fastq_ftp",
        "fastq_bytes",
        "fastq_md5",
        "sra_ftp",
        "sra_bytes",
        "ncbi_sra_url",
        "ncbi_sra_normalized_url",
        "ncbi_sra_normalized_bytes",
        "ncbi_sra_lite_url",
        "ncbi_sra_lite_bytes",
        "ncbi_sra_lite_s3_url",
        "ncbi_sra_lite_gs_url",
    ]
    fieldnames = ["study_accession"] + run_cols + exp_cols + sample_cols + attr_cols

    def _run_to_row(run: dict) -> dict:
        exp_acc = run.get("experiment_accession") or ""
        exp = exp_map.get(exp_acc, {})
        s_acc = exp.get("_sample_acc", "")
        sample = sample_map.get(s_acc, {})
        return {
            "study_accession": study,
            "run_accession": run.get("run_accession") or "",
            "run_alias": run.get("run_alias") or "",
            "fastq_ftp": run.get("fastq_ftp") or "",
            "fastq_bytes": run.get("fastq_bytes") or "",
            "fastq_md5": run.get("fastq_md5") or "",
            "sra_ftp": run.get("sra_ftp") or "",
            "sra_bytes": run.get("sra_bytes") or "",
            "ncbi_sra_url": run.get("ncbi_sra_url") or "",
            "ncbi_sra_normalized_url": run.get("ncbi_sra_normalized_url") or "",
            "ncbi_sra_normalized_bytes": run.get("ncbi_sra_normalized_bytes") or "",
            "ncbi_sra_lite_url": run.get("ncbi_sra_lite_url") or "",
            "ncbi_sra_lite_bytes": run.get("ncbi_sra_lite_bytes") or "",
            "ncbi_sra_lite_s3_url": run.get("ncbi_sra_lite_s3_url") or "",
            "ncbi_sra_lite_gs_url": run.get("ncbi_sra_lite_gs_url") or "",
            "experiment_accession": exp_acc,
            "experiment_title": exp.get("title") or "",
            "design_description": exp.get("design_description") or "",
            "library_layout": exp.get("library_layout")
            or run.get("library_layout")
            or "",
            "library_name": exp.get("library_name") or "",
            "library_selection": exp.get("library_selection") or "",
            "library_source": exp.get("library_source") or "",
            "library_strategy": exp.get("library_strategy") or "",
            "platform": exp.get("platform") or "",
            "instrument_model": exp.get("instrument_model") or "",
            "sample_accession": s_acc,
            "sample_alias": sample.get("alias") or "",
            "sample_description": sample.get("description") or "",
            "sample_title": sample.get("title") or "",
            "scientific_name": sample.get("scientific_name") or "",
            "taxon_id": sample.get("taxon_id") or "",
            **sample_attrs.get(s_acc, {}),
        }

    def generate():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        yield buf.getvalue()

        try:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(STUDY_RUNS_SQL, (study,))
                    while True:
                        rows = cur.fetchmany(500)
                        if not rows:
                            break
                        buf = io.StringIO()
                        writer = csv.DictWriter(
                            buf,
                            fieldnames=fieldnames,
                            extrasaction="ignore",
                        )
                        for run in rows:
                            writer.writerow(_run_to_row(run))
                        yield buf.getvalue()
        except Exception:
            logger.exception("Error streaming metadata CSV for %s", study)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{study}_metadata.csv"'},
    )


@api_app.get(
    "/project/{accession}/metadata/rows",
    summary="Get project metadata rows as JSON",
    description=(
        "Return paginated JSON metadata rows for a project. SRA/ENA studies return "
        "one row per run with experiment metadata, sample metadata, flattened "
        "sample_attribute:* columns, FASTQ/SRA download links, byte sizes, and "
        "MD5 fields. GEO and ArrayExpress projects return flattened sample rows. "
        "Prefer this structured JSON endpoint over CSV downloads for MCP use."
    ),
    operation_id="get_project_metadata_rows",
)
@limiter.limit("100/minute")
def get_project_metadata_rows(
    request: Request,
    accession: str = Path(..., pattern=PROJECT_REGEX),
    limit: int = Query(500, ge=1, le=1000),
    cursor: int = Query(0, ge=0),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if accession.startswith("GSE"):
                    rows = flatten_geo_series(cur, accession)
                    source = "geo"
                elif accession.startswith("E-"):
                    rows = flatten_ae_experiment(cur, accession)
                    source = "arrayexpress"
                else:
                    exp_map, sample_map, sample_attrs = _prepare_study_metadata(
                        cur, accession
                    )
                    cur.execute(STUDY_RUNS_SQL, (accession,))
                    rows = []
                    while True:
                        batch = cur.fetchmany(500)
                        if not batch:
                            break
                        rows.extend(
                            _study_metadata_row(
                                accession, run, exp_map, sample_map, sample_attrs
                            )
                            for run in batch
                        )
                        if len(rows) >= MAX_METADATA_ROWS:
                            break
                    if not rows and accession.startswith("PRJ"):
                        rows = flatten_sra_study(cur, accession)
                    if _is_gsa_study(accession):
                        source = "gsa"
                    elif accession.startswith("PRJ"):
                        source = "ena"
                    else:
                        source = "sra"
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_project_metadata_rows")
        raise HTTPException(500, "Internal server error")

    truncated = len(rows) >= MAX_METADATA_ROWS
    if truncated:
        rows = rows[:MAX_METADATA_ROWS]
    return _rows_response(accession, source, rows, cursor, limit, truncated=truncated)


def _rows_response(
    accession: str,
    source: str,
    rows: list[dict],
    cursor: int,
    limit: int,
    truncated: bool = False,
) -> dict:
    """Build the paginated metadata-rows envelope shared by the project,
    experiment, and run metadata/rows endpoints. `truncated` is True when the
    source row set was capped at MAX_METADATA_ROWS before paging."""
    page = rows[cursor : cursor + limit]
    next_cursor = cursor + len(page) if cursor + len(page) < len(rows) else None
    columns = list(page[0].keys()) if page else (list(rows[0].keys()) if rows else [])
    return {
        "accession": accession,
        "source": source,
        "total_rows": len(rows),
        "truncated": truncated,
        "cursor": cursor,
        "limit": limit,
        "next_cursor": next_cursor,
        "columns": columns,
        "rows": page,
    }


@api_app.get(
    "/experiment/{accession}/metadata/rows",
    summary="Get experiment metadata rows as JSON",
    description=(
        "Return per-run JSON metadata rows for a single SRA/ENA experiment "
        "(SRX/ERX/DRX), in the same row format as /project/{accession}/metadata/rows "
        "but scoped to this experiment's runs."
    ),
    operation_id="get_experiment_metadata_rows",
)
@limiter.limit("100/minute")
def get_experiment_metadata_rows(
    request: Request,
    accession: str = Path(..., pattern=EXPERIMENT_REGEX),
    limit: int = Query(500, ge=1, le=1000),
    cursor: int = Query(0, ge=0),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                ctx = _build_exp_context(cur, accession)
                if ctx is None:
                    raise HTTPException(
                        404, f"No experiment found for accession {accession}"
                    )
                cur.execute(EXPERIMENT_RUNS_SQL, (accession,))
                rows = [
                    _study_metadata_row(
                        ctx["study"],
                        run,
                        ctx["exp_map"],
                        ctx["sample_map"],
                        ctx["sample_attrs"],
                    )
                    for run in cur.fetchall()
                ]
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_experiment_metadata_rows")
        raise HTTPException(500, "Internal server error")

    return _rows_response(accession, ctx["source"], rows, cursor, limit)


@api_app.get(
    "/run/{run}/metadata/rows",
    summary="Get run metadata row as JSON",
    description=(
        "Return the single JSON metadata row for one run accession (SRR/ERR/DRR), "
        "in the same row format as /project/{accession}/metadata/rows."
    ),
    operation_id="get_run_metadata_rows",
)
@limiter.limit("1000/minute")
def get_run_metadata_rows(
    request: Request,
    run: str = Path(..., pattern=RUN_REGEX),
):
    run = run.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(RUN_DOWNLOAD_SQL, (run,))
                run_row = cur.fetchone()
                if not run_row:
                    raise HTTPException(404, f"No run found for accession {run}")
                study = run_row.get("study_accession") or ""
                source = "sra"
                exp_map, sample_map, sample_attrs = {}, {}, {}
                exp_acc = (run_row.get("experiment_accession") or "").upper()
                if exp_acc:
                    ctx = _build_exp_context(cur, exp_acc)
                    if ctx:
                        study = study or ctx["study"]
                        source = ctx["source"]
                        exp_map = ctx["exp_map"]
                        sample_map = ctx["sample_map"]
                        sample_attrs = ctx["sample_attrs"]
                row = _study_metadata_row(
                    study, run_row, exp_map, sample_map, sample_attrs
                )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_run_metadata_rows")
        raise HTTPException(500, "Internal server error")

    return _rows_response(run, source, [row], 0, 1)


_DOWNLOAD_HELPER = r"""# Verify file integrity.
_is_complete() {
  local f="$1"
  [ -s "$f" ] || return 1
  [ -f "$f.done" ] && [ ! "$f" -nt "$f.done" ] && return 0
  case "$f" in
    *.gz) gzip -t "$f" 2>/dev/null ;;
    *.bam)
      if command -v samtools >/dev/null 2>&1; then
        samtools quickcheck "$f" 2>/dev/null
      else
        return 0
      fi
      ;;
    *) return 0 ;;
  esac
}

_human() { numfmt --to=iec --suffix=B --format='%.1f' "$1" 2>/dev/null || echo "${1}B"; }

_done_count=0
_total_count=0

download_one() {
  local url="$1" out="$2" method="${3:-wget}" expected="${4:-0}"
  _done_count=$((_done_count + 1))
  local prefix="[$_done_count/$_total_count] $out"
  local sz=0
  [ -f "$out" ] && sz=$(stat -c %s "$out" 2>/dev/null || echo 0)
  local sz_msg=""
  [ "$expected" -gt 0 ] && sz_msg=" ($(_human $sz) / $(_human $expected))"
  printf '%s: checking%s... ' "$prefix" "$sz_msg" >&2

  if [ "$expected" -gt 0 ] && [ "$sz" -gt "$expected" ]; then
    echo "oversized, redownloading" >&2
    rm -f "$out" "$out.done"
  elif [ "$expected" -gt 0 ] && [ "$sz" -gt 0 ] && [ "$sz" -lt "$expected" ] && { [ "$method" = "wget" ] || [ "$method" = "curl" ]; }; then
    echo "partial, resuming" >&2
    rm -f "$out.done"
    case "$method" in
      wget) wget -c -q --show-progress --tries=10 --timeout=60 --retry-connrefused -O "$out" "$url" || true ;;
      curl) curl -L -C - --retry 10 --retry-delay 5 --retry-all-errors --fail -o "$out" "$url" || true ;;
    esac
    if { [ "$expected" -le 0 ] || [ "$(stat -c %s "$out" 2>/dev/null || echo 0)" -ge "$expected" ]; } && _is_complete "$out"; then
      : > "$out.done"
      echo "$prefix: resumed OK" >&2
      return 0
    fi
    echo "$prefix: WARN integrity check failed after resume; redownloading from scratch" >&2
    rm -f "$out" "$out.done"
  elif { [ "$expected" -le 0 ] || [ "$sz" -ge "$expected" ]; } && _is_complete "$out"; then
    : > "$out.done"
    echo "valid, skip" >&2
    return 0
  elif [ "$sz" -gt 0 ] && { [ "$method" = "wget" ] || [ "$method" = "curl" ]; }; then
    echo "partial, resuming" >&2
    rm -f "$out.done"
    case "$method" in
      wget) wget -c -q --show-progress --tries=10 --timeout=60 --retry-connrefused -O "$out" "$url" || true ;;
      curl) curl -L -C - --retry 10 --retry-delay 5 --retry-all-errors --fail -o "$out" "$url" || true ;;
    esac
    if { [ "$expected" -le 0 ] || [ "$(stat -c %s "$out" 2>/dev/null || echo 0)" -ge "$expected" ]; } && _is_complete "$out"; then
      : > "$out.done"
      echo "$prefix: resumed OK" >&2
      return 0
    fi
    echo "$prefix: WARN integrity check failed after resume; redownloading from scratch" >&2
    rm -f "$out" "$out.done"
  else
    echo "missing, downloading" >&2
    rm -f "$out.done"
  fi

  mkdir -p "${out%/*}"
  case "$method" in
    wget)   wget -q --show-progress --tries=10 --timeout=60 --retry-connrefused -O "$out" "$url" ;;
    curl)   curl -L --retry 10 --retry-delay 5 --retry-all-errors --fail -o "$out" "$url" ;;
    aws)    aws s3 cp --no-progress "$url" "$out" ;;
    gsutil) gsutil cp "$url" "$out" ;;
    *) echo "ERR: unknown download method '$method'" >&2; return 1 ;;
  esac
  { [ "$expected" -le 0 ] || [ "$(stat -c %s "$out" 2>/dev/null || echo 0)" -ge "$expected" ]; } && _is_complete "$out" || { echo "$prefix: ERR corrupted after fresh download (re-run script)" >&2; rm -f "$out" "$out.done"; return 1; }
  : > "$out.done"
  echo "$prefix: done" >&2
}

"""

_SRACHA_FASTQ_HELPER = r"""# Interleaved paired FASTQ handling.
# For paired-layout runs where ENA exposes one interleaved FASTQ, this script uses
# sracha-rs from the Hesselberth group at CU Anschutz to fetch SRA/SRA Lite and
# emit split FASTQ files.
# Project: https://github.com/rnabioco/sracha-rs
# Install before running this section, for example:
#   pixi add bioconda::sracha
# or:
#   cargo install --git https://github.com/rnabioco/sracha-rs sracha
sracha_interleaved_fastq() {
  local run="$1" outdir="$2" format="${3:-sra}"
  local r1="$outdir/${run}_1.fastq.gz"
  local r2="$outdir/${run}_2.fastq.gz"
  if [ -s "$r1" ] && [ -s "$r2" ] && gzip -t "$r1" 2>/dev/null && gzip -t "$r2" 2>/dev/null; then
    echo "FASTQs already converted for $run, skip" >&2
    return 0
  fi
  command -v sracha >/dev/null 2>&1 || {
    echo "ERR: sracha is required for paired runs with one ENA FASTQ." >&2
    echo "Install with: pixi add bioconda::sracha" >&2
    echo "Or: cargo install --git https://github.com/rnabioco/sracha-rs sracha" >&2
    return 1
  }
  mkdir -p "$outdir"
  sracha get "$run" --format "$format" --split split-files -O "$outdir"
  [ -s "$r1" ] && [ -s "$r2" ] || {
    echo "ERR: expected split paired FASTQs for $run after sracha conversion" >&2
    return 1
  }
}

"""


def _coerce_int(v: object) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_sample_attributes(sample: dict | None) -> dict[str, str]:
    """Flatten SRA/ENA sample attributes into stable metadata columns."""
    if not sample:
        return {}
    attrs_raw = sample.get("attributes_json")
    if isinstance(attrs_raw, str):
        try:
            attrs_raw = json.loads(attrs_raw)
        except (json.JSONDecodeError, TypeError):
            attrs_raw = None
        sample["attributes_json"] = attrs_raw

    attrs: dict[str, str] = {}
    if isinstance(attrs_raw, list):
        for attr in attrs_raw:
            if not isinstance(attr, dict):
                continue
            key = attr.get("tag") or attr.get("key") or ""
            if key:
                attrs[f"sample_attribute:{key}"] = attr.get("value") or ""
    elif isinstance(attrs_raw, dict):
        for key, val in attrs_raw.items():
            attrs[f"sample_attribute:{key}"] = val or ""
    return attrs


def _study_metadata_row(
    study: str,
    run: dict,
    exp_map: dict[str, dict],
    sample_map: dict[str, dict],
    sample_attrs: dict[str, dict[str, str]],
) -> dict:
    exp_acc = run.get("experiment_accession") or ""
    exp = exp_map.get(exp_acc, {})
    s_acc = exp.get("_sample_acc", "")
    sample = sample_map.get(s_acc, {})
    return {
        "study_accession": study,
        "run_accession": run.get("run_accession") or "",
        "run_alias": run.get("run_alias") or "",
        "fastq_file_count": run.get("fastq_file_count", _fastq_file_count(run)),
        "fastq_is_interleaved": run.get(
            "fastq_is_interleaved", _is_interleaved_paired_fastq(run)
        ),
        "fastq_conversion_required": run.get(
            "fastq_conversion_required", _is_interleaved_paired_fastq(run)
        ),
        "fastq_conversion_available": run.get(
            "fastq_conversion_available",
            (
                bool(_preferred_fastq_conversion_source_from_row(run))
                if _is_interleaved_paired_fastq(run)
                else False
            ),
        ),
        "recommended_fastq_source": run.get("recommended_fastq_source")
        or (
            (_preferred_fastq_conversion_source_from_row(run) or {}).get("source")
            if _is_interleaved_paired_fastq(run)
            else "fastq"
        ),
        "fastq_ftp": run.get("fastq_ftp") or "",
        "fastq_bytes": run.get("fastq_bytes") or "",
        "fastq_md5": run.get("fastq_md5") or "",
        "sra_ftp": run.get("sra_ftp") or "",
        "sra_bytes": run.get("sra_bytes") or "",
        "ncbi_sra_url": run.get("ncbi_sra_url") or "",
        "ncbi_sra_normalized_url": run.get("ncbi_sra_normalized_url") or "",
        "ncbi_sra_normalized_bytes": run.get("ncbi_sra_normalized_bytes") or "",
        "ncbi_sra_lite_url": run.get("ncbi_sra_lite_url") or "",
        "ncbi_sra_lite_bytes": run.get("ncbi_sra_lite_bytes") or "",
        "ncbi_sra_lite_s3_url": run.get("ncbi_sra_lite_s3_url") or "",
        "ncbi_sra_lite_gs_url": run.get("ncbi_sra_lite_gs_url") or "",
        "experiment_accession": exp_acc,
        "experiment_title": exp.get("title") or "",
        "design_description": exp.get("design_description") or "",
        "library_layout": exp.get("library_layout") or run.get("library_layout") or "",
        "library_name": exp.get("library_name") or "",
        "library_selection": exp.get("library_selection") or "",
        "library_source": exp.get("library_source") or "",
        "library_strategy": exp.get("library_strategy") or "",
        "platform": exp.get("platform") or "",
        "instrument_model": exp.get("instrument_model") or "",
        "sample_accession": s_acc,
        "sample_alias": sample.get("alias") or "",
        "sample_description": sample.get("description") or "",
        "sample_title": sample.get("title") or "",
        "scientific_name": sample.get("scientific_name") or "",
        "taxon_id": sample.get("taxon_id") or "",
        **sample_attrs.get(s_acc, {}),
    }


def _prepare_study_metadata(cur, study: str) -> tuple[dict, dict, dict]:
    experiments = _fetch_study_experiments(cur, study)
    if not experiments:
        raise HTTPException(404, f"No experiments found for {study}")

    exp_map: dict[str, dict] = {}
    sample_accs: list[str] = []
    for exp in experiments:
        samples_list = exp.get("samples") or []
        sample_acc = samples_list[0] if samples_list else ""
        exp["_sample_acc"] = sample_acc
        exp_map[exp["accession"]] = exp
        if sample_acc:
            sample_accs.append(sample_acc)

    sample_map = _fetch_samples_batch(cur, sample_accs)

    sample_attrs = {
        acc: _parse_sample_attributes(sample) for acc, sample in sample_map.items()
    }
    return exp_map, sample_map, sample_attrs


def _build_exp_context(cur, accession: str) -> dict | None:
    """Build the per-run metadata context for a single SRA/ENA experiment.

    Returns {study, source, exp_map, sample_map, sample_attrs} ready for
    _study_metadata_row(), or None if the experiment is unknown.
    """
    cur.execute(SRA_EXPERIMENT_SQL, (accession,))
    exp = cur.fetchone()
    source = "sra"
    if not exp:
        cur.execute(ENA_EXPERIMENT_SQL, (accession,))
        exp = cur.fetchone()
        source = "ena"
    if not exp:
        cur.execute(GSA_EXPERIMENT_SQL, (accession,))
        exp = cur.fetchone()
        source = "gsa"
    if not exp:
        return None

    samples_list = exp.get("samples") or []
    sample_acc = samples_list[0] if samples_list else ""
    exp["_sample_acc"] = sample_acc
    exp_map = {exp["accession"]: exp}

    sample_map: dict[str, dict] = {}
    sample_attrs: dict[str, dict[str, str]] = {}
    if sample_acc:
        sample = _fetch_sample(cur, sample_acc)
        if sample:
            sample_map[sample_acc] = sample
            sample_attrs[sample_acc] = _parse_sample_attributes(sample)

    return {
        "study": exp.get("study") or "",
        "source": source,
        "exp_map": exp_map,
        "sample_map": sample_map,
        "sample_attrs": sample_attrs,
    }


def _resume_status_preamble(paths: list[str], sizes: list[int] | None = None) -> str:
    """Bash snippet that estimates resume progress by file count and bytes."""
    if not paths:
        return ""
    quoted = "\n".join(f"  {_shell_escape(p)}" for p in paths)
    if sizes and len(sizes) == len(paths):
        sizes_arr = "\n".join(f"  {s}" for s in sizes)
        return (
            "# Resume estimate from local file sizes vs known remote sizes.\n"
            f"expected_files=(\n{quoted}\n)\n"
            f"expected_sizes=(\n{sizes_arr}\n)\n"
            "_total_count=${#expected_files[@]}\n"
            "present=0; local_bytes=0; total_bytes=0\n"
            'for i in "${!expected_files[@]}"; do\n'
            "  exp=${expected_sizes[$i]}; total_bytes=$((total_bytes + exp))\n"
            "  f=${expected_files[$i]}\n"
            '  if [ -f "$f" ]; then\n'
            '    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)\n'
            '    [ "$sz" -gt "$exp" ] && sz=0\n'
            "    local_bytes=$((local_bytes + sz))\n"
            '    [ "$sz" -ge "$exp" ] && [ "$exp" -gt 0 ] && present=$((present + 1))\n'
            "  fi\n"
            "done\n"
            "pct=$(( total_bytes > 0 ? local_bytes * 100 / total_bytes : 0 ))\n"
            'echo "Resume estimate: $present/$_total_count files complete '
            '($(_human $local_bytes) / $(_human $total_bytes) ≈ ${pct}%)."\n\n'
        )
    return (
        "# Resume estimate. \n"
        f"expected_files=(\n{quoted}\n)\n"
        'present=0; for f in "${expected_files[@]}"; do [ -s "$f" ] && present=$((present+1)); done\n'
        "_total_count=${#expected_files[@]}\n"
        'echo "Resume estimate: $present/$_total_count files present on disk'
        ' (will integrity-check each below)."\n\n'
    )


_DOWNLOAD_MODES = {
    "fastq": {
        "label": "FASTQ files",
        "url_key": "fastq_url",
        "md5_key": "fastq_md5",
        "bytes_key": "fastq_bytes",
        "method": "wget",
    },
    "sra": {
        "label": "SRA format",
        "url_key": "sra_url",
        "md5_key": "sra_md5",
        "bytes_key": "sra_bytes",
        "method": "wget",
    },
    "sra_lite": {
        "label": "SRA Lite",
        "url_key": "sra_lite_url",
        "md5_key": "sra_md5",
        "bytes_key": "sra_lite_bytes",
        "method": "wget",
    },
    "s3": {
        "label": "AWS S3",
        "url_key": "s3_url",
        "md5_key": "sra_md5",
        "bytes_key": "sra_bytes",
        "method": "aws",
    },
    "gcs": {
        "label": "Google Cloud",
        "url_key": "gs_url",
        "md5_key": "sra_md5",
        "bytes_key": "sra_bytes",
        "method": "gsutil",
    },
}

_DOWNLOAD_MODE_REGEX = "|".join(_DOWNLOAD_MODES)


@api_app.get(
    "/project/{study}/download-manifest",
    summary="Get structured download manifest for a study",
    description=(
        "Return paginated JSON file entries for downloading an SRA/ENA study. "
        "This is the preferred MCP tool for complete per-file FASTQ retrieval. "
        "For paired-layout runs with one ENA FASTQ, mode=fastq returns an "
        "SRA/SRA Lite source plus sracha conversion command. "
        "Modes: fastq, sra, sra_lite, s3, or gcs. Each entry includes URL, "
        "target path, checksum/size when available, and run/experiment metadata."
    ),
    operation_id="get_study_download_manifest",
)
@limiter.limit("100/minute")
def get_study_download_manifest(
    request: Request,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
    mode: str = Query("fastq", pattern=_DOWNLOAD_MODE_REGEX),
    limit: int = Query(500, ge=1, le=1000),
    cursor: int = Query(0, ge=0),
):
    study = study.upper()
    cfg = _DOWNLOAD_MODES.get(mode)
    if not cfg:
        raise HTTPException(400, f"Invalid mode: {mode}")

    url_key = cfg["url_key"]
    md5_key = cfg["md5_key"]
    bytes_key = cfg.get("bytes_key")
    method = cfg["method"]
    entries: list[dict] = []
    seen = 0
    has_more = False
    summary: dict = {}

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(STUDY_RUNS_SUMMARY_SQL, (study,))
                summary = cur.fetchone() or {}
                cur.execute(STUDY_RUNS_SQL, (study,))
                while True:
                    batch = cur.fetchmany(500)
                    if not batch:
                        break
                    for row in batch:
                        for entry in _split_fastq_fields(row, study):
                            if mode == "fastq" and entry.get("fastq_is_interleaved"):
                                item = _fastq_conversion_manifest_entry(study, entry)
                                if item is None:
                                    item = _download_manifest_entry(
                                        study,
                                        mode,
                                        method,
                                        url_key,
                                        bytes_key,
                                        md5_key,
                                        entry,
                                    )
                            else:
                                item = _download_manifest_entry(
                                    study,
                                    mode,
                                    method,
                                    url_key,
                                    bytes_key,
                                    md5_key,
                                    entry,
                                )
                            if item is None:
                                continue
                            if seen < cursor:
                                seen += 1
                                continue
                            if len(entries) >= limit:
                                has_more = True
                                break
                            entries.append(item)
                            seen += 1
                        if has_more:
                            break
                    if has_more:
                        break
    except Exception:
        logger.exception("Database error in get_study_download_manifest")
        raise HTTPException(500, "Internal server error")

    return {
        "study_accession": study,
        "mode": mode,
        "method": method,
        "total_runs": summary.get("total_runs", 0),
        "total_fastq_bytes": summary.get("total_fastq_bytes", 0),
        "cursor": cursor,
        "limit": limit,
        "next_cursor": cursor + len(entries) if has_more else None,
        "files": entries,
    }


@api_app.get(
    "/project/{study}/download/{mode}",
    summary="Download script for a study",
    description=(
        "Return a bash script to download all runs in a study using the "
        "specified source: fastq (EBI FASTQ via wget; paired single-FASTQ "
        "runs are converted from SRA/SRA Lite with sracha), sra "
        "(SRA normalized via wget), sra_lite (SRA Lite via wget), s3 (AWS S3 via aws cli), "
        "or gcs (Google Cloud via gsutil)."
    ),
    operation_id="download_study_script",
)
@limiter.limit("200/minute")
def download_study_script(
    request: Request,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
    mode: str = Path(..., pattern=_DOWNLOAD_MODE_REGEX),
):
    study = study.upper()
    cfg = _DOWNLOAD_MODES.get(mode)
    if not cfg:
        raise HTTPException(400, f"Invalid mode: {mode}")

    url_key = cfg["url_key"]
    md5_key = cfg["md5_key"]
    bytes_key = cfg.get("bytes_key")
    method = cfg["method"]
    label = cfg["label"]

    lines: list[str] = []
    expected_paths: list[str] = []
    expected_sizes: list[int] = []
    checksums: list[tuple[str, str]] = []  # (md5, filepath)
    download_count = 0
    interleaved_conversion_count = 0
    interleaved_direct_count = 0
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(STUDY_RUNS_SQL, (study,))
                while True:
                    batch = cur.fetchmany(500)
                    if not batch:
                        break
                    for row in batch:
                        for entry in _split_fastq_fields(row, study):
                            conversion_source = None
                            if mode == "fastq" and entry.get("fastq_is_interleaved"):
                                conversion_source = (
                                    _preferred_fastq_conversion_source_from_entry(entry)
                                )
                            if conversion_source:
                                dirpath = entry["directory_path"]
                                fastq_url = entry.get("fastq_url") or ""
                                if fastq_url:
                                    fastq_path = f"{dirpath}/{entry['filename']}"
                                    fastq_size = _coerce_int(entry.get("fastq_bytes"))
                                    lines.append(
                                        "# Direct ENA FASTQ is interleaved for this paired run; "
                                        "kept for reference only."
                                    )
                                    lines.append(
                                        f"# download_one {_shell_escape(fastq_url)} {_shell_escape(fastq_path)} wget {fastq_size}"
                                    )
                                lines.append(
                                    _sracha_fastq_shell_call(
                                        entry["run_accession"],
                                        dirpath,
                                        conversion_source["source"],
                                    )
                                )
                                for expected_path in _expected_split_fastq_paths(entry):
                                    expected_paths.append(expected_path)
                                    expected_sizes.append(0)
                                download_count += 1
                                interleaved_conversion_count += 1
                                continue

                            url = entry.get(url_key) or ""
                            if not url:
                                continue
                            dirpath = entry["directory_path"]
                            filename = entry["filename"]
                            path = f"{dirpath}/{filename}"
                            size = _coerce_int(entry.get(bytes_key)) if bytes_key else 0
                            lines.append(
                                f"download_one {_shell_escape(url)} {_shell_escape(path)} {method} {size}"
                            )
                            expected_paths.append(path)
                            expected_sizes.append(size)
                            md5 = entry.get(md5_key) or ""
                            if md5:
                                checksums.append((md5, path))
                            download_count += 1
                            if mode == "fastq" and entry.get("fastq_is_interleaved"):
                                interleaved_direct_count += 1
    except Exception:
        logger.exception("Error generating download script for %s", study)
        raise HTTPException(500, "Internal error generating download script")

    if not lines:
        raise HTTPException(
            404,
            f"No {label} URLs available for {study}",
        )

    conversion_note = (
        f"# Paired-layout runs with one ENA FASTQ: {interleaved_conversion_count}; "
        f"using sracha-rs SRA/SRA Lite fetch with --split split-files.\n"
        if interleaved_conversion_count
        else ""
    )
    direct_note = (
        f"# WARNING: {interleaved_direct_count} paired-layout run(s) had one ENA FASTQ "
        f"but no SRA/SRA Lite URL was available, so the interleaved FASTQ was downloaded directly.\n"
        if interleaved_direct_count
        else ""
    )
    conversion_helper = _SRACHA_FASTQ_HELPER if interleaved_conversion_count else ""

    script = (
        f"#!/usr/bin/env bash\n"
        f"# Download {label} for {study}\n"
        f"# Generated by seqout.org\n"
        f"# Resumable: re-run this script to skip completed files and continue partial transfers.\n"
        f"# Integrity-checked: corrupted .gz / .bam files are detected and redownloaded automatically.\n"
        f"{conversion_note}"
        f"{direct_note}\n"
        f"set -euo pipefail\n\n"
        f"{_DOWNLOAD_HELPER}"
        f"{conversion_helper}"
        f"{_resume_status_preamble(expected_paths, expected_sizes if all(expected_sizes) else None)}"
        f"# Download metadata\n"
        f"curl -sS --retry 10 --retry-delay 5 --retry-all-errors --fail"
        f' "https://seqout.org/api/project/{study}/metadata/download"'
        f' -o "{study}/metadata.csv" --create-dirs\n\n'
    )
    script += "\n".join(lines) + "\n"
    script += (
        f'\necho "Done. {download_count} source files processed under ./{study}/"\n'
    )
    if checksums:
        script += "\n# Verify checksums\n"
        script += "echo 'Verifying MD5 checksums...'\n"
        script += "md5sum -c <<'MD5SUMS'\n"
        for md5, path in checksums:
            script += f"{md5}  {path}\n"
        script += "MD5SUMS\n"

    return PlainTextResponse(script, media_type="text/plain; charset=utf-8")


_SUPP_GEO_SQL = "SELECT supplementary_data FROM geo_series WHERE accession = %s"
_SUPP_AE_SQL = "SELECT processed_data_files AS supplementary_data FROM arrayexpress_experiments WHERE accession = %s"
_SUPP_GEA_SQL = (
    "SELECT data_files AS supplementary_data FROM gea_experiments WHERE accession = %s"
)


def _ftp_to_https(url: str) -> str:
    if url.startswith("ftp://"):
        return "https://" + url[6:]
    return url


def _extract_supplementary_urls(supplementary_data) -> list[dict]:
    """Parse supplementary_data into [{url, filename}] for GEO and ArrayExpress."""
    if not supplementary_data:
        return []
    items = []
    for entry in supplementary_data:
        if not isinstance(entry, dict):
            continue
        raw_url = (entry.get("#text") or entry.get("url") or "").strip()
        if not raw_url:
            continue
        url = _ftp_to_https(raw_url)
        filename = entry.get("path") or url.rstrip("/").rsplit("/", 1)[-1] or "file"
        items.append({"url": url, "filename": filename})
    return items


def _shell_escape(s: str) -> str:
    """Escape a string for safe use in single-quoted shell context."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _download_command(method: str, url: str, path: str) -> str:
    if method == "wget":
        return f"wget -c -O {_shell_escape(path)} {_shell_escape(url)}"
    if method == "curl":
        return f"curl -L -C - -o {_shell_escape(path)} {_shell_escape(url)}"
    if method == "aws":
        return f"aws s3 cp {_shell_escape(url)} {_shell_escape(path)}"
    if method == "gsutil":
        return f"gsutil cp {_shell_escape(url)} {_shell_escape(path)}"
    return f"{method} {_shell_escape(url)} {_shell_escape(path)}"


@api_app.get(
    "/project/{accession}/supplementary",
    summary="List supplementary files",
    description=(
        "Return structured supplementary / processed data file links for a GEO "
        "(GSE) or ArrayExpress (E-*) project."
    ),
    operation_id="get_supplementary_files",
)
@limiter.limit("100/minute")
def get_supplementary_files(
    request: Request,
    accession: str = Path(..., pattern=PROJECT_REGEX),
):
    accession = accession.upper()
    if accession.startswith("E-GEAD-"):
        sql = _SUPP_GEA_SQL
        source = "gea"
    elif accession.startswith("E-"):
        sql = _SUPP_AE_SQL
        source = "arrayexpress"
    elif accession.startswith("G"):
        sql = _SUPP_GEO_SQL
        source = "geo"
    else:
        raise HTTPException(
            400,
            "Supplementary files are only available for GEO and ArrayExpress projects",
        )

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (accession,))
                row = cur.fetchone()
    except Exception:
        logger.exception("Error fetching supplementary data for %s", accession)
        raise HTTPException(500, "Internal server error")

    if not row:
        raise HTTPException(404, f"Project {accession} not found")

    files = []
    for f in _extract_supplementary_urls(row.get("supplementary_data")):
        path = f"{accession}/{f['filename']}"
        files.append(
            {
                "accession": accession,
                "source": source,
                "url": f["url"],
                "filename": f["filename"],
                "path": path,
                "download_command": _download_command("curl", f["url"], path),
            }
        )

    return {
        "accession": accession,
        "source": source,
        "total_files": len(files),
        "files": files,
    }


@api_app.get(
    "/project/{accession}/supplementary/download",
    summary="Download script for supplementary files",
    description=(
        "Return a bash script to download all supplementary / processed data files "
        "for a GEO (GSE) or ArrayExpress (E-MTAB) project using curl."
    ),
    operation_id="download_supplementary_script",
)
@limiter.limit("200/minute")
def download_supplementary_script(
    request: Request,
    accession: str = Path(..., pattern=PROJECT_REGEX),
):
    accession = accession.upper()
    if accession.startswith("E-GEAD-"):
        sql = _SUPP_GEA_SQL
    elif accession.startswith("E-"):
        sql = _SUPP_AE_SQL
    elif accession.startswith("G"):
        sql = _SUPP_GEO_SQL
    else:
        raise HTTPException(
            400,
            "Supplementary downloads are only available for GEO and ArrayExpress projects",
        )

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (accession,))
                row = cur.fetchone()
    except Exception:
        logger.exception("Error fetching supplementary data for %s", accession)
        raise HTTPException(500, "Internal server error")

    if not row:
        raise HTTPException(404, f"Project {accession} not found")

    files = _extract_supplementary_urls(row.get("supplementary_data"))
    if not files:
        raise HTTPException(404, f"No supplementary files found for {accession}")

    lines = []
    expected_paths: list[str] = []
    for f in files:
        path = f"{accession}/{f['filename']}"
        lines.append(f'download_one "{f["url"]}" "{path}" curl')
        expected_paths.append(path)

    script = (
        f"#!/usr/bin/env bash\n"
        f"# Download supplementary files for {accession}\n"
        f"# {len(files)} file{'s' if len(files) != 1 else ''}\n"
        f"# Generated by seqout.org\n"
        f"# Resumable: re-run this script to continue any partial/failed transfers.\n"
        f"# Integrity-checked: corrupted .gz files are detected and redownloaded automatically.\n\n"
        f"set -euo pipefail\n\n"
        f"{_DOWNLOAD_HELPER}"
        f"{_resume_status_preamble(expected_paths)}"
    )
    script += "\n".join(lines) + "\n"
    script += f'\necho "Done. {len(files)} file{"s" if len(files) != 1 else ""} saved under ./{accession}/"\n'

    return PlainTextResponse(script, media_type="text/plain; charset=utf-8")


@api_app.get(
    "/sample/{accession}",
    summary="Get sample",
    description="Return an SRA/ENA sample (SRS/DRS/ERS/SAM*) by accession.",
    operation_id="get_sample",
)
@limiter.limit("100/minute")
def get_sample(
    request: Request,
    response: Response,
    accession: str = Path(..., pattern=SAMPLE_OR_ENA_REGEX),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                sample = _fetch_sample(cur, accession)
    except Exception as e:
        logger.exception("Database error in get_sample")
        raise HTTPException(500, "Internal server error")
    if sample:
        response.headers["Cache-Control"] = _PROJECT_CACHE_CONTROL
    return sample


SAMPLE_DETAIL_REGEX = (
    rf"^({_EXPERIMENT_PREFIX}|{_SAMPLE_PREFIX}|GSM|SAM[A-Z]+|CRX|HRX|HRS)\d+$"
)


def _resolve_sample_detail(cur, accession: str) -> dict:
    """Resolve any sample/experiment accession to full detail.

    Returns a dict with:
      - project: full parent project metadata
      - sample_type: 'geo_sample' | 'sra_experiment' | 'sra_sample'
      - sample: the sample/experiment record
      - experiment: experiment metadata (for SRA types)
      - runs: run download links (for SRA types)
    """
    acc = accession.upper()

    # --- GSM sample ---
    if GEO_SAMPLE_PATTERN.match(acc):
        # Fetch GEO sample
        cur.execute(GEO_SAMPLE_SQL, (acc,))
        geo_sample = cur.fetchone()
        if not geo_sample:
            raise HTTPException(404, f"Sample not found: {acc}")

        # Find parent series
        cur.execute(GEO_SAMPLE_TO_SERIES_SQL, (acc,))
        series_row = cur.fetchone()
        if not series_row:
            raise HTTPException(404, f"No parent series found for {acc}")

        series_acc = series_row["accession"]
        cur.execute(PROJECT_GEO_SQL, (series_acc,))
        project = cur.fetchone()
        if project:
            _enrich_with_publications([project], cur)

        result = {
            "sample_type": "geo_sample",
            "project": project,
            "project_accession": series_acc,
            "sample": geo_sample,
            "experiment": None,
            "runs": None,
        }
        return result

    # --- SRX/DRX/ERX / CRX/HRX experiment ---
    if EXPERIMENT_PATTERN.match(acc) or GSA_EXPERIMENT_PATTERN.match(acc):
        # Fetch experiment
        cur.execute(SRA_EXPERIMENT_SQL, (acc,))
        experiment = cur.fetchone()
        if not experiment:
            cur.execute(ENA_EXPERIMENT_SQL, (acc,))
            experiment = cur.fetchone()
        if not experiment:
            cur.execute(GSA_EXPERIMENT_SQL, (acc,))
            experiment = cur.fetchone()
        if not experiment:
            raise HTTPException(404, f"Experiment not found: {acc}")

        study_acc = experiment.get("study")
        if not study_acc:
            raise HTTPException(404, f"No parent study found for {acc}")

        # Fetch parent project
        if study_acc.startswith(("CRA", "HRA")):
            cur.execute(PROJECT_GSA_SQL, (study_acc,))
        elif study_acc.startswith("PRJ"):
            cur.execute(PROJECT_ENA_SQL, (study_acc,))
        else:
            cur.execute(PROJECT_SRA_SQL, (study_acc,))
        project = cur.fetchone()
        if project:
            _enrich_with_publications([project], cur)

        # Fetch associated sample
        sample = None
        samples_list = experiment.get("samples") or []
        if samples_list:
            sample = _fetch_sample(cur, samples_list[0])

        # Fetch runs for this experiment
        cur.execute(EXPERIMENT_RUNS_SQL, (acc,))
        runs = _annotate_run_downloads(cur.fetchall())

        result = {
            "sample_type": "sra_experiment",
            "project": project,
            "project_accession": study_acc,
            "sample": sample,
            "experiment": experiment,
            "runs": runs,
        }
        return result

    # --- SRS/DRS/ERS/SAM* sample ---
    sample = _fetch_sample(cur, acc)
    if not sample:
        raise HTTPException(404, f"Sample not found: {acc}")

    # Find the experiment that references this sample
    cur.execute(SRA_SAMPLE_TO_EXPERIMENT_SQL, (acc,))
    exp_row = cur.fetchone()
    experiment = None
    runs = []

    if exp_row:
        exp_acc = exp_row["accession"]
        cur.execute(SRA_EXPERIMENT_SQL, (exp_acc,))
        experiment = cur.fetchone()
        cur.execute(EXPERIMENT_RUNS_SQL, (exp_acc,))
        runs = _annotate_run_downloads(cur.fetchall())
    else:
        # Try ENA
        cur.execute(ENA_SAMPLE_TO_EXPERIMENT_SQL, (acc,))
        exp_row = cur.fetchone()
        if exp_row:
            exp_acc = exp_row["experiment_accession"]
            cur.execute(ENA_EXPERIMENT_SQL, (exp_acc,))
            experiment = cur.fetchone()
            cur.execute(EXPERIMENT_RUNS_SQL, (exp_acc,))
            runs = _annotate_run_downloads(cur.fetchall())
        else:
            # Try GSA
            cur.execute(GSA_SAMPLE_TO_EXPERIMENT_SQL, (acc,))
            exp_row = cur.fetchone()
            if exp_row:
                exp_acc = exp_row["experiment_accession"]
                cur.execute(GSA_EXPERIMENT_SQL, (exp_acc,))
                experiment = cur.fetchone()
                cur.execute(EXPERIMENT_RUNS_SQL, (exp_acc,))
                runs = _annotate_run_downloads(cur.fetchall())

    # Find parent study
    study_acc = None
    if experiment:
        study_acc = experiment.get("study")
    else:
        cur.execute(SRA_SAMPLE_TO_STUDY_SQL, (acc,))
        study_row = cur.fetchone()
        if study_row:
            study_acc = study_row["accession"]

    project = None
    if study_acc:
        if study_acc.startswith(("CRA", "HRA")):
            cur.execute(PROJECT_GSA_SQL, (study_acc,))
        elif study_acc.startswith("PRJ"):
            cur.execute(PROJECT_ENA_SQL, (study_acc,))
        else:
            cur.execute(PROJECT_SRA_SQL, (study_acc,))
        project = cur.fetchone()
        if project:
            _enrich_with_publications([project], cur)

    result = {
        "sample_type": "sra_sample",
        "project": project,
        "project_accession": study_acc,
        "sample": sample,
        "experiment": experiment,
        "runs": runs,
    }
    return result


@api_app.get(
    "/sample-detail/{accession}",
    summary="Get sample detail",
    description=(
        "Return full metadata for a sample or experiment accession "
        "(GSM, SRX/DRX/ERX, SRS/DRS/ERS, SAM*). Includes parent project metadata, "
        "sample attributes, experiment info, and run download links."
    ),
    operation_id="get_sample_detail",
)
@limiter.limit("100/minute")
def get_sample_detail(
    request: Request,
    accession: str = Path(..., pattern=SAMPLE_DETAIL_REGEX),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                return _resolve_sample_detail(cur, accession)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_sample_detail")
        raise HTTPException(500, "Internal server error")


@api_app.get(
    "/sample-detail/{accession}/download",
    summary="Download sample metadata as CSV",
    description=(
        "Return a CSV file with metadata for a single sample or experiment accession. "
        "Includes experiment info, sample attributes, and run download links."
    ),
    operation_id="download_sample_detail_csv",
)
@limiter.limit("20/minute")
def download_sample_detail_csv(
    request: Request,
    accession: str = Path(..., pattern=SAMPLE_DETAIL_REGEX),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                detail = _resolve_sample_detail(cur, accession)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in download_sample_detail_csv")
        raise HTTPException(500, "Internal server error")

    sample_type = detail["sample_type"]
    sample = detail.get("sample") or {}
    experiment = detail.get("experiment") or {}
    runs = detail.get("runs") or []
    project_accession = detail.get("project_accession") or ""

    if sample_type == "geo_sample":
        # For GEO samples, flatten channel info into a CSV
        channels = sample.get("channels") or []
        rows = []
        for ch in channels:
            row = {
                "sample_accession": sample.get("accession", ""),
                "sample_title": sample.get("title", ""),
                "sample_description": sample.get("description", ""),
                "sample_type": sample.get("sample_type", ""),
                "platform": sample.get("platform_ref", ""),
                "series_accession": project_accession,
            }
            if isinstance(ch, dict):
                org = ch.get("Organism") or {}
                row["organism"] = (
                    org.get("#text", "") if isinstance(org, dict) else str(org)
                )
                row["source"] = ch.get("Source", "")
                row["molecule"] = ch.get("Molecule", "")
                row["label"] = ch.get("Label", "")
                for char in ch.get("Characteristics") or []:
                    if isinstance(char, dict):
                        tag = char.get("@tag", "")
                        text = char.get("#text", "")
                        if tag:
                            row[f"characteristic:{tag}"] = text
            rows.append(row)

        if not rows:
            rows = [
                {
                    "sample_accession": sample.get("accession", ""),
                    "sample_title": sample.get("title", ""),
                    "sample_description": sample.get("description", ""),
                    "series_accession": project_accession,
                }
            ]

        csv_text = rows_to_csv(rows)
        return StreamingResponse(
            iter([csv_text]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{accession}_metadata.csv"'
            },
        )

    # SRA experiment or sample
    # Parse sample attributes
    attrs_raw = sample.get("attributes_json")
    if isinstance(attrs_raw, str):
        try:
            attrs_raw = json.loads(attrs_raw)
        except (json.JSONDecodeError, TypeError):
            attrs_raw = None
        sample["attributes_json"] = attrs_raw

    attr_dict = {}
    if isinstance(attrs_raw, list):
        for attr in attrs_raw:
            key = attr.get("tag") or attr.get("key") or ""
            val = attr.get("value") or ""
            if key:
                attr_dict[f"sample_attribute:{key}"] = val
    elif isinstance(attrs_raw, dict):
        for key, val in attrs_raw.items():
            attr_dict[f"sample_attribute:{key}"] = val

    if runs:
        # One row per run
        rows = []
        for run in runs:
            row = {
                "study_accession": project_accession,
                "experiment_accession": experiment.get("accession", ""),
                "sample_accession": sample.get("accession", ""),
                "sample_alias": sample.get("alias", ""),
                "sample_title": sample.get("title", ""),
                "scientific_name": sample.get("scientific_name", ""),
                "taxon_id": sample.get("taxon_id", ""),
                "library_layout": experiment.get("library_layout")
                or run.get("library_layout", ""),
                "library_strategy": experiment.get("library_strategy", ""),
                "library_source": experiment.get("library_source", ""),
                "library_selection": experiment.get("library_selection", ""),
                "platform": experiment.get("platform", ""),
                "instrument_model": experiment.get("instrument_model", ""),
                "run_accession": run.get("run_accession", ""),
                "run_alias": run.get("run_alias", ""),
                "fastq_ftp": run.get("fastq_ftp", ""),
                "fastq_bytes": run.get("fastq_bytes", ""),
                "fastq_md5": run.get("fastq_md5", ""),
                "sra_ftp": run.get("sra_ftp", ""),
                "sra_bytes": run.get("sra_bytes", ""),
                **attr_dict,
            }
            rows.append(row)
    else:
        rows = [
            {
                "study_accession": project_accession,
                "experiment_accession": experiment.get("accession", ""),
                "sample_accession": sample.get("accession", ""),
                "sample_alias": sample.get("alias", ""),
                "sample_title": sample.get("title", ""),
                "scientific_name": sample.get("scientific_name", ""),
                "taxon_id": sample.get("taxon_id", ""),
                "library_strategy": experiment.get("library_strategy", ""),
                "platform": experiment.get("platform", ""),
                "instrument_model": experiment.get("instrument_model", ""),
                **attr_dict,
            }
        ]

    csv_text = rows_to_csv(rows)
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{accession}_metadata.csv"'
        },
    )


@api_app.get(
    "/common-name",
    summary="Get common name by scientific name",
    description=(
        "Return matching common names from the common_names table for a scientific name."
    ),
    operation_id="get_common_name_by_scientific_name",
)
@limiter.limit("100/minute")
def get_common_name_by_scientific_name(
    request: Request,
    scientific_name: str = Query(..., min_length=1, max_length=255),
):
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    COMMON_NAME_BY_SCIENTIFIC_NAME_SQL, (scientific_name.strip(),)
                )
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_common_name_by_scientific_name")
        raise HTTPException(500, "Internal server error")

    if not rows:
        raise HTTPException(
            404,
            f"No common names found for scientific_name '{scientific_name}'",
        )

    return rows


@api_app.get(
    "/common-names",
    summary="Get common names for many scientific names",
    description=(
        "Batch variant of /common-name: pass scientific_name repeatedly to "
        "resolve a whole list in one request. Missing names are simply absent "
        "from the result (no 404)."
    ),
    operation_id="get_common_names_by_scientific_names",
)
@limiter.limit("100/minute")
def get_common_names_by_scientific_names(
    request: Request,
    scientific_name: list[str] = Query(..., min_length=1, max_length=200),
):
    names = sorted({n.strip().lower() for n in scientific_name if n.strip()})
    if not names:
        raise HTTPException(400, "No valid scientific_name provided")
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(COMMON_NAMES_BY_SCIENTIFIC_NAMES_SQL, (names,))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_common_names_by_scientific_names")
        raise HTTPException(500, "Internal server error")

    return rows


@api_app.get(
    "/geo/series/{accession}/samples",
    summary="List GEO series or ArrayExpress experiment samples",
    description="List all samples for a GEO series (GSE) or ArrayExpress experiment (E-XXXX-NNN).",
    operation_id="get_geo_series_samples",
)
@limiter.limit("100/minute")
def get_geo_series_samples(
    request: Request,
    response: Response,
    accession: str = Path(...),
    limit: int | None = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    accession = accession.upper()
    source = _accession_source(accession)
    if source not in ["geo", "arrayexpress", "gea"]:
        raise HTTPException(400, f"Invalid accession format: {accession}")

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # Cheap indexed count + a SQL-paginated page so 100k-sample
                # series don't fetch every row.
                total = _count_geo_series_samples(cur, accession, source)
                rows = _fetch_geo_series_samples(
                    cur, accession, source, limit=limit, offset=offset
                )
    except Exception as e:
        logger.exception("Database error in get_geo_series_samples")
        raise HTTPException(500, "Internal server error")
    response.headers["X-Total-Count"] = str(total)
    return rows


@api_app.get(
    "/arrayexpress/experiment/{accession}/samples",
    summary="List ArrayExpress experiment samples",
    description="List all samples for an ArrayExpress experiment accession (E-XXXX-NNN).",
    operation_id="get_ae_experiment_samples",
)
@limiter.limit("100/minute")
def get_ae_experiment_samples(
    request: Request,
    accession: str = Path(..., pattern=AE_REGEX),
):
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(AE_EXPERIMENT_SAMPLES_SQL, (accession,))
                rows = cur.fetchall()
    except Exception as e:
        logger.exception("Database error in get_ae_experiment_samples")
        raise HTTPException(500, "Internal server error")

    return rows


@api_app.get(
    "/geo/series/{accession}/neighbors",
    summary="Get GEO series neighbors and 3D coordinates",
    description="Return only neighbors and coords_3d for a GEO series accession (GSE).",
    operation_id="get_geo_series_neighbors_coords",
)
@limiter.limit("100/minute")
def get_geo_series_neighbors_coords(
    request: Request,
    accession: str = Path(..., pattern=GEO_SERIES_REGEX),
):
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(GEO_SERIES_NEIGHBORS_COORDS_SQL, (accession,))
                return cur.fetchone()
    except Exception:
        logger.exception("Database error in get_geo_series_neighbors_coords")
        raise HTTPException(500, "Internal server error")


@api_app.post(
    "/bulk/metadata",
    summary="Bulk metadata download",
    description=(
        "Generate a ZIP of CSV files for bulk accessions. "
        "Accepts GSE, SRP/DRP/ERP, or PRJNA accessions."
    ),
    operation_id="bulk_metadata",
)
@limiter.limit("10/minute")
def bulk_metadata(request: Request, payload: dict):
    accessions = payload.get("accessions", [])
    if not accessions:
        raise HTTPException(400, "No accessions provided")

    validated = []
    for acc in accessions:
        if not isinstance(acc, str):
            raise HTTPException(400, f"Invalid accession type: {type(acc).__name__}")
        acc = acc.strip().upper()
        if not BULK_PATTERN.match(acc):
            raise HTTPException(400, f"Invalid accession format: {acc}")
        validated.append(acc)

    _require_bulk_limit(validated)

    try:
        buf = io.BytesIO()

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    for acc in validated:
                        source = _accession_source(acc)

                        if source == "sra":
                            rows = flatten_sra_study(cur, acc)
                            name = f"{acc}_experiments.csv"
                        elif source == "geo":
                            rows = flatten_geo_series(cur, acc)
                            name = f"{acc}_samples.csv"
                        elif source == "arrayexpress":
                            rows = flatten_ae_experiment(cur, acc)
                            name = f"{acc}_samples.csv"
                        elif acc.startswith("PRJNA"):
                            cur.execute(BIO_PROJECT_GEO_SERIES_SQL, (acc,))
                            series_list = cur.fetchall()
                            for series in series_list:
                                gse = series["accession"]
                                rows = flatten_geo_series(cur, gse)
                                if rows:
                                    name = f"{gse}_samples.csv"
                                    csv_buf = io.StringIO()
                                    writer = csv.DictWriter(
                                        csv_buf, fieldnames=rows[0].keys()
                                    )
                                    writer.writeheader()
                                    writer.writerows(rows)
                                    zf.writestr(name, csv_buf.getvalue())
                            continue
                        else:
                            continue

                        if rows:
                            csv_buf = io.StringIO()
                            writer = csv.DictWriter(csv_buf, fieldnames=rows[0].keys())
                            writer.writeheader()
                            writer.writerows(rows)
                            zf.writestr(name, csv_buf.getvalue())

        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=bulk_metadata.zip"},
        )
    except Exception as e:
        logger.exception("Error in bulk_metadata")
        raise HTTPException(500, "Internal server error")


@api_app.post(
    "/bulk/project-metadata",
    summary="Bulk project metadata lookup",
    description=(
        "Return accession, title, description, and organisms for GEO/SRA/ArrayExpress project accessions "
        "(GSE, SRP, ERP, DRP, E-XXXX)."
    ),
    operation_id="bulk_project_metadata",
)
@limiter.limit("30/minute")
def bulk_project_metadata(request: Request, payload: dict):
    accessions = payload.get("accessions", [])
    if not accessions:
        raise HTTPException(400, "No accessions provided")

    validated = []
    seen = set()
    for acc in accessions:
        if not isinstance(acc, str):
            raise HTTPException(400, f"Invalid accession type: {type(acc).__name__}")
        normalized = acc.strip().upper()
        if not PROJECT_PATTERN.match(normalized):
            raise HTTPException(400, f"Invalid accession format: {acc}")
        if normalized not in seen:
            seen.add(normalized)
            validated.append(normalized)

    _require_bulk_limit(validated)

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(BULK_PROJECT_METADATA_SQL, (validated,))
                rows = cur.fetchall()
                return rows
    except Exception:
        logger.exception("Database error in bulk_project_metadata")
        raise HTTPException(500, "Internal server error")


@api_app.post(
    "/bulk/project-samples-metadata",
    summary="Bulk project metadata with flattened samples",
    description=(
        "Return project title/description/overall_design/source plus flattened sample rows "
        "for project accessions (GSE, SRP, ERP, DRP)."
    ),
    operation_id="bulk_project_samples_metadata",
)
def bulk_project_samples_metadata(request: Request, payload: dict):
    accessions = payload.get("accessions", [])
    if not accessions:
        raise HTTPException(400, "No accessions provided")

    validated = []
    for acc in accessions:
        if not isinstance(acc, str):
            raise HTTPException(400, f"Invalid accession type: {type(acc).__name__}")
        normalized = acc.strip().upper()
        if not PROJECT_PATTERN.match(normalized):
            raise HTTPException(400, f"Invalid accession format: {acc}")
        validated.append(normalized)

    _require_bulk_limit(validated)

    try:
        out = []
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                for acc in validated:
                    if acc.startswith("GSE"):
                        cur.execute(PROJECT_GEO_SQL, (acc,))
                        project = cur.fetchone()
                        if not project:
                            continue
                        samples = flatten_geo_series(cur, acc)
                        out.append(
                            {
                                "accession": acc,
                                "title": project.get("title"),
                                "description": project.get("summary"),
                                "overall_design": project.get("overall_design"),
                                "source": "geo",
                                "samples": samples,
                            }
                        )
                    else:
                        cur.execute(PROJECT_SRA_SQL, (acc,))
                        project = cur.fetchone()
                        if not project:
                            continue
                        samples = flatten_sra_study(cur, acc)
                        out.append(
                            {
                                "accession": acc,
                                "title": project.get("title"),
                                "description": project.get("abstract"),
                                "overall_design": None,
                                "source": "sra",
                                "samples": samples,
                            }
                        )

        return out
    except Exception:
        logger.exception("Database error in bulk_project_samples_metadata")
        raise HTTPException(500, "Internal server error")


# no *_QUERY_ACCESSIONS_SQL for these; their search SQL already yields accession,
# source and updated_at
_SEARCH_BACKED_DOWNLOAD_DBS = ("ena", "gsa", "dra", "gea")


def _download_accessions_from_search(
    q: str, db: str, year_from: int | None, year_to: int | None
) -> list[dict]:
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SET LOCAL statement_timeout = '60s'")
            cur.execute(
                _SEARCH_SQL_BY_DB[db],
                (q, _search_query_variants(q), None, None, None, 0),
            )
            rows = cur.fetchall()

    kept = []
    for row in rows:
        updated = row.get("updated_at")
        year = updated.year if updated else None
        if year_from is not None and (year is None or year < year_from):
            continue
        if year_to is not None and (year is None or year > year_to):
            continue
        kept.append(row)
    return kept[:MAX_QUERY_DOWNLOAD]


def _flatten_by_source(cur, acc: str, source: str) -> tuple[list[dict], str]:
    # ena/gsa/dra are study-grained like sra
    if source in ("sra", "ena", "gsa", "dra"):
        return flatten_sra_study(cur, acc), f"{acc}_experiments.csv"
    if source == "arrayexpress":
        return flatten_ae_experiment(cur, acc), f"{acc}_samples.csv"
    if source == "gea":
        return flatten_gea_experiment(cur, acc), f"{acc}_samples.csv"
    return flatten_geo_series(cur, acc), f"{acc}_samples.csv"


def _zip_query_results(rows: list[dict]) -> StreamingResponse:
    if not rows:
        raise HTTPException(404, "No results found for query")

    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    for row in rows:
                        data_rows, name = _flatten_by_source(
                            cur, row["accession"], row["source"]
                        )
                        if not data_rows:
                            continue
                        csv_buf = io.StringIO()
                        writer = csv.DictWriter(csv_buf, fieldnames=data_rows[0].keys())
                        writer.writeheader()
                        writer.writerows(data_rows)
                        zf.writestr(name, csv_buf.getvalue())

        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=query_metadata.zip"},
        )
    except Exception:
        logger.exception("Error building query download ZIP")
        raise HTTPException(500, "Internal server error")


@api_app.get(
    "/download/query",
    summary="Download metadata by query",
    description=(
        "Generate a ZIP of CSV files for query-matched projects. "
        "Accepts q, optional db=geo|sra|arrayexpress|ena|gsa|dra|gea, and optional "
        "updated year filters. Results are capped at 100 projects."
    ),
    operation_id="download_by_query",
)
@limiter.limit("20/minute")
def download_by_query(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    db: str | None = Query(None, pattern="^(geo|sra|arrayexpress|ena|gsa|dra|gea)$"),
    updated_year_from: int | None = Query(None, ge=1900, le=2100),
    updated_year_to: int | None = Query(None, ge=1900, le=2100),
):
    if (
        updated_year_from is not None
        and updated_year_to is not None
        and updated_year_from > updated_year_to
    ):
        raise HTTPException(
            400, "updated_year_from must be less than or equal to updated_year_to"
        )

    yp = year_params(updated_year_from, updated_year_to)

    if db in _SEARCH_BACKED_DOWNLOAD_DBS:
        try:
            rows = _download_accessions_from_search(
                q, db, updated_year_from, updated_year_to
            )
        except Exception:
            logger.exception("Database error in download_by_query (%s)", db)
            raise HTTPException(500, "Internal server error")
        return _zip_query_results(rows)

    if db == "geo":
        sql = GEO_QUERY_ACCESSIONS_SQL
        params = [q]
        params.extend([q, *yp])
        params.extend([q, *yp])
        params.extend([q, *yp])
        params.extend(yp)
    elif db == "sra":
        sql = SRA_QUERY_ACCESSIONS_SQL
        params = [q]
        params.extend([q, *yp])
        params.extend([q, *yp])
        params.extend([q, *yp])
        params.extend([q, *yp])
        # Submission center name match
        params.extend([q, *yp])
        params.extend(yp)
    elif db == "arrayexpress":
        sql = AE_QUERY_ACCESSIONS_SQL
        params = [q]
        params.extend([q, *yp])
        params.extend([q, q, *yp])
        # Organization name match
        params.extend([q, *yp])
        params.extend(yp)
    else:
        sql = ALL_QUERY_ACCESSIONS_SQL
        params = [q]
        # GEO: exact, GSM, PRJNA
        params.extend([q, *yp])
        params.extend([q, *yp])
        params.extend([q, *yp])
        # SRA: exact, SRX, PRJNA alias, SRS sample
        params.extend([q, *yp])
        params.extend([q, *yp])
        params.extend([q, *yp])
        params.extend([q, *yp])
        # SRA/ENA center name match
        params.extend([q, *yp])
        # GEO FTS, SRA FTS
        params.extend(yp)
        params.extend(yp)
        # AE: exact, ENA match, org match, FTS
        params.extend([q, *yp])
        params.extend([q, q, *yp])
        params.extend([q, *yp])
        params.extend(yp)

    params.append(MAX_QUERY_DOWNLOAD)

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in download_by_query")
        raise HTTPException(500, "Internal server error")

    return _zip_query_results(rows)


@api_app.get(
    "/accession/{accession}/project",
    summary="Resolve accession to project",
    description=(
        "Resolve a sample/experiment accession (GSM/SRS/DRS/ERS/SRX/ERX/DRX) "
        "to its parent project accession (GSE or SRP/DRP/ERP)."
    ),
    operation_id="resolve_accession_project",
)
@limiter.limit("100/minute")
def get_project_from_accession(
    request: Request,
    accession: str = Path(
        ..., pattern=f"({SAMPLE_REGEX}|{EXPERIMENT_REGEX}|{GEO_SAMPLE_REGEX})"
    ),
):
    """
    Get the project accession (study/series) for a given sample or experiment accession (SRS/DRS/ERS/SRX/ERX/DRX/GSM).

    Returns the project accession that contains the given sample or experiment.
    """
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if GEO_SAMPLE_PATTERN.match(accession):
                    # GSM sample - find GEO series
                    cur.execute(GEO_SAMPLE_TO_SERIES_SQL, (accession,))
                    result = cur.fetchone()
                    if result:
                        return {"project_accession": result["accession"]}
                elif EXPERIMENT_PATTERN.match(accession):
                    # SRX/ERX/DRX experiment - get SRA study directly
                    cur.execute(SRA_EXPERIMENT_TO_STUDY_SQL, (accession,))
                    result = cur.fetchone()
                    if not result:
                        cur.execute(DRA_EXPERIMENT_TO_STUDY_SQL, (accession,))
                        result = cur.fetchone()
                    if result:
                        return {"project_accession": result["study"]}
                else:
                    # SRS/DRS/ERS sample - find SRA study
                    cur.execute(SRA_SAMPLE_TO_STUDY_SQL, (accession,))
                    result = cur.fetchone()
                    if not result:
                        cur.execute(DRA_SAMPLE_TO_STUDY_SQL, (accession,))
                        result = cur.fetchone()
                    if result:
                        return {"project_accession": result["accession"]}

                # If no result found
                raise HTTPException(404, f"No project found for accession {accession}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Database error in get_project_from_accession")
        raise HTTPException(500, "Internal server error")


@api_app.get(
    "/prj/{prj_accession}",
    summary="Resolve PRJ to project",
    description=(
        "Resolve a PRJ accession to its project accession. "
        "PRJNA checks GEO series then SRA studies; PRJEB/PRJDA checks SRA only."
    ),
    operation_id="resolve_prj_project",
)
@limiter.limit("100/minute")
def get_prj_info(
    request: Request,
    prj_accession: str = Path(..., pattern=PRJ_REGEX),
):
    """
    Get the project accession for a PRJ accession.

    For PRJNA: searches geo_series first (bioproject_id), then sra_studies.
    For PRJDA/PRJEB: searches only sra_studies.

    Returns the project accession.
    """
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if prj_accession.upper().startswith("PRJNA"):
                    # PRJNA: search geo_series first, then sra_studies
                    cur.execute(PRJNA_TO_GEO_SERIES_SQL, (prj_accession,))
                    result = cur.fetchone()
                    if result:
                        return {"project_accession": result["accession"]}

                    # If not found in geo_series, search sra_studies
                    cur.execute(PRJ_TO_SRA_STUDY_SQL, (prj_accession,))
                    result = cur.fetchone()
                    if result:
                        return {"project_accession": result["accession"]}
                else:
                    # PRJDA, PRJEB, etc.: search only sra_studies
                    cur.execute(PRJ_TO_SRA_STUDY_SQL, (prj_accession,))
                    result = cur.fetchone()
                    if result:
                        return {"project_accession": result["accession"]}

                # GSA (CNCB-NGDC) BioProjects (PRJCA*) resolve to a CRA/HRA study.
                cur.execute(PRJ_TO_GSA_STUDY_SQL, (prj_accession,))
                result = cur.fetchone()
                if result:
                    return {"project_accession": result["accession"]}

                # ENA (incl. DDBJ mirror) keys some BioProjects by the PRJ
                # accession itself (no GEO/SRA/GSA study), e.g. assembly-only
                # projects — the project page IS /p/<PRJ>, so resolve to itself.
                cur.execute(PRJ_TO_ENA_STUDY_SQL, (prj_accession,))
                result = cur.fetchone()
                if result:
                    return {"project_accession": result["accession"]}

                # If no result found
                raise HTTPException(
                    404, f"No project found for PRJ accession {prj_accession}"
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Database error in get_prj_info")
        raise HTTPException(500, "Internal server error")


_KIND_INFO = {
    "gse": {"entity": "series", "database": "geo"},
    "gsm": {"entity": "sample", "database": "geo"},
    "srp": {"entity": "study", "database": "sra"},
    "srx": {"entity": "experiment", "database": "sra"},
    "srs": {"entity": "sample", "database": "sra"},
    "srr": {"entity": "run", "database": "sra"},
    "sam": {"entity": "biosample", "database": "biosample"},
    "ae": {"entity": "experiment", "database": "arrayexpress"},
    "prj": {"entity": "bioproject", "database": "bioproject"},
}
# GSA-native accessions (CRA/CRR/CRX, HRA/HRR/HRS/HRX) all start with C or H;
# map those first letters to the gsa archive the way S/E/D map to sra/ena/ddbj.
_SED_ARCHIVE = {"S": "ncbi-sra", "E": "ena", "D": "ddbj", "C": "gsa", "H": "gsa"}


@api_app.get(
    "/accession/{accession}/classify",
    summary="Classify an accession by database and entity kind",
    description=(
        "Pattern-match an accession and report which database family it belongs to "
        "and what entity it names (study/series/experiment/sample/run/bioproject). "
        "Format validation only — does not query the DB. Returns {valid: false} for "
        "unrecognized strings."
    ),
    operation_id="classify_accession",
)
@limiter.limit("1000/minute")
def classify_accession(
    request: Request,
    accession: str = Path(..., min_length=2, max_length=64),
):
    classified = _classify_accession_query(accession)
    if classified is None:
        return {"accession": accession, "valid": False}
    acc, kind = classified
    info = _KIND_INFO.get(kind, {})
    return {
        "accession": acc,
        "valid": True,
        "kind": kind,
        "entity": info.get("entity"),
        "database": info.get("database"),
        "archive": _SED_ARCHIVE.get(acc[0]) or info.get("database"),
    }


@api_app.get(
    "/search/authors",
    summary="Search publications and linked studies by author name",
    description=(
        "Case-insensitive substring search across publication author lists, ranked "
        "by citation count. Each result is a publication (PMID, title, journal, DOI, "
        "authors, citation count) plus its linked dataset accessions across GEO, "
        "SRA, ArrayExpress, and ENA. Only studies with a linked PubMed publication "
        "are findable here."
    ),
    operation_id="search_by_author",
)
@limiter.limit("100/minute")
def search_by_author(
    request: Request,
    q: str = Query(..., min_length=2, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
):
    pattern = f"%{q}%"
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SEARCH_AUTHORS_SQL, (pattern, limit))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in search_by_author")
        raise HTTPException(500, "Internal server error")
    return {"q": q, "total": len(rows), "results": rows}


_ERE_SPECIAL = re.compile(r"([.^$*+?()\[\]{}|\\])")


# GEO's organizations array mixes real affiliations with platform vendors and a
# "GEO" marker; these are dropped so the institute facet reflects institutes.
_INSTITUTE_NOISE = {"geo", "illumina inc.", "illumina", "pacific biosciences"}


def _author_name_regex(q: str) -> str | None:
    """First+last regex for pubmed_metadata.authors ("Given [Middle] Surname",
    comma-separated), tolerant of middle tokens so "Michael Wang" matches
    "Michael X Wang". Returns None for an empty/whitespace query."""
    tokens = [t for t in q.split() if t]
    if not tokens:
        return None
    esc = [_ERE_SPECIAL.sub(r"\\\1", t) for t in tokens]
    if len(esc) >= 2:
        body = f"{esc[0]}( [^,]*)? {esc[-1]}"
    else:
        # A single token matches a whole word anywhere in the name (given,
        # middle, or surname), not just the surname — so "saket" finds
        # "Saket Choudhary". Leading/trailing spaces enforce word boundaries.
        body = f"([^,]* )?{esc[0]}( [^,]*)?"
    return f"(^|, ){body}(,|$)"


@api_app.get(
    "/author/projects",
    summary="All projects by an author across GEO, SRA, ArrayExpress, and ENA",
    description=(
        "Given a person's name, find every dataset they authored across all four "
        "databases. Matches the name (first + last, tolerant of middle initials) "
        "against publication author lists, then resolves the linked studies. Returns "
        "project rows compatible with the search result cards. Only studies linked "
        "to a PubMed publication are findable."
    ),
    operation_id="search_author_projects",
)
@limiter.limit("20/minute")
def search_author_projects(
    request: Request,
    q: str = Query(..., min_length=3, max_length=100),
    limit: int = Query(default=200, ge=1, le=200),
):
    regex = _author_name_regex(q)
    if regex is None:
        return {"q": q, "total": 0, "results": [], "institutes": []}

    key = (q.strip().lower(), limit)
    with _author_projects_cache_lock:
        cached = _get_bounded_cache_entry(
            _author_projects_cache, key, _AUTHOR_PROJECTS_CACHE_TTL
        )
    if cached is not None:
        return cached

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # One CTE run: fetch the full deduped set (capped), then paginate
                # and facet in Python instead of re-running the CTE for facets.
                cur.execute(
                    SEARCH_AUTHOR_PROJECTS_SQL,
                    {"re": regex, "cap": _AUTHOR_PROJECTS_FETCH_CAP},
                )
                rows = cur.fetchall()
                total = rows[0]["full_total"] if rows else 0
                for r in rows:
                    r.pop("full_total", None)

                # Clean each card's institute string: drop noise, dedup orgs per project.
                for r in rows:
                    seen: dict[str, str] = {}
                    for part in (r.get("institute") or "").split(";"):
                        name = part.strip()
                        if name and name.lower() not in _INSTITUTE_NOISE:
                            seen.setdefault(name.lower(), name)
                    r["institute"] = "; ".join(seen.values()) or None

                # Institute facets over the full fetched set (case-insensitive,
                # first-seen casing wins; each project counts an institute once).
                facets: dict[str, dict] = {}
                for r in rows:
                    if not r["institute"]:
                        continue
                    for name in r["institute"].split("; "):
                        entry = facets.get(name.lower())
                        if entry:
                            entry["count"] += 1
                        else:
                            facets[name.lower()] = {"name": name, "count": 1}
                institutes = sorted(
                    facets.values(), key=lambda e: (-e["count"], e["name"])
                )

                # Enrich only the page we return, not the whole fetched set.
                results = rows[:limit]
                _enrich_with_publications(results, cur)
    except Exception:
        logger.exception("Database error in search_author_projects")
        raise HTTPException(500, "Internal server error")

    result = {"q": q, "total": total, "results": results, "institutes": institutes}
    with _author_projects_cache_lock:
        _set_bounded_cache_entry(
            _author_projects_cache,
            key,
            result,
            _MAX_AUTHOR_PROJECTS_CACHE_ENTRIES,
            _AUTHOR_PROJECTS_CACHE_TTL,
        )
    return result


@api_app.get(
    "/publication",
    summary="Find a publication and all linked projects",
    description=(
        "Reverse lookup from a publication (PMID or DOI) to every project linked to "
        "it across GEO, SRA, ArrayExpress, ENA, DRA, and GEA. Provide either pmid or "
        "doi as a query parameter. Each project carries `via`, naming the table the "
        "link came from — the inverse of how a project page resolves its publication. "
        "Returns {..publication fields.., projects: [{accession, source, title, "
        "published_at, via}], total_projects}."
    ),
    operation_id="find_publication",
)
@limiter.limit("1000/minute")
def find_publication(
    request: Request,
    pmid: str | None = Query(default=None, pattern=r"^\d+$"),
    doi: str | None = Query(default=None, min_length=1, max_length=200),
):
    if not pmid and not doi:
        raise HTTPException(400, "Provide either pmid or doi as a query parameter")
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if pmid:
                    cur.execute(PUBLICATION_BY_PMID_SQL, (pmid,))
                else:
                    cur.execute(PUBLICATION_BY_DOI_SQL, (doi,))
                pub = cur.fetchone()
                projects = []
                if pub:
                    cur.execute(
                        PROJECTS_BY_PMID_SQL,
                        {
                            "pmid": pub["pmid"],
                            "pmid_json": json.dumps([{"pmid": pub["pmid"]}]),
                        },
                    )
                    projects = cur.fetchall()
    except Exception:
        logger.exception("Database error in find_publication")
        raise HTTPException(500, "Internal server error")
    if not pub:
        raise HTTPException(404, "Publication not found")
    return {
        **pub,
        "projects": projects,
        "total_projects": len(projects),
    }


@api_app.get(
    "/search/structured",
    summary="Structured search with filters",
    description=(
        "Search across GEO, SRA, ArrayExpress, and ENA with structured filters "
        "(organism, library_strategy, platform, country, center, year range, "
        "journal, instrument_model, assay_l1/assay_l2, geo_* location fields, "
        "published_after/published_before, multi_platform, and enriched sample "
        "filters sample_tissue/sample_disease/sample_cell_type) optionally "
        "combined with full-text search. "
        "At least one of q or a filter parameter is required. "
        "Use cursors (rank + accession) for pagination. "
        "Returns {results: object[], total: int, took_ms: float, "
        "next_cursor: {rank: float, accession: string} | null}."
    ),
    operation_id="search_structured",
)
@limiter.limit("100/minute")
def search_structured(
    request: Request,
    q: str | None = Query(None, max_length=500),
    organism: str | None = Query(None),
    library_strategy: str | None = Query(None),
    platform: str | None = Query(None),
    country: str | None = Query(None),
    center: str | None = Query(None),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
    source: str | None = Query(None, pattern="^(geo|sra|arrayexpress|ena|gsa|all)$"),
    cursor_rank: float | None = Query(None),
    cursor_acc: str | None = Query(None),
    journal: str | None = Query(None),
    instrument_model: str | None = Query(None),
    geo_country: str | None = Query(None),
    geo_country_code: str | None = Query(None),
    geo_country_code_iso2: str | None = Query(None),
    geo_city: str | None = Query(None),
    geo_state: str | None = Query(None),
    geo_district: str | None = Query(None),
    geo_postcode: str | None = Query(None),
    geo_lat: float | None = Query(None, ge=-90, le=90),
    geo_lng: float | None = Query(None, ge=-180, le=180),
    geo_radius_km: float | None = Query(None, ge=0.1, le=20000),
    assay_l1: str | None = Query(
        None, description="Filter by dominant assay L1 (from unified_metadata)"
    ),
    assay_l2: str | None = Query(
        None, description="Filter by dominant assay L2 (from unified_metadata)"
    ),
    multi_platform: bool = Query(
        False, description="Only studies with 2+ sequencing platforms"
    ),
    published_after: str | None = Query(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Day-granularity lower bound (YYYY-MM-DD)",
    ),
    published_before: str | None = Query(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Day-granularity upper bound (YYYY-MM-DD)",
    ),
    sample_tissue: str | None = Query(
        None, description="Filter to studies with samples whose tissue matches (ILIKE)"
    ),
    sample_disease: str | None = Query(
        None, description="Filter to studies with samples whose disease matches (ILIKE)"
    ),
    sample_cell_type: str | None = Query(
        None,
        description="Filter to studies with samples whose cell_type matches (ILIKE)",
    ),
    count_by: str | None = Query(
        None,
        pattern="^(year|source|organism|country)$",
        description="Aggregation mode: return bucket counts instead of rows",
    ),
):
    if q:
        q = _replace_query_hyphens(q)

    has_geo_filter = any(
        v is not None
        for v in (
            geo_country,
            geo_country_code,
            geo_country_code_iso2,
            geo_city,
            geo_state,
            geo_district,
            geo_postcode,
            geo_lat,
            geo_lng,
        )
    )

    if not any(
        [
            q,
            organism,
            library_strategy,
            platform,
            country,
            center,
            year_from,
            year_to,
            journal,
            instrument_model,
            has_geo_filter,
            assay_l1,
            assay_l2,
            multi_platform,
            published_after,
            published_before,
            sample_tissue,
            sample_disease,
            sample_cell_type,
        ]
    ):
        raise HTTPException(
            400,
            "At least one of q, organism, library_strategy, platform, "
            "country, center, year_from, year_to, journal, "
            "instrument_model, assay_l1, assay_l2, multi_platform, "
            "published_after, published_before, sample_tissue, sample_disease, "
            "sample_cell_type, or a geo_* filter is required",
        )

    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(400, "year_from must be <= year_to")

    if (geo_lat is None) != (geo_lng is None):
        raise HTTPException(
            400, "geo_lat and geo_lng must both be provided or both omitted"
        )

    if geo_radius_km is not None and geo_lat is None:
        raise HTTPException(400, "geo_radius_km requires geo_lat and geo_lng")

    if not ENRICHMENT_ENABLED and any(
        [sample_tissue, sample_disease, sample_cell_type]
    ):
        raise HTTPException(403, "Enriched sample filters are temporarily disabled")

    q_variants = None
    if q:
        with pool.connection() as conn:
            q_variants = _search_query_variants(q)

    sql, params = build_structured_search(
        q=q,
        q_variants=q_variants,
        organism=organism,
        library_strategy=library_strategy,
        platform=platform,
        country=country,
        center=center,
        year_from=year_from,
        year_to=year_to,
        source=source,
        cursor_rank=cursor_rank,
        cursor_acc=cursor_acc,
        journal=journal,
        instrument_model=instrument_model,
        geo_country=geo_country,
        geo_country_code=geo_country_code,
        geo_country_code_iso2=geo_country_code_iso2,
        geo_city=geo_city,
        geo_state=geo_state,
        geo_district=geo_district,
        geo_postcode=geo_postcode,
        geo_lat=geo_lat,
        geo_lng=geo_lng,
        geo_radius_km=geo_radius_km,
        assay_l1=assay_l1,
        assay_l2=assay_l2,
        multi_platform=multi_platform,
        published_after=published_after,
        published_before=published_before,
        sample_tissue=sample_tissue,
        sample_disease=sample_disease,
        sample_cell_type=sample_cell_type,
        count_by=count_by,
    )

    start = time.perf_counter()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SET LOCAL statement_timeout = '30s'")
                cur.execute(sql, params)
                rows = cur.fetchall()
                if not count_by:
                    _enrich_with_publications(rows, cur)
    except Exception as e:
        err_msg = str(e).lower()
        if "canceling statement" in err_msg or "timeout" in err_msg:
            logger.warning("search_structured timed out: %s", params)
            raise HTTPException(
                504,
                "Query timed out. Try narrowing your search with more filters, "
                "adding a text query (q=), or restricting to a single source.",
            )
        logger.exception("Database error in search_structured")
        raise HTTPException(500, "Internal server error")

    took_ms = (time.perf_counter() - start) * 1000

    if count_by:
        return {
            "count_by": count_by,
            "total_buckets": len(rows),
            "took_ms": round(took_ms, 2),
            "buckets": rows,
        }

    total = rows[0]["total_count"] if rows else 0
    return {
        "results": rows,
        "total": total,
        "took_ms": round(took_ms, 2),
        "next_cursor": (
            None
            if not rows
            else {
                "rank": rows[-1]["rank"],
                "accession": rows[-1]["accession"],
            }
        ),
    }


@api_app.get(
    "/project/{accession}/xref",
    summary="Cross-reference lookup across archives",
    description=(
        "Discover cross-references for a study across GEO, SRA, ArrayExpress and ENA "
        "by following cross-reference links, SRA aliases, and E-GEOD naming conventions. "
        "Returns {accession: string, xref: [{accession, link_type, source}]}."
    ),
    operation_id="get_project_xref",
)
@limiter.limit("100/minute")
def get_project_xref(
    request: Request,
    accession: str = Path(...),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(RELATED_SQL, {"acc": accession})
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_project_xref")
        raise HTTPException(500, "Internal server error")

    # Deduplicate by accession (keep first occurrence)
    seen = set()
    unique = []
    for row in rows:
        acc = row["accession"]
        if acc and acc not in seen:
            seen.add(acc)
            unique.append(row)

    return {"accession": accession, "xref": unique}


@api_app.get(
    "/project/{accession}/cite",
    summary="Get BibTeX citation for a project",
    description=(
        "Return BibTeX citation(s) for a project accession. "
        "By default returns the original publication (smallest PMID). "
        "Use type=all to get BibTeX for every linked publication."
    ),
    operation_id="get_project_cite",
)
@limiter.limit("100/minute")
def get_project_cite(
    request: Request,
    accession: str = Path(...),
    type: str = Query("original", pattern="^(original|all)$"),
    format: str = Query("json", pattern="^(json|bibtex)$"),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                pubs = _project_publications(accession, cur)
    except Exception:
        logger.exception("Database error in get_project_cite")
        raise HTTPException(500, "Internal server error")

    if not pubs:
        raise HTTPException(404, f"No publications found for {accession}")

    # Sort by PMID ascending (smallest = earliest/original)
    pubs_sorted = sorted(pubs, key=lambda p: int(p.get("pmid") or 0))

    if type == "original":
        pubs_sorted = pubs_sorted[:1]

    citations = []
    for pub in pubs_sorted:
        citations.append(
            {
                "pmid": pub.get("pmid"),
                "title": pub.get("title"),
                "authors": pub.get("authors"),
                "journal": pub.get("journal"),
                "doi": pub.get("doi"),
                "pub_date": pub.get("pub_date"),
                "citation_count": pub.get("citation_count"),
                "bibtex": _pub_to_bibtex(pub, accession),
            }
        )

    if format == "bibtex":
        bibtex_text = "\n\n".join(c["bibtex"] for c in citations)
        return PlainTextResponse(bibtex_text, media_type="text/plain")

    return {"accession": accession, "citations": citations}


@api_app.get(
    "/sitemap/counts",
    summary="Sitemap accession counts",
    description="Return total accession counts per source for sitemap generation.",
    operation_id="sitemap_counts",
)
def sitemap_counts(request: Request):
    with _sitemap_counts_cache_lock:
        if (
            _sitemap_counts_cache["ready"]
            and (time.time() - _sitemap_counts_cache["built_at"]) < _GROWTH_CACHE_TTL
        ):
            return _sitemap_counts_cache["data"]
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SITEMAP_COUNTS_SQL)
                row = cur.fetchone()
                # Exact page counts where bounds are built; estimate otherwise.
                cur.execute(SITEMAP_BOUNDS_COUNTS_SQL, (SITEMAP_PAGE_SIZE,))
                bounds = {r["source"]: r["count"] for r in cur.fetchall()}
    except Exception:
        logger.exception("Database error in sitemap_counts")
        raise HTTPException(500, "Internal server error")
    counts = {k: bounds.get(k, v) for k, v in row.items()}
    data = {**counts, "total": sum(counts.values())}
    with _sitemap_counts_cache_lock:
        _sitemap_counts_cache.update(ready=True, data=data, built_at=time.time())
    return data


@api_app.get(
    "/sitemap/accessions",
    summary="Paginated sitemap accessions",
    description="Return a paginated list of accessions from a single source for sitemap generation.",
    operation_id="sitemap_accessions",
)
def sitemap_accessions(
    request: Request,
    source: str = Query(..., pattern=f"^({'|'.join(SITEMAP_ACCESSIONS_SQL)})$"),
    page: int = Query(0, ge=0),
    limit: int = Query(50000, ge=1, le=50000),
):
    if source not in SITEMAP_KEYSET_SQL:
        raise HTTPException(400, f"Unknown source: {source}")
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SITEMAP_BOUND_LOOKUP_SQL, (source, page))
                bound = cur.fetchone()
                if bound is not None:
                    cur.execute(
                        SITEMAP_KEYSET_SQL[source], (bound["start_accession"], limit)
                    )
                    rows = cur.fetchall()
                else:
                    cur.execute(SITEMAP_SOURCE_BUILT_SQL, (source,))
                    if cur.fetchone() is not None:
                        rows = []  # past the last page
                    else:
                        # bounds not built yet OFFSET fallback
                        cur.execute(
                            SITEMAP_ACCESSIONS_SQL[source], (limit, page * limit)
                        )
                        rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in sitemap_accessions")
        raise HTTPException(500, "Internal server error")
    return {"accessions": [r["accession"] for r in rows], "page": page, "limit": limit}


def _get_source_totals() -> dict:
    with _source_totals_cache_lock:
        if (
            _source_totals_cache["ready"]
            and (time.time() - _source_totals_cache["built_at"]) < _GROWTH_CACHE_TTL
        ):
            return _source_totals_cache["data"]

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SOURCE_TOTALS_SQL)
            row = cur.fetchone()

    data = {
        source: {
            "projects": row[f"{source}_projects"],
            "samples": row[f"{source}_samples"],
        }
        for source in _STATS_SOURCES
    }
    with _source_totals_cache_lock:
        _source_totals_cache.update(ready=True, data=data, built_at=time.time())
    return data


def _get_last_updated() -> dict:
    with _last_updated_cache_lock:
        if (
            _last_updated_cache["ready"]
            and (time.time() - _last_updated_cache["built_at"]) < _GROWTH_CACHE_TTL
        ):
            return _last_updated_cache["data"]

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(LAST_UPDATED_SQL)
            row = cur.fetchone()

    raw = {source: row[source] for source in _STATS_SOURCES}
    overall = max((v for v in raw.values() if v), default=None)
    data = {
        "by_source": {s: (v.isoformat() if v else None) for s, v in raw.items()},
        "overall": overall.isoformat() if overall else None,
    }
    with _last_updated_cache_lock:
        _last_updated_cache.update(ready=True, data=data, built_at=time.time())
    return data


@api_app.get(
    "/stats/source-totals",
    summary="Project and sample counts per source",
    description=(
        "Return total project and sample counts for GEO, SRA, ArrayExpress, and "
        "ENA. Cached for 7 days. Returns {geo: {projects, samples}, sra: {...}, ...}."
    ),
    operation_id="get_source_totals",
)
@limiter.limit("60/minute")
def get_source_totals(request: Request):
    try:
        return _get_source_totals()
    except Exception:
        logger.exception("Database error in get_source_totals")
        raise HTTPException(500, "Internal server error")


@api_app.get(
    "/stats/last-updated",
    summary="Most recent record date in the index",
    description=(
        "Return the date of the newest record in the index. With ?db=<source> "
        "(geo|sra|arrayexpress|ena|gsa|dra|gea) returns that source's latest date; "
        "without it, "
        "the overall maximum. Cached for 7 days. Returns {db, last_updated, by_source}."
    ),
    operation_id="get_last_updated",
)
@limiter.limit("60/minute")
def get_last_updated(
    request: Request,
    db: str | None = Query(None, pattern="^(geo|sra|arrayexpress|ena|gsa|dra|gea)$"),
):
    try:
        data = _get_last_updated()
    except Exception:
        logger.exception("Database error in get_last_updated")
        raise HTTPException(500, "Internal server error")

    if db:
        return {"db": db, "last_updated": data["by_source"].get(db)}
    return {
        "db": None,
        "last_updated": data["overall"],
        "by_source": data["by_source"],
    }


@api_app.get(
    "/stats/enriched",
    summary="Enriched metadata coverage and per-column stats",
    description=(
        "Return how many projects and samples have AI-enriched ontology metadata, "
        "plus per-column fill count and distinct-value count. Reads the "
        "enriched_summary materialized view."
    ),
    operation_id="get_enriched_stats",
)
@limiter.limit("60/minute")
def get_enriched_stats(request: Request):
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(ENRICHED_SUMMARY_SQL)
                row = cur.fetchone()
    except Exception:
        logger.exception("Database error in get_enriched_stats")
        raise HTTPException(500, "Internal server error")

    if not row:
        raise HTTPException(503, "Enriched stats not available")

    return {
        "enriched_projects": row.get("enriched_projects"),
        "enriched_samples": row.get("enriched_samples"),
        "columns": [
            {
                "name": c,
                "filled": row.get(f"{c}_filled"),
                "distinct": row.get(f"{c}_distinct"),
            }
            for c in ENRICHED_STAT_COLUMNS
        ],
    }


def _get_enriched_crosstab(group: str, breakdown: str) -> dict:
    key = (group, breakdown)
    with _enriched_crosstab_cache_lock:
        entry = _enriched_crosstab_cache.get(key)
        if entry and (time.time() - entry["built_at"]) < _GROWTH_CACHE_TTL:
            return entry["data"]

    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(ENRICHED_CROSSTAB_SQL, (group, breakdown))
            rows = cur.fetchall()

    groups: dict = {}
    for r in rows:
        g = groups.get(r["group_val"])
        if g is None:
            g = {
                "value": r["group_val"],
                "total": r["group_total"],
                "distinct": r["group_distinct"],
                "breakdowns": [],
            }
            groups[r["group_val"]] = g
        g["breakdowns"].append({"value": r["breakdown_val"], "count": r["n"]})

    data = {"group": group, "breakdown": breakdown, "groups": list(groups.values())}
    with _enriched_crosstab_cache_lock:
        if (
            len(_enriched_crosstab_cache) >= _MAX_CROSSTAB_CACHE_ENTRIES
            and key not in _enriched_crosstab_cache
        ):
            oldest = min(
                _enriched_crosstab_cache,
                key=lambda k: _enriched_crosstab_cache[k]["built_at"],
            )
            del _enriched_crosstab_cache[oldest]
        _enriched_crosstab_cache[key] = {"data": data, "built_at": time.time()}
    return data


@api_app.get(
    "/stats/enriched/crosstab",
    summary="Crosstab of one enriched column broken down by another",
    description=(
        "Return counts of one standardised column (breakdown) within each value "
        "of another (group) — e.g. group=organism&breakdown=tissue gives the "
        "tissues seen per organism, with a distinct-tissue count per organism. "
        "Both axes must be in the curated crosstab column set. Reads the "
        "enriched_crosstab materialized view."
    ),
    operation_id="get_enriched_crosstab",
)
@limiter.limit("60/minute")
def get_enriched_crosstab(
    request: Request,
    group: str = Query(..., pattern="^[a-z_]+$"),
    breakdown: str = Query(..., pattern="^[a-z_]+$"),
):
    if group not in ENRICHED_CROSSTAB_COLUMNS:
        raise HTTPException(404, f"Unknown crosstab column: {group}")
    if breakdown not in ENRICHED_CROSSTAB_COLUMNS:
        raise HTTPException(404, f"Unknown crosstab column: {breakdown}")
    if group == breakdown:
        raise HTTPException(400, "group and breakdown must differ")
    try:
        return _get_enriched_crosstab(group, breakdown)
    except Exception:
        logger.exception("Database error in get_enriched_crosstab")
        raise HTTPException(500, "Internal server error")


@api_app.get(
    "/stats/enriched/{column}",
    summary="Top values for an enriched column",
    description=(
        "Return the most common values (with counts) for one enriched column "
        "(e.g. tissue, cell_type, disease, assay). Reads the "
        "enriched_column_top_values materialized view."
    ),
    operation_id="get_enriched_column",
)
@limiter.limit("60/minute")
def get_enriched_column(
    request: Request,
    column: str = Path(..., pattern="^[a-z_]+$"),
    limit: int = Query(50, ge=1, le=ENRICHED_TOP_VALUES_PER_COLUMN),
):
    if column not in ENRICHED_STAT_COLUMNS:
        raise HTTPException(404, f"Unknown enriched column: {column}")
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(ENRICHED_COLUMN_VALUES_SQL, (column, limit))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_enriched_column")
        raise HTTPException(500, "Internal server error")

    return {
        "column": column,
        "values": [{"value": r["value"], "count": r["n"]} for r in rows],
    }


@api_app.get(
    "/project/{accession}/enriched",
    summary="Get ontology-enriched sample metadata",
    description=(
        "Return structured metadata extracted by LLM with ontology mappings. "
        "Fields include tissue, cell_type, disease, assay, development_stage "
        "each with standardised ontology IDs (UBERON, CL, MONDO, EFO). "
        "Falls back to legacy enriched_samples if v3 data is unavailable. "
        "Use alongside metadata/rows when raw run/sample fields or FASTQ links "
        "are also needed."
    ),
    operation_id="get_project_enriched",
)
@limiter.limit("100/minute")
def get_project_enriched(
    request: Request,
    response: Response,
    accession: str = Path(..., pattern=PROJECT_REGEX),
    human_readable_age: bool = Query(
        True, description="Convert age from hours to human-readable format"
    ),
    limit: int | None = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    accession = accession.upper()
    response.headers["Cache-Control"] = _PROJECT_CACHE_CONTROL
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                result = _fetch_project_enriched(
                    cur, accession, human_readable_age, limit=limit, offset=offset
                )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_project_enriched")
        raise HTTPException(500, "Internal server error")
    return result


def _resolve_prj(cur, accession: str) -> dict:
    """PRJ* -> {"gse", "srp"}. PRJNA checks geo_series first; all check sra_studies."""
    gse = None
    if accession.upper().startswith("PRJNA"):
        cur.execute(PRJNA_TO_GEO_SERIES_SQL, (accession,))
        row = cur.fetchone()
        gse = row["accession"] if row else None
    cur.execute(PRJ_TO_SRA_STUDY_SQL, (accession,))
    row = cur.fetchone()
    return {"gse": gse, "srp": row["accession"] if row else None}


def _resolve_enrichment_accession(cur, accession: str) -> str:
    """Map a PRJ* to the accession enrichment is keyed under (GSE or SRP).

    Enrichment keys on the GEO series / SRA study, not the bare PRJ, so a raw PRJ*
    404s even when the study is enriched. Unchanged for non-PRJ / unmapped input.
    """
    if not accession.upper().startswith("PRJ"):
        return accession
    r = _resolve_prj(cur, accession)
    return r["gse"] or r["srp"] or accession


def _exec_enriched_samples(cur, sql, accession, limit, offset) -> list[dict]:
    if limit is None:
        cur.execute(sql, (accession,))
    else:
        cur.execute(
            sql.rstrip().rstrip(";") + "\nLIMIT %s OFFSET %s",
            (accession, limit, offset),
        )
    return cur.fetchall()


def _fetch_sample_titles(cur, sample_accs: list[str]) -> dict:
    """Map enriched sample accessions to (title, description). GEO samples live in
    geo_samples, SRA/ENA in sra_samples; the namespaces are disjoint so a union
    covers both without resolving the study's archive first."""
    if not sample_accs:
        return {}
    cur.execute(
        "SELECT accession, title, description FROM geo_samples WHERE accession = ANY(%s) "
        "UNION ALL "
        "SELECT accession, title, description FROM sra_samples WHERE accession = ANY(%s)",
        (sample_accs, sample_accs),
    )
    return {r["accession"]: (r["title"], r["description"]) for r in cur.fetchall()}


def _fetch_project_enriched(
    cur,
    accession: str,
    human_readable_age: bool,
    sample_filter: set[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Fetch ontology-enriched study + sample metadata for a project accession.

    If sample_filter is given, only samples whose `sample` is in the set are
    returned (used by the experiment/run-scoped endpoints); pagination is
    ignored in that case since those callers need the full set to filter.
    """
    page_limit = None if sample_filter is not None else limit

    # PRJ* accessions are keyed under their GSE/SRP in the enrichment tables.
    accession = _resolve_enrichment_accession(cur, accession)

    cur.execute(ENRICHED_STUDY_V3_SQL, (accession,))
    study = cur.fetchone()
    if study:
        # Pick the source once (per-study, offset-independent) so paginated pages
        # stay on the same table instead of an empty page tripping the fallback.
        cur.execute(
            "SELECT 1 FROM ontology_samples_v4 WHERE study_accession = %s LIMIT 1",
            (accession,),
        )
        sql = ONTOLOGY_SAMPLES_V3_SQL if cur.fetchone() else ENRICHED_SAMPLES_V3_SQL
        samples = _exec_enriched_samples(cur, sql, accession, page_limit, offset)
        version = "v3"
    else:
        cur.execute(ENRICHED_STUDY_SQL, (accession,))
        study = cur.fetchone()
        if not study:
            raise HTTPException(404, f"No enriched metadata for {accession}")
        samples = _exec_enriched_samples(
            cur, ENRICHED_SAMPLES_SQL, accession, page_limit, offset
        )
        version = "v1"

    if sample_filter is not None:
        samples = [s for s in samples if s.get("sample") in sample_filter]

    # Scope low-confidence tags to the rows we're returning so a 100k-sample
    # study doesn't pull its whole low_confidence table on every page.
    sample_accs = [s.get("sample") for s in samples if s.get("sample")]
    if sample_accs:
        cur.execute(
            "SELECT sample_accession, tags FROM low_confidence "
            "WHERE study_accession = %s AND sample_accession = ANY(%s)",
            (accession, sample_accs),
        )
        low_confidence = {r["sample_accession"]: r["tags"] for r in cur.fetchall()}
    else:
        low_confidence = {}

    for s in samples:
        s["low_confidence_tags"] = low_confidence.get(s.get("sample"), [])

    titles = _fetch_sample_titles(cur, sample_accs)
    for s in samples:
        td = titles.get(s.get("sample"))
        s["title"] = td[0] if td else None
        s["description"] = td[1] if td else None

    if human_readable_age:
        for s in samples:
            s["age"] = _age_hours_to_human(s.get("age"))

    return {
        "accession": study["accession"],
        "title": study["title"],
        "n_samples": study["n_samples"],
        "single_cell_modality": study.get("single_cell_modality"),
        "version": version,
        "samples": samples,
    }


@api_app.get(
    "/experiment/{accession}/enriched",
    summary="Get ontology-enriched metadata for an experiment's sample(s)",
    description=(
        "Return the ontology-enriched sample record(s) for a single SRA/ENA "
        "experiment (SRX/ERX/DRX), scoped from the parent study's enriched metadata. "
        "Same fields as /project/{accession}/enriched."
    ),
    operation_id="get_experiment_enriched",
)
@limiter.limit("100/minute")
def get_experiment_enriched(
    request: Request,
    accession: str = Path(..., pattern=EXPERIMENT_REGEX),
    human_readable_age: bool = Query(
        True, description="Convert age from hours to human-readable format"
    ),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                ctx = _build_exp_context(cur, accession)
                if ctx is None:
                    raise HTTPException(
                        404, f"No experiment found for accession {accession}"
                    )
                study = ctx["study"]
                samples = set(ctx["sample_map"].keys())
                if not study or not samples:
                    raise HTTPException(404, f"No enriched metadata for {accession}")
                return _fetch_project_enriched(
                    cur, study, human_readable_age, sample_filter=samples
                )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_experiment_enriched")
        raise HTTPException(500, "Internal server error")


@api_app.get(
    "/run/{run}/enriched",
    summary="Get ontology-enriched metadata for a run's sample",
    description=(
        "Return the ontology-enriched sample record for a single run accession "
        "(SRR/ERR/DRR), resolved via run → experiment → sample. "
        "Same fields as /project/{accession}/enriched."
    ),
    operation_id="get_run_enriched",
)
@limiter.limit("100/minute")
def get_run_enriched(
    request: Request,
    run: str = Path(..., pattern=RUN_REGEX),
    human_readable_age: bool = Query(
        True, description="Convert age from hours to human-readable format"
    ),
):
    run = run.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(RUN_DOWNLOAD_SQL, (run,))
                run_row = cur.fetchone()
                if not run_row:
                    raise HTTPException(404, f"No run found for accession {run}")
                exp_acc = (run_row.get("experiment_accession") or "").upper()
                ctx = _build_exp_context(cur, exp_acc) if exp_acc else None
                if not ctx or not ctx["study"] or not ctx["sample_map"]:
                    raise HTTPException(404, f"No enriched metadata for {run}")
                return _fetch_project_enriched(
                    cur,
                    ctx["study"],
                    human_readable_age,
                    sample_filter=set(ctx["sample_map"].keys()),
                )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_run_enriched")
        raise HTTPException(500, "Internal server error")


@api_app.get(
    "/project/{accession}/similar",
    summary="Find similar studies",
    description=(
        "Return the nearest studies to a project based on the precomputed 2D "
        "embedding (geo_series.neighbors / sra_studies.neighbors). Each result "
        "includes accession, distance, title, organisms, and source. Useful for "
        "'find studies like X' / 'what else is similar to this dataset'."
    ),
    operation_id="get_project_similar",
)
@limiter.limit("1000/minute")
def get_project_similar(
    request: Request,
    accession: str = Path(..., pattern=rf"^(GSE|{_STUDY_PREFIX})\d+$"),
    limit: int = Query(default=10, ge=1, le=100),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SIMILAR_STUDIES_SQL, {"acc": accession, "limit": limit})
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_project_similar")
        raise HTTPException(500, "Internal server error")
    if not rows:
        raise HTTPException(404, f"No precomputed neighbors for {accession}")
    return {"accession": accession, "total": len(rows), "similar": rows}


def _list_filter(sql: str, params: tuple = ()) -> list[dict]:
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()


_filter_list_cache_lock = threading.Lock()
_filter_list_cache: dict[str, dict] = {}


def _cached_list_filter(sql: str) -> list[dict]:
    with _filter_list_cache_lock:
        entry = _filter_list_cache.get(sql)
        if entry and (time.time() - entry["built_at"]) < _GROWTH_CACHE_TTL:
            return entry["data"]
    rows = _list_filter(sql)
    with _filter_list_cache_lock:
        _filter_list_cache[sql] = {"data": rows, "built_at": time.time()}
    return rows


@api_app.get(
    "/filters/library-strategies",
    summary="Enumerate library strategies",
    description=(
        "Return every library_strategy seen in sra_experiments with a count, "
        "sorted by frequency. Use to populate filter dropdowns or to discover "
        "valid values for search_structured(library_strategy=...)."
    ),
    operation_id="list_library_strategies",
)
@limiter.limit("100/minute")
def list_library_strategies(request: Request):
    try:
        rows = _cached_list_filter(LIST_LIBRARY_STRATEGIES_SQL)
    except Exception:
        logger.exception("Database error in list_library_strategies")
        raise HTTPException(500, "Internal server error")
    return {"total": len(rows), "values": rows}


@api_app.get(
    "/filters/instrument-models",
    summary="Enumerate instrument models",
    description=(
        "Return every instrument_model seen in sra_experiments with a count, "
        "sorted by frequency. Use to discover valid values for "
        "search_structured(instrument_model=...)."
    ),
    operation_id="list_instrument_models",
)
@limiter.limit("100/minute")
def list_instrument_models(request: Request):
    try:
        rows = _cached_list_filter(LIST_INSTRUMENT_MODELS_SQL)
    except Exception:
        logger.exception("Database error in list_instrument_models")
        raise HTTPException(500, "Internal server error")
    return {"total": len(rows), "values": rows}


@api_app.get(
    "/filters/journals",
    summary="Enumerate journals",
    description=(
        "Return the top journals appearing in linked publications, sorted by "
        "frequency. Use to discover valid values for search_structured(journal=...)."
    ),
    operation_id="list_journals",
)
@limiter.limit("100/minute")
def list_journals(
    request: Request,
    limit: int = Query(default=500, ge=1, le=5000),
):
    try:
        rows = _list_filter(LIST_JOURNALS_SQL, (limit,))
    except Exception:
        logger.exception("Database error in list_journals")
        raise HTTPException(500, "Internal server error")
    return {"total": len(rows), "values": rows}


@api_app.get(
    "/filters/centers",
    summary="Enumerate submission centers",
    description=(
        "Return the top submission centers from sra_submissions + ena_studies, "
        "sorted by frequency. Use to discover values for search_structured(center=...)."
    ),
    operation_id="list_centers",
)
@limiter.limit("100/minute")
def list_centers(
    request: Request,
    limit: int = Query(default=500, ge=1, le=5000),
):
    try:
        rows = _list_filter(LIST_CENTERS_SQL, (limit,))
    except Exception:
        logger.exception("Database error in list_centers")
        raise HTTPException(500, "Internal server error")
    return {"total": len(rows), "values": rows}


@api_app.get(
    "/sample/{accession}/experiments",
    summary="List experiments referencing a sample",
    description=(
        "Return the SRA/ENA experiment accession(s) that reference a sample "
        "(SRS/ERS/DRS/SAM*). Inverse of /experiment/{accession}'s samples list."
    ),
    operation_id="get_sample_experiments",
)
@limiter.limit("1000/minute")
def get_sample_experiments(
    request: Request,
    accession: str = Path(..., pattern=SAMPLE_OR_ENA_REGEX),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SRA_SAMPLE_TO_EXPERIMENT_SQL, (accession,))
                row = cur.fetchone()
                source = "sra"
                if not row:
                    cur.execute(ENA_SAMPLE_TO_EXPERIMENT_SQL, (accession,))
                    row = cur.fetchone()
                    source = "ena"
                if not row:
                    cur.execute(GSA_SAMPLE_TO_EXPERIMENT_SQL, (accession,))
                    row = cur.fetchone()
                    source = "gsa"
    except Exception:
        logger.exception("Database error in get_sample_experiments")
        raise HTTPException(500, "Internal server error")
    if not row:
        raise HTTPException(404, f"No experiment found for sample {accession}")
    experiment = row.get("accession") or row.get("experiment_accession")
    return {
        "sample_accession": accession,
        "experiment_accession": experiment,
        "source": source,
    }


def _age_hours_to_human(value: str | None) -> str | None:
    """Convert age in hours (float string) to a human-friendly string.

    Examples: 8760.0 -> "1 year", 720.0 -> "30 days", 48.0 -> "2 days",
              0.5 -> "0.5 hours", 168.0 -> "1 week"
    """
    if not value:
        return value
    try:
        hours = float(value)
    except (ValueError, TypeError):
        return value  # not a number, return as-is (e.g. already formatted)

    if hours < 0:
        return value

    # < 1 day: show hours
    if hours < 24:
        h = round(hours, 1)
        return f"{h:g} hour{'s' if h != 1 else ''}"

    days = hours / 24

    # < 14 days: show days
    if days < 14:
        d = round(days, 1)
        return f"{d:g} day{'s' if d != 1 else ''}"

    # < 60 days: show weeks
    if days < 60:
        weeks = round(days / 7, 1)
        return f"{weeks:g} week{'s' if weeks != 1 else ''}"

    # < 730 days (~2 years): show months
    if days < 730:
        months = round(days / 30, 1)
        return f"{months:g} month{'s' if months != 1 else ''}"

    # >= 2 years: show years
    years = round(days / 365, 1)
    return f"{years:g} year{'s' if years != 1 else ''}"


MCP_INSTRUCTIONS = """\
You are connected to seqout, a genomics study search engine covering NCBI
GEO, SRA/ENA/DDBJ, ArrayExpress, DDBJ GEA, GSA (CNCB-NGDC, including
GSA-Human), BioProject, publication links, download links, and
ontology-enriched sample metadata.

Your job is to choose the narrowest correct tool, preserve context, and avoid
making users re-state accessions or filters. Do not guess when an accession can
be classified or resolved by a tool.

## DDBJ accessions

DRP/DRX/DRS/DRR are DDBJ DRA and behave exactly like their SRA counterparts --
use the same project/experiment/sample/run tools. Most DDBJ studies are mirrored
into SRA/ENA; the ones that are not still resolve, and their FASTQ links point at
ddbj.nig.ac.jp rather than ENA.

E-GEAD-N is DDBJ GEA, DDBJ's expression archive. Despite the leading "E-" it is
NOT ArrayExpress -- never route it to an ArrayExpress tool. Treat it like a GEO
series: `get_project` for metadata, `get_geo_series_samples` for its sample list.
GEA holds no reads of its own, so its FASTQs come from the linked DRA study, which
`get_project` returns in `alias` (the same way a GSE links to its SRP). Sequencing
experiments have that link; microarray ones legitimately have no FASTQ.

## Critical Routing Rules

1. Reuse existing results before searching.
   Before calling `search_all`, `search_structured`, or a source-specific search,
   ask: "Do I already have search results in this conversation that can answer
   the current request?" If yes, filter/sort/summarize those rows locally.
   Do not search again just to narrow by keyword, organism, source, year, country,
   title, summary, organisms, updated_at, or publication fields already in hand.

2. Paginate instead of starting over.
   Search tools return up to 200 rows, `total`, and `next_cursor`.
   - Default relevance pagination: call the same search with the same `q` plus
     `cursor_rank=next_cursor.rank` and `cursor_acc=next_cursor.accession`.
   - `sortby` pagination: call the same search with the same `q`, `sortby`, and
     `order`, plus `cursor_sort=next_cursor.sort_value` and `cursor_acc`.
   Changing `q` is a new search. Changing only cursors is continuation.

3. Classify unknown accessions first.
   Use `classify_accession` when the user gives an accession and the right tool is
   unclear. It is pattern-only and cheap. Use `resolve_accession_project` for
   GSM/SRS/ERS/DRS/SRX/ERX/DRX child-to-project lookup. Use `resolve_prj_project`
   for PRJNA/PRJEB/PRJDA before calling study-only download tools.

4. Prefer structured JSON tools for MCP.
   Use JSON tools (`get_project_metadata_rows`, `get_study_download_manifest`,
   `get_run_checksums`, `get_project_enriched`) when the agent must inspect or
   transform data. Use script/plain-text tools only when the user explicitly asks
   for a ready-to-run script or accession list.

## Accession Routing

- `GSE...` GEO series: `get_project`, `get_geo_series_samples`,
  `get_project_metadata_rows`, `get_supplementary_files`,
  `download_supplementary_script`, `get_project_enriched` if enriched data exists.
- `GSM...` GEO sample: `get_sample_detail`; use `resolve_accession_project` to find
  the parent GSE.
- `SRP/ERP/DRP...` study: `get_project`, `get_study_experiments`,
  `get_study_samples`, `get_study_runs`, `get_study_download_manifest`,
  `download_study_script`, `get_project_metadata_rows`, `get_project_enriched`.
- `SRX/ERX/DRX...` experiment: `get_experiment`, `get_experiment_runs`,
  `get_experiment_metadata_rows`, `get_experiment_enriched`,
  `resolve_accession_project`.
- `SRR/ERR/DRR...` run: `get_run_checksums` for parsed FASTQ/SRA files,
  `get_run` for raw run fields, `get_run_metadata_rows` for the joined metadata row,
  `get_run_enriched` for ontology-enriched sample context.
- `SRS/ERS/DRS...` or `SAM...` sample/BioSample: `get_sample_detail` for parent
  project, experiment, sample attributes, and runs; `get_sample` only for raw
  SRA/ENA sample metadata; `get_sample_experiments` for reverse sample->experiment.
- `E-XXXX-NNN` ArrayExpress experiment: `get_project`, `get_ae_experiment_samples`,
  `get_project_metadata_rows`, `get_supplementary_files`,
  `download_supplementary_script`.
- `PRJ...` BioProject: `get_project` can return ENA PRJ project metadata, but FASTQ
  download tools require a resolved SRP/ERP/DRP study accession. Call
  `resolve_prj_project` first when the user asks for FASTQs/downloads from a PRJ.
  `PRJCA...` are GSA projects and resolve to CRA/HRA studies.
- GSA (CNCB-NGDC) accessions: `CRA/HRA...` study, `CRX/HRX...` experiment,
  `CRR/HRR...` run, `HRS...` sample, `PRJCA...` project (`SAMC...` BioSample).
  `HR*` are GSA-Human (controlled-access). Use `get_project`,
  `get_study_experiments`, `get_study_samples`, and `get_study_runs` on a CRA/HRA
  study just like an SRA study; `get_sample_detail` on HRS/CRX/CRR. GSA rows carry
  raw run/download links only where the archive publishes them — GSA-Human data is
  typically access-controlled, so download links may be absent.

## Search And Cohort Discovery

Use `search_all` for ordinary keyword search across all sources. Pass
`db=geo|sra|arrayexpress|ena|gsa|dra|gea` only when the user restricts the source. Use
`sortby=citations|journal|year` with `order=desc|asc` when the user asks for
most cited, journal-sorted, newest, or oldest results. Results are automatically
enriched with publication and center fields when available: `publications`,
`pmid`, `publication_title`, `journal`, `doi`, `authors`, `citation_count`, and
`center_name`.

Use `search_suggest` only when a query returns no or very few results and appears
misspelled. If suggestions are returned inside `search_all`, present or use them
instead of guessing.

Use `search_structured` for explicit metadata filters or cohort questions. It can
combine `q` with:
- `organism`: exact scientific name, such as `Homo sapiens`.
- `library_strategy`: RNA-Seq, ChIP-Seq, WGS, ATAC-seq, etc.
- `platform`: platform code such as ILLUMINA, OXFORD_NANOPORE, PACBIO_SMRT.
- `instrument_model`: exact instrument model, such as Illumina NovaSeq 6000.
- `source=geo|sra|arrayexpress|ena|gsa|all` to restrict the archive.
- `country`, `center`, `journal`, `year_from`, `year_to`.
- `published_after` and `published_before` as YYYY-MM-DD for exact windows.
- `assay_l1`, `assay_l2`, `geo_country`, `geo_country_code`,
  `geo_country_code_iso2`, `geo_city`, `geo_state`, `geo_district`,
  `geo_postcode`, and `geo_lat`/`geo_lng` with optional `geo_radius_km`.
- `multi_platform=true` for studies with 2+ sequencing platforms.
- `sample_tissue`, `sample_disease`, `sample_cell_type` for ontology-enriched
  sample filters. These only find studies that have enriched v3 sample metadata.
- `count_by=year|source|organism|country` for bucket counts instead of rows.

Structured-search caveats:
- `platform` excludes GEO and ArrayExpress.
- `instrument_model` excludes ArrayExpress, ENA, and GSA.
- `country` excludes ArrayExpress.
- `multi_platform` is GEO/SRA only (excludes ArrayExpress, ENA, GSA).
- `sample_tissue`, `sample_disease`, and `sample_cell_type` prove at least one
  enriched sample matched; call `get_project_enriched` to inspect which sample(s).

Before choosing filter values, use `list_library_strategies`,
`list_instrument_models`, `list_journals`, and `list_centers`.
Use `search_organisms` for organism autocomplete and `get_common_name_by_scientific_name`
for common-name display.

## Project Drilldown

Use `get_project` for full project/study/series metadata after a search result or
known accession. Use `get_project_metadata` only for a fast title + description.
Use `get_project_xref` to find cross-archive equivalents, including GEO<->SRA,
BioProject, ENA, and E-GEOD/GSE naming links. Use `get_project_similar` for
"studies like this" on GSE or SRP accessions.

Use sample-list tools when the user wants the child records:
- GEO: `get_geo_series_samples`.
- ArrayExpress: `get_ae_experiment_samples`.
- SRA/ENA study: `get_study_experiments` and `get_study_samples`.
- SRA/ENA experiment: `get_experiment` for metadata plus runs, or
  `get_experiment_runs` for just runs.
- SRA/ENA sample/experiment/GSM with parent context: `get_sample_detail`.

## FASTQ, SRA, BAM, And Supplementary Retrieval

FASTQ files exist at the run level. A study/project accession is a container for
runs. Do not claim that GEO or ArrayExpress projects have SRA FASTQs unless a
linked SRA/ENA accession is found with `get_project_xref` or `resolve_prj_project`.
For GEO/ArrayExpress processed files, use supplementary-file tools instead.

Preferred FASTQ workflow:
1. If the user gives PRJ, GSM, SRS/ERS/DRS, SRX/ERX/DRX, or GSE, resolve to the
   relevant SRP/ERP/DRP or run/experiment first.
2. For a whole study, call `get_study_download_manifest(study, mode="fastq")`.
   This is complete, paginated, per-file JSON. Each file has `url`, `path`,
   `filename`, `bytes`, `md5`, `run_accession`, `experiment_accession`, and
   `download_command`. Continue with `next_cursor` until enough files are seen.
3. For a quick study overview, call `get_study_runs`. It returns `total_runs`,
   `paired_runs`, `single_runs`, `total_fastq_bytes`, and a preview of up to 500
   runs. Its `fastq_ftp`, `fastq_bytes`, and `fastq_md5` values are semicolon-joined
   for paired-end runs.
4. Interleaved paired FASTQ rule: if `library_layout=PAIRED` and
   `fastq_file_count=1` or `fastq_is_interleaved=true`, do not treat that single
   ENA FASTQ as a complete R1/R2 pair. Use SRA normalized format first, or SRA Lite
   if normalized SRA is unavailable, then fetch/convert with `sracha get --split split-files`.
   `get_study_download_manifest(mode="fastq")` and `download_study_script(...,
   mode="fastq")` do this automatically when `fastq_conversion_available=true`.
5. For one run, prefer `get_run_checksums`. It returns parsed `fastq` file records,
   optional `sra` file record, and interleaved/conversion flags. Use `get_run` only
   when raw run-level fields are needed.
6. For one experiment, use `get_experiment_runs` or `get_experiment_metadata_rows`.
7. For a ready-to-run script, use `download_study_script(study, mode)`. Modes are
   `fastq`, `sra`, `sra_lite`, `s3`, and `gcs`. Default/recommended raw FASTQ mode
   is `fastq`; `sra_lite` is smaller but lossy for quality scores.

URL/checksum rules:
- In raw run rows, `fastq_ftp` may be a semicolon-separated EBI host/path with no
  scheme. Do not hand-parse unless needed; prefer manifest or checksum tools.
- `get_study_download_manifest` and `get_run_checksums` already provide per-file
  URL/bytes/MD5 fields suitable for download and verification. For interleaved
  paired runs, look at `fastq_conversion_required`, `fastq_conversion_available`,
  `recommended_fastq_source`, `source_mode`, `conversion_command`, and
  `expected_fastq_paths`.
- If a user asks for "MD5", "checksums", "file sizes", or "verify downloads",
  use `get_run_checksums` for a run or `get_study_download_manifest` for a study.

Other retrieval tools:
- `get_study_bams` lists original submitted BAMs for an SRA/ENA study when present.
- `get_supplementary_files` lists GEO/ArrayExpress supplementary or processed files
  as JSON; `download_supplementary_script` returns a curl script.
- Web UI pages such as `https://seqout.org/p/{accession}` have interactive download
  sections, but use MCP tools first when answering programmatically.

## Metadata Rows And Enrichment

There are three different metadata layers. Keep them distinct:

1. Project metadata: `get_project` and search results. These include study-level
   title/abstract/organisms and publication/center enrichment when available.
2. Raw flattened row metadata: `get_project_metadata_rows`,
   `get_experiment_metadata_rows`, and `get_run_metadata_rows`. SRA/ENA rows are
   one row per run with experiment fields, sample fields, dynamic `sample_attribute:*`
   columns, FASTQ/SRA URLs, bytes, and MD5 fields. GEO and ArrayExpress rows are
   flattened sample rows. Use these for "metadata table", "sample sheet",
   "one row per run", or "metadata plus FASTQs".
3. Ontology/AI-enriched metadata: `get_project_enriched`, `get_experiment_enriched`,
   and `get_run_enriched`. These return normalized fields such as tissue, cell_type,
   disease, assay, development_stage, organism/taxid, ontology IDs (UBERON, CL,
   MONDO, EFO), low_confidence_tags, and enrichment version. Use these for "parsed",
   "standardized", "ontology", "tissue/disease/cell type", or "enriched" metadata.

When users need both sample attributes and ontology fields, call both
`get_project_metadata_rows` and `get_project_enriched` and explain that the first is
raw archive metadata while the second is normalized enrichment. Enriched endpoints
may return 404 for projects without enrichment; fall back to raw metadata rows.
For multiple GSE/SRP/ERP/DRP projects, use `bulk_project_samples_metadata` to get
project summaries plus flattened sample rows; for one project, prefer
`get_project_metadata_rows`.

For enriched aggregate exploration:
- `get_enriched_stats`: coverage and per-column fill/distinct counts.
- `get_enriched_column`: call with `column=...` for top values from tissue,
  cell_type, disease, assay, etc.
- `get_enriched_crosstab`: call with `group=...` and `breakdown=...` for cross-tabs
  such as organism by tissue or disease by assay.

## Publications And Citations

Search results and `get_project` are enriched with publication fields from linked
PubMed data when available. Use:
- `get_project_cite` for citation metadata and BibTeX. `type=original` returns the
  earliest/smallest PMID; `type=all` returns all linked publications; `format=bibtex`
  returns plain BibTeX.
- `find_publication` for paper-to-datasets lookup by PMID or DOI.
- `search_author_projects` when the user wants every dataset a person authored:
  it returns project rows (search-card compatible) plus their institutes. Prefer
  this for "datasets/studies by <name>".
- `search_by_author` when the user wants the publications themselves: one row per
  PMID (title, journal, DOI, citations) with the accessions linked to each paper.
Both only find studies linked to a PubMed publication.

## Statistics And Discovery

- `get_source_totals`: project/sample totals by source.
- `get_last_updated`: index freshness overall or by source.
- `get_growth_stats`: monthly projects, experiments, or data volume. In `mode=bases`,
  ENA uses nucleotide bases while SRA uses FASTQ/SRA bytes; optional `db` includes
  `sra_fastq_bytes` and `sra_sra_bytes`.
- `get_organism_growth`: monthly counts/share for one scientific organism name.
- `get_organism_totals`: largest organisms by total experiments, with source split.
- `search_organisms`: organism autocomplete; prefer this over `list_organisms`.
- `get_global_contributions`: geographic aggregation by lat/lng from unified_metadata;
  filters include organism, assay_l1, assay_l2, place_type, address_type.
- `get_global_contribution_filters`: valid filter values for the global map, optional
  country/organism scope.
- `get_country_accessions`: plain newline accession list for a country, optionally
  organism and assay_l2 filtered.
- `get_platform_totals`, `get_platform_growth`, `get_platform_instruments`, and
  `get_platform_filters`: sequencing technology totals, trends, instruments, and
  filter options.

## Tools Not Exposed Or Usually Avoided In MCP

- `bulk_metadata`, `bulk_project_metadata`, and `download_by_query` are binary/bulk
  REST downloads; direct users to REST or web UI when needed.
- `download_study_runs_tsv`, `download_study_bams_tsv`, `download_study_metadata_csv`,
  and `download_sample_detail_csv` stream TSV/CSV files. Use JSON tools instead:
  `get_study_download_manifest`, `get_study_bams`, `get_project_metadata_rows`, and
  `get_sample_detail`.
- `get_clusters`, `get_projects_atlas_v1`, `get_atlas_labels_v1`, `get_map_meta`,
  `get_map_filter`, `get_map_labels`, `get_map_countries`, and `get_map_tile` serve
  visualization assets, not conversational answers.
- `search_facets` and `deep_dive_terms`/`deep_dive_children` power search-page UI
  widgets (sidebar counts, ontology drilldown). For cohort counts use
  `search_structured(count_by=...)` instead.
- `get_geo_series_neighbors_coords` is low-level map/similarity data; use
  `get_project_similar` for conversational similarity.
- `search_geo` and `search_sra` are redundant; use `search_all` with `db`.
- `sitemap_counts` and `sitemap_accessions` are for SEO.
- `purge_cache` is admin-only. `health_check` is REST connectivity only.
- `list_organisms` can return hundreds of thousands of rows; use `search_organisms`
  or `get_organism_totals(limit=...)`.
"""
MCP_OPERATION_IDS = {
    "search_all",
    "search_suggest",
    "search_structured",
    "search_by_author",
    "search_author_projects",
    "get_project",
    "get_project_metadata",
    "get_project_metadata_rows",
    "get_study_experiments",
    "get_study_samples",
    "get_study_runs",
    "get_study_bams",
    "get_study_download_manifest",
    "download_study_script",
    "get_supplementary_files",
    "download_supplementary_script",
    "get_experiment",
    "get_experiment_runs",
    "get_experiment_metadata_rows",
    "get_run",
    "get_run_checksums",
    "get_run_metadata_rows",
    "get_sample",
    "get_sample_detail",
    "get_sample_experiments",
    "get_project_similar",
    "list_library_strategies",
    "list_instrument_models",
    "list_journals",
    "list_centers",
    "get_geo_series_samples",
    "get_ae_experiment_samples",
    "resolve_accession_project",
    "resolve_prj_project",
    "classify_accession",
    "get_project_xref",
    "get_project_cite",
    "find_publication",
    "get_common_name_by_scientific_name",
    "get_source_totals",
    "get_last_updated",
    "get_enriched_stats",
    "get_enriched_crosstab",
    "get_enriched_column",
    "get_growth_stats",
    "get_organism_growth",
    "get_organism_totals",
    "search_organisms",
    "get_global_contributions",
    "get_global_contribution_filters",
    "get_country_accessions",
    "get_project_enriched",
    "get_experiment_enriched",
    "get_run_enriched",
    "get_platform_totals",
    "get_platform_growth",
    "get_platform_instruments",
    "get_platform_filters",
    "bulk_project_samples_metadata",
}

ENRICHMENT_OPERATION_IDS = {
    "get_enriched_stats",
    "get_enriched_crosstab",
    "get_enriched_column",
    "get_project_enriched",
    "get_experiment_enriched",
    "get_run_enriched",
}

if not ENRICHMENT_ENABLED:
    MCP_OPERATION_IDS -= ENRICHMENT_OPERATION_IDS
    api_app.router.routes[:] = [
        route
        for route in api_app.router.routes
        if getattr(route, "operation_id", None) not in ENRICHMENT_OPERATION_IDS
    ]

MCP_INSTRUCTIONS_ACTIVE = MCP_INSTRUCTIONS
if not ENRICHMENT_ENABLED:
    MCP_INSTRUCTIONS_ACTIVE += """

## Disabled

Enriched metadata tools and filters are unavailable.
"""


_ENRICHMENT_DISABLED_PATHS = (
    re.compile(r"^/project/[^/]+/enriched/?$", re.IGNORECASE),
    re.compile(r"^/experiment/[^/]+/enriched/?$", re.IGNORECASE),
    re.compile(r"^/run/[^/]+/enriched/?$", re.IGNORECASE),
    re.compile(r"^/stats/enriched(?:/.*)?$", re.IGNORECASE),
    re.compile(r"^/beacon(?:/.*)?$", re.IGNORECASE),
)


def _request_path_without_root(path: str) -> str:
    if path.startswith("/api/"):
        return path[4:]
    return path


class EnrichmentDisabledMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not ENRICHMENT_ENABLED:
            path = _request_path_without_root(request.url.path)
            if any(pattern.match(path) for pattern in _ENRICHMENT_DISABLED_PATHS):
                return JSONResponse(
                    {"detail": "Not Found"},
                    status_code=404,
                    headers={"Cache-Control": "no-store"},
                )
        return await call_next(request)

mcp_api_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
mcp_api_app.state.limiter = limiter
mcp_api_app.router.routes.extend(
    route
    for route in api_app.router.routes
    if getattr(route, "operation_id", None) in MCP_OPERATION_IDS
)

mcp = FastMCP.from_fastapi(
    app=mcp_api_app,
    name="seqout MCP",
    instructions=MCP_INSTRUCTIONS_ACTIVE,
)
mcp_app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)

if ENRICHMENT_ENABLED:
    import beacon as _beacon  # noqa: E402

    _beacon.pool = pool
    api_app.include_router(_beacon.beacon_router)

combined_app = FastAPI(
    title="seqout API",
    description="Programmatic access to 1M+ GEO, SRA, ENA & ArrayExpress sequencing projects.",
    routes=[*mcp_app.routes, *api_app.routes],
    lifespan=mcp_app.lifespan,
    root_path="/api",
)
combined_app.state.limiter = limiter
combined_app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
combined_app.add_middleware(EnrichmentDisabledMiddleware)
combined_app.add_middleware(AnalyticsMiddleware)
# combined_app.add_middleware(
#    CORSMiddleware,
#    allow_origins=["*"],
#    allow_methods=["GET", "OPTIONS"],
#    allow_headers=["*"],
# )

app = combined_app


def _prewarm_caches() -> None:
    for warm in (
        _ensure_spelling_loaded,
        _get_atlas_payloads,
        _get_global_contributions_cached,
        _get_global_contributions_filters,
    ):
        try:
            warm()
        except Exception:
            logger.exception("cache prewarm failed: %s", warm.__name__)


threading.Thread(target=_prewarm_caches, daemon=True, name="prewarm").start()
