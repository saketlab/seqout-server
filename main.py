import csv
import hashlib
import io
import json
import logging
import os
import re
import struct
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
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastmcp import FastMCP
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from user_agents import parse as parse_ua

from queries import (
    AE_EXPERIMENT_SAMPLES_SQL,
    AE_QUERY_ACCESSIONS_SQL,
    AE_SEARCH_SQL,
    BIO_PROJECT_GEO_SERIES_SQL,
    BULK_PROJECT_METADATA_SQL,
    COMMON_NAME_BY_SCIENTIFIC_NAME_SQL,
    ALL_QUERY_ACCESSIONS_SQL,
    GEO_SAMPLE_TO_SERIES_SQL,
    GEO_SERIES_NEIGHBORS_COORDS_SQL,
    GEO_QUERY_ACCESSIONS_SQL,
    GEO_SEARCH_SQL,
    GEO_SERIES_SAMPLES_SQL,
    PRJ_TO_SRA_STUDY_SQL,
    PRJNA_TO_GEO_SERIES_SQL,
    PROJECT_AE_SQL,
    PROJECT_GEO_SQL,
    PROJECT_SRA_SQL,
    RELATED_SQL,
    SAMPLE_SQL,
    SRA_QUERY_ACCESSIONS_SQL,
    SEARCH_SQL,
    SRA_EXPERIMENT_TO_STUDY_SQL,
    SRA_SAMPLE_TO_STUDY_SQL,
    SRA_SEARCH_SQL,
    STUDY_EXPERIMENTS_SQL,
    STUDY_PUBLICATIONS_BATCH_SQL,
    STUDY_RUNS_PREVIEW_SQL,
    STUDY_RUNS_SQL,
    STUDY_RUNS_SUMMARY_SQL,
    build_sorted_search_sql,
    build_structured_search,
)

load_dotenv()

_STUDY_PREFIX = r"[SED]RP"
_EXPERIMENT_PREFIX = r"[SED]RX"
_SAMPLE_PREFIX = r"[SED]RS"

STUDY_PATTERN = re.compile(rf"^({_STUDY_PREFIX})\d+$", re.IGNORECASE)
SAMPLE_PATTERN = re.compile(rf"^({_SAMPLE_PREFIX})\d+$", re.IGNORECASE)
EXPERIMENT_PATTERN = re.compile(rf"^({_EXPERIMENT_PREFIX})\d+$", re.IGNORECASE)
GEO_SAMPLE_PATTERN = re.compile(r"^GSM\d+$", re.IGNORECASE)
PRJ_PATTERN = re.compile(r"^PRJ[A-Z]+\d+$", re.IGNORECASE)
PROJECT_PATTERN = re.compile(
    rf"^(GSE|{_STUDY_PREFIX}|[Ee]-[A-Za-z]{{4}}-)\d+$", re.IGNORECASE
)
BULK_PATTERN = re.compile(
    rf"^(GSE|{_STUDY_PREFIX}|PRJNA|[Ee]-[A-Za-z]{{4}}-)\d+$", re.IGNORECASE
)

STUDY_REGEX = rf"^{_STUDY_PREFIX}\d+$"
SAMPLE_REGEX = rf"^{_SAMPLE_PREFIX}\d+$"
EXPERIMENT_REGEX = rf"^{_EXPERIMENT_PREFIX}\d+$"
GEO_SAMPLE_REGEX = r"^GSM\d+$"
PRJ_REGEX = r"^PRJ[A-Z]+\d+$"
PROJECT_REGEX = rf"^(GSE|{_STUDY_PREFIX}|[Ee]-[A-Za-z]{{4}}-)\d+$"
GEO_SERIES_REGEX = r"^GSE\d+$"
AE_REGEX = r"^[Ee]-[A-Za-z]{4}-\d+$"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_QUERY_DOWNLOAD = 100
COMBINED_BIN_ROWS_SQL = """
WITH combined AS (
    SELECT accession, coords_2d, countries_iso AS countries
    FROM geo_series
    WHERE coords_2d IS NOT NULL
    UNION ALL
    SELECT accession, coords_2d, countries
    FROM sra_studies
    WHERE coords_2d IS NOT NULL
),
eligible AS (
    SELECT
        accession,
        coords_2d,
        countries
    FROM combined
    WHERE array_length(coords_2d, 1) >= 2
      AND coords_2d[1] IS NOT NULL
      AND coords_2d[2] IS NOT NULL
      AND accession ~ '^[A-Za-z]+\\d+$'
)
SELECT
    accession,
    countries,
    coords_2d[1]::real AS x,
    coords_2d[2]::real AS y
FROM eligible
ORDER BY accession;
"""
CLUSTERS_SQL = """
SELECT
    title,
    centroid,
    num_points
FROM clusters
ORDER BY title;
"""
_bin_cache_lock = threading.Lock()
_bin_cache = {"ready": False, "points": b"", "accessions": b""}
_clusters_cache_lock = threading.Lock()
_clusters_cache = {"ready": False, "rows": []}


pool = ConnectionPool(
    os.environ["DATABASE_URL"],
    min_size=1,
    max_size=10,
    kwargs={"autocommit": True},
)

limiter = Limiter(key_func=get_remote_address)
api_app = FastAPI()
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
    request: Request,
    status_code: int,
    response_ms: float,
    search_query: str | None,
) -> None:
    try:
        client_ip = request.client.host if request.client else ""
        ip_hashed = hash_ip(client_ip) if client_ip else None
        country, city = get_location_from_ip(client_ip) if client_ip else (None, None)

        now_ist = datetime.now(_IST)

        ua_string = request.headers.get("user-agent", "")
        ua = parse_ua(ua_string)
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
                        request.method,
                        request.url.path,
                        str(request.url.query) or None,
                        search_query,
                        status_code,
                        round(response_ms, 2),
                        client_ip or None,
                        ip_hashed,
                        country,
                        city,
                        ua_string[:512] if ua_string else None,
                        browser,
                        browser_version,
                        os_name,
                        os_version,
                        device_type,
                        (request.headers.get("accept-language") or "")[:255] or None,
                        (request.headers.get("referer") or "")[:512] or None,
                    ),
                )
    except Exception:
        logger.debug("Failed to log request", exc_info=True)


class AnalyticsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        response_ms = (time.perf_counter() - start) * 1000

        # Extract search query from 'q' param if present
        search_query = request.query_params.get("q")

        # Invoke a daemon thread so we never slow down the response
        t = threading.Thread(
            target=log_request,
            args=(request, response.status_code, response_ms, search_query),
            daemon=True,
        )
        t.start()

        return response


# Middleware is registered on combined_app (below) so it covers
# both REST and MCP routes.


@api_app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded. Please try again later."},
    )


def classify_accession(acc: str) -> str | None:
    """Classify accession into database source."""
    if acc.startswith("GSE"):
        return "geo"
    if acc.startswith("E-"):
        return "arrayexpress"
    if STUDY_PATTERN.match(acc):
        return "sra"
    return None


def _enrich_with_publications(rows: list[dict], cur) -> list[dict]:
    """Batch-lookup publications from study_publications and merge into result rows.

    Adds the full `publications` JSONB array plus top-level convenience fields
    from the primary (most-cited) publication: pmid, publication_title,
    journal, doi, citation_count.
    """
    if not rows:
        return rows
    accessions = [r["accession"] for r in rows if r.get("accession")]
    if not accessions:
        return rows
    cur.execute(STUDY_PUBLICATIONS_BATCH_SQL, (accessions,))
    pub_map = {r["accession"]: r["publications"] for r in cur.fetchall()}
    for row in rows:
        pubs = pub_map.get(row["accession"])
        row["publications"] = pubs
        if pubs and len(pubs) > 0:
            primary = pubs[0]
            row["pmid"] = primary.get("pmid")
            row["publication_title"] = primary.get("title")
            row["journal"] = primary.get("journal")
            row["doi"] = primary.get("doi")
            row["authors"] = primary.get("authors")
            row["citation_count"] = primary.get("citation_count")
    return rows


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


def rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def flatten_sra_study(cur, study: str) -> list[dict]:
    # 1. Fetch experiments (like /project/{study}/experiments endpoint)
    cur.execute(STUDY_EXPERIMENTS_SQL, (study,))
    experiments = cur.fetchall()
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
    for sample_acc in unique_sample_accs:
        cur.execute(SAMPLE_SQL, (sample_acc,))
        sample = cur.fetchone()
        if sample:
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
        for key in attribute_keys:
            if isinstance(attrs, dict):
                row[key] = attrs.get(key, "-")
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


def _build_binary_payloads() -> tuple[bytes, bytes]:
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(COMBINED_BIN_ROWS_SQL)
            rows = cur.fetchall()

    points_buf = bytearray(len(rows) * 8)
    accessions_buf = bytearray()
    offset = 0

    for accession, countries, x, y in rows:
        struct.pack_into("<ff", points_buf, offset, x, y)
        offset += 8

        accession_encoded = accession.encode("utf-8")
        accessions_buf.extend(struct.pack("<I", len(accession_encoded)))
        accessions_buf.extend(accession_encoded)

        country_values = countries or []
        accessions_buf.extend(struct.pack("<I", len(country_values)))
        for country in country_values:
            country_encoded = country.encode("utf-8")
            accessions_buf.extend(struct.pack("<I", len(country_encoded)))
            accessions_buf.extend(country_encoded)

    return bytes(points_buf), bytes(accessions_buf)


def _get_binary_payloads() -> tuple[bytes, bytes]:
    with _bin_cache_lock:
        if _bin_cache["ready"]:
            return _bin_cache["points"], _bin_cache["accessions"]

        points_bytes, accessions_bytes = _build_binary_payloads()
        _bin_cache["ready"] = True
        _bin_cache["points"] = points_bytes
        _bin_cache["accessions"] = accessions_bytes
        return points_bytes, accessions_bytes


def _get_clusters_rows() -> list[dict]:
    with _clusters_cache_lock:
        if _clusters_cache["ready"]:
            return _clusters_cache["rows"]

        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(CLUSTERS_SQL)
                rows = cur.fetchall()

        _clusters_cache["ready"] = True
        _clusters_cache["rows"] = rows
        return rows


@api_app.get(
    "/",
    summary="Health check",
    description="Return a simple status message to confirm the API is running.",
    operation_id="health_check",
)
def hello():
    return {"message": "pysradb-server is up and running!", "errors": None}


@api_app.get(
    "/points.bin",
    summary="Download 2D points as binary",
    description=(
        "Return all GEO+SRA 2D coordinates in accession order as packed little-endian "
        "float32 pairs (x, y)."
    ),
    operation_id="get_points_bin",
)
@limiter.limit("20/minute")
def get_points_bin(request: Request):
    try:
        points_bytes, _ = _get_binary_payloads()
    except Exception:
        logger.exception("Database error in get_points_bin")
        raise HTTPException(500, "Internal server error")

    return Response(content=points_bytes, media_type="application/octet-stream")


@api_app.get(
    "/accessions.bin",
    summary="Download accessions as binary",
    description=(
        "Return all GEO+SRA full accessions and countries in accession order. "
        "Record format: accession_len(uint32 LE), accession_utf8, "
        "country_count(uint32 LE), then repeated country_len(uint32 LE), country_utf8."
    ),
    operation_id="get_accessions_bin",
)
@limiter.limit("20/minute")
def get_accessions_bin(request: Request):
    try:
        _, accessions_bytes = _get_binary_payloads()
    except Exception:
        logger.exception("Database error in get_accessions_bin")
        raise HTTPException(500, "Internal server error")

    return Response(content=accessions_bytes, media_type="application/octet-stream")


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

    return rows


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
    start = time.perf_counter()

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(GEO_SEARCH_SQL, (q, cursor_rank, cursor_rank, cursor_acc))
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
    start = time.perf_counter()

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    SRA_SEARCH_SQL,
                    (q, cursor_rank, cursor_rank, cursor_acc),
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
    "/search",
    summary="Search all databases",
    description=(
        "Unified search across GEO, SRA, and ArrayExpress. "
        "Optionally restrict by db=geo|sra|arrayexpress. "
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
    db: str | None = Query(None, pattern="^(geo|sra|arrayexpress)$"),
    sortby: str | None = Query(None, pattern="^(citations|journal|year)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    if sortby:
        sql, params = build_sorted_search_sql(
            q=q,
            sortby=sortby,
            order=order,
            db=db,
            cursor_sort=cursor_sort,
            cursor_acc=cursor_acc,
        )
        start = time.perf_counter()
        try:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                    _enrich_with_publications(rows, cur)
        except Exception:
            logger.exception("Database error in search (sorted)")
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
                    "sort_value": rows[-1].get("sort_val"),
                    "accession": rows[-1]["accession"],
                }
            ),
        }

    # Default rank-based search
    if db == "geo":
        return search_geo(request, q, cursor_rank, cursor_acc)
    elif db == "sra":
        return search_sra(request, q, cursor_rank, cursor_acc)
    elif db == "arrayexpress":
        start = time.perf_counter()
        try:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        AE_SEARCH_SQL,
                        (q, cursor_rank, cursor_rank, cursor_acc),
                    )
                    rows = cur.fetchall()
                    _enrich_with_publications(rows, cur)
        except Exception as e:
            logger.exception("Database error in search_arrayexpress")
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

    start = time.perf_counter()

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    SEARCH_SQL,
                    (
                        q,
                        cursor_rank,
                        cursor_rank,
                        cursor_acc,
                    ),
                )
                rows = cur.fetchall()
                _enrich_with_publications(rows, cur)
    except Exception as e:
        logger.exception("Database error in search")
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
    "/project/{accession}",
    summary="Get project",
    description=(
        "Return a GEO series (GSE) or SRA study (SRP/DRP/ERP) record by accession."
    ),
    operation_id="get_project",
)
@limiter.limit("100/minute")
def get_project(
    request: Request,
    accession: str = Path(..., pattern=PROJECT_REGEX),
):
    accession = accession.upper()

    if accession.startswith("E-"):
        sql = PROJECT_AE_SQL
    elif accession.startswith("G"):
        sql = PROJECT_GEO_SQL
    else:
        sql = PROJECT_SRA_SQL

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (accession,))
                row = cur.fetchone()
                if row:
                    _enrich_with_publications([row], cur)
    except Exception as e:
        logger.exception("Database error in get_project")
        raise HTTPException(500, "Internal server error")

    return row


@api_app.get(
    "/project/{accession}/metadata",
    summary="Get project title and description",
    description=(
        "Return only title and description for a GEO series (GSE) or "
        "SRA study (SRP/DRP/ERP) accession."
    ),
    operation_id="get_project_metadata",
)
@limiter.limit("100/minute")
def get_project_metadata(
    request: Request,
    accession: str = Path(..., pattern=PROJECT_REGEX),
):
    sql = (
        """
        SELECT accession, title, summary AS description
        FROM geo_series
        WHERE accession = %s;
        """
        if accession.upper().startswith("G")
        else """
        SELECT accession, title, abstract AS description
        FROM sra_studies
        WHERE accession = %s;
        """
    )

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (accession,))
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
    description="List all experiments for an SRA study accession (SRP/DRP/ERP).",
    operation_id="get_study_experiments",
)
@limiter.limit("100/minute")
def get_study_experiments(
    request: Request,
    study: str = Path(..., pattern=STUDY_REGEX),
):
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(STUDY_EXPERIMENTS_SQL, (study,))
                return cur.fetchall()
    except Exception as e:
        logger.exception("Database error in get_study_experiments")
        raise HTTPException(500, "Internal server error")


def _split_fastq_fields(row: dict, study: str) -> list[dict]:
    """Expand a single run row into one dict per FASTQ file.

    Paired-end runs have semicolon-separated fastq_ftp/bytes/md5 fields;
    this splits them into individual file entries with directory paths.
    """
    ftp = row.get("fastq_ftp") or ""
    bytes_ = row.get("fastq_bytes") or ""
    md5s = row.get("fastq_md5") or ""
    urls = [u.strip() for u in ftp.split(";") if u.strip()]
    sizes = [s.strip() for s in bytes_.split(";") if s.strip()]
    checksums = [m.strip() for m in md5s.split(";") if m.strip()]

    run = row["run_accession"]
    exp = row.get("experiment_accession") or "unknown"
    dir_path = f"{study}/{exp}/{run}"

    if not urls:
        # Fall back to SRA format if no FASTQ links
        sra_ftp = row.get("sra_ftp") or ""
        if sra_ftp:
            filename = sra_ftp.rsplit("/", 1)[-1] if "/" in sra_ftp else run
            return [
                {
                    "run_accession": run,
                    "experiment_accession": exp,
                    "library_layout": row.get("library_layout") or "",
                    "fastq_url": (
                        f"ftp://{sra_ftp}"
                        if not sra_ftp.startswith("ftp://")
                        else sra_ftp
                    ),
                    "fastq_bytes": row.get("sra_bytes") or "",
                    "fastq_md5": row.get("sra_md5") or "",
                    "filename": filename,
                    "directory_path": dir_path,
                }
            ]
        return []

    results = []
    for i, url in enumerate(urls):
        filename = url.rsplit("/", 1)[-1] if "/" in url else url
        results.append(
            {
                "run_accession": run,
                "experiment_accession": exp,
                "library_layout": row.get("library_layout") or "",
                "fastq_url": f"ftp://{url}" if not url.startswith("ftp://") else url,
                "fastq_bytes": sizes[i] if i < len(sizes) else "",
                "fastq_md5": checksums[i] if i < len(checksums) else "",
                "filename": filename,
                "directory_path": dir_path,
            }
        )
    return results


@api_app.get(
    "/project/{study}/runs",
    summary="List download links for a study",
    description=(
        "Return FASTQ/SRA download links for all runs in an SRA study. "
        "Returns a preview (up to 500 runs) plus summary statistics."
    ),
    operation_id="get_study_runs",
)
@limiter.limit("100/minute")
def get_study_runs(
    request: Request,
    study: str = Path(..., pattern=STUDY_REGEX),
):
    study = study.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(STUDY_RUNS_SUMMARY_SQL, (study, study))
                summary = cur.fetchone()
                if not summary or summary["total_runs"] == 0:
                    return {
                        "total_runs": 0,
                        "paired_runs": 0,
                        "single_runs": 0,
                        "total_fastq_bytes": 0,
                        "runs": [],
                    }

                cur.execute(STUDY_RUNS_PREVIEW_SQL, (study, study))
                rows = cur.fetchall()
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
    "/project/{study}/runs/download",
    summary="Download run links as TSV",
    description=(
        "Stream a TSV file containing all FASTQ download links for a study, "
        "with one row per file. Paired-end runs are split into separate rows. "
        "Includes a directory_path column for organizing files as STUDY/EXPERIMENT/RUN/."
    ),
    operation_id="download_study_runs_tsv",
)
@limiter.limit("20/minute")
def download_study_runs_tsv(
    request: Request,
    study: str = Path(..., pattern=STUDY_REGEX),
):
    study = study.upper()

    def generate():
        header = "run_accession\texperiment_accession\tlibrary_layout\tfastq_url\tfastq_bytes\tfastq_md5\tfilename\tdirectory_path\n"
        yield header
        try:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(STUDY_RUNS_SQL, (study, study))
                    while True:
                        rows = cur.fetchmany(500)
                        if not rows:
                            break
                        for row in rows:
                            for entry in _split_fastq_fields(row, study):
                                yield (
                                    f"{entry['run_accession']}\t"
                                    f"{entry['experiment_accession']}\t"
                                    f"{entry['library_layout']}\t"
                                    f"{entry['fastq_url']}\t"
                                    f"{entry['fastq_bytes']}\t"
                                    f"{entry['fastq_md5']}\t"
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
    "/sample/{accession}",
    summary="Get sample",
    description="Return an SRA sample (SRS/DRS/ERS) by accession.",
    operation_id="get_sample",
)
@limiter.limit("100/minute")
def get_sample(
    request: Request,
    accession: str = Path(..., pattern=SAMPLE_REGEX),
):
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SAMPLE_SQL, (accession,))
                return cur.fetchone()
    except Exception as e:
        logger.exception("Database error in get_sample")
        raise HTTPException(500, "Internal server error")


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
    "/geo/series/{accession}/samples",
    summary="List GEO series or ArrayExpress experiment samples",
    description="List all samples for a GEO series (GSE) or ArrayExpress experiment (E-XXXX-NNN).",
    operation_id="get_geo_series_samples",
)
@limiter.limit("100/minute")
def get_geo_series_samples(
    request: Request,
    accession: str = Path(...),
):
    accession = accession.upper()
    source = classify_accession(accession)
    if source not in ["geo", "arrayexpress"]:
        raise HTTPException(400, f"Invalid accession format: {accession}")

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if source == "arrayexpress":
                    cur.execute(AE_EXPERIMENT_SAMPLES_SQL, (accession,))
                    return [_transform_ae_sample_to_geo(s) for s in cur.fetchall()]
                else:
                    cur.execute(GEO_SERIES_SAMPLES_SQL, (accession,))
                    return cur.fetchall()
    except Exception as e:
        logger.exception("Database error in get_geo_series_samples")
        raise HTTPException(500, "Internal server error")


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
@limiter.limit("20/minute")
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

    try:
        buf = io.BytesIO()

        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    for acc in validated:
                        source = classify_accession(acc)

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


@api_app.get(
    "/download/query",
    summary="Download metadata by query",
    description=(
        "Generate a ZIP of CSV files for query-matched projects. "
        "Accepts q, optional db=geo|sra|arrayexpress, and optional updated year filters. "
        "Results are capped at 100 projects."
    ),
    operation_id="download_by_query",
)
@limiter.limit("20/minute")
def download_by_query(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    db: str | None = Query(None, pattern="^(geo|sra|arrayexpress)$"),
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

    if not rows:
        raise HTTPException(404, "No results found for query")

    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    for row in rows:
                        acc = row["accession"]
                        source = row["source"]
                        if source == "sra":
                            data_rows = flatten_sra_study(cur, acc)
                            name = f"{acc}_experiments.csv"
                        elif source == "arrayexpress":
                            data_rows = flatten_ae_experiment(cur, acc)
                            name = f"{acc}_samples.csv"
                        else:
                            data_rows = flatten_geo_series(cur, acc)
                            name = f"{acc}_samples.csv"

                        if data_rows:
                            csv_buf = io.StringIO()
                            writer = csv.DictWriter(
                                csv_buf, fieldnames=data_rows[0].keys()
                            )
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
        logger.exception("Error in download_by_query")
        raise HTTPException(500, "Internal server error")


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
                    if result:
                        return {"project_accession": result["study"]}
                else:
                    # SRS/DRS/ERS sample - find SRA study
                    cur.execute(SRA_SAMPLE_TO_STUDY_SQL, (accession,))
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

                # If no result found
                raise HTTPException(
                    404, f"No project found for PRJ accession {prj_accession}"
                )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Database error in get_prj_info")
        raise HTTPException(500, "Internal server error")


@api_app.get(
    "/search/structured",
    summary="Structured search with filters",
    description=(
        "Search across GEO, SRA, ArrayExpress, and ENA with structured filters "
        "(organism, library_strategy, platform, country, center, year range) "
        "optionally combined with full-text search. At least one of q or a filter "
        "parameter is required. Use cursors (rank + accession) for pagination. "
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
    source: str | None = Query(None, pattern="^(geo|sra|arrayexpress|ena|all)$"),
    cursor_rank: float | None = Query(None),
    cursor_acc: str | None = Query(None),
):
    if not any(
        [q, organism, library_strategy, platform, country, center, year_from, year_to]
    ):
        raise HTTPException(
            400,
            "At least one of q, organism, library_strategy, platform, "
            "country, center, year_from, or year_to is required",
        )

    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(400, "year_from must be <= year_to")

    sql, params = build_structured_search(
        q=q,
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
    )

    start = time.perf_counter()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SET LOCAL statement_timeout = '30s'")
                cur.execute(sql, params)
                rows = cur.fetchall()
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
                cur.execute(STUDY_PUBLICATIONS_BATCH_SQL, ([accession],))
                row = cur.fetchone()
    except Exception:
        logger.exception("Database error in get_project_cite")
        raise HTTPException(500, "Internal server error")

    if not row or not row.get("publications"):
        raise HTTPException(404, f"No publications found for {accession}")

    pubs = row["publications"]
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


MCP_INSTRUCTIONS = """\
You are connected to pysraweb, a genomics study search engine covering
NCBI GEO, SRA/ENA/DDBJ, and ArrayExpress databases.

## CRITICAL: Search tool usage rules

BEFORE calling `search_all` or any database-specific search, ask yourself:

**"Do I already have search results from this conversation that could answer
the user's current request?"**

If YES → DO NOT search again. Filter/analyze existing results instead.
If NO → Proceed with the search.

A new search is ONLY appropriate when the user requests a genuinely new topic
that cannot be answered by filtering, summarizing, or analyzing existing results.

### What counts as filtering (NEVER re-search for these)

- Narrowing by keyword: "show only liver results" → check `title` and `summary` fields
  for the keyword.
- Narrowing by organism: "only human studies" → check `organisms` array.
- Narrowing by source: "only GEO" → check `source` field.
- Narrowing by date: "after 2020" → check `updated_at` field.
- Summarizing, counting, or comparing existing results.

All of these are client-side operations on data you already have. Use `title`,
`summary`, `organisms`, `updated_at`, and `source` fields.

### Paginate-and-filter pattern

Each search returns up to 200 results, a `total` count, and a `next_cursor`.
When the user asks to filter and the current page has few or no matches:

1. Check `total` — if it's much larger than results seen so far, more pages exist.
2. Call `search_all` again with the **SAME `q`** parameter plus `cursor_rank` and
   `cursor_acc` from `next_cursor` to fetch the next page.
3. Filter that page. Repeat until you find enough matches or exhaust results.

This is NOT a new search — it is pagination of the original query. The `q` parameter
must stay the same. Only `cursor_rank` and `cursor_acc` change.

## Data hierarchy

GEO:  Series (GSE) → Samples (GSM)
SRA:  Study (SRP/ERP/DRP) → Experiments (SRX/ERX/DRX) → Samples (SRS/ERS/DRS)
ArrayExpress:  Experiment (E-MTAB-NNN, E-GEOD-NNN, etc.) → Samples (with ENA links)
BioProject:  PRJNA/PRJEB/PRJDA can link to GEO or SRA

Many GEO series have a linked SRA study (the SRA study's `alias` field = GSE accession).
E-GEOD experiments in ArrayExpress correspond to GEO series (E-GEOD-50760 ↔ GSE50760).

## Recommended workflow

1. **Search** — call `search_all` for simple keyword searches.
   The `source` field tells you: 'geo', 'sra', or 'arrayexpress'.
   Only pass `db="geo"|"sra"|"arrayexpress"` if user explicitly restricts.
   - **Sorting**: pass `sortby` to re-order results:
     - `sortby=citations`: sort by citation count of the primary publication.
     - `sortby=journal`: sort by journal name of the primary publication.
     - `sortby=year`: sort by study date (updated_at).
     - `order=desc` (default) or `order=asc`.
   - When `sortby` is used, pagination cursors change: `next_cursor` returns
     `{sort_value, accession}` instead of `{rank, accession}`. Pass these back
     as `cursor_sort` + `cursor_acc` (not `cursor_rank`).

2. **Structured search** — call `search_structured` when the user wants to filter by
   organism, library strategy (e.g. RNA-Seq, ChIP-Seq), platform (e.g. ILLUMINA),
   country, institution/center, or date range. Searches across GEO, SRA, ArrayExpress,
   and ENA. Filters can be combined with a keyword query (`q`). At least one filter or
   `q` is required.
   - `organism`: exact species name, e.g. "Homo sapiens", "Mus musculus"
   - `library_strategy`: SRA/ENA library strategy, e.g. "RNA-Seq", "ChIP-Seq", "WGS"
     (also matches ArrayExpress `technology` field)
   - `platform`: sequencing platform, e.g. "ILLUMINA", "OXFORD_NANOPORE"
     (SRA and ENA only; GEO and ArrayExpress are excluded when this filter is active)
   - `country`: country name, e.g. "United States", "United Kingdom"
     (GEO via contributors, SRA via geocoded countries, ENA via experiment metadata;
     ArrayExpress is excluded — no structured country data)
   - `center`: institution name (partial match), e.g. "Broad Institute"
   - `year_from`/`year_to`: publication year range
   - `source`: restrict to "geo", "sra", "arrayexpress", "ena", or "all"
   Use `search_all` for simple keyword searches; use `search_structured` when specific
   metadata filters are needed.

3. **Drill into a result** — call `get_project` with the accession.
   Returns full metadata for GSE, SRP, or E-XXXX.

4. **Explore samples/experiments**:
   - SRA study → `get_study_experiments`, then `get_sample` for individual samples
   - GEO series → `get_geo_series_samples`
   - ArrayExpress experiment → `get_ae_experiment_samples`

5. **FASTQ download links** — call `get_study_runs` with an SRA study accession
   (SRP/ERP/DRP) to get FASTQ download URLs for all runs. Returns summary statistics
   (total_runs, paired_runs, single_runs, total_fastq_bytes) and a list of runs with
   fields: run_accession, experiment_accession, library_layout, fastq_ftp,
   fastq_bytes, fastq_md5. Use this when the user asks how to download raw data,
   FASTQs, or sequencing files for a study.
   - FASTQ URLs are EBI FTP paths (prefix with `https://` to make downloadable links).
   - Paired-end runs have semicolon-separated fastq_ftp/bytes/md5 fields (R1;R2).
   - For a wget/curl one-liner, point users to the TSV download endpoint:
     `curl -sS "https://pysraweb.saketlab.org/api/project/{study}/runs/download" | tail -n +2 | cut -f4 | xargs -P4 -I{} wget -q -x -nH --cut-dirs=6 "{}"`
   - Or direct them to the web UI at `https://pysraweb.saketlab.org/p/{study}` which
     has an interactive download section with per-file links and batch download scripts.

6. **Cross-reference lookup** — call `get_project_xref` with any accession (GSE, SRP,
   E-XXXX, PRJNA) to discover cross-references across all archives. Shows cross-reference
   links (SRA, BioProject, ENA, GEO), naming convention links (E-GEOD ↔ GSE), and
   SRA alias connections. Use this when the user asks "is this study also in SRA/GEO?"
   or wants to find all versions of a dataset.

7. **Citations & publications** — call `get_project_cite` with any accession to get
   publication metadata and BibTeX citations. Returns title, authors, journal, DOI,
   pub_date, citation_count, and formatted BibTeX for each linked paper.
   - `type=original` (default): returns the original/earliest publication (smallest PMID).
   - `type=all`: returns all linked publications sorted by PMID ascending.
   - `format=json` (default): returns structured JSON with all fields.
   - `format=bibtex`: returns raw plain-text BibTeX, ready to copy-paste into a .bib file.
   Use this when the user asks for citations, references, BibTeX, or paper details
   for a study. When the user wants copy-pasteable BibTeX, use `format=bibtex`.
   Note: search results and `get_project` also include top-level
   publication fields (pmid, publication_title, journal, doi, authors, citation_count)
   from the primary (most-cited) publication.

8. **Resolve child → parent** — `resolve_accession_project` maps samples/experiments
   to their parent study/series.

9. **Resolve BioProject** — `resolve_prj_project` maps PRJNA/PRJEB/PRJDA to GSE or SRP.

## Context rules

- **Stay focused** on current search results unless user explicitly requests new search.
- Summarize key fields: title, organisms, summary, sample/experiment count, dates.
- When comparing studies, fetch both with `get_project` and present side-by-side.

## Tools to avoid

- `bulk_metadata`, `download_by_query`, `bulk_project_metadata` return binary/bulk data
  useless for LLMs. Tell users to use the REST API or web interface directly.
- `download_study_runs_tsv` streams a TSV file — use `get_study_runs` instead to get
  structured JSON with download links. Point users to the TSV endpoint URL if they
  want a wget/curl one-liner.
- `health_check` is only for connectivity checks.
- `search_geo` and `search_sra` are redundant — use `search_all` with `db` parameter.
"""

mcp = FastMCP.from_fastapi(
    app=api_app,
    name="pysraweb MCP",
    instructions=MCP_INSTRUCTIONS,
)
mcp_app = mcp.http_app(path="/mcp")

combined_app = FastAPI(
    title="pysradb-server",
    routes=[*mcp_app.routes, *api_app.routes],
    lifespan=mcp_app.lifespan,
)
combined_app.state.limiter = limiter
combined_app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
combined_app.add_middleware(AnalyticsMiddleware)
combined_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app = combined_app
