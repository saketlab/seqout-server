"""Search paging, compact rows and the MCP layer."""

import difflib
import json
import logging
import re
from typing import Any

import httpx
from mcp.types import TextContent

from accessions import (
    AE_PATTERN,
    BIOSAMPLE_PATTERN,
    EXPERIMENT_PATTERN,
    GEA_PATTERN,
    GEO_SAMPLE_PATTERN,
    GEO_SERIES_PATTERN,
    GSA_EXPERIMENT_PATTERN,
    GSA_RUN_PATTERN,
    GSA_SAMPLE_PATTERN,
    GSA_STUDY_PATTERN,
    PRJ_PATTERN,
    RUN_PATTERN,
    SAMPLE_PATTERN,
    STUDY_PATTERN,
    SUBMISSION_REGEX,
)
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from mcp_param_docs import param_doc
from queries import SEARCH_PAGE_SIZE

logger = logging.getLogger(__name__)

COMPACT_SUMMARY_CHARS = 300
COMPACT_FIELDS = (
    "accession",
    "alias",
    "source",
    "title",
    "summary",
    "summary_truncated",
    "published_at",
    "updated_at",
    "organisms",
    "library_strategies",
    "instrument_models",
    "countries",
    "pmid",
    "doi",
    "rank",
)

# drops submitter aliases such as UUIDs
_ALIAS_ACCESSION = re.compile(r"^(?:[A-Z]{2,6}\d+|E-[A-Z]{4}-\d+)$")


def alias_accessions(aliases: list[str], own: str | None) -> list[str]:
    """Linked accessions for a search row, without the row's own accession."""
    return [a for a in dict.fromkeys(aliases) if a != own and _ALIAS_ACCESSION.match(a)]


def split_project_alias(row: dict) -> dict:
    """MCP: alias becomes the linked-accession list, as on a search row."""
    if "linked_accessions" not in row:
        return row
    row = dict(row)
    row["alias"] = row.pop("linked_accessions")
    return row


def normalize_mcp_aliases(structured: dict) -> dict:
    """Apply split_project_alias to top-level, nested project, and result rows."""
    out = split_project_alias(structured)
    project = out.get("project")
    if isinstance(project, dict) and "linked_accessions" in project:
        out = {**out, "project": split_project_alias(project)}
    rows = out.get("results")
    if isinstance(rows, list) and any(
        isinstance(r, dict) and "linked_accessions" in r for r in rows
    ):
        out = {
            **out,
            "results": [
                split_project_alias(r) if isinstance(r, dict) else r for r in rows
            ],
        }
    return out


def make_cursor(row: dict, sorted_page: bool) -> dict:
    if sorted_page:
        return {"sort_value": row.get("sort_val"), "accession": row["accession"]}
    return {"rank": row.get("rank"), "accession": row["accession"]}


def finish_page(
    rows: list[dict],
    *,
    sorted_page: bool,
    first_page: bool,
    offset: int | None = None,
    total: int | None = None,
) -> dict:
    """Trim the probe row and set has_more, next_cursor and total.

    Without a SQL count, total is known only for a complete first page."""
    has_more = len(rows) > SEARCH_PAGE_SIZE
    rows = rows[:SEARCH_PAGE_SIZE]
    # an empty page past the end says nothing about the rows before the offset
    if total is None and not has_more and first_page and (rows or not offset):
        total = (offset or 0) + len(rows)
    return {
        "results": rows,
        "total": total,
        "total_is_known": total is not None,
        "has_more": has_more,
        "next_cursor": make_cursor(rows[-1], sorted_page) if has_more else None,
    }


def sql_total(rows: list[dict], *, first_page: bool = True) -> int | None:
    if rows:
        return rows[0].get("total_count")
    return 0 if first_page else None


def compact_row(row: dict) -> dict:
    out = {k: row.get(k) for k in COMPACT_FIELDS if k != "summary_truncated"}
    summary = out.get("summary")
    truncated = False
    if isinstance(summary, str) and len(summary) > COMPACT_SUMMARY_CHARS:
        out["summary"] = summary[:COMPACT_SUMMARY_CHARS]
        truncated = True
    out["summary_truncated"] = truncated
    return out


def shape_search(
    result: dict, limit: int | None, fields: str, sorted_page: bool
) -> dict:
    """Apply limit and fields; never mutates result, which may be cached."""
    rows = result.get("results") or []
    out = dict(result)
    if limit is not None and len(rows) > limit:
        rows = rows[:limit]
        out["has_more"] = True
        out["next_cursor"] = make_cursor(rows[-1], sorted_page)
    if fields == "compact":
        rows = [compact_row(r) for r in rows]
        corr = out.get("correction")
        if isinstance(corr, dict) and corr.get("extra_results"):
            out["correction"] = {
                **corr,
                "extra_results": [compact_row(r) for r in corr["extra_results"]],
            }
    out["results"] = rows
    return out


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def line_json(obj: Any) -> str:
    """JSON with one top-level list element per line, for clients that read by line."""
    if not isinstance(obj, dict):
        return _dumps(obj)
    parts = []
    for key, value in obj.items():
        if (
            isinstance(value, list)
            and len(value) > 1
            and all(isinstance(v, dict) for v in value)
        ):
            body = "[\n" + ",\n".join(_dumps(v) for v in value) + "\n]"
        else:
            body = _dumps(value)
        parts.append(f"{_dumps(key)}:{body}")
    return "{" + ",".join(parts) + "}\n"


# web UI assets, files, admin, unpublished grid filters
MCP_EXCLUDED_OPERATION_IDS = {
    "health_check",
    "purge_cache",
    "get_projects_atlas_v1",
    "get_atlas_labels_v1",
    "get_clusters",
    "get_map_meta",
    "get_map_labels",
    "get_map_countries",
    "get_map_filter",
    "get_map_tile",
    "get_geo_series_neighbors_coords",
    "search_geo",
    "search_sra",
    "search_facets",
    "deep_dive_terms",
    "deep_dive_children",
    "download_study_runs_tsv",
    "download_study_bams_tsv",
    "download_study_metadata_csv",
    "download_sample_detail_csv",
    "download_by_query",
    "bulk_metadata",
    "bulk_project_metadata",
    "bulk_project_samples_metadata",
    "list_organisms",
    # Excluded until get_sample_microbes aggregates before applying LIMIT.
    "get_sample_microbes",
    "sitemap_counts",
    "sitemap_accessions",
    "get_country_accessions",
    "get_country_points",
    "get_ae_experiment_samples",
    "get_sc_quality_samples",
    "get_platform_filters",
    "get_global_contributions",
    "get_global_contribution_filters",
    "download_pentimento_detections_csv",
    "get_project_overlap",
    "find_study_experiments",
    "find_study_runs",
    "find_geo_series_samples",
    # the batch route takes this tool name
    "convert_accession",
}

MCP_TOOL_NAMES = {"convert_accessions": "convert_accession"}

# unlisted but callable for backwards compatibility; calls are logged
MCP_DEPRECATED_TOOLS = {
    "get_project_xref": "convert_accession",
    "resolve_prj_project": "convert_accession",
    "resolve_accession_project": "convert_accession",
    "get_project_metadata": "get_project(fields=summary)",
    "get_common_name_by_scientific_name": "get_common_names_by_scientific_names",
    "get_sample": "get_sample_detail",
    "get_project_metadata_rows": "get_metadata_rows",
    "get_experiment_metadata_rows": "get_metadata_rows",
    "get_run_metadata_rows": "get_metadata_rows",
    "get_project_enriched": "get_enriched_metadata",
    "get_experiment_enriched": "get_enriched_metadata",
    "get_run_enriched": "get_enriched_metadata",
    "get_project_single_cell_status": "get_project_single_cell(view=status)",
    "get_project_single_cell_summary": "get_project_single_cell(view=summary)",
    "list_library_strategies": "list_filter_values(field=library_strategy)",
    "list_instrument_models": "list_filter_values(field=instrument_model)",
    "list_journals": "list_filter_values(field=journal)",
    "list_centers": "list_filter_values(field=center)",
    "get_longread_summary": "get_collection_overview(collection=longread)",
    "get_longread_facets": "get_collection_overview(collection=longread)",
    "get_singlecell_summary": "get_collection_overview(collection=singlecell)",
    "get_singlecell_facets": "get_collection_overview(collection=singlecell)",
    "get_perturbation_summary": "get_collection_overview(collection=perturbation)",
    "get_perturbation_facets": "get_collection_overview(collection=perturbation)",
    "get_spatial_summary": "get_collection_overview(collection=spatial)",
    "get_spatial_facets": "get_collection_overview(collection=spatial)",
    "get_country_summary": "get_collection_overview(collection=country)",
    "get_country_facets": "get_collection_overview(collection=country)",
    "get_tissue_summary": "get_collection_overview(collection=tissue)",
    "get_tissue_facets": "get_collection_overview(collection=tissue)",
    "get_disease_summary": "get_collection_overview(collection=disease)",
    "get_disease_facets": "get_collection_overview(collection=disease)",
}

# REST keeps the route defaults; MCP caps the page size.
MCP_ARG_DEFAULTS: dict[str, dict[str, Any]] = {
    "search_all": {"limit": 50, "fields": "compact"},
    "search_structured": {"limit": 50, "fields": "compact"},
    "search_nl": {"limit": 50, "fields": "compact"},
    "get_project": {"include_neighbors": False},
    "get_sample_detail": {"include_neighbors": False},
    "ontology_term": {"children": False},
    "get_metadata_rows": {"limit": 50},
    "get_project_metadata_rows": {"limit": 50},
    "get_experiment_metadata_rows": {"limit": 50},
    "get_enriched_metadata": {"limit": 50},
    "get_project_enriched": {"limit": 50},
    "get_study_download_manifest": {"limit": 50},
    "get_study_experiments": {"limit": 50, "envelope": True},
    "get_study_samples": {"limit": 50},
    "get_geo_series_samples": {"limit": 50, "envelope": True},
    "get_project_single_cell": {"limit": 50},
    "search_enriched_samples": {"limit": 50},
    "search_author_projects": {"limit": 50},
    "list_single_cell_studies": {"limit": 50},
    "get_organism_totals": {"limit": 50},
    "get_tissue_microbes": {"limit": 100},
    "list_filter_values": {"limit": 50},
    "list_journals": {"limit": 50},
    "list_centers": {"limit": 50},
}

MCP_INSTRUCTIONS = """\
seqout searches study metadata from NCBI GEO, SRA/ENA/DDBJ, ArrayExpress, DDBJ GEA \
and GSA. Read a tool's description before its first call.

## Which search tool
- `search_all`: keywords or boolean query (`("aging" OR aged) immun*`). `db=` limits the archive.
- `search_structured`: explicit filters (organism, library_strategy, platform, dates, country) or `count_by` buckets.
- `search_nl`: a full English sentence. Always read `parsed.warnings`.
- `search_enriched_samples`: SAMPLES (not studies) by tissue, disease, age, sex, cell type or microbe.
- Collections for "what data exists on X": call `get_collection_overview` for totals + valid filter values, \
then `get_disease_projects`, `get_tissue_projects`, `get_singlecell_projects`, `get_spatial_projects`, \
`get_perturbation_projects`, `get_longread_projects`, or `get_country_projects`.
Filter or sort rows you already have before running another search.

## Search rows & paging
Each row includes `published_at` (release date), `updated_at`, `alias` (linked accessions in other archives), \
organisms, library_strategies, pmid and doi. Do not call `get_project` per row for those fields. Mirrors are folded. \
MCP returns 50 compact rows (summaries capped at 300 chars); raise `limit` (1-200) or set `fields=full` if needed.

Follow `next_cursor`: `cursor_rank`+`cursor_acc` for relevance order, `cursor_sort`+`cursor_acc` with `sortby`. \
`has_more=false` (`next_cursor=null`) is the last page. A null `total` (`total_is_known=false`) means unknown, not zero. \
Use `sortby` only for newest, oldest or most cited.

## Dates
Release date: `search_all` (`date_from`/`date_to`/`year_*`), `search_structured` (`published_after`/`published_before`/`year_*`), \
`count_by=year`, `sortby=year`. Linked paper date: `pub_date_*`.

## Accessions & metadata layers
- Projects (`GSE`, `E-XXXX-N`, `E-GEAD-N`, `SRP/ERP/DRP`, `CRA/HRA`, `PRJ*`): `get_project`.
- Samples/experiments (`GSM`, `SRX/ERX/DRX`, `SRS/ERS/DRS`, `SAM*`): `get_sample_detail`. Runs (`SRR/ERR/DRR`): `get_run_checksums` or `get_run`.
- Raw flattened rows (FASTQ URLs, `sample_attribute:*`): `get_metadata_rows`. Ontology-normalized fields: `get_enriched_metadata`.
- Cross-archive links or parent project: `convert_accession` (one or many). Unknown shape: `classify_accession`. Submission (`SRA/ERA/DRA`+digits): `resolve_submission_studies`.
- `exists: false` or 404 means absent from seqout; never invent datasets.

## Files & errors
FASTQ files belong to runs (`get_study_download_manifest` for a study); GEO/ArrayExpress processed files use `get_supplementary_files`. \
A 504 is a timeout, never an empty result; narrow filters and retry.
"""

_NOTE_FASTQ = (
    "FASTQ files belong to runs; GEO/ArrayExpress projects only have FASTQs via a linked "
    "SRA/ENA/DRA study (`alias` or `convert_accession`) — use `get_supplementary_files` for "
    "their processed files. Resolve PRJ* via `convert_accession` to SRP/ERP/DRP first. "
    "Paired runs with `fastq_is_interleaved=true` (1 FASTQ) are not R1/R2 pairs; use "
    "`recommended_fastq_source`, `conversion_command`, and `expected_fastq_paths` (sracha split-files)."
)

_NOTE_MICROBE_EVIDENCE = (
    "Microbial calls are evidence, not a diagnosis (unitigs across >=20% reference, >=50% for "
    "MLV/MMTV/HIV1/HTLV1). NULL or `measurable` false/null means unmeasured; `[]` means measured "
    "and negative. `kmer_mass` is relative within one run. Viral/bacterial totals exclude "
    "`spike_in_control`, `negative_control` and `bacterial_background`. Do not add unfiltered "
    "barcode `cells` counts to filtered cell counts."
)

# MCP descriptions that replace the REST docstring.
MCP_TOOL_DESCRIPTIONS: dict[str, str] = {
    "search_all": (
        'Full-text or boolean search (`("aging" OR aged) immun*`) across GEO, SRA, ArrayExpress, '
        "ENA, GSA, DRA and GEA. Mirrors fold onto one row (GEO > SRA > rest). Set `db=` only to "
        "limit archives; only this tool finds DDBJ DRA/GEA (`db=dra|gea`). `country` accepts names "
        "or ISO-2 codes. `long_read=true` keeps PacBio/ONT studies. Surface `suggestions`/`correction` "
        "on possible typos."
    ),
    "search_structured": (
        "Filtered search or `count_by` bucket counts across GEO, SRA, ArrayExpress, ENA and GSA "
        "(no DRA/GEA). `q` accepts plain keywords (ontology-expanded) or boolean syntax (literal). "
        "`organism` and `library_strategy` accept lists; `country` accepts names or ISO-2 codes. "
        "Archive exclusions: `library_strategy` drops GEO; `platform` drops GEO/AE; `country` drops AE; "
        "`instrument_model` drops AE/ENA/GSA; `multi_platform` keeps GEO/SRA only. In `count_by`, "
        "ENA/GSA lack `instrument_model`/`library_strategy` (and ENA lacks `country`). "
        "`sample_tissue`/`sample_disease`/`sample_cell_type` return studies with >=1 matching sample "
        "(inspect via `get_enriched_metadata`; for sample rows use `search_enriched_samples`). "
        "Discover filter values via `list_filter_values` and `search_organisms`."
    ),
    "search_nl": (
        "Rule-based English query parser + search (not an LLM). Always report `parsed.warnings` to "
        "the user. `parsed.excluded` lists negations ('excluding mouse') that are NOT applied; filter "
        "rows or use boolean `search_all`. For DDBJ/DRA/GEA use `search_all(db=dra|gea)`. Use "
        "`dry_run=true` to inspect parsed filters; prefer `search_structured` when filters are explicit."
    ),
    "search_expansion": (
        "Show the ontology synonyms used by plain-keyword search and how many exist before the "
        "variant cap. Use when a user asks why results are broad or narrow. Boolean queries are never expanded."
    ),
    "search_highlight": (
        "Highlight query stems/synonyms in a project's title, summary and overall design. An empty "
        "`words` list does not mean a false match (the hit may be in contributors or samples)."
    ),
    "get_project": (
        "Full metadata for one project (`GSE`, `SRP/ERP/DRP`, `PRJ*`, `CRA/HRA`, `E-XXXX-N`, `E-GEAD-N`). "
        "`fields=summary` returns only accession, title and description. MCP omits similarity neighbors "
        "(`include_neighbors=false`; use `get_project_similar`). `alias` is the linked-accession list; "
        "`submitter_alias` is submitter free text (or null). `HR*` (GSA-Human) is usually access-controlled."
    ),
    "get_sample_detail": (
        "Sample/experiment metadata, raw attributes, runs, and slim parent `project` (omits neighbors; "
        "use `get_project_similar`). Accepts `GSM`, `SRX/ERX/DRX`, `SRS/ERS/DRS`, `SAM*`."
    ),
    "get_study_experiments": (
        "Paginated experiments for an SRA/ENA/DRA/GSA study (`SRP/ERP/DRP/PRJ*/CRA/HRA`). "
        "Returns `{study_accession, total, count, offset, has_more, next_offset, experiments}`."
    ),
    "get_study_samples": (
        "Paginated distinct samples referenced by an SRA/DRA/GEA study (`SRP/DRP/ERP/PRJ*/E-GEAD-N`). "
        "Returns `{study_accession, total, count, offset, has_more, next_offset, samples}`."
    ),
    "get_geo_series_samples": (
        "Paginated samples for a GEO series (`GSE`), ArrayExpress experiment (`E-XXXX-N`), or "
        "DDBJ GEA series (`E-GEAD-N`). Returns `{accession, total, count, offset, has_more, next_offset, samples}`."
    ),
    "get_study_runs": (
        "First 500 runs plus `total_runs`, `paired_runs`, `single_runs` and `total_fastq_bytes`. "
        "Paired runs join `fastq_ftp`/`fastq_bytes`/`fastq_md5` with `;`. Page larger studies via "
        "`get_study_download_manifest`. GSA runs (`CRR/HRR`) have no single-run tool; read them here."
    ),
    "get_study_download_manifest": (
        "Paginated per-file download manifest (`url`, `bytes`, `md5`, `run_accession`, `download_command`) "
        "for a study; follow `next_cursor`. " + _NOTE_FASTQ
    ),
    "download_study_script": (
        "Generate a ready-to-run bash download script for a study (`mode`: fastq, sra, sra_lite, s3, gcs). "
        "Use only when the user asks for a runnable script; otherwise use `get_study_download_manifest`."
    ),
    "get_run_checksums": (
        "Parsed FASTQ/SRA download URLs, byte sizes, MD5 checksums, and interleaved/conversion flags "
        "for one run (`SRR/ERR/DRR`). Prefer over `get_run` for download verification."
    ),
    "get_metadata_rows": (
        "Paginated raw flattened metadata rows for a project (`GSE`, `SRP/ERP/DRP`, `PRJ*`, `CRA/HRA`, "
        "`E-XXXX-N`, `E-GEAD-N`), experiment (`SRX/ERX/DRX`), or run (`SRR/ERR/DRR`). SRA/ENA/GSA "
        "returns one row per run with `sample_attribute:*`, FASTQ URLs, bytes and MD5; GEO/AE/GEA "
        "returns one row per sample. For ontology-normalized fields, use `get_enriched_metadata`."
    ),
    "get_enriched_metadata": (
        "Ontology-normalized sample metadata (`tissue`, `cell_type`, `disease`, `assay`, UBERON/CL/MONDO/EFO "
        "IDs, `low_confidence_tags`) for a project, experiment (`SRX/ERX/DRX`), or run (`SRR/ERR/DRR`). "
        "404 if not enriched — fall back to `get_metadata_rows` for raw archive attributes."
    ),
    "get_project_single_cell": (
        "Single-cell counts, QC (`sc_stats`), read-derived calls (preflightx), long-read chemistry, and "
        "microbial detections for a study. `view=status` checks single-cell classification + evidence "
        "(works for any study); `view=summary` gives study-level totals; `view=samples` (default) pages "
        "per-sample rows (compare with `n_samples_detailed` and note if truncated). 404 if absent from "
        "Pentimento (use `get_enriched_metadata`). In multiome studies, `RNA-Seq` and `ATAC-seq` are "
        "separate samples (`ssRNA-seq` is strand-specific, not single-cell). "
        + _NOTE_MICROBE_EVIDENCE
    ),
    "get_collection_overview": (
        "Corpus totals (`summary`) and valid filter values (`facets`) for any curated/ontology collection "
        "in one call (`include=both|summary|facets`). Call before `get_*_projects` to discover exact "
        "filter values and resolution metadata (`resolution`, `matched_labels`). "
        "Global collections (`longread`, `singlecell`, `perturbation`, `spatial`) omit `target`. "
        "Scoped collections require `target`: `country` (ISO-2, e.g. `IN`), `tissue` (name or UBERON CURIE), "
        "`disease` (`rare`, `nord`, disease name, or MONDO CURIE)."
    ),
    "get_longread_projects": (
        "Filter PacBio/Oxford Nanopore studies (mirrors folded; excludes Onso). `long_read_only=false` "
        "means hybrid short+long read; NULL means no experiment rows (GEO-only). `n_experiments` is "
        "long-read count vs `n_experiments_total`. Get filter values/totals from "
        "`get_collection_overview(collection='longread')`."
    ),
    "get_singlecell_projects": (
        "Filter curated single-cell studies by chemistry, cell/nucleus, modality, tissue, organism, "
        "matrix/FASTQ availability, and `perturbation_method` (text-detected named method; null means "
        "none detected). Get filter values/totals from `get_collection_overview(collection='singlecell')`."
    ),
    "get_perturbation_projects": (
        "Filter single-cell CRISPR/Perturb-seq/drug perturbation studies (rule-based; excludes germline "
        "knockouts, transgenics, dox/tamoxifen inducers). Prefer `min_confidence=medium`; warn users "
        "that `low` has many false positives. `compounds` come from sample treatments (empty when arms "
        "are in supplementary files); `title_compounds` alone is weak evidence (`low`). `cell_lines` and "
        "`sample_types` describe material. Get filter values from "
        "`get_collection_overview(collection='perturbation')`."
    ),
    "get_spatial_projects": (
        "Filter spatial transcriptomics studies (Visium, Xenium, MERFISH, CosMx, GeoMx DSP, ...). "
        "`platforms` is text-mined and often null — a mention does not prove use (e.g. comparison papers). "
        "`resolution` (`single-cell`, `spot`, `roi` for GeoMx DSP) maps to `technology` "
        "(`imaging`, `sequencing`, `hybrid`). Get filter values from "
        "`get_collection_overview(collection='spatial')`."
    ),
    "get_country_projects": (
        "Filter studies submitted from one ISO-2 country (`code`, e.g. `IN`, `US`). `has_fastq`/`has_sra` "
        "NULL means unknown. Get filter values/totals from "
        "`get_collection_overview(collection='country', target=...)`."
    ),
    "get_tissue_projects": (
        "Filter studies for a tissue (`term` name or UBERON CURIE + descendants). Check `resolution` and "
        "`matched_labels`; 404 means no UBERON match. Keyset-paginated via `cursor_sort`+`cursor_acc`. "
        "`has_fastq`/`has_sra` NULL means unknown. Get filter values/totals from "
        "`get_collection_overview(collection='tissue', target=...)`."
    ),
    "get_disease_projects": (
        "Filter studies for a disease via MONDO IDs and descendants. `collection='rare'` (NIH GARD, exact "
        "MONDO xref, offset-paged) or `'nord'` (NORD, name-matched, offset-paged) return sample-rollup rows "
        "with default `scope=human_primary` (report scope used; `ancestries` is submitter ethnicity text; "
        "`n_samples` is collection-matched count). Free-text names or MONDO CURIEs (e.g. `MONDO:0005148`) "
        "return cursor-paged study rows (`cursor_sort`+`cursor_acc`). Get valid filters from "
        "`get_collection_overview(collection='disease', target=...)`; invalid mode filters return 400."
    ),
    "get_project_longread_chemistry": (
        "Per-run PacBio/ONT chemistry calls for a study (`runs=[]` if none). Report `chemistry_confidence`: "
        "`exact` (BAM header), `declared` (ONT protocol text), `bucket` (instrument model), or `unknown`."
    ),
    "get_tissue_microbes": (
        "Paginated/filterable microbial detection rates per `(cohort, tissue, organism)` over Pentimento-"
        "quantified samples (`studies>=2`, `screened>=200`). Use one `cohort` (`human`, `mouse`, `all`) "
        "at a time. Always flag `is_background`, `class='culture'`, or `is_endogenous` (contamination/host, "
        "not infection)."
    ),
    "ontology_term": (
        "Look up an ontology graph node by exact NAME (any case; not CURIEs — use `get_disease_aliases` "
        "for MONDO IDs or `ontology_search` for fuzzy lookup). Returns source CURIEs, `MAPS_TO` synonyms, "
        "and optional direct children (`children=false` by default in MCP)."
    ),
    "search_enriched_samples": (
        "Search SAMPLES (not studies) by enriched ontology, QC, and microbial detections. "
        "Substring filters: `tissue`, `disease`, `cell_type`, `assay`, `assay_category`, `phenotype`, "
        "`treatment`, `development_stage`, `sample_type`, `genetic_modification`, `strain`, `cell_line`, "
        "`ethnicity`, `tissue_primary_site` (use short stems like `cervi`). Exact: `organism`, `sex`, "
        "`taxid`, `study_accession`. `*_ontology_id` accepts CURIEs (+ descendants unless `include_descendants=false`). "
        "Age/cell/gene bounds exclude unmeasured samples (tell the user). `has_matrix` is single-cell proof; "
        "`single_cell_only` needs preflightx FASTQ scan (~4.7% of matrix studies). "
        "For infections use `microbe*`: `microbe_tier` is `low_breadth` (default, >=1% bacterial), "
        "`high_breadth` (strict), or `any` (noisy). Never compare viral vs bacterial breadth (viral genomes "
        "are ~250x smaller). Say 'reference signal consistent with X', never 'infected', and report `class`. "
        "On 504, add `organism` or `tissue`."
    ),
    "get_search_investigators": (
        "Rank investigators (PIs) publishing on a search topic ('who works on CRISPR in India'). "
        "Supports keywords/boolean `q` and archive/organism/assay/country filters; `senior_only=true` "
        "ranks last authors. For a whole country without a topic, use `get_country_pis`."
    ),
    "list_filter_values": (
        "Enumerate valid filter values sorted by frequency for `search_structured`: "
        "`field=library_strategy|instrument_model|journal|center`."
    ),
    "convert_accession": (
        "Resolve one (`accession`) or many (`accessions`, max 500) accessions across GEO, SRA, ENA, "
        "DDBJ, ArrayExpress, BioProject and GSA, mapping child accessions to their parent study and "
        "listing cross-archive mirrors. `exists: false` means not in seqout."
    ),
    "get_single_cell_overview": (
        "Corpus-level single-cell statistics (totals, top tissues/organisms). For filtered study lists, "
        "use `get_singlecell_projects`."
    ),
    "list_single_cell_studies": (
        "List studies with measured single-cell evidence (matrix, barcode whitelist, R1 structure). "
        "For curated filters, use `get_singlecell_projects`."
    ),
    "get_sc_quality": (
        "Single-cell QC quantiles (median UMI `ncount` and genes `nfeature` p25/p50/p75) by release year "
        "and technology across filtered matrices."
    ),
    "search_author_projects": (
        "Datasets authored by a person (project rows + institutes) via linked PubMed papers. "
        "For the paper list, use `search_by_author`."
    ),
    "search_by_author": (
        "PubMed publications of an author with linked dataset accessions. "
        "For project rows, use `search_author_projects`."
    ),
}

# Notes appended for tools outside MCP_TOOL_DESCRIPTIONS.
MCP_TOOL_NOTES: dict[str, str] = {
    "search_suggest": "Use only when a search returns zero/very few results and may have a typo.",
    "get_study_bams": "Original submitter-uploaded BAM files for an SRA/ENA study, when present.",
    "get_supplementary_files": "GEO/ArrayExpress/GEA supplementary & processed files as JSON (`download_supplementary_script` for a curl script).",
    "get_project_similar": "Use for 'studies like this' on a GSE or SRP.",
    "get_enriched_coverage": "Enriched project/sample counts and approximate % coverage per archive, organism, assay and assay_category.",
    "get_enriched_column": "Top values for one enriched column (`tissue`, `cell_type`, `disease`, `assay`, ...).",
    "get_enriched_crosstab": "2D cross-tab of curated enriched columns (e.g. `group=organism`, `breakdown=tissue`).",
    "ontology_search": "Fuzzy lookup returning exact node names to pass to `ontology_term`.",
    "get_disease_aliases": "When `truncated=true`, raise `limit` (max 100) or narrow `q`.",
    "get_project_cite": "`type=original` gives earliest PMID; `type=all` gives all linked papers; `format=bibtex` returns BibTeX.",
    "find_publication": "Find datasets linked to a paper by `pmid` or `doi`.",
    "get_country_pis": "Investigators for a whole country without a topic. For a topic, use `get_search_investigators`.",
    "get_growth_stats": "In `mode=bases`, ENA counts nucleotide bases and SRA counts FASTQ/SRA bytes.",
    "get_common_names_by_scientific_names": "Accepts one or a list of scientific names.",
}


def path_tool_patterns(paths: dict[str, str]) -> list[tuple[re.Pattern[str], str]]:
    """REST path patterns to MCP tool names; any {param} matches any {param}."""
    out = []
    for path, tool in sorted(paths.items(), key=lambda kv: -len(kv[0])):
        body = re.sub(r"\\\{[^}]*\\\}", r"\\{[^}]+\\}", re.escape(path))
        out.append((re.compile(rf"(?<![\w./]){body}(?![\w{{/|-])"), f"`{tool}`"))
    return out


def replace_rest_paths(text: str, patterns: list[tuple[re.Pattern[str], str]]) -> str:
    """Name the MCP tool where a description cites a REST path."""
    for pat, tool in patterns:
        text = pat.sub(tool, text)
    return text


PATH_TOOL_PATTERNS: list[tuple[re.Pattern[str], str]] = []


def set_path_tools(paths: dict[str, str]) -> None:
    """Register REST path to tool name pairs before FastMCP builds the tools."""
    PATH_TOOL_PATTERNS[:] = path_tool_patterns(paths)


def _schema_accepts_array(schema: dict) -> bool:
    if schema.get("type") == "array":
        return True
    return any(
        isinstance(branch, dict) and branch.get("type") == "array"
        for branch in schema.get("anyOf") or ()
    )


_SIMPLE_CHOICE_RE = re.compile(r"^\^\(([A-Za-z0-9_|-]+)\)\$$")
_REST_DEFAULT_RE = re.compile(
    r"\s*(?:\(default [^)]+\)|;?\s*omit for every row\.?|\bDefault:?\s+\d+\.?)",
    re.IGNORECASE,
)


def clean_param_schema(schema: dict) -> dict:
    """Strip Pydantic schema bloat: titles, nullable anyOf wrappers, and simple choice regexes."""
    out = dict(schema)
    out.pop("title", None)

    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2:
        non_null = [
            b for b in any_of if isinstance(b, dict) and b.get("type") != "null"
        ]
        if len(non_null) == 1:
            out.pop("anyOf")
            branch = clean_param_schema(non_null[0])
            for k, v in branch.items():
                out.setdefault(k, v)
            if out.get("default") is None:
                out.pop("default", None)
    elif isinstance(any_of, list):
        out["anyOf"] = [
            clean_param_schema(b) if isinstance(b, dict) else b for b in any_of
        ]

    if isinstance(out.get("items"), dict):
        out["items"] = clean_param_schema(out["items"])

    pattern = out.get("pattern")
    if isinstance(pattern, str):
        match = _SIMPLE_CHOICE_RE.match(pattern)
        if match:
            out.pop("pattern")
            out["enum"] = match.group(1).split("|")

    return out


def annotate_tool(component: Any) -> None:
    """MCP-only descriptions, compact JSON schemas, defaults and closed input schema."""
    name = component.name
    if name in MCP_TOOL_DESCRIPTIONS:
        component.description = MCP_TOOL_DESCRIPTIONS[name]
    else:
        component.description = replace_rest_paths(
            component.description or "", PATH_TOOL_PATTERNS
        )
        note = MCP_TOOL_NOTES.get(name)
        if note:
            component.description = f"{component.description.rstrip()}\n\n{note}"

    params = component.parameters or {}
    params.pop("title", None)
    props = params.get("properties") or {}
    cleaned_props: dict[str, Any] = {}
    defaults = MCP_ARG_DEFAULTS.get(name, {})
    for arg, schema in props.items():
        s = clean_param_schema(schema)
        if not (s.get("description") or "").strip() and (doc := param_doc(name, arg)):
            s["description"] = doc
        if arg in defaults:
            s["default"] = defaults[arg]
            desc = _REST_DEFAULT_RE.sub("", s.get("description", "")).strip()
            if desc and not desc.endswith((".", "!", "?")):
                desc += "."
            s["description"] = f"{desc} Default: {json.dumps(defaults[arg])}.".lstrip()
        cleaned_props[arg] = s

    params["properties"] = cleaned_props
    params["additionalProperties"] = False
    TOOL_PARAMS[name] = frozenset(cleaned_props)
    TOOL_ARRAY_PARAMS[name] = frozenset(
        arg for arg, schema in cleaned_props.items() if _schema_accepts_array(schema)
    )


TOOL_PARAMS: dict[str, frozenset[str]] = {}
TOOL_ARRAY_PARAMS: dict[str, frozenset[str]] = {}

_ACCESSION_ARG_KEYS = frozenset(
    {"accession", "study", "run", "submission", "study_accession"}
)

# wrong parameter name -> real names to suggest, in order
_PARAM_ALIASES: dict[str, tuple[str, ...]] = {
    "query": ("q",),
    "text": ("q",),
    "search": ("q",),
    "keyword": ("q",),
    "keywords": ("q",),
    "db": ("source", "archive"),
    "database": ("db", "source", "archive"),
    "source": ("db", "archive"),
    "archive": ("db", "source"),
    "date_from": ("published_after", "year_from"),
    "date_to": ("published_before", "year_to"),
    "published_after": ("date_from", "year_from"),
    "published_before": ("date_to", "year_to"),
    "start_date": ("date_from", "published_after"),
    "end_date": ("date_to", "published_before"),
    "start_year": ("year_from", "year"),
    "end_year": ("year_to", "year"),
    "page_size": ("limit",),
    "max_results": ("limit",),
    "per_page": ("limit",),
    "top": ("limit",),
    "sort_by": ("sortby", "sort"),
    "sortby": ("sort",),
    "sort": ("sortby",),
    "term": ("target", "collection", "q"),
    "collection": ("term",),
    "code": ("target",),
    "disease_name": ("target", "collection", "disease"),
    "tissue_name": ("target", "term", "tissue", "sample_tissue"),
    "facet": ("field", "include"),
    "filter": ("field",),
    "dimension": ("field", "column"),
    "study_accession": ("study", "accession"),
    "project": ("accession", "study"),
    "project_accession": ("accession", "study"),
    "study_id": ("study", "accession"),
    "run_accession": ("run", "accession"),
    "run": ("accession",),
    "experiment": ("accession",),
    "experiment_accession": ("accession",),
    "sample": ("accession",),
    "sample_accession": ("accession",),
    "country_code": ("code", "target", "geo_country_code_iso2", "country"),
    "species": ("organism",),
    "taxon": ("organism", "taxid"),
    "assay_type": ("library_strategy", "assay", "assay_l1"),
}

# first match wins; GEA before ArrayExpress, as in accessions.py
_ACCESSION_ROUTING: tuple[tuple[tuple[re.Pattern[str], ...], str, str], ...] = (
    (
        (GEO_SERIES_PATTERN,),
        "a GEO series",
        "Use `get_project`, `get_geo_series_samples`, or `get_supplementary_files`; "
        "for FASTQ runs, call `convert_accession` to find the linked SRP/ERP/DRP study.",
    ),
    (
        (GEO_SAMPLE_PATTERN,),
        "a GEO sample",
        "Use `get_sample_detail` or resolve its parent series with `convert_accession`.",
    ),
    (
        (STUDY_PATTERN,),
        "an SRA/ENA/DDBJ study",
        "Use `get_project`, `get_study_runs`, `get_study_download_manifest`, or `get_study_experiments`.",
    ),
    (
        (EXPERIMENT_PATTERN, GSA_EXPERIMENT_PATTERN),
        "an experiment accession",
        "Use `get_experiment`, `get_experiment_runs`, `get_sample_detail`, or resolve its study with `convert_accession`.",
    ),
    (
        (SAMPLE_PATTERN, BIOSAMPLE_PATTERN, GSA_SAMPLE_PATTERN),
        "a sample/BioSample accession",
        "Use `get_sample_detail`, `get_sample_experiments`, or resolve its study with `convert_accession`.",
    ),
    (
        (RUN_PATTERN,),
        "a sequencing run",
        "Use `get_run_checksums`, `get_run`, or resolve its study with `convert_accession`.",
    ),
    (
        (GSA_RUN_PATTERN,),
        "a GSA sequencing run (no single-run endpoint)",
        "Resolve its parent CRA/HRA study with `convert_accession`, then call `get_study_runs` or `get_study_download_manifest`.",
    ),
    (
        (PRJ_PATTERN,),
        "a BioProject accession",
        "Use `get_project` or resolve to its GSE/SRP/ERP/DRP with `convert_accession`.",
    ),
    (
        (GSA_STUDY_PATTERN,),
        "a GSA study",
        "Use `get_project`, `get_study_runs`, or `get_study_download_manifest`.",
    ),
    (
        (GEA_PATTERN,),
        "a DDBJ GEA series",
        "Use `get_project`, `get_geo_series_samples`, or `get_supplementary_files`; "
        "for FASTQ runs, use `convert_accession` to find its linked DRA study.",
    ),
    (
        (AE_PATTERN,),
        "an ArrayExpress experiment",
        "Use `get_project`, `get_geo_series_samples`, or `get_supplementary_files`; "
        "for FASTQ runs, use `convert_accession` to find a linked ENA/SRA study.",
    ),
    (
        (re.compile(SUBMISSION_REGEX),),
        "a submission accession",
        "Use `resolve_submission_studies`.",
    ),
)


def suggest_param(bad_key: str, known: frozenset[str]) -> list[str]:
    """Suggest valid parameter names for an unrecognized argument."""
    suggestions: list[str] = []
    for cand in _PARAM_ALIASES.get(bad_key.lower(), ()):
        if cand in known and cand not in suggestions:
            suggestions.append(cand)
    for cand in difflib.get_close_matches(bad_key, sorted(known), n=2, cutoff=0.55):
        if cand not in suggestions:
            suggestions.append(cand)
    return suggestions


def accession_routing_hint(value: str, tool_name: str, field: str) -> str:
    """Routing guidance for an accession that fails a tool's path regex."""
    norm = value.strip().upper()
    for pats, kind_desc, guidance in _ACCESSION_ROUTING:
        if any(pat.match(norm) for pat in pats):
            return (
                f"Invalid `{field}` {value!r} for `{tool_name}`: `{norm}` is {kind_desc}. "
                f"{guidance}"
            )
    return (
        f"Invalid `{field}` {value!r} for `{tool_name}`: unrecognized accession format. "
        "Call `classify_accession` or `convert_accession` to identify it."
    )


def prepare_mcp_args(tool_name: str, raw_args: dict[str, Any] | None) -> dict[str, Any]:
    """Validate, apply MCP defaults, coerce scalar-to-list, and normalize accessions."""
    args = dict(raw_args or {})
    known = TOOL_PARAMS.get(tool_name)
    if known is not None:
        unknown = sorted(k for k in args if k not in known)
        if unknown:
            parts = []
            for k in unknown:
                sugg = suggest_param(k, known)
                if sugg:
                    quoted = ", ".join(f"`{s}`" for s in sugg)
                    parts.append(f"`{k}` (did you mean {quoted}?)")
                else:
                    parts.append(f"`{k}`")
            valid_list = ", ".join(sorted(known)) if known else "(none)"
            raise ToolError(
                f"Unknown parameter(s) for `{tool_name}`: {'; '.join(parts)}. "
                f"Valid parameters: {valid_list}."
            )

    for k, v in MCP_ARG_DEFAULTS.get(tool_name, {}).items():
        if args.get(k) is None:
            args[k] = v

    # drop None so optional params don't go out as null
    args = {k: v for k, v in args.items() if v is not None}

    array_params = TOOL_ARRAY_PARAMS.get(tool_name, frozenset())
    for k in array_params:
        if k in args and isinstance(args[k], str):
            args[k] = [args[k]]

    if tool_name == "classify_accession":
        if isinstance(args.get("accession"), str):
            args["accession"] = args["accession"].strip()
    else:
        for k in _ACCESSION_ARG_KEYS:
            if isinstance(args.get(k), str):
                args[k] = args[k].strip().upper()
        if isinstance(args.get("accessions"), list):
            args["accessions"] = [
                a.strip().upper() if isinstance(a, str) else a
                for a in args["accessions"]
            ]

    return args


_DATE_ERROR_TYPES = frozenset(
    {"date_from_datetime_inexact", "date_parsing", "date_from_datetime_parsing"}
)


def http_422_detail(exc: BaseException | None) -> list | None:
    """Validation errors of the FastAPI 422 that caused a tool error, if any."""
    while exc is not None and not isinstance(exc, httpx.HTTPStatusError):
        exc = exc.__cause__
    if exc is None or exc.response.status_code != 422:
        return None
    try:
        payload = exc.response.json()
    except ValueError:
        return None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return detail if isinstance(detail, list) and detail else None


def translate_mcp_error(tool_name: str, detail: list) -> str | None:
    """Turn the validation errors of a FastAPI 422 into a short MCP error."""
    messages: list[str] = []
    for item in detail:
        if not isinstance(item, dict):
            continue
        loc = item.get("loc") or []
        err_type = item.get("type") or ""
        val = item.get("input")
        msg = item.get("msg") or ""
        field = str(loc[-1]) if loc else "input"

        if (
            tool_name == "convert_accession"
            and loc == ["body"]
            and err_type == "missing"
        ):
            messages.append(
                "`convert_accession` requires `accession` (one string) or "
                "`accessions` (a non-empty list of strings)."
            )
            continue

        pattern = (item.get("ctx") or {}).get("pattern") or ""
        if err_type in _DATE_ERROR_TYPES or pattern == r"^\d{4}-\d{2}-\d{2}$":
            messages.append(
                f"Invalid date for `{field}`: got {val!r}. "
                "Use YYYY-MM-DD (or `year_from`/`year_to` for whole years)."
            )
            continue

        # FastMCP sends the unfilled path template when a path argument is absent
        if err_type == "missing" or val == f"{{{field}}}":
            messages.append(f"Missing required parameter `{field}` for `{tool_name}`.")
            continue

        if err_type == "string_pattern_mismatch":
            choice_match = _SIMPLE_CHOICE_RE.match(pattern)
            if choice_match:
                choices = ", ".join(choice_match.group(1).split("|"))
                messages.append(
                    f"Invalid value {val!r} for `{field}` in `{tool_name}`. "
                    f"Expected one of: {choices}."
                )
            elif field in _ACCESSION_ARG_KEYS and isinstance(val, str):
                messages.append(accession_routing_hint(val, tool_name, field))
            else:
                messages.append(
                    f"Invalid format for `{field}` in `{tool_name}`: got {val!r} ({msg})."
                )
            continue

        messages.append(f"Invalid `{field}` for `{tool_name}`: {msg} (got {val!r}).")

    return " ".join(messages) if messages else None


class SeqoutMcpMiddleware(Middleware):
    """MCP defaults, strict arg validation, normalization, error hints, line JSON."""

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        return [t for t in tools if t.name not in MCP_DEPRECATED_TOOLS]

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        name = context.message.name
        if name in MCP_DEPRECATED_TOOLS:
            logger.warning(
                "deprecated MCP tool %s called; use %s",
                name,
                MCP_DEPRECATED_TOOLS[name],
            )
        args = prepare_mcp_args(name, context.message.arguments)
        message = context.message.model_copy(update={"arguments": args})
        try:
            result = await call_next(context.copy(message=message))
        except Exception as exc:
            detail = http_422_detail(exc)
            friendly = translate_mcp_error(name, detail) if detail else None
            if friendly is not None:
                raise ToolError(friendly) from None
            raise
        structured = result.structured_content
        if not isinstance(structured, dict):
            return result
        structured = normalize_mcp_aliases(structured)
        return ToolResult(
            content=[TextContent(type="text", text=line_json(structured))],
            structured_content=structured,
            meta=result.meta,
        )
