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
    ENA_SEARCH_SQL,
    BIO_PROJECT_GEO_SERIES_SQL,
    BULK_PROJECT_METADATA_SQL,
    COMMON_NAME_BY_SCIENTIFIC_NAME_SQL,
    ENA_SAMPLE_SQL,
    ENA_STUDY_EXPERIMENTS_SQL,
    ALL_QUERY_ACCESSIONS_SQL,
    GEO_SAMPLE_TO_SERIES_SQL,
    GEO_SERIES_NEIGHBORS_COORDS_SQL,
    GEO_QUERY_ACCESSIONS_SQL,
    GEO_SEARCH_SQL,
    GEO_SERIES_SAMPLES_SQL,
    PRJ_TO_SRA_STUDY_SQL,
    PRJNA_TO_GEO_SERIES_SQL,
    PROJECT_AE_SQL,
    PROJECT_ENA_SQL,
    PROJECT_GEO_SQL,
    PROJECT_SRA_SQL,
    RELATED_SQL,
    BATCH_SAMPLES_SQL,
    SAMPLE_SQL,
    SRA_QUERY_ACCESSIONS_SQL,
    SEARCH_SQL,
    SRA_EXPERIMENT_TO_STUDY_SQL,
    SRA_SAMPLE_TO_STUDY_SQL,
    SRA_SEARCH_SQL,
    STUDY_EXPERIMENTS_SQL,
    SUBMITTER_BATCH_SQL,
    STUDY_PUBLICATIONS_BATCH_SQL,
    STUDY_BAMS_SQL,
    STUDY_RUNS_PREVIEW_SQL,
    STUDY_RUNS_SQL,
    STUDY_RUNS_SUMMARY_SQL,
    GROWTH_PROJECTS_GEO_SQL,
    GROWTH_PROJECTS_SRA_SQL,
    GROWTH_PROJECTS_AE_SQL,
    GROWTH_PROJECTS_ENA_SQL,
    GROWTH_EXPERIMENTS_GEO_SQL,
    GROWTH_EXPERIMENTS_SRA_SQL,
    GROWTH_EXPERIMENTS_AE_SQL,
    GROWTH_EXPERIMENTS_ENA_SQL,
    GROWTH_BASES_ENA_SQL,
    GROWTH_BYTES_SRA_FASTQ_SQL,
    GROWTH_BYTES_SRA_SRA_SQL,
    GROWTH_ORGANISM_SQL,
    LIST_ORGANISMS_SQL,
    LIST_ORGANISMS_WITH_COMMON_NAMES_SQL,
    ORGANISM_SEARCH_SQL,
    ORGANISM_TOTALS_SQL,
    ORGANISM_TOTALS_YEAR_SQL,
    GLOBAL_CONTRIBUTIONS_FILTERS_SQL,
    build_global_contributions_sql,
    SITEMAP_ACCESSIONS_SQL,
    SITEMAP_COUNTS_SQL,
    ENRICHED_SAMPLES_SQL,
    ENRICHED_STUDY_SQL,
    ENRICHED_STUDY_V3_SQL,
    ONTOLOGY_SAMPLES_V3_SQL,
    build_enriched_samples_sql,
    init_global_contributions_schema,
    init_global_contributions_filters_sql,
    build_country_filters_sql,
    build_country_accessions_sql,
    build_sorted_search_sql,
    build_structured_search,
    SRA_EXPERIMENT_SQL,
    ENA_EXPERIMENT_SQL,
    GEO_SAMPLE_SQL,
    EXPERIMENT_RUNS_SQL,
    ENA_SAMPLE_TO_EXPERIMENT_SQL,
    SRA_SAMPLE_TO_EXPERIMENT_SQL,
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
PROJECT_REGEX = rf"^(GSE|{_STUDY_PREFIX}|PRJ[A-Z]+|[Ee]-[A-Za-z]{{4}}-)\d+$"
STUDY_EXPERIMENTS_REGEX = rf"^({_STUDY_PREFIX}|PRJ[A-Z]+)\d+$"
SAMPLE_OR_ENA_REGEX = rf"^({_SAMPLE_PREFIX}|SAM[A-Z]+)\d+$"
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
_growth_cache_lock = threading.Lock()
_growth_cache = {"ready": False, "data": {}, "built_at": 0.0}
_MAX_ORGANISM_CACHE_ENTRIES = 500
_organism_growth_cache_lock = threading.Lock()
_organism_growth_cache: dict[str, dict] = {}  # key -> {data, built_at}
_organisms_list_cache_lock = threading.Lock()
_organisms_list_cache: dict[str, dict] = {}  # "plain"/"common" -> {data, built_at}
_organism_totals_cache_lock = threading.Lock()
_organism_totals_cache: dict[str, dict] = {}  # cache_key -> {data, built_at}
_global_contributions_cache_lock = threading.Lock()
_global_contributions_cache = {"ready": False, "data": [], "built_at": 0.0}


pool = ConnectionPool(
    os.environ["DATABASE_URL"],
    min_size=4,
    max_size=20,
    timeout=10,
    kwargs={"autocommit": True},
)

with pool.connection() as _conn:
    ENRICHED_SAMPLES_SQL = build_enriched_samples_sql(_conn)
    init_global_contributions_schema(_conn)
    GLOBAL_CONTRIBUTIONS_FILTERS_SQL = init_global_contributions_filters_sql()

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
    """Batch-lookup publications and center names, merge into result rows.

    Adds the full `publications` JSONB array plus top-level convenience fields
    from the primary (most-cited) publication: pmid, publication_title,
    journal, doi, citation_count, authors.

    Always sets `center_name` from unified_metadata.
    For studies without publication authors, falls back to center_name.
    """
    if not rows:
        return rows
    accessions = [r["accession"] for r in rows if r.get("accession")]
    if not accessions:
        return rows
    cur.execute(STUDY_PUBLICATIONS_BATCH_SQL, (accessions,))
    pub_map = {r["accession"]: r["publications"] for r in cur.fetchall()}
    cur.execute(SUBMITTER_BATCH_SQL, {"acc": accessions})
    sub_map = {}
    for r in cur.fetchall():
        sub_map[r["accession"]] = {
            "submitter": r["submitter"],
            "country_code": r["country_code"],
            "is_single_cell": r["is_single_cell"],
            "single_cell_modality": r["single_cell_modality"],
        }
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
        meta = sub_map.get(row["accession"], {})
        row["center_name"] = meta.get("submitter")
        row["country_code"] = meta.get("country_code")
        row["is_single_cell"] = meta.get("is_single_cell")
        row["single_cell_modality"] = meta.get("single_cell_modality")
        if not row.get("authors") and row.get("center_name"):
            row["authors"] = row["center_name"]
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


def _is_ena_study(study: str) -> bool:
    return study.upper().startswith("PRJ")


def _fetch_study_experiments(cur, study: str) -> list[dict]:
    sql = ENA_STUDY_EXPERIMENTS_SQL if _is_ena_study(study) else STUDY_EXPERIMENTS_SQL
    cur.execute(sql, (study,))
    return cur.fetchall()


def _fetch_sample(cur, accession: str) -> dict | None:
    cur.execute(SAMPLE_SQL, (accession,))
    sample = cur.fetchone()
    if sample:
        return sample

    cur.execute(ENA_SAMPLE_SQL, (accession,))
    return cur.fetchone()


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
    for sample_acc in unique_sample_accs:
        sample = _fetch_sample(cur, sample_acc)
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


def _build_growth_data() -> dict:
    """Run all growth queries in one connection and return nested dict."""
    queries = {
        "projects": {
            "geo": GROWTH_PROJECTS_GEO_SQL,
            "sra": GROWTH_PROJECTS_SRA_SQL,
            "arrayexpress": GROWTH_PROJECTS_AE_SQL,
            "ena": GROWTH_PROJECTS_ENA_SQL,
        },
        "experiments": {
            "geo": GROWTH_EXPERIMENTS_GEO_SQL,
            "sra": GROWTH_EXPERIMENTS_SRA_SQL,
            "arrayexpress": GROWTH_EXPERIMENTS_AE_SQL,
            "ena": GROWTH_EXPERIMENTS_ENA_SQL,
        },
        "bases": {
            "ena": GROWTH_BASES_ENA_SQL,
            "sra_fastq_bytes": GROWTH_BYTES_SRA_FASTQ_SQL,
            "sra_sra_bytes": GROWTH_BYTES_SRA_SRA_SQL,
        },
    }
    data = {}
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            for mode, dbs in queries.items():
                data[mode] = {}
                for db_name, sql in dbs.items():
                    cur.execute(sql)
                    data[mode][db_name] = cur.fetchall()
    return data


_GROWTH_CACHE_TTL = 7 * 24 * 3600  # 7 days in seconds


def _get_growth_data() -> dict:
    with _growth_cache_lock:
        now = time.time()
        if (
            _growth_cache["ready"]
            and (now - _growth_cache["built_at"]) < _GROWTH_CACHE_TTL
        ):
            return _growth_cache["data"]

        data = _build_growth_data()
        _growth_cache["ready"] = True
        _growth_cache["data"] = data
        _growth_cache["built_at"] = now
        return data


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

    return Response(
        content=points_bytes,
        media_type="application/octet-stream",
        headers={"Cache-Control": "public, max-age=2592000"},
    )


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

    return Response(
        content=accessions_bytes,
        media_type="application/octet-stream",
        headers={"Cache-Control": "public, max-age=2592000"},
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
        pattern="^(geo|sra|arrayexpress|ena|sra_fastq_bytes|sra_sra_bytes)$",
        description="Filter to a single series",
    ),
):
    t0 = time.perf_counter()
    try:
        all_data = _get_growth_data()
    except Exception:
        logger.exception("Database error in get_growth_stats")
        raise HTTPException(500, "Internal server error")

    mode_data = all_data.get(mode, {})
    if db:
        series = {db: mode_data.get(db, [])}
    else:
        series = mode_data

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
        pattern="^(geo|sra|arrayexpress|ena)$",
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
            all_series = {"geo": [], "sra": [], "arrayexpress": [], "ena": []}
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
            growth_data = _get_growth_data()
        except Exception:
            logger.exception("Database error fetching total growth for percentage")
            raise HTTPException(500, "Internal server error")
        total_exp = growth_data.get("experiments", {})

        result_series = {}
        dbs = [db] if db else ["geo", "sra", "arrayexpress", "ena"]
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
                "geo": 0,
                "sra": 0,
                "arrayexpress": 0,
                "ena": 0,
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

    return filters


@api_app.get(
    "/stats/global-contributions",
    summary="Geographic contributions across all databases",
    description=(
        "Return aggregated project/experiment/sample counts per geographic location "
        "(latitude/longitude rounded to 2 decimal places) from the unified_metadata table. "
        "Includes source breakdown (GEO/SRA/ArrayExpress/ENA), city, country, and top 5 organisms "
        "per location. Unfiltered results are cached for 7 days; filtered requests run fresh."
    ),
    operation_id="get_global_contributions",
)
@limiter.limit("20/minute")
def get_global_contributions(
    request: Request,
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
    has_filters = organism or assay_l1 or assay_l2 or place_type or address_type
    try:
        if has_filters:
            data = _query_global_contributions(
                organism, assay_l1, assay_l2, place_type, address_type
            )
        else:
            data = _get_global_contributions_cached()
    except Exception:
        logger.exception("Database error in get_global_contributions")
        raise HTTPException(500, "Internal server error")

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
        "to that country (not cached). Without country, results are cached for 7 days."
    ),
    operation_id="get_global_contribution_filters",
)
@limiter.limit("20/minute")
def get_global_contribution_filters(
    request: Request,
    country: str | None = Query(
        None, description="Scope filter counts to this country"
    ),
    organism: str | None = Query(
        None,
        description="Scope assay counts to this organism (requires country)",
    ),
):
    t0 = time.perf_counter()
    try:
        if country:
            filters = _get_country_scoped_filters(country, organism=organism)
        else:
            filters = _get_global_contributions_filters()
    except Exception:
        logger.exception("Database error in get_global_contribution_filters")
        raise HTTPException(500, "Internal server error")

    return {
        **filters,
        "took_ms": round((time.perf_counter() - t0) * 1000, 2),
    }


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
    sql, params = build_country_accessions_sql(
        country, organism=organism, assay_l2=assay_l2
    )
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in get_country_accessions")
        raise HTTPException(500, "Internal server error")
    return "\n".join(r[0] for r in rows if r[0])


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
        "Unified search across GEO, SRA, ArrayExpress, and ENA. "
        "Optionally restrict by db=geo|sra|arrayexpress|ena. "
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
    db: str | None = Query(None, pattern="^(geo|sra|arrayexpress|ena)$"),
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
    elif db == "ena":
        start = time.perf_counter()
        try:
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        ENA_SEARCH_SQL,
                        (q, cursor_rank, cursor_rank, cursor_acc),
                    )
                    rows = cur.fetchall()
                    _enrich_with_publications(rows, cur)
        except Exception:
            logger.exception("Database error in search_ena")
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
        "Return a GEO series (GSE), SRA study (SRP/DRP/ERP), ENA project (PRJ*), "
        "or ArrayExpress project (E-*) record by accession."
    ),
    operation_id="get_project",
)
@limiter.limit("1000/minute")
def get_project(
    request: Request,
    accession: str = Path(..., pattern=PROJECT_REGEX),
):
    accession = accession.upper()
    params = (accession,)

    if accession.startswith("E-"):
        sql = PROJECT_AE_SQL
    elif accession.startswith("G"):
        sql = PROJECT_GEO_SQL
    elif accession.startswith("PRJ"):
        sql = PROJECT_ENA_SQL
    else:
        sql = PROJECT_SRA_SQL

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
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
@limiter.limit("1000/minute")
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
    description=(
        "List all experiments for a study accession (SRP/DRP/ERP/PRJ*). "
        "For ENA (PRJ*), returns one row per experiment_accession (first run only)."
    ),
    operation_id="get_study_experiments",
)
@limiter.limit("1000/minute")
def get_study_experiments(
    request: Request,
    study: str = Path(..., pattern=STUDY_EXPERIMENTS_REGEX),
):
    study = study.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                return _fetch_study_experiments(cur, study)
    except Exception as e:
        logger.exception("Database error in get_study_experiments")
        raise HTTPException(500, "Internal server error")


def _split_fastq_fields(row: dict, study: str) -> list[dict]:
    """Expand a single run row into one dict per FASTQ file.

    Paired-end runs have semicolon-separated fastq_ftp/bytes/md5 fields;
    this splits them into individual file entries with directory paths.
    Each entry includes separate columns for SRA Lite, SRA (normalized),
    S3/AWS, and GCS URLs alongside the FASTQ URL.
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

    # Cloud / SRA URLs
    sra_lite_url = row.get("ncbi_sra_lite_url") or ""
    sra_lite_bytes = row.get("ncbi_sra_lite_bytes") or ""
    sra_url = row.get("ncbi_sra_normalized_url") or ""
    sra_bytes = row.get("ncbi_sra_normalized_bytes") or ""
    s3_url = row.get("ncbi_sra_lite_s3_url") or ""
    gs_url = row.get("ncbi_sra_lite_gs_url") or ""

    sra_md5 = row.get("sra_md5") or ""

    common = {
        "run_accession": run,
        "experiment_accession": exp,
        "library_layout": row.get("library_layout") or "",
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
            results.append(
                {
                    **common,
                    "fastq_url": (
                        f"ftp://{url}" if not url.startswith("ftp://") else url
                    ),
                    "fastq_bytes": sizes[i] if i < len(sizes) else "",
                    "fastq_md5": checksums[i] if i < len(checksums) else "",
                    "filename": filename,
                }
            )
        return results

    # No FASTQ FTP so emit one row
    # with empty FASTQ columns but cloud URLs
    best_url = (
        sra_url
        or sra_lite_url
        or row.get("ncbi_sra_url")
        or row.get("ncbi_sra_url_aws")
        or ""
    )
    if not best_url:
        sra_ftp = row.get("sra_ftp") or ""
        if sra_ftp:
            best_url = (
                f"ftp://{sra_ftp}" if not sra_ftp.startswith("ftp://") else sra_ftp
            )
            common["sra_url"] = best_url
            common["sra_bytes"] = row.get("sra_bytes") or ""
    if not best_url and not s3_url:
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
        "Return FASTQ/SRA download links for all runs in an SRA study. "
        "Returns a preview (up to 500 runs) plus summary statistics."
    ),
    operation_id="get_study_runs",
)
@limiter.limit("1000/minute")
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
    "/project/{study}/bams",
    summary="List original BAM files for a study",
    description=(
        "Return direct download links for original submitted BAM files in an SRA study. "
        "BAM files are extracted from the ncbi_files JSONB column where supertype is 'Original'. "
        "Not all studies have BAM files — returns total_bams=0 when none exist."
    ),
    operation_id="get_study_bams",
)
@limiter.limit("1000/minute")
def get_study_bams(
    request: Request,
    study: str = Path(..., pattern=STUDY_REGEX),
):
    study = study.upper()
    empty = {"total_bams": 0, "total_bam_bytes": "0", "bams": []}
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(STUDY_BAMS_SQL, (study, study))
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
@limiter.limit("1000/minute")
def download_study_bams_tsv(
    request: Request,
    study: str = Path(..., pattern=STUDY_REGEX),
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
                    cur.execute(STUDY_BAMS_SQL, (study, study))
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
@limiter.limit("1000/minute")
def download_study_runs_tsv(
    request: Request,
    study: str = Path(..., pattern=STUDY_REGEX),
):
    study = study.upper()

    def generate():
        header = (
            "run_accession\texperiment_accession\tlibrary_layout\t"
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
@limiter.limit("1000/minute")
def download_study_metadata_csv(
    request: Request,
    study: str = Path(..., pattern=STUDY_REGEX),
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

                # 2. Batch-fetch samples
                sample_map: dict[str, dict] = {}
                if sample_accs:
                    if _is_ena_study(study):
                        for sample_acc in sample_accs:
                            s = _fetch_sample(cur, sample_acc)
                            if s:
                                sample_map[s["accession"]] = s
                    else:
                        cur.execute(BATCH_SAMPLES_SQL, (sample_accs,))
                        for s in cur.fetchall():
                            sample_map[s["accession"]] = s

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
                cur.execute(STUDY_RUNS_SUMMARY_SQL, (study, study))
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
                    cur.execute(STUDY_RUNS_SQL, (study, study))
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


_DOWNLOAD_MODES = {
    "fastq": {
        "label": "FASTQ files",
        "url_key": "fastq_url",
        "md5_key": "fastq_md5",
        "cmd": 'wget -q --show-progress -O "{dirpath}/{filename}" "{url}"',
    },
    "sra": {
        "label": "SRA format",
        "url_key": "sra_url",
        "md5_key": "sra_md5",
        "cmd": 'wget -q --show-progress -O "{dirpath}/{filename}" "{url}"',
    },
    "sra_lite": {
        "label": "SRA Lite",
        "url_key": "sra_lite_url",
        "md5_key": "sra_md5",
        "cmd": 'wget -q --show-progress -O "{dirpath}/{filename}" "{url}"',
    },
    "s3": {
        "label": "AWS S3",
        "url_key": "s3_url",
        "md5_key": "sra_md5",
        "cmd": 'aws s3 cp "{url}" "{dirpath}/{filename}"',
    },
    "gcs": {
        "label": "Google Cloud",
        "url_key": "gs_url",
        "md5_key": "sra_md5",
        "cmd": 'gsutil cp "{url}" "{dirpath}/{filename}"',
    },
}

_DOWNLOAD_MODE_REGEX = "|".join(_DOWNLOAD_MODES)


@api_app.get(
    "/project/{study}/download/{mode}",
    summary="Download script for a study",
    description=(
        "Return a bash script to download all runs in a study using the "
        "specified source: fastq (EBI FASTQ via wget), sra (SRA normalized "
        "via wget), sra_lite (SRA Lite via wget), s3 (AWS S3 via aws cli), "
        "or gcs (Google Cloud via gsutil)."
    ),
    operation_id="download_study_script",
)
@limiter.limit("200/minute")
def download_study_script(
    request: Request,
    study: str = Path(..., pattern=STUDY_REGEX),
    mode: str = Path(..., pattern=_DOWNLOAD_MODE_REGEX),
):
    study = study.upper()
    cfg = _DOWNLOAD_MODES.get(mode)
    if not cfg:
        raise HTTPException(400, f"Invalid mode: {mode}")

    url_key = cfg["url_key"]
    md5_key = cfg["md5_key"]
    cmd_tpl = cfg["cmd"]
    label = cfg["label"]

    # Collect all entries first so we can 404 if none have URLs for this mode
    lines: list[str] = []
    checksums: list[tuple[str, str]] = []  # (md5, filepath)
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(STUDY_RUNS_SQL, (study, study))
                while True:
                    batch = cur.fetchmany(500)
                    if not batch:
                        break
                    for row in batch:
                        for entry in _split_fastq_fields(row, study):
                            url = entry.get(url_key) or ""
                            if not url:
                                continue
                            dirpath = entry["directory_path"]
                            filename = entry["filename"]
                            lines.append(
                                f'mkdir -p "{dirpath}" && {cmd_tpl.format(url=url, dirpath=dirpath, filename=filename)}'
                            )
                            md5 = entry.get(md5_key) or ""
                            if md5:
                                checksums.append((md5, f"{dirpath}/{filename}"))
    except Exception:
        logger.exception("Error generating download script for %s", study)
        raise HTTPException(500, "Internal error generating download script")

    if not lines:
        raise HTTPException(
            404,
            f"No {label} URLs available for {study}",
        )

    script = (
        f"#!/usr/bin/env bash\n"
        f"# Download {label} for {study}\n"
        f"# Generated by seqout.org\n\n"
        f"set -euo pipefail\n\n"
        f"# Download metadata\n"
        f'curl -sS "https://seqout.org/api/project/{study}/metadata/download"'
        f' -o "{study}/metadata.csv" --create-dirs\n\n'
    )
    script += "\n".join(lines) + "\n"
    script += f'\necho "Done — {len(lines)} files saved under ./{study}/"\n'
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
    if accession.startswith("E-"):
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
    for f in files:
        safe_name = _shell_escape(f"{accession}/{f['filename']}")
        safe_url = _shell_escape(f["url"])
        lines.append(
            f"mkdir -p {_shell_escape(accession)} && curl -L -o {safe_name} {safe_url}"
        )

    script = (
        f"#!/usr/bin/env bash\n"
        f"# Download supplementary files for {accession}\n"
        f"# {len(files)} file{'s' if len(files) != 1 else ''}\n"
        f"# Generated by seqout.org\n\n"
        f"set -euo pipefail\n\n"
    )
    script += "\n".join(lines) + "\n"
    script += f'\necho "Done — {len(files)} file{"s" if len(files) != 1 else ""} saved under ./{accession}/"\n'

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
    accession: str = Path(..., pattern=SAMPLE_OR_ENA_REGEX),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                return _fetch_sample(cur, accession)
    except Exception as e:
        logger.exception("Database error in get_sample")
        raise HTTPException(500, "Internal server error")


SAMPLE_DETAIL_REGEX = rf"^({_EXPERIMENT_PREFIX}|{_SAMPLE_PREFIX}|GSM|SAM[A-Z]+)\d+$"


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

    # --- SRX/DRX/ERX experiment ---
    if EXPERIMENT_PATTERN.match(acc):
        # Fetch experiment
        cur.execute(SRA_EXPERIMENT_SQL, (acc,))
        experiment = cur.fetchone()
        if not experiment:
            cur.execute(ENA_EXPERIMENT_SQL, (acc,))
            experiment = cur.fetchone()
        if not experiment:
            raise HTTPException(404, f"Experiment not found: {acc}")

        study_acc = experiment.get("study")
        if not study_acc:
            raise HTTPException(404, f"No parent study found for {acc}")

        # Fetch parent project
        if study_acc.startswith("PRJ"):
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
        runs = cur.fetchall()

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
        runs = cur.fetchall()
    else:
        # Try ENA
        cur.execute(ENA_SAMPLE_TO_EXPERIMENT_SQL, (acc,))
        exp_row = cur.fetchone()
        if exp_row:
            exp_acc = exp_row["experiment_accession"]
            cur.execute(ENA_EXPERIMENT_SQL, (exp_acc,))
            experiment = cur.fetchone()
            cur.execute(EXPERIMENT_RUNS_SQL, (exp_acc,))
            runs = cur.fetchall()

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
        if study_acc.startswith("PRJ"):
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
@limiter.limit("1000/minute")
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
@limiter.limit("1000/minute")
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
@limiter.limit("1000/minute")
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
@limiter.limit("1000/minute")
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
@limiter.limit("1000/minute")
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
@limiter.limit("100000/minute")
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
        "(organism, library_strategy, platform, country, center, year range, "
        "journal, instrument_model) optionally combined with full-text search. "
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
    source: str | None = Query(None, pattern="^(geo|sra|arrayexpress|ena|all)$"),
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
):
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
        ]
    ):
        raise HTTPException(
            400,
            "At least one of q, organism, library_strategy, platform, "
            "country, center, year_from, year_to, journal, "
            "instrument_model, assay_l1, assay_l2, or a geo_* filter is required",
        )

    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(400, "year_from must be <= year_to")

    if (geo_lat is None) != (geo_lng is None):
        raise HTTPException(
            400, "geo_lat and geo_lng must both be provided or both omitted"
        )

    if geo_radius_km is not None and geo_lat is None:
        raise HTTPException(400, "geo_radius_km requires geo_lat and geo_lng")

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


@api_app.get(
    "/sitemap/counts",
    summary="Sitemap accession counts",
    description="Return total accession counts per source for sitemap generation.",
    operation_id="sitemap_counts",
)
def sitemap_counts(request: Request):
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(SITEMAP_COUNTS_SQL)
                row = cur.fetchone()
    except Exception:
        logger.exception("Database error in sitemap_counts")
        raise HTTPException(500, "Internal server error")
    total = row["geo"] + row["sra"] + row["arrayexpress"] + row["ena"]
    return {
        "geo": row["geo"],
        "sra": row["sra"],
        "arrayexpress": row["arrayexpress"],
        "ena": row["ena"],
        "total": total,
    }


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
    offset = page * limit
    sql = SITEMAP_ACCESSIONS_SQL.get(source)
    if sql is None:
        raise HTTPException(400, f"Unknown source: {source}")
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (limit, offset))
                rows = cur.fetchall()
    except Exception:
        logger.exception("Database error in sitemap_accessions")
        raise HTTPException(500, "Internal server error")
    return {"accessions": [r["accession"] for r in rows], "page": page, "limit": limit}


# ── Enriched Metadata ────────────────────────────────────────────────────────


@api_app.get(
    "/project/{accession}/enriched",
    summary="Get ontology-enriched sample metadata",
    description=(
        "Return structured metadata extracted by LLM with ontology mappings. "
        "Fields include tissue, cell_type, disease, assay, development_stage "
        "each with standardised ontology IDs (UBERON, CL, MONDO, EFO). "
        "Falls back to legacy enriched_samples if v3 data is unavailable."
    ),
    operation_id="get_project_enriched",
)
@limiter.limit("100/minute")
def get_project_enriched(
    request: Request,
    accession: str = Path(..., pattern=PROJECT_REGEX),
    human_readable_age: bool = Query(
        True, description="Convert age from hours to human-readable format"
    ),
):
    accession = accession.upper()
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(ENRICHED_STUDY_V3_SQL, (accession,))
                study = cur.fetchone()
                if study:
                    cur.execute(ONTOLOGY_SAMPLES_V3_SQL, (accession,))
                    samples = cur.fetchall()
                    version = "v3"
                else:
                    cur.execute(ENRICHED_STUDY_SQL, (accession,))
                    study = cur.fetchone()
                    if not study:
                        raise HTTPException(
                            404, f"No enriched metadata for {accession}"
                        )
                    cur.execute(ENRICHED_SAMPLES_SQL, (accession,))
                    samples = cur.fetchall()
                    version = "v1"
    except HTTPException:
        raise
    except Exception:
        logger.exception("Database error in get_project_enriched")
        raise HTTPException(500, "Internal server error")

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
   country, institution/center, date range, journal, or instrument model. Searches
   across GEO, SRA, ArrayExpress, and ENA. Filters can be combined with a keyword
   query (`q`). At least one filter or `q` is required.
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
   - `journal`: exact journal name, e.g. "Nature", "Cell"
     (filters by the primary publication's journal across all sources)
   - `instrument_model`: exact instrument model, e.g. "Illumina HiSeq 2500"
     (GEO and SRA only; ArrayExpress and ENA are excluded when this filter is active)
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
   fastq_bytes, fastq_md5, plus cloud URLs (ncbi_sra_lite_url, ncbi_sra_normalized_url,
   ncbi_sra_lite_s3_url, ncbi_sra_lite_gs_url). Use this when the user asks how to
   download raw data, FASTQs, or sequencing files for a study.
   - FASTQ URLs are EBI FTP paths (prefix with `https://` to make downloadable links).
   - Paired-end runs have semicolon-separated fastq_ftp/bytes/md5 fields (R1;R2).
   - **Download scripts** — call `download_study_script` with a study accession and a
     mode to get a ready-to-run bash script. Available modes:
     - `fastq` — wget commands for EBI FASTQ files (default, recommended)
     - `sra` — wget commands for SRA normalized format (full quality)
     - `sra_lite` — wget commands for SRA Lite format (smaller, lossy quality scores)
     - `s3` — aws s3 cp commands using s3:// URIs
     - `gcs` — gsutil cp commands using gs:// URIs
     Example: `https://seqout.org/api/project/SRP123456/download/fastq`
   - **Merged metadata CSV** — call `download_study_metadata_csv` with a study accession
     to get a CSV merging experiment metadata, sample attributes (as dynamic columns),
     and run download links. One row per run. Useful when users need a complete metadata
     table for a study.
     Example: `https://seqout.org/api/project/SRP123456/metadata/download`
   - **Supplementary file download** — call `download_supplementary_script` with a
     GEO (GSE) or ArrayExpress (E-MTAB) accession to get a bash script that downloads
     all supplementary / processed data files using curl.
     Example: `https://seqout.org/api/project/GSE119945/supplementary/download`
     FTP URLs are automatically converted to HTTPS. Only works for GEO and ArrayExpress
     accessions (SRA studies use `download_study_script` instead).
   - Or direct them to the web UI at `https://seqout.org/p/{study}` which
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

10. **Quick metadata** — call `get_project_metadata` with any accession (GSE, SRP, E-XXXX)
    to get just the title and description without full project details. Faster than
    `get_project` when you only need a quick summary.

11. **Species common name** — call `get_common_name_by_scientific_name` with a scientific
    name (e.g. "Homo sapiens") to get its common name (e.g. "human"). Useful when
    displaying organism info in a friendly format.

12. **Database growth stats** — call `get_growth_stats` with `mode=projects`,
    `mode=experiments`, or `mode=bases` (data volume in bytes) to get monthly counts
    per database (GEO, SRA, ArrayExpress, ENA). Optionally filter by `db` (e.g.
    `db=geo`). Returns per-month (non-cumulative) counts. Use this when the user asks
    about database size, growth trends, or historical data volume.

13. **Bulk project+samples metadata** — call `bulk_project_samples_metadata` with a list
    of project accessions (GSE, SRP, ERP, DRP) to get project title/description plus
    flattened sample rows. Useful for comparing sample-level metadata across studies.

14. **Organism growth stats** — call `get_organism_growth` with an organism scientific
    name (e.g. "Homo sapiens") to get monthly experiment counts across GEO, SRA,
    ArrayExpress, and ENA. Use `mode=absolute` for raw counts or `mode=percentage`
    for the organism's share of total experiments per month. Optionally filter to a
    single database with `db=geo|sra|arrayexpress|ena`. Returns per-db series with
    monthly count and cumulative total. Use this when the user asks about trends,
    growth, or historical data for a specific organism.

15. **Organism totals** — call `get_organism_totals` to get total experiment counts
    per organism across all databases. Optionally filter by `year_from`/`year_to`
    and limit results with `limit`. Returns organisms sorted by total count
    (descending) with per-database breakdown (geo, sra, arrayexpress, ena) and
    common names. Use this when the user asks which organisms have the most data.

16. **Organism search** — call `search_organisms` with a partial name query (`q`)
    to find organisms by scientific or common name. Returns up to `limit` results
    sorted by total experiment count. Use this for organism name lookup/autocomplete.

17. **Global contributions map** — call `get_global_contributions` to get geographic
    contribution data aggregated by lat/lng from the `unified_metadata` table. Returns
    per-location counts (projects, experiments, samples), source breakdown (GEO, SRA,
    ArrayExpress, ENA), city, country, and top 5 organisms. Optionally filter by:
    - `organism`: exact scientific name (e.g. "Homo sapiens")
    - `assay_l1`: broad assay category
    - `assay_l2`: specific assay type
    Unfiltered results are cached for 7 days; filtered requests run fresh.
    Use this when the user asks about geographic distribution of genomics studies,
    which countries or institutions contribute the most data, or wants a world map
    view of sequencing contributions.

18. **Global contribution filters** — call `get_global_contribution_filters` to get
    the available filter values (organisms, assay_l1, assay_l2) for the global
    contributions map, each with counts, sorted by frequency. Cached for 7 days.
    Optionally pass `country` to scope counts to a specific country, and `organism`
    to further scope assay counts to a specific organism within that country (organism
    list always reflects the full country, never self-filtered). When `country` is
    provided, the response includes a `total` field with the exact project count for
    that scope. Use this to populate filter dropdowns or to discover valid filter
    values before calling `get_global_contributions` with filters.

19. **Country accessions** — call `get_country_accessions` with a required `country`
    name to get all distinct project accessions from that country. Optionally filter
    by `organism` (scientific name) and `assay_l2` (assay type). Returns a plain-text
    newline-separated list of accession strings. Use this when the user wants to list
    or export all project accessions for a country.

20. **Enriched metadata** — call `get_project_enriched` with a project accession to get
    ontology-enriched sample metadata. Returns `version: "v3"` with ontology IDs
    (MONDO, UBERON, CL, EFO) for disease, tissue, cell_type, assay, development_stage,
    plus organism/taxid from Cellosaurus. Falls back to `version: "v1"` (legacy) with
    basic LLM-extracted fields. Returns 404 if no enriched data exists.
    Use this when the user asks for structured/parsed sample attributes beyond what the
    raw metadata provides.

## Context rules

- **Stay focused** on current search results unless user explicitly requests new search.
- Summarize key fields: title, organisms, summary, sample/experiment count, dates.
- When comparing studies, fetch both with `get_project` and present side-by-side.

## Tools to avoid

- `bulk_metadata`, `download_by_query`, `bulk_project_metadata` return binary/bulk data
  useless for LLMs. Tell users to use the REST API or web interface directly.
- `download_study_runs_tsv` streams a TSV file — use `get_study_runs` instead to get
  structured JSON with download links, or `download_study_script` to get a ready-to-run
  bash script for any download mode (fastq, sra, sra_lite, s3, gcs).
- `download_study_metadata_csv` streams a merged metadata CSV — not useful for LLMs.
  Use `get_study_experiments` + `get_sample` for structured metadata access.
- `get_points_bin`, `get_accessions_bin`, `get_clusters` return binary/JSON data for
  the 2D map visualization — not useful for conversational LLM interactions.
- `get_geo_series_neighbors_coords` returns raw neighbor/3D coordinate data for the
  similarity graph — not useful for conversational use.
- `health_check` is only for connectivity checks.
- `search_geo` and `search_sra` are redundant — use `search_all` with `db` parameter.
- `list_organisms` returns all ~283K organisms — use `search_organisms` or
  `get_organism_totals` (with `limit`) instead for conversational use.
"""

mcp = FastMCP.from_fastapi(
    app=api_app,
    name="pysraweb MCP",
    instructions=MCP_INSTRUCTIONS,
)
mcp_app = mcp.http_app(path="/mcp")

combined_app = FastAPI(
    title="seqout API",
    description="Programmatic access to 1M+ GEO, SRA, ENA & ArrayExpress sequencing projects.",
    routes=[*mcp_app.routes, *api_app.routes],
    lifespan=mcp_app.lifespan,
    root_path="/api",
)
combined_app.state.limiter = limiter
combined_app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
combined_app.add_middleware(AnalyticsMiddleware)
# combined_app.add_middleware(
#    CORSMiddleware,
#    allow_origins=["*"],
#    allow_methods=["*"],
#    allow_headers=["*"],
# )

app = combined_app
