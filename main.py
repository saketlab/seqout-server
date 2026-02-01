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
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from queries import (
    BIO_PROJECT_GEO_SERIES_SQL,
    GEO_SEARCH_SQL,
    GEO_SERIES_SAMPLES_SQL,
    PROJECT_GEO_SQL,
    PROJECT_SRA_SQL,
    SAMPLE_SQL,
    SEARCH_SQL,
    SRA_SEARCH_SQL,
    STUDY_EXPERIMENTS_SQL,
)

load_dotenv()

_STUDY_PREFIX = r"[SE]RP"
_EXPERIMENT_PREFIX = r"[SE]RX"
_SAMPLE_PREFIX = r"[SE]RS"

STUDY_PATTERN = re.compile(rf"^({_STUDY_PREFIX})\d+$", re.IGNORECASE)
SAMPLE_PATTERN = re.compile(rf"^({_SAMPLE_PREFIX})\d+$", re.IGNORECASE)
PROJECT_PATTERN = re.compile(rf"^(GSE|{_STUDY_PREFIX})\d+$", re.IGNORECASE)
BULK_PATTERN = re.compile(rf"^(GSE|{_STUDY_PREFIX}|PRJNA)\d+$", re.IGNORECASE)

STUDY_REGEX = rf"^{_STUDY_PREFIX}\d+$"
SAMPLE_REGEX = rf"^{_SAMPLE_PREFIX}\d+$"
PROJECT_REGEX = rf"^(GSE|{_STUDY_PREFIX})\d+$"
GEO_SERIES_REGEX = r"^GSE\d+$"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

pool = ConnectionPool(
    os.environ["DATABASE_URL"],
    min_size=1,
    max_size=10,
    kwargs={"autocommit": True},
)

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
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
                        char_map[c.get("@tag")] = c.get("#text")
                elif isinstance(chars, dict):
                    char_map[chars.get("@tag")] = chars.get("#text")

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
                "Organism": ch.get("Organism", {}).get("#text") if ch else None,
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


@app.get("/")
def hello():
    return {"message": "pysradb-server is up and running!", "errors": None}


@app.get("/search/geo")
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


@app.get("/search/sra")
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
                    SRA_SEARCH_SQL, (q, q, q, q, cursor_rank, cursor_rank, cursor_acc)
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


@app.get("/search")
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
                    (q, q, q, q, q, q, q, cursor_rank, cursor_rank, cursor_acc),
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


@app.get("/project/{accession}")
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


@app.get("/project/{study}/experiments")
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


@app.get("/sample/{accession}")
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


@app.get("/geo/series/{accession}/samples")
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


@app.post("/bulk/metadata")
@limiter.limit("20/minute")
def bulk_metadata(request: Request, payload: dict):
    accessions = payload.get("accessions", [])
    if not accessions:
        raise HTTPException(400, "No accessions provided")
    if len(accessions) > 100:
        raise HTTPException(400, "Maximum 100 accessions allowed per request")

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
