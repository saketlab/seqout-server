import csv
import io
import logging
import os
import time
import zipfile

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastmcp import FastMCP
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from constants import (
    BULK_PATTERN,
    EXPERIMENT_PATTERN,
    EXPERIMENT_REGEX,
    GEO_SAMPLE_PATTERN,
    GEO_SAMPLE_REGEX,
    GEO_SERIES_REGEX,
    PRJ_REGEX,
    PROJECT_REGEX,
    SAMPLE_REGEX,
    STUDY_PATTERN,
    STUDY_REGEX,
)
from queries import (
    BIO_PROJECT_GEO_SERIES_SQL,
    GEO_SAMPLE_TO_SERIES_SQL,
    GEO_SEARCH_SQL,
    GEO_SERIES_SAMPLES_SQL,
    PRJ_TO_SRA_STUDY_SQL,
    PRJNA_TO_GEO_SERIES_SQL,
    PROJECT_GEO_SQL,
    PROJECT_SRA_SQL,
    SAMPLE_SQL,
    SEARCH_SQL,
    SRA_EXPERIMENT_TO_STUDY_SQL,
    SRA_SAMPLE_TO_STUDY_SQL,
    SRA_SEARCH_SQL,
    STUDY_EXPERIMENTS_SQL,
)
from utils import flatten_geo_series, flatten_sra_study

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        "Use cursors (rank + accession) for pagination."
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
        "Use cursors (rank + accession) for pagination."
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
        "Use cursors (rank + accession) for pagination."
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
        "Generate a ZIP of CSV files for up to 100 accessions. "
        "Accepts GSE, SRP/DRP/ERP, or PRJNA accessions."
    ),
    operation_id="bulk_metadata",
)
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


mcp = FastMCP.from_fastapi(
    app=api_app,
    name="pysradb MCP",
    description="MCP tools mirroring pysradb-server API routes.",
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
