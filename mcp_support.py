"""Search paging, compact rows and the MCP layer."""

import json
import logging
import re
from typing import Any

from mcp.types import TextContent

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


def sql_total(rows: list[dict]) -> int | None:
    return rows[0].get("total_count") if rows else 0


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
    "list_organisms",
    # Excluded until get_sample_microbes aggregates before applying LIMIT.
    "get_sample_microbes",
    "sitemap_counts",
    "sitemap_accessions",
    "get_country_points",
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

# unlisted but callable for one release; calls are logged
MCP_DEPRECATED_TOOLS = {
    "get_project_xref": "convert_accession",
    "resolve_prj_project": "convert_accession",
    "resolve_accession_project": "convert_accession",
    "get_project_metadata": "get_project(fields=summary)",
    "get_common_name_by_scientific_name": "get_common_names_by_scientific_names",
    "get_sample": "get_sample_detail",
}

# REST keeps the route defaults
MCP_ARG_DEFAULTS: dict[str, dict[str, Any]] = {
    "search_all": {"limit": 50, "fields": "compact"},
    "search_structured": {"limit": 50, "fields": "compact"},
    "search_nl": {"limit": 50, "fields": "compact"},
    "get_project": {"include_neighbors": False},
}

MCP_INSTRUCTIONS = """\
seqout searches study metadata from NCBI GEO, SRA/ENA/DDBJ, ArrayExpress, DDBJ GEA \
and GSA. Each tool description carries that tool's rules; read it before the first call.

## Which search tool
- `search_all`: keywords or a boolean query (`("aging" OR aged) immun*`). `db=` limits the archive.
- `search_structured`: explicit filters (organism, library_strategy, platform, dates, country) or `count_by` buckets.
- `search_nl`: a whole English sentence. Read `parsed.warnings`.
- `search_enriched_samples`: SAMPLES (not studies) by tissue, disease, age, sex, cell type or microbe.
- Collections for "what data exists on X": `get_disease_projects`, `get_tissue_projects`, \
`get_singlecell_projects`, `get_spatial_projects`, `get_perturbation_projects`, `get_longread_projects`.
Filter or sort the rows you already have before you search again.

## Search rows
Rows carry `published_at` (release date, first public), `updated_at`, `alias` (the same \
dataset in other archives, e.g. the SRP or E-GEOD of a GSE), organisms, library_strategies, \
pmid and doi. Do not call `get_project` per hit for these. A dataset appears once; its \
mirrors are in `alias`. Through MCP a page holds 50 compact rows (summary cut to 300 \
characters). Pass `limit` (1-200) or `fields=full` for more.

## Paging
Repeat the same call with the cursor from `next_cursor`: `cursor_rank` + `cursor_acc` \
for relevance order, `cursor_sort` + `cursor_acc` with `sortby`. `has_more=false` \
(next_cursor null) is the last page. `total` is null when unknown (`total_is_known=false`); \
it is not zero. `sortby` replaces relevance order: use it only for newest, oldest or most cited.

## Dates
`date_from`/`date_to`/`year_from`/`year_to` (search_all), `published_after`/`published_before` \
and `year_*` (search_structured), `count_by=year` and `sortby=year` all use the release \
date. `pub_date_*` is the linked paper's date.

## Accessions
- GSE, E-XXXX-N, E-GEAD-N (DDBJ GEA, not ArrayExpress), SRP/ERP/DRP, CRA/HRA, PRJ*: `get_project`.
- GSM, SRX/ERX/DRX, SRS/ERS/DRS, SAM*: `get_sample_detail`. SRR/ERR/DRR: `get_run_checksums` or `get_run`.
- Same dataset in another archive, or a child accession's project: `convert_accession` (one or many).
- Unknown shape: `classify_accession`. Submission (SRA/ERA/DRA + digits): `resolve_submission_studies`.
- `exists: false` or a 404 means the accession is not in seqout. Never present it as a real dataset.

## Files and errors
FASTQs are per run: `get_study_download_manifest` for a study, `get_supplementary_files` \
for GEO/ArrayExpress processed files. A 504 is a timeout, not an empty result: narrow the call.
"""

_NOTE_FASTQ = """\
FASTQ files exist per run; a study is a container of runs. GEO and ArrayExpress \
projects have FASTQs only through a linked SRA/ENA study (`alias`, or \
`convert_accession`); for their processed files use `get_supplementary_files`. A PRJ \
must first resolve to its SRP/ERP/DRP with `convert_accession`. Interleaved paired \
runs (`library_layout=PAIRED` with `fastq_file_count=1` or `fastq_is_interleaved=true`) \
are not a complete R1/R2 pair: use the SRA normalized format, or SRA Lite, and \
`sracha get --split split-files`. The manifest and `download_study_script(mode="fastq")` \
do this when `fastq_conversion_available=true`; read `fastq_conversion_required`, \
`recommended_fastq_source`, `conversion_command` and `expected_fastq_paths`."""

_NOTE_ENRICH_LAYERS = """\
Three metadata layers. Project metadata: `get_project` and search rows. Raw flattened \
rows: `get_project_metadata_rows`, `get_experiment_metadata_rows`, \
`get_run_metadata_rows` (SRA/ENA: one row per run with `sample_attribute:*` columns, \
FASTQ URLs, bytes, MD5; GEO/ArrayExpress: one row per sample). Ontology-enriched: \
`get_project_enriched`, `get_experiment_enriched`, `get_run_enriched` (tissue, \
cell_type, disease, assay, UBERON/CL/MONDO/EFO ids, low_confidence_tags). For both raw \
attributes and ontology fields call both and say which is which. Enriched tools may \
404 for unenriched projects; fall back to the raw rows."""

_NOTE_MICROBE_EVIDENCE = """\
Microbial calls are evidence, not diagnosis. A flag means unitigs align across at \
least 20% of a panel reference (50% for MLV, MMTV, HIV1, HTLV1); there is no raw-read, \
UMI or replicate confirmation. NULL is not false: a null flag list or `measurable` \
false/null means nothing was measurable, while an EMPTY list means measured and none \
found. `kmer_mass` is a relative within-run proxy, not an absolute count. \
`spike_in_control` (PhiX), `negative_control` (animalPV) and `bacterial_background` \
(reagent kitome, skin flora) are never summed into viral or bacterial totals. `cells` \
is a matrix column count; when `unfiltered` is set they are 10x barcodes, never sum them."""

MCP_TOOL_NOTES: dict[str, str] = {
    "search_all": """\
Pass `db=geo|sra|arrayexpress|ena|gsa|dra|gea` only when the user restricts the source. \
`long_read=true` keeps studies with a PacBio or Oxford Nanopore experiment. \
`suggestions`/`correction` in the response mean the query looked misspelled: present \
or use them instead of guessing. Structured search has no DRA or GEA source, so \
DDBJ-exclusive and GEA records are reachable only here (`db=dra`, `db=gea`).""",
    "search_structured": """\
Filter caveats: `platform` drops GEO and ArrayExpress; `instrument_model` drops \
ArrayExpress, ENA and GSA; `country` drops ArrayExpress; `multi_platform` is GEO/SRA \
only; `library_strategy` drops GEO. `organism` and `library_strategy` take several \
values. `sample_tissue`/`sample_disease`/`sample_cell_type` prove one enriched sample \
matched (inspect with `get_project_enriched`); for the samples themselves use \
`search_enriched_samples`. Choose filter values with `list_library_strategies`, \
`list_instrument_models`, `list_journals`, `list_centers` and `search_organisms`.""",
    "search_nl": """\
Rule-based, no LLM. ALWAYS read `parsed.warnings` and repeat them. Exclusions \
("excluding mouse") are listed in `parsed.excluded` but NOT applied: filter the rows \
yourself or use a boolean query in `search_all`. DDBJ/DRA/GEA are recognised but have \
no structured source; use `search_all(db=dra|gea)`. `dry_run=true` parses only. When \
the user gives explicit filters, call `search_structured`.""",
    "search_expansion": """\
Explains a plain-keyword result set: the synonyms that reached the search per term, \
and how many exist, which differ when the variant cap truncates. Use it when results \
look too broad or narrow and the user asks why. Boolean queries are never expanded.""",
    "search_suggest": "Use only when a search returns no or very few results and looks misspelled.",
    "search_highlight": """\
Covers title, summary and overall design only, stemmed and synonym-expanded like the \
search. An empty `words` list does not mean the project matched for no reason; the \
hit may come from a field this tool does not read. Present it as "these query words \
appear in the description".""",
    "get_project": """\
`fields=summary` returns only accession, title and description. Similar studies are \
not included through MCP; call `get_project_similar`. `alias` is the list of linked \
accessions in other archives, the same list as on a search row: a GSE and its SRP and \
E-GEOD, an E-GEAD (DDBJ GEA) and its DRA study, which holds its FASTQs. \
`submitter_alias` is the submitter's free-text name for the study, never an accession; \
it is null when there is none. DRP/DRX/DRS/DRR \
behave like SRA; DDBJ-exclusive FASTQ links point at ddbj.nig.ac.jp. HR* (GSA-Human) \
data is usually access-controlled, so download links may be absent.""",
    "get_geo_series_samples": "Also lists the samples of a DDBJ GEA series (E-GEAD-N).",
    "get_study_runs": """\
Previews the first 500 runs with `total_runs`, `paired_runs`, `single_runs` and \
`total_fastq_bytes`. Its `fastq_ftp`, `fastq_bytes` and `fastq_md5` are \
semicolon-joined for paired runs. For every run of a larger study, page \
`get_study_download_manifest`. GSA runs (CRR/HRR) have no per-run tool; read them \
from this list on their CRA/HRA study.""",
    "get_study_download_manifest": "Complete, paginated, one entry per file with url, bytes, md5, run_accession and download_command. Follow `next_cursor`. "
    + _NOTE_FASTQ,
    "download_study_script": "Returns a shell script. Use it only when the user asks for a ready-to-run script; otherwise use `get_study_download_manifest`. Modes: fastq (default), sra, sra_lite (smaller, lossy quality scores), s3, gcs. FASTQ rules: `get_study_download_manifest`.",
    "get_run_checksums": "The tool for MD5, checksums, file sizes or verifying downloads of one run: parsed `fastq` records, an optional `sra` record, interleaved and conversion flags. Use `get_run` only for raw run fields.",
    "get_study_bams": "Original submitted BAMs of an SRA/ENA study, when present.",
    "get_supplementary_files": "GEO/ArrayExpress supplementary and processed files as JSON. `download_supplementary_script` returns the curl script.",
    "get_project_metadata_rows": _NOTE_ENRICH_LAYERS,
    "get_project_enriched": "Normalized ontology fields, not raw archive metadata; see `get_project_metadata_rows` for the three metadata layers. May 404 for unenriched projects.",
    "bulk_project_samples_metadata": "Several GSE/SRP/ERP/DRP projects at once: project summaries plus flattened sample rows. For one project prefer `get_project_metadata_rows`.",
    "get_project_similar": "The tool for 'studies like this' on a GSE or SRP.",
    "get_enriched_coverage": "Enriched projects and samples per archive with the archive total and percentage, plus counts per organism, assay and assay_category.",
    "get_enriched_column": "Call with `column=` for the top values of tissue, cell_type, disease, assay and other enriched columns.",
    "get_enriched_crosstab": "Call with `group=` and `breakdown=` for cross-tabs such as organism by tissue.",
    "get_tissue_microbes": "Rates are over quantified samples only. Report `is_background`, a `class` of `culture`, or `is_endogenous` with the rate: those organisms are contamination or host genome, not infection.",
    "ontology_term": """\
Looks up a term by exact NAME: source CURIEs (UBERON, CL, MONDO, EFO, HGNC, MeSH, \
Cellosaurus), synonym cluster and direct children. It does not resolve a CURIE; pass \
the name, or use `get_disease_aliases` for MONDO ids. A 404 means no node has that \
exact name: try another surface form. `children=false` is much cheaper.""",
    "ontology_search": "Typo-tolerant name match returning candidate node names to pass to `ontology_term`.",
    "search_enriched_samples": """\
The only tool that answers with SAMPLES. Do not approximate it with \
`search_structured` sample filters, which answer with studies.
Filters: tissue, disease, cell_type, assay, assay_category, phenotype, treatment, \
development_stage, sample_type, genetic_modification, strain, cell_line, ethnicity and \
tissue_primary_site are case-insensitive SUBSTRING matches (prefer a short stem: \
`cervi`). organism, sex, taxid and study_accession are EXACT. The *_ontology_id \
filters take CURIEs and include descendants unless include_descendants=false; get \
MONDO ids from `get_disease_aliases`. age_min_years/age_max_years are years and drop \
samples with no age; min/max_cell_count and min/max_gene_count drop unmeasured \
samples (~85k measured): say the result is the measured subset. Returned per-sample \
counts are `cells` and `genes`.
Read-derived filters: `has_matrix` is the proof tier for single-cell data; \
`single_cell_only` is a barcode call needing FASTQ plus a preflightx scan (4.7% of \
studies with a matrix). `single_cell_only`, `has_viral_reads`, `has_bacterial_reads` \
and `hpv_type` only constrain when true.
Infection questions use `microbe*`, NOT has_viral_reads/has_bacterial_reads (true on \
~370 samples each, a >10x undercount). A `microbe*` filter restricts to matching \
detections and attaches `microbe_n_detections`, `microbe_reads`, `microbe_kmer_mass`, \
`microbe_max_breadth_frac` and a `microbes` array (capped at 50 rows with \
`microbes_truncated`; aggregates use all detections). `microbe` is a substring \
(`papillomavirus` catches every HPV type). `microbe_tier`: `high_breadth` (strict; HPV \
323 samples, pathogen 998), `low_breadth` default (bacteria at >=1% breadth; HPV 1,338, \
pathogen 14,345), `any` (no floor; trace noise, only on explicit request). Breadth is \
a genome FRACTION; viral genomes are ~250x smaller, so never compare viral and \
bacterial breadth. `low_breadth` bacterial rows are ~1.3x over background, \
`high_breadth` ~3x: say "reference signal consistent with X", never "infected with \
X", and report `class` with bacterial numbers (Mycoplasma and vector E. coli dominate).
Example: `search_enriched_samples(tissue="cervi", assay="single cell rna", \
microbe="papillomavirus", single_cell_only=true)`.
Paging is limit/offset with a true `total` and `next_offset`. A broad cohort plus a \
microbe filter is slow; on a 504 narrow by organism or tissue.""",
    "get_longread_projects": """\
One row per study, mirrors folded. `long_read_only` false means the study also used a \
short-read platform; NULL means no experiment rows tell (GEO-only). `n_experiments` \
counts long-read experiments, `n_experiments_total` all. Onso is excluded. Get filter \
values from `get_longread_facets`; totals from `get_longread_summary`.""",
    "get_project_longread_chemistry": "Every PacBio/ONT run with its chemistry call. Report `chemistry_confidence` beside the call: exact (BAM header), declared (ONT protocol text), bucket (instrument model only) or unknown. `runs` is [] for a study with no long-read runs.",
    "get_disease_aliases": "When `truncated` is true, raise `limit` (max 100) or narrow `q`.",
    "get_disease_summary": "See `get_disease_projects` for collections and how to read rows.",
    "get_disease_facets": "Call before `get_disease_projects`: filters a collection does not carry are rejected.",
    "get_disease_projects": """\
For "what data exists on <disease>" prefer these tools to a keyword search: they \
resolve through MONDO ids and descendants. `collection=rare` (NIH GARD, exact MONDO \
xref) or `nord` (NORD, by name, looser): say which. Or a free-text disease name or \
MONDO id: full MONDO match (exact label, else substring) plus descendants; \
`resolution` and `matched_labels` in the summary say what matched; a 404 means no \
MONDO match. Row shapes differ: rare/nord rows are a per-sample rollup (cells, sex, \
ancestry, inheritance), sortable by cells, n_samples, n_diseases, title, \
study_accession, assay_category, organism; free-text rows match `get_tissue_projects`, \
sortable by n_samples, n_experiments, pub_date, title, study_accession, organism.
Reading rare/nord rows: `scope` defaults to human_primary (patient_derived_model, \
cell_line, all show what it excludes); say which scope a count used. `n_samples` \
counts samples matching this collection, not the study total. A null cell count is \
unmeasured; blank ancestry is unrecorded. `has_fastq`/`has_sra` NULL means unknown. \
`ancestries` is submitter-reported ethnicity text (~13% of studies), not genetic \
ancestry. `inheritance` comes from HPO, Orphanet, GARD; "Not applicable" means no \
Mendelian pattern. For a free-text term, the summary's `matched_samples` counts \
matched samples and `samples` is the studies' total.""",
    "get_perturbation_projects": """\
For CRISPR screens, Perturb-seq or drug-treatment single-cell data. Rule-based \
detection (named methods, guide-library and control-arm tests, a ChEBI lexicon). Get \
filter values from `get_perturbation_facets`. Default to `min_confidence=medium`; \
`low` is one weak signal with many false positives, say so. `evidence` lists the \
signals. `compounds` come from sample treatments (designs with drug arms in \
supplementary files have a method but no compounds); `title_compounds` alone is a \
weak lead. `other` is a cytokine or stimulation design. `cell_lines`/`sample_types` \
describe material, not the perturbation. Germline knockouts, transgenic lines and \
doxycycline/tamoxifen inducers are not counted.""",
    "get_spatial_projects": """\
For spatial transcriptomics, Visium or Xenium data. Population is the single-cell \
modality classifier; platform naming is regex over the study text. Get filter values \
from `get_spatial_facets`. `platforms` is often null, and a named platform is a text \
mention (a comparison paper can name one it did not run): a weak lead. \
`resolution=roi` (GeoMx DSP) is region level. `technology` tracks `resolution` \
(imaging~single-cell, sequencing~spot, hybrid~roi); a study naming two groups \
collapses to single-cell, then spot, then roi.""",
    "get_tissue_projects": """\
For "what data exists on <tissue>". `term` resolves through UBERON (exact label, else \
substring) and descendants, so "blood" reaches "whole blood"; `resolution` and \
`matched_labels` in `get_tissue_summary` say what matched; a 404 means no match. \
`n_samples_with_tissue` counts every sample with any resolved tissue; the summary's \
`matched_samples` is the matching count. `has_fastq`/`has_sra` NULL means unknown. \
Get filter values from `get_tissue_facets`. No curated tissue catalogue exists.""",
    "get_project_single_cell": """\
404 means the study is not in the Pentimento (common): fall back to \
`get_project_enriched`. `samples` pages by limit/offset (default 200, max 1000) with no \
`total`: compare with the study's `n_samples_detailed` and say if you stopped early. \
For per-organism microbial breakdowns use \
`search_enriched_samples(study_accession=..., microbe=...)`. """
    + _NOTE_MICROBE_EVIDENCE,
    "get_project_single_cell_summary": "Study-level totals (cells, preflightx evidence, microbe measurability). Per-sample rows and how to read microbial flags: `get_project_single_cell`.",
    "get_project_single_cell_status": "Only the single-cell label of one study and its evidence. Totals: `get_project_single_cell_summary`; per sample: `get_project_single_cell`.",
    "get_single_cell_overview": "Corpus statistics (study counts by data type, top tissues and organisms). For the study list use `get_singlecell_projects`.",
    "list_single_cell_studies": "Studies with measured single-cell evidence (matrix, barcode whitelist, R1 structure). For the curated collection with filters use `get_singlecell_projects`.",
    "get_project_cite": "`type=original` returns the earliest/smallest PMID, `type=all` every linked paper, `format=bibtex` plain BibTeX.",
    "find_publication": "Paper to datasets, by PMID or DOI.",
    "search_author_projects": "Every dataset a person authored, as project rows plus institutes. Prefer it for 'datasets by <name>'. Use `search_by_author` for the papers themselves. Only studies linked to PubMed are found.",
    "search_by_author": "The publications of an author, one row per PMID with linked accessions. For the datasets use `search_author_projects`. Only studies linked to PubMed are found.",
    "get_search_investigators": "Investigators behind any search; use it whenever the question names a topic ('who works on CRISPR in India'). `senior_only=true` ranks last authors.",
    "get_country_pis": "Investigators of a whole country with no topic. For a topic use `get_search_investigators`; never filter this list by topic yourself.",
    "get_growth_stats": "In `mode=bases`, ENA counts nucleotide bases while SRA counts FASTQ/SRA bytes.",
    "get_common_names_by_scientific_names": "Takes one or many scientific names.",
    "get_sample_detail": "Also returns the raw sample attributes; use it for any sample or experiment accession.",
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


def annotate_tool(component: Any) -> None:
    """MCP-only description notes, defaults and a closed input schema."""
    name = component.name
    component.description = replace_rest_paths(
        component.description or "", PATH_TOOL_PATTERNS
    )
    note = MCP_TOOL_NOTES.get(name)
    if note:
        component.description = f"{component.description.rstrip()}\n\n{note}"
    params = component.parameters or {}
    props = params.get("properties") or {}
    for arg, schema in props.items():
        if not (schema.get("description") or "").strip() and (
            doc := param_doc(name, arg)
        ):
            schema["description"] = doc
    for arg, value in MCP_ARG_DEFAULTS.get(name, {}).items():
        if arg in props:
            props[arg]["default"] = value
            desc = props[arg].get("description", "").rstrip()
            props[arg]["description"] = (
                f"{desc} MCP default: {json.dumps(value)}.".lstrip()
            )
    params["additionalProperties"] = False
    TOOL_PARAMS[name] = frozenset(props)


TOOL_PARAMS: dict[str, frozenset[str]] = {}


class SeqoutMcpMiddleware(Middleware):
    """MCP defaults, ignored-argument report, deprecated-tool hiding, line JSON."""

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        return [t for t in tools if t.name not in MCP_DEPRECATED_TOOLS]

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        name = context.message.name
        args = dict(context.message.arguments or {})
        if name in MCP_DEPRECATED_TOOLS:
            logger.warning(
                "deprecated MCP tool %s called; use %s",
                name,
                MCP_DEPRECATED_TOOLS[name],
            )
        known = TOOL_PARAMS.get(name)
        ignored = sorted(k for k in args if known is not None and k not in known)
        for k in ignored:
            del args[k]
        for k, v in MCP_ARG_DEFAULTS.get(name, {}).items():
            args.setdefault(k, v)
        message = context.message.model_copy(update={"arguments": args})
        result = await call_next(context.copy(message=message))
        structured = result.structured_content
        if not isinstance(structured, dict):
            return result
        structured = split_project_alias(structured)
        rows = structured.get("results")
        if isinstance(rows, list) and rows and "linked_accessions" in rows[0]:
            structured = {
                **structured,
                "results": [
                    split_project_alias(r) if isinstance(r, dict) else r
                    for r in structured["results"]
                ],
            }
        if ignored:
            structured = {**structured, "ignored_params": ignored}
        return ToolResult(
            content=[TextContent(type="text", text=line_json(structured))],
            structured_content=structured,
            meta=result.meta,
        )
