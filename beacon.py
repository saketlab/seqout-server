"""GA4GH Beacon v2.2.0 metadata API for seqout."""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

import beacon_queries as bq


BEACON_ID = os.environ.get("BEACON_ID", "org.seqout.beacon")
API_VERSION = "v2.2.0"
BEACON_ROOT = os.environ.get("BEACON_ROOT", "/api/beacon")
ENVIRONMENT = os.environ.get("BEACON_ENVIRONMENT", "prod")
DEFAULT_STATEMENT_TIMEOUT_MS = int(
    os.environ.get("BEACON_STATEMENT_TIMEOUT_MS", "15000")
)
MAX_RECORD_LIMIT = 100

ORGANIZATION = {
    "id": "seqout",
    "name": "seqout",
    "description": "Search engine over public genomics-repository metadata "
    "(GEO, SRA, ENA, ArrayExpress, GSA).",
    "welcomeUrl": "https://seqout.org",
    "contactUrl": "mailto:saketc@iitb.ac.in",
}

BEACON_DESCRIPTION = (
    "Metadata Beacon over GEO/SRA/ENA/ArrayExpress/GSA. Not a variant beacon "
    "(no g_variants/analyses). Individuals are a 1:1 projection of biosamples; "
    "counts reflect subjects-per-sample, not distinct persons. GSA contributes "
    "datasets and runs only (its samples are not ontology-enriched). Runs follow the "
    "default schema; runDate is omitted (no performed-date exists in source metadata; "
    "release dates are in info.releaseDate). Ontology filters are scoped to biosamples "
    "and individuals; run-level ontology filtering is deferred. Runs are accessible by "
    "id or browse."
)

SOURCES = [
    {"id": "SRA", "name": "NCBI SRA", "description": "NCBI Sequence Read Archive."},
    {"id": "GEO", "name": "NCBI GEO", "description": "NCBI Gene Expression Omnibus."},
    {"id": "ENA", "name": "EBI ENA", "description": "European Nucleotide Archive."},
    {
        "id": "ArrayExpress",
        "name": "EBI ArrayExpress",
        "description": "EBI ArrayExpress / BioStudies.",
    },
    {
        "id": "GSA",
        "name": "CNCB-NGDC GSA",
        "description": "Genome Sequence Archive (China).",
    },
]

_DEFAULT_MODEL = "https://raw.githubusercontent.com/ga4gh-beacon/beacon-v2/main/models/src/beacon-v2-default-model"

ENTRY_TYPES: dict[str, dict[str, Any]] = {
    "dataset": {
        "id": "dataset",
        "name": "Dataset",
        "partOfSpecification": "Beacon v2.0.0",
        "defaultSchema": {
            "id": "ga4gh-beacon-dataset-v2.0.0",
            "name": "Default schema for datasets",
            "referenceToSchemaDefinition": f"{_DEFAULT_MODEL}/datasets/defaultSchema.json",
        },
        "ontologyTermForThisType": {"id": "NCIT:C47824", "label": "Data set"},
    },
    "biosample": {
        "id": "biosample",
        "name": "Biosample",
        "partOfSpecification": "Beacon v2.0.0",
        "defaultSchema": {
            "id": "ga4gh-beacon-biosample-v2.0.0",
            "name": "Default schema for biosamples",
            "referenceToSchemaDefinition": f"{_DEFAULT_MODEL}/biosamples/defaultSchema.json",
        },
        "ontologyTermForThisType": {"id": "NCIT:C43412", "label": "Biospecimen"},
    },
    "individual": {
        "id": "individual",
        "name": "Individual",
        "partOfSpecification": "Beacon v2.0.0",
        "defaultSchema": {
            "id": "ga4gh-beacon-individual-v2.0.0",
            "name": "Default schema for individuals",
            "referenceToSchemaDefinition": f"{_DEFAULT_MODEL}/individuals/defaultSchema.json",
        },
        "ontologyTermForThisType": {"id": "NCIT:C25190", "label": "Person"},
    },
    "run": {
        "id": "run",
        "name": "Run",
        "partOfSpecification": "Beacon v2.0.0",
        "defaultSchema": {
            "id": "ga4gh-beacon-run-v2.0.0",
            "name": "Default schema for a sequencing run",
            "referenceToSchemaDefinition": f"{_DEFAULT_MODEL}/runs/defaultSchema.json",
        },
        "ontologyTermForThisType": {"id": "NCIT:C148088", "label": "Sequencing run"},
    },
}

pool: Any = None


def query(
    sql: str, params: tuple = (), timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS
) -> list[dict]:
    """Run a read query with a per-statement timeout (broad ontology terms can be slow)."""
    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                cur.execute(sql, params)
                return cur.fetchall()


class BeaconRequest:
    """Normalized Beacon query request (GET params or POST body)."""

    def __init__(
        self,
        *,
        granularity: str,
        skip: int,
        limit: int,
        filters: list[dict],
        test_mode: bool,
        entry_id: str | None,
    ):
        self.granularity = granularity  # boolean | count | record
        self.skip = skip  # PAGE index (spec §2: OFFSET skip*limit)
        self.limit = limit
        self.filters = filters
        self.test_mode = test_mode
        self.entry_id = entry_id

    @property
    def offset(self) -> int:
        return self.skip * self.limit


def _normalize_filters(raw: Any) -> list[dict]:
    """Accept POST filter objects or GET comma-separated ids → list of {id, includeDescendantTerms?}."""
    out: list[dict] = []
    if not raw:
        return out
    if isinstance(raw, str):
        raw = [t.strip() for t in raw.split(",") if t.strip()]
    for f in raw:
        if isinstance(f, str):
            out.append({"id": f})
        elif isinstance(f, dict) and f.get("id"):
            out.append(f)
    return out


def _nonneg_int(raw: Any, default: int, field: str) -> int:
    """Parse a pagination param as a non-negative int; 400 on garbage/negative (else it reaches SQL as a negative OFFSET)."""
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, f"Beacon '{field}' must be a non-negative integer")
    if value < 0:
        raise HTTPException(400, f"Beacon '{field}' must be >= 0")
    return value


async def parse_request(request: Request, body: dict | None) -> BeaconRequest:
    qp = request.query_params
    q = body.get("query", {}) if body else {}
    pagination = q.get("pagination", {}) if isinstance(q, dict) else {}
    granularity = (
        q.get("requestedGranularity") or qp.get("requestedGranularity") or "record"
    )
    skip = _nonneg_int(pagination.get("skip", qp.get("skip")), 0, "skip")
    limit = _nonneg_int(pagination.get("limit", qp.get("limit")), 10, "limit") or 10
    if granularity == "record":
        limit = min(limit, MAX_RECORD_LIMIT)
    filters = _normalize_filters(q.get("filters") if body else qp.get("filters"))
    test_mode = str(qp.get("testMode", "")).lower() == "true" or bool(
        (body or {}).get("meta", {}).get("testMode")
    )
    entry_id = qp.get("id") or (q.get("id") if isinstance(q, dict) else None)
    return BeaconRequest(
        granularity=granularity,
        skip=skip,
        limit=limit,
        filters=filters,
        test_mode=test_mode,
        entry_id=entry_id,
    )


def _received_summary(req: BeaconRequest | None) -> dict:
    s: dict[str, Any] = {
        "apiVersion": API_VERSION,
        "requestedSchemas": [],
        "pagination": {
            "skip": req.skip if req else 0,
            "limit": req.limit if req else 0,
        },
        "requestedGranularity": req.granularity if req else "record",
    }
    if req:
        s["filters"] = [f.get("id") for f in req.filters]
        s["testMode"] = req.test_mode
    return s


def build_meta(returned_granularity: str, req: BeaconRequest | None = None) -> dict:
    return {
        "beaconId": BEACON_ID,
        "apiVersion": API_VERSION,
        "returnedGranularity": returned_granularity,
        "returnedSchemas": [],
        "receivedRequestSummary": _received_summary(req),
    }


def boolean_response(exists: bool, req: BeaconRequest) -> dict:
    return {"meta": build_meta("boolean", req), "responseSummary": {"exists": exists}}


def count_response(count: int, req: BeaconRequest) -> dict:
    return {
        "meta": build_meta("count", req),
        "responseSummary": {"exists": count > 0, "numTotalResults": count},
    }


def resultsets_response(
    result_sets: list[dict], total: int, req: BeaconRequest
) -> dict:
    return {
        "meta": build_meta("record", req),
        "responseSummary": {"exists": total > 0, "numTotalResults": total},
        "response": {"resultSets": result_sets},
    }


def collections_response(
    collections: list[dict], total: int, req: BeaconRequest
) -> dict:
    return {
        "meta": build_meta("record", req),
        "responseSummary": {"exists": total > 0, "numTotalResults": total},
        "response": {"collections": collections},
    }


def result_set(set_id: str, set_type: str, results: list[dict], count: int) -> dict:
    return {
        "id": set_id,
        "setType": set_type,
        "exists": count > 0,
        "resultsCount": count,
        "results": results,
    }


class PrettyJSONResponse(JSONResponse):
    """Indented JSON so raw Beacon responses are readable in a browser."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, indent=2, ensure_ascii=False).encode("utf-8")


beacon_router = APIRouter(
    prefix="/beacon", tags=["beacon"], default_response_class=PrettyJSONResponse
)


def _info_payload() -> dict:
    return {
        "meta": build_meta("record"),
        "response": {
            "id": BEACON_ID,
            "name": "seqout Beacon",
            "apiVersion": API_VERSION,
            "environment": ENVIRONMENT,
            "organization": ORGANIZATION,
            "description": BEACON_DESCRIPTION,
            "version": API_VERSION,
        },
    }


@beacon_router.get("/")
@beacon_router.get("/info")
def beacon_info() -> dict:
    return _info_payload()


@beacon_router.get("/service-info")
def service_info() -> dict:
    return {
        "id": BEACON_ID,
        "name": "seqout Beacon",
        "type": {"group": "org.ga4gh", "artifact": "beacon", "version": API_VERSION},
        "description": BEACON_DESCRIPTION,
        "organization": {
            "name": ORGANIZATION["name"],
            "url": ORGANIZATION["welcomeUrl"],
        },
        "contactUrl": ORGANIZATION["contactUrl"],
        "version": API_VERSION,
        "environment": ENVIRONMENT,
    }


@beacon_router.get("/configuration")
def configuration() -> dict:
    return {
        "meta": build_meta("record"),
        "response": {
            "$schema": "https://raw.githubusercontent.com/ga4gh-beacon/beacon-v2/main/framework/json/configuration/beaconConfigurationSchema.json",
            "maturityAttributes": {"productionStatus": "DEV"},
            "securityAttributes": {
                "defaultGranularity": "record",
                "securityLevels": ["PUBLIC"],
            },
            "entryTypes": ENTRY_TYPES,
        },
    }


@beacon_router.get("/entry_types")
def entry_types() -> dict:
    return {"meta": build_meta("record"), "response": {"entryTypes": ENTRY_TYPES}}


@beacon_router.get("/map")
def beacon_map() -> dict:
    def ep(path: str) -> dict:
        return {
            "rootUrl": f"{BEACON_ROOT}{path}",
            "singleEntryUrl": f"{BEACON_ROOT}{path}?id={{id}}",
            "openAPIEndpointsDefinition": None,
        }

    return {
        "meta": build_meta("record"),
        "response": {
            "endpointSets": {
                "dataset": {"entryType": "dataset", **ep("/datasets")},
                "biosample": {"entryType": "biosample", **ep("/biosamples")},
                "individual": {"entryType": "individual", **ep("/individuals")},
                "run": {"entryType": "run", **ep("/runs")},
            }
        },
    }


_ONTOLOGY_RESOURCES = [
    {
        "id": "mondo",
        "name": "Mondo Disease Ontology",
        "nameSpacePrefix": "MONDO",
        "iriPrefix": "http://purl.obolibrary.org/obo/MONDO_",
    },
    {
        "id": "uberon",
        "name": "Uberon multi-species anatomy ontology",
        "nameSpacePrefix": "UBERON",
        "iriPrefix": "http://purl.obolibrary.org/obo/UBERON_",
    },
    {
        "id": "cl",
        "name": "Cell Ontology",
        "nameSpacePrefix": "CL",
        "iriPrefix": "http://purl.obolibrary.org/obo/CL_",
    },
    {
        "id": "efo",
        "name": "Experimental Factor Ontology",
        "nameSpacePrefix": "EFO",
        "iriPrefix": "http://www.ebi.ac.uk/efo/EFO_",
    },
]
_filtering_terms_cache: list[dict] | None = None


def _load_filtering_terms() -> list[dict]:
    """Read the precomputed beacon_filtering_terms table (built offline by
    pysradb-ingestion/enrichment/build_ontology_closure.py); cached in-process."""
    terms: list[dict] = []
    for r in query(
        "SELECT term_type, id, label, scopes, vals FROM beacon_filtering_terms "
        "ORDER BY term_type, id"
    ):
        t: dict[str, Any] = {
            "type": r["term_type"],
            "id": r["id"],
            "label": r["label"] or r["id"],
            "scopes": r["scopes"],
        }
        if r["vals"]:
            t["values"] = r["vals"]
        terms.append(t)
    return terms


@beacon_router.get("/filtering_terms")
def filtering_terms(skip: int = 0, limit: int = 0) -> dict:
    global _filtering_terms_cache
    if _filtering_terms_cache is None:
        _filtering_terms_cache = _load_filtering_terms()
    terms = _filtering_terms_cache
    if limit:
        terms = terms[skip * limit : skip * limit + limit]
    return {
        "meta": build_meta("record"),
        "response": {"filteringTerms": terms, "resources": _ONTOLOGY_RESOURCES},
    }


def _dataset_record(source: dict) -> dict:
    """Map a SOURCES entry to a Beacon dataset (collection) record (id+name required)."""
    return {
        "id": source["id"],
        "name": source["name"],
        "description": source["description"],
        "externalUrl": ORGANIZATION["welcomeUrl"],
    }


async def _query_datasets(request: Request, body: dict | None) -> dict:
    req = await parse_request(request, body)
    # datasets are the 5 source archives; they carry no ontology facets, so a
    # sample/biosample filter can't match one. Return an honest empty set rather
    # than silently ignoring filters and reporting exists=true / all 5.
    if req.filters:
        rows: list = []
    else:
        rows = [s for s in SOURCES if req.entry_id is None or s["id"] == req.entry_id]
    total = len(rows)
    if req.granularity == "boolean":
        return boolean_response(total > 0, req)
    if req.granularity == "count":
        return count_response(total, req)
    page = rows[req.offset : req.offset + req.limit] if req.limit else rows
    return collections_response([_dataset_record(s) for s in page], total, req)


@beacon_router.get("/datasets")
async def datasets_get(request: Request) -> dict:
    return await _query_datasets(request, None)


@beacon_router.post("/datasets")
async def datasets_post(request: Request, body: dict = Body(default=None)) -> dict:
    return await _query_datasets(request, body)


def individual_id_for(sample: str) -> str:
    return f"IND-{sample}"


def _term(tid: str | None, label: str | None) -> dict | None:
    return {"id": tid, "label": label or tid} if tid else None


_NORMAL_DISEASE = {
    "normal",
    "healthy",
    "control",
    "wild type",
    "wild-type",
    "none",
    "na",
    "non-tumor",
    "non tumor",
    "reference",
}


def biosample_record(row: dict) -> dict:
    disease_term = _term(
        row.get("disease_ontology_id"), row.get("disease_ontology_name")
    )
    disease_txt = (
        (row.get("disease") or row.get("disease_ontology_name") or "").strip().lower()
    )
    is_abnormal = bool(disease_term) and disease_txt not in _NORMAL_DISEASE

    rec: dict[str, Any] = {
        "id": row["sample"],
        "individualId": individual_id_for(row["sample"]),
        # required
        "biosampleStatus": (
            {"id": "EFO:0009655", "label": "abnormal sample"}
            if is_abnormal
            else {"id": "EFO:0009654", "label": "reference sample"}
        ),
        "sampleOriginType": (
            {"id": "OBI:0001876", "label": "cell culture"}
            if row.get("cell_line")
            else {"id": "OBI:0001479", "label": "specimen from organism"}
        ),
    }
    tissue_term = _term(row.get("tissue_ontology_id"), row.get("tissue_ontology_name"))
    if tissue_term:
        rec["sampleOriginDetail"] = tissue_term
    if is_abnormal:
        rec["histologicalDiagnosis"] = disease_term
        rec["diseases"] = [{"diseaseCode": disease_term}]
    info: dict[str, Any] = {"source": _source_of(row["sample"])}
    for key, col in (("organism", "organism"), ("taxid", "taxid")):
        if row.get(col):
            info[key] = row[col]
    for key, idc, namec in (
        ("cellType", "cell_type_ontology_id", "cell_type_ontology_name"),
        ("assay", "assay_ontology_id", "assay_ontology_name"),
        (
            "developmentStage",
            "development_stage_ontology_id",
            "development_stage_ontology_name",
        ),
    ):
        t = _term(row.get(idc), row.get(namec))
        if t:
            info[key] = t
    rec["info"] = info
    return rec


def _source_of(sample: str) -> str:
    if sample.startswith("GSM"):
        return "GEO"
    if sample.startswith(("SR", "DR")):
        return "SRA"
    if sample.startswith("ER"):
        return "ENA"
    return "other"


def _sample_exists(where: str, params: list) -> bool:
    rows = query(
        f"SELECT EXISTS(SELECT 1 FROM ontology_samples_v4 WHERE {where}) AS e",
        tuple(params),
    )
    return bool(rows[0]["e"])


def _sample_total(where: str, params: list) -> int:
    """Total matching biosamples; unfiltered uses a fast reltuples estimate (full 13.6M count too slow), filtered/id an exact count."""
    if where == "TRUE":
        rows = query(
            "SELECT GREATEST(reltuples, 0)::bigint AS n FROM pg_class "
            "WHERE relname = 'ontology_samples_v4'"
        )
        n = int(rows[0]["n"]) if rows else 0
        if n == 0:  # stats missing (pre-ANALYZE) -> exact count; never return -1
            rows = query("SELECT count(*) AS n FROM ontology_samples_v4")
            n = int(rows[0]["n"])
        return n
    rows = query(
        f"SELECT count(*) AS n FROM ontology_samples_v4 WHERE {where}", tuple(params)
    )
    return int(rows[0]["n"])


def _grouped_by_source(page: list, mapper) -> list[dict]:
    """Group the returned page by source into resultSets."""
    by_src: dict[str, list] = {}
    for r in page:
        by_src.setdefault(_source_of(r["sample"]), []).append(mapper(r))
    set_type = "biosample" if mapper is biosample_record else "individual"
    return [
        result_set(src, set_type, recs, len(recs))
        for src, recs in sorted(by_src.items())
    ]


async def _query_biosamples(request: Request, body: dict | None) -> dict:
    req = await parse_request(request, body)
    if req.entry_id:
        where, params = "sample = %s", [req.entry_id]
    else:
        where, params = bq.build_biosample_where(req.filters)

    if req.granularity == "boolean":
        return boolean_response(_sample_exists(where, params), req)
    total = _sample_total(where, params)
    if req.granularity == "count":
        return count_response(total, req)

    page = query(
        f"SELECT {bq.BIOSAMPLE_COLUMNS} FROM ontology_samples_v4 WHERE {where} "
        f"ORDER BY study_accession, sample OFFSET %s LIMIT %s",
        tuple(params) + (req.offset, req.limit),
    )
    return resultsets_response(_grouped_by_source(page, biosample_record), total, req)


@beacon_router.get("/biosamples")
async def biosamples_get(request: Request) -> dict:
    return await _query_biosamples(request, None)


@beacon_router.post("/biosamples")
async def biosamples_post(request: Request, body: dict = Body(default=None)) -> dict:
    return await _query_biosamples(request, body)


_SEX_TERMS = {
    "female": {"id": "NCIT:C16576", "label": "Female"},
    "f": {"id": "NCIT:C16576", "label": "Female"},
    "male": {"id": "NCIT:C20197", "label": "Male"},
    "m": {"id": "NCIT:C20197", "label": "Male"},
}
_SEX_UNKNOWN = {"id": "NCIT:C17998", "label": "Unknown"}


def individual_record(row: dict) -> dict:
    rec: dict[str, Any] = {
        "id": individual_id_for(row["sample"]),
        "sex": _SEX_TERMS.get((row.get("sex") or "").strip().lower(), _SEX_UNKNOWN),
    }
    disease_term = _term(
        row.get("disease_ontology_id"), row.get("disease_ontology_name")
    )
    disease_txt = (
        (row.get("disease") or row.get("disease_ontology_name") or "").strip().lower()
    )
    if disease_term and disease_txt not in _NORMAL_DISEASE:
        rec["diseases"] = [{"diseaseCode": disease_term}]
    info: dict[str, Any] = {
        "source": _source_of(row["sample"]),
        "derivedFromBiosample": row["sample"],
    }
    if row.get("organism"):
        info["organism"] = row["organism"]
    rec["info"] = info
    return rec


async def _query_individuals(request: Request, body: dict | None) -> dict:
    req = await parse_request(request, body)
    if req.entry_id:
        # individual id is IND-<sample>; strip prefix to look up the biosample row
        sample = req.entry_id[4:] if req.entry_id.startswith("IND-") else req.entry_id
        where, params = "sample = %s", [sample]
    else:
        where, params = bq.build_biosample_where(req.filters)

    if req.granularity == "boolean":
        return boolean_response(_sample_exists(where, params), req)
    total = _sample_total(where, params)
    if req.granularity == "count":
        return count_response(total, req)

    page = query(
        f"SELECT {bq.BIOSAMPLE_COLUMNS} FROM ontology_samples_v4 WHERE {where} "
        f"ORDER BY study_accession, sample OFFSET %s LIMIT %s",
        tuple(params) + (req.offset, req.limit),
    )
    return resultsets_response(_grouped_by_source(page, individual_record), total, req)


@beacon_router.get("/individuals")
async def individuals_get(request: Request) -> dict:
    return await _query_individuals(request, None)


@beacon_router.post("/individuals")
async def individuals_post(request: Request, body: dict = Body(default=None)) -> dict:
    return await _query_individuals(request, body)


_RUN_SRA_SQL = """
    SELECT r.accession AS id, e.samples AS samples, e.library_source, e.library_strategy,
           e.library_selection, e.library_layout, e.platform, e.instrument_model, e.study
    FROM sra_runs r JOIN sra_experiments e ON e.accession = r.experiment
    WHERE r.accession = %s
"""
_RUN_ENA_SQL = """
    SELECT run_accession AS id, sample_accession, library_source, library_strategy,
           library_selection, library_layout, instrument_platform AS platform,
           instrument_model, study_accession AS study, first_public
    FROM ena_experiments WHERE run_accession = %s
"""
_RUN_GSA_SQL = """
    SELECT run_accession AS id, sample_accession, library_source, library_strategy,
           library_selection, library_layout, instrument_platform AS platform,
           instrument_model, study_accession AS study, first_public
    FROM gsa_experiments WHERE run_accession = %s
"""
_RUN_BROWSE_SQL = """
    SELECT r.accession AS id, e.samples AS samples, e.library_source, e.library_strategy,
           e.library_selection, e.library_layout, e.platform, e.instrument_model, e.study
    FROM sra_runs r JOIN sra_experiments e ON e.accession = r.experiment
    ORDER BY r.accession OFFSET %s LIMIT %s
"""

_run_total_cache: int | None = None


def _run_source(rid: str) -> str:
    if rid.startswith("ERR"):
        return "ENA"
    if rid.startswith(("CRR", "HRR")):
        return "GSA"
    return "SRA"  # SRR / DRR


def _run_record(row: dict, source: str) -> dict:
    """Shape a run to the GA4GH Beacon v2 run defaultSchema."""
    rec: dict[str, Any] = {"id": row["id"]}
    biosample = (
        (row.get("samples") or [None])[0]
        if source == "SRA"
        else row.get("sample_accession")
    )
    if biosample:
        rec["biosampleId"] = biosample
        rec["individualId"] = individual_id_for(biosample)
    for key, col in (
        ("libraryStrategy", "library_strategy"),
        ("librarySelection", "library_selection"),
        ("libraryLayout", "library_layout"),
        ("platform", "platform"),
    ):
        if row.get(col):
            rec[key] = row[col]
    info: dict[str, Any] = {"source": source}
    if row.get("library_source"):
        info["librarySource"] = row["library_source"]
    if row.get("instrument_model"):
        info["instrumentModel"] = row["instrument_model"]
    if row.get("study"):
        info["studyAccession"] = row["study"]
    if row.get("first_public"):
        info["releaseDate"] = str(row["first_public"])
        info["dateSource"] = "first_public"
    rec["info"] = info
    return rec


def _run_by_id(rid: str) -> dict | None:
    source = _run_source(rid)
    sql = {"ENA": _RUN_ENA_SQL, "GSA": _RUN_GSA_SQL}.get(source, _RUN_SRA_SQL)
    rows = query(sql, (rid,))
    return _run_record(rows[0], source) if rows else None


def _run_total() -> int:
    """Total for the unfiltered runs browse; counts sra_runs only, matching what browse pages (else numTotalResults is unreachable by pagination). reltuples estimate (exact over 42M too slow), cached."""
    global _run_total_cache
    if _run_total_cache is None:
        rows = query(
            "SELECT GREATEST(reltuples, 0)::bigint AS n FROM pg_class "
            "WHERE relname = 'sra_runs'"
        )
        n = int(rows[0]["n"]) if rows else 0
        if n == 0:  # stats missing (pre-ANALYZE) -> exact count; never return -1
            rows = query("SELECT count(*) AS n FROM sra_runs")
            n = int(rows[0]["n"])
        _run_total_cache = n
    return _run_total_cache


async def _query_runs(request: Request, body: dict | None) -> dict:
    req = await parse_request(request, body)

    if req.entry_id:
        rec = _run_by_id(req.entry_id)
        total = 1 if rec else 0
        if req.granularity == "boolean":
            return boolean_response(total > 0, req)
        if req.granularity == "count":
            return count_response(total, req)
        rs = [result_set(_run_source(req.entry_id), "run", [rec] if rec else [], total)]
        return resultsets_response(rs, total, req)

    if req.filters:
        if req.granularity == "boolean":
            return boolean_response(False, req)
        if req.granularity == "count":
            return count_response(0, req)
        return resultsets_response([], 0, req)

    total = _run_total()
    if req.granularity == "boolean":
        return boolean_response(total > 0, req)
    if req.granularity == "count":
        return count_response(total, req)
    page = query(_RUN_BROWSE_SQL, (req.offset, req.limit))
    by_src: dict[str, list] = {}
    for r in page:
        by_src.setdefault(_run_source(r["id"]), []).append(
            _run_record(r, _run_source(r["id"]))
        )
    rs = [
        result_set(src, "run", recs, len(recs)) for src, recs in sorted(by_src.items())
    ]
    return resultsets_response(rs, total, req)


@beacon_router.get("/runs")
async def runs_get(request: Request) -> dict:
    return await _query_runs(request, None)


@beacon_router.post("/runs")
async def runs_post(request: Request, body: dict = Body(default=None)) -> dict:
    return await _query_runs(request, body)
