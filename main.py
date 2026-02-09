import csv
import io
import json
import logging
import os
import re
import time
import zipfile

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastmcp import FastMCP
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from queries import (
    BIO_PROJECT_GEO_SERIES_SQL,
    BULK_PROJECT_METADATA_SQL,
    ALL_QUERY_ACCESSIONS_SQL,
    GEO_SAMPLE_TO_SERIES_SQL,
    GEO_QUERY_ACCESSIONS_SQL,
    GEO_SEARCH_SQL,
    GEO_SERIES_SAMPLES_SQL,
    PRJ_TO_SRA_STUDY_SQL,
    PRJNA_TO_GEO_SERIES_SQL,
    PROJECT_GEO_SQL,
    PROJECT_SRA_SQL,
    SAMPLE_SQL,
    SRA_QUERY_ACCESSIONS_SQL,
    SEARCH_SQL,
    SRA_EXPERIMENT_TO_STUDY_SQL,
    SRA_SAMPLE_TO_STUDY_SQL,
    SRA_SEARCH_SQL,
    STUDY_EXPERIMENTS_SQL,
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
PROJECT_PATTERN = re.compile(rf"^(GSE|{_STUDY_PREFIX})\d+$", re.IGNORECASE)
BULK_PATTERN = re.compile(rf"^(GSE|{_STUDY_PREFIX}|PRJNA)\d+$", re.IGNORECASE)

STUDY_REGEX = rf"^{_STUDY_PREFIX}\d+$"
SAMPLE_REGEX = rf"^{_SAMPLE_PREFIX}\d+$"
EXPERIMENT_REGEX = rf"^{_EXPERIMENT_PREFIX}\d+$"
GEO_SAMPLE_REGEX = r"^GSM\d+$"
PRJ_REGEX = r"^PRJ[A-Z]+\d+$"
PROJECT_REGEX = rf"^(GSE|{_STUDY_PREFIX})\d+$"
GEO_SERIES_REGEX = r"^GSE\d+$"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_QUERY_DOWNLOAD = 100


pool = ConnectionPool(
    os.environ["DATABASE_URL"],
    min_size=1,
    max_size=10,
    kwargs={"autocommit": True},
)

limiter = Limiter(key_func=get_remote_address)
api_app = FastAPI()
api_app.state.limiter = limiter


@api_app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded. Please try again later."},
    )


def classify_accession(acc: str) -> str | None:
    if acc.startswith("GSE"):
        return "geo"
    if STUDY_PATTERN.match(acc):
        return "sra"
    return None


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


@api_app.get(
    "/",
    summary="Health check",
    description="Return a simple status message to confirm the API is running.",
    operation_id="health_check",
)
def hello():
    return {"message": "pysradb-server is up and running!", "errors": None}


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
                cur.execute(
                    GEO_SEARCH_SQL, (q, q, q, q, cursor_rank, cursor_rank, cursor_acc)
                )
                rows = cur.fetchall()
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
                    (q, q, q, q, q, cursor_rank, cursor_rank, cursor_acc),
                )
                rows = cur.fetchall()
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
        "Unified search across GEO and SRA. Optionally restrict by db=geo|sra. "
        "Use cursors (rank + accession) for pagination. "
        "Results objects: {accession: string, title: string, summary: string | null, "
        "updated_at: string | null, organisms: string[] | null, rank: float, source: 'geo' | 'sra'}. "
        "Returns {results: object[], total: int, took_ms: float, "
        "next_cursor: {rank: float, accession: string} | null}."
    ),
    operation_id="search_all",
)
@limiter.limit("100/minute")
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    cursor_rank: float | None = Query(None),
    cursor_acc: str | None = Query(None),
    db: str | None = Query(None, pattern="^(geo|sra)$"),
):
    if db == "geo":
        return search_geo(request, q, cursor_rank, cursor_acc)
    elif db == "sra":
        return search_sra(request, q, cursor_rank, cursor_acc)

    start = time.perf_counter()

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    SEARCH_SQL,
                    (q, q, q, q, q, q, q, q, cursor_rank, cursor_rank, cursor_acc),
                )
                rows = cur.fetchall()
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
    sql = PROJECT_GEO_SQL if accession.upper().startswith("G") else PROJECT_SRA_SQL

    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (accession,))
                row = cur.fetchone()
    except Exception as e:
        logger.exception("Database error in get_project")
        raise HTTPException(500, "Internal server error")

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
    "/geo/series/{accession}/samples",
    summary="List GEO series samples",
    description="List all GSM samples for a GEO series accession (GSE).",
    operation_id="get_geo_series_samples",
)
@limiter.limit("100/minute")
def get_geo_series_samples(
    request: Request,
    accession: str = Path(..., pattern=GEO_SERIES_REGEX),
):
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(GEO_SERIES_SAMPLES_SQL, (accession,))
                rows = cur.fetchall()
    except Exception as e:
        logger.exception("Database error in get_geo_series_samples")
        raise HTTPException(500, "Internal server error")

    return rows


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
                        if STUDY_PATTERN.match(acc):
                            rows = flatten_sra_study(cur, acc)
                            name = f"{acc}_experiments.csv"
                            if rows:
                                csv_buf = io.StringIO()
                                writer = csv.DictWriter(
                                    csv_buf, fieldnames=rows[0].keys()
                                )
                                writer.writeheader()
                                writer.writerows(rows)
                                zf.writestr(name, csv_buf.getvalue())
                        elif acc.startswith("GSE"):
                            rows = flatten_geo_series(cur, acc)
                            name = f"{acc}_samples.csv"
                            if rows:
                                csv_buf = io.StringIO()
                                writer = csv.DictWriter(
                                    csv_buf, fieldnames=rows[0].keys()
                                )
                                writer.writeheader()
                                writer.writerows(rows)
                                zf.writestr(name, csv_buf.getvalue())
                        elif acc.startswith("PRJNA"):
                            cur.execute(BIO_PROJECT_GEO_SERIES_SQL, (acc,))
                            series_list = cur.fetchall()
                            for series in series_list:
                                gse = series["accession"]
                                rows = flatten_geo_series(cur, gse)
                                name = f"{gse}_samples.csv"
                                if rows:
                                    csv_buf = io.StringIO()
                                    writer = csv.DictWriter(
                                        csv_buf, fieldnames=rows[0].keys()
                                    )
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
        "Return accession, title, description, and organisms for GEO/SRA project accessions "
        "(GSE, SRP, ERP, DRP)."
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
@limiter.limit("20/minute")
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
        "Accepts q, optional db=geo|sra, and optional updated year filters. "
        "Results are capped at 100 projects."
    ),
    operation_id="download_by_query",
)
@limiter.limit("20/minute")
def download_by_query(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    db: str | None = Query(None, pattern="^(geo|sra)$"),
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

    if db == "geo":
        sql = GEO_QUERY_ACCESSIONS_SQL
        params = [q]
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend(year_params(updated_year_from, updated_year_to))
    elif db == "sra":
        sql = SRA_QUERY_ACCESSIONS_SQL
        params = [q]
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend(year_params(updated_year_from, updated_year_to))
    else:
        sql = ALL_QUERY_ACCESSIONS_SQL
        params = [q]
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend([q, *year_params(updated_year_from, updated_year_to)])
        params.extend(year_params(updated_year_from, updated_year_to))
        params.extend(year_params(updated_year_from, updated_year_to))

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


MCP_INSTRUCTIONS = """\
You are connected to pysraweb, a genomics study search engine covering
NCBI GEO and SRA/ENA/DDBJ databases.

## CRITICAL: Search tool usage rules

BEFORE calling `search_all`, `search_geo`, or `search_sra`, ask yourself:

**"Do I already have search results from this conversation that could answer
the user's current request?"**

If YES → DO NOT call any search tool. Filter/analyze results in memory instead.
If NO → Proceed with the search.

A new search is ONLY appropriate when the user requests a genuinely new topic
that cannot be answered by filtering, summarizing, or analyzing existing results.

Filtering, narrowing, subsetting, summarizing, or asking questions about
existing results should NEVER trigger a new search. The `title`, `summary`,
`organisms`, `updated_at`, and `source` fields in each result contain all
the information needed for client-side filtering.

For pagination (more results from the same query), use cursor parameters
(`cursor_rank`, `cursor_acc`) instead of re-searching.

## Data hierarchy

GEO side:  Series (GSE) → Samples (GSM)
SRA side:  Study (SRP/ERP/DRP) → Experiments (SRX/ERX/DRX) → Samples (SRS/ERS/DRS)
BioProject (PRJNA/PRJEB/PRJDA) can link to either or both.
Many GEO series have a linked SRA study (the SRA study's `alias` field holds the GSE accession).

## Recommended workflow

1. **Search** — call `search_all` with the user's query.
   The `source` field in each result tells you whether it came from GEO or SRA.
   Pass `db="geo"` or `db="sra"` only when the user explicitly asks to restrict.
2. **Drill into a result** — call `get_project` with the accession the user
   picked (or the best match).  This returns full metadata for a GSE or SRP.
3. **Explore experiments / samples**:
   - SRA study → `get_study_experiments`, then `get_sample` for samples of interest.
   - GEO series → `get_geo_series_samples`.
4. **Resolve child → parent** — given a sample or experiment accession, call
   `resolve_accession_project` to find its parent study/series.
5. **Resolve BioProject** — call `resolve_prj_project` to map PRJNA/PRJEB/PRJDA
   to the corresponding GSE or SRP.

## Context rules

- **Stay focused** on the current search results and the project the user is
  exploring.
- Summarise key fields for the user: title, organisms, summary/abstract,
  number of experiments or samples, publication date.
- When comparing studies, fetch both with `get_project` and present
  differences side-by-side.

## Tools to avoid

- `bulk_metadata` and `download_by_query` return binary ZIP files you cannot
  read.  If the user wants bulk downloads, tell them to use the REST API or
  web interface directly.
- `health_check` is only useful for connectivity checks, not for answering
  questions.
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

app = combined_app
