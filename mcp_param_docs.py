"""MCP descriptions for parameters whose Query() has none; tool:param beats bare param."""

_ARCHIVES = "geo, sra, arrayexpress, ena, gsa, dra or gea"
_EXACT = "Exact value from {facets}; the study must carry it."
_BOOL_KNOWN = "true or false; NULL rows (unknown) never match."
_YEAR_PUB = "Publication year of the study (first published), exact match."
_Q_SUBSTR = "Case-insensitive substring of the title or study_accession."
_ORDER = "Sort direction for sort: asc or desc."
_LIMIT = "Rows per page."
_OFFSET = "Rows to skip, for paging."
_CURSOR_SORT = "Sorted paging: `next_cursor.sort_value` of the previous page, sent with cursor_acc."
_CURSOR_ACC = "`next_cursor.accession` of the previous page."
_PROJECT = "Project accession: GSE, SRP/ERP/DRP, PRJ*, CRA/HRA or E-XXXX-N."
_STUDY = "Study accession: SRP/ERP/DRP, PRJ*, CRA/HRA or E-GEAD-N."
_EXPERIMENT = "Experiment accession: SRX/ERX/DRX, CRX or HRX."
_RUN = "Run accession: SRR, ERR or DRR."
_ROW_CURSOR = "Row offset: `next_cursor` of the previous page; 0 for the first page."
_DL_MODE = (
    "fastq (EBI FASTQ), sra, sra_lite (smaller, lossy quality scores), s3 or gcs."
)


def _collection(facets: str) -> dict[str, str]:
    """Filters shared by the collection project tools, read from one facets tool."""
    exact = _EXACT.format(facets=facets)
    return {
        "organism": f"Scientific name, e.g. Homo sapiens. {exact}",
        "tissue": f"Tissue name. {exact}",
        "assay_l1": f"Assay category. {exact}",
        "platform": f"Platform. {exact}",
        "chemistry": f"Chemistry. {exact}",
        "technology": f"Technology. {exact}",
        "instrument_model": f"Instrument model, e.g. PacBio Revio. {exact}",
        "library_strategy": f"Library strategy, e.g. WGS, RNA-Seq. {exact}",
        "readout_assay": f"Sample-level assay category. {exact}",
        "cell_line": f"Named cell line. {exact}",
        "sample_type": f"Material type (cell line, primary tissue, organoid, ...). {exact}",
        "perturbation_method": f"Named method (Perturb-seq, CROP-seq, ...). {exact}",
        "source": f"Archive: {_ARCHIVES}. {exact}",
        "journal": f"Journal of the linked paper. {exact}",
        "country": f"Submitter country, ISO-3166-1 alpha-2 (IN, US). {exact}",
        "year": _YEAR_PUB,
        "q": _Q_SUBSTR,
        "order": _ORDER,
        "limit": _LIMIT,
        "offset": _OFFSET,
        "cursor_sort": _CURSOR_SORT,
        "cursor_acc": _CURSOR_ACC,
        "has_fastq": f"Has EBI FASTQ links. {_BOOL_KNOWN}",
        "has_sra": f"Has .sra files. {_BOOL_KNOWN}",
        "has_matrix": "Has a counted expression matrix.",
        "is_long_read": "Also has PacBio or Oxford Nanopore sequencing.",
        "is_single_cell": "Single-cell status of the study.",
    }


_SORT = "Sort column; one of the values in the pattern."
_VIEWS = ("summary", "facets", "projects")

MCP_PARAM_DOCS: dict[str, str] = {
    # bare names, any tool
    "accession": _PROJECT,
    "study": _STUDY,
    "run": _RUN,
    "limit": _LIMIT,
    "offset": _OFFSET,
    "cursor": _ROW_CURSOR,
    "order": _ORDER,
    "sort": _SORT,
    "mode": _DL_MODE,
    "db": f"Restrict to one archive: {_ARCHIVES}. Omit for all.",
    "q": _Q_SUBSTR,
    "year_from": "Lower bound on the release year (first public), inclusive.",
    "year_to": "Upper bound on the release year (first public), inclusive.",
    # per tool
    **{
        f"get_disease_{v}:collection": "rare (GARD) or nord (NORD) catalogue, or a free-text disease name."
        for v in _VIEWS
    },
    **{
        f"get_country_{v}:code": "Country, ISO-3166-1 alpha-2, e.g. IN, US."
        for v in _VIEWS
    },
    **{
        f"get_tissue_{v}:term": "Tissue name; matches UBERON labels (exact, else substring) and their descendants."
        for v in _VIEWS
    },
    **{
        f"{t}:accession": "GEO series (GSE) or ArrayExpress experiment (E-XXXX-N)."
        for t in ("get_supplementary_files", "download_supplementary_script")
    },
    **{
        f"{t}:mode": f"Download source: {_DL_MODE}"
        for t in ("download_study_script", "get_study_download_manifest")
    },
    "get_experiment:accession": _EXPERIMENT,
    "get_experiment_runs:accession": _EXPERIMENT,
    "get_experiment_metadata_rows:accession": _EXPERIMENT,
    "get_experiment_enriched:accession": _EXPERIMENT,
    "get_sample_detail:accession": "Sample or experiment accession: GSM, SRX/ERX/DRX, SRS/ERS/DRS or SAM*.",
    "get_sample_experiments:accession": "Sample accession: SRS/ERS/DRS, SAM* or HRS.",
    "get_geo_series_samples:accession": "GEO series (GSE), ArrayExpress experiment (E-XXXX-N) or DDBJ GEA series (E-GEAD-N).",
    "get_ae_experiment_samples:accession": "ArrayExpress experiment accession, E-XXXX-N.",
    "get_project_similar:accession": "GEO series (GSE) or SRA study (SRP/ERP/DRP).",
    "classify_accession:accession": "Any accession string to identify, e.g. GSE12345 or SRR123.",
    "get_platform_growth:mode": "experiments: monthly experiment counts; projects: monthly project counts.",
    "get_organism_totals:year_from": "Lower bound on the publication year, inclusive.",
    "get_organism_totals:year_to": "Upper bound on the publication year, inclusive.",
    "get_last_updated:db": f"One archive ({_ARCHIVES}) for its newest record date; omit for the whole index.",
    "resolve_submission_studies:submission": "Submission accession: SRA, ERA or DRA plus digits.",
    "get_common_names_by_scientific_names:scientific_name": "Scientific names to resolve, e.g. Mus musculus; repeat for a list.",
    "find_publication:pmid": "PubMed id, digits only. Give pmid or doi.",
    "find_publication:doi": "DOI, e.g. 10.1038/s41586-020-2649-2. Give pmid or doi.",
    "get_project_cite:type": "original: the earliest linked paper (smallest PMID); all: every linked paper.",
    "get_project_cite:format": "json: citation records; bibtex: plain BibTeX text.",
    "ontology_term:term": "Ontology term name, any case, e.g. hepatocyte.",
    "ontology_term:max_hops": "Bounds the MAPS_TO synonym walk, 1-4; children are always direct.",
    "ontology_term:children": "Include the direct hierarchy children; false is much cheaper.",
    "ontology_search:q": "Term name to look up; typos are tolerated.",
    "ontology_search:limit": "Maximum terms to return.",
    "get_enriched_crosstab:group": "Enriched column to group by, e.g. organism; must be in the curated crosstab set.",
    "get_enriched_crosstab:breakdown": "Enriched column counted within each group, e.g. tissue; curated crosstab set.",
    "get_enriched_column:column": "Enriched column, e.g. tissue, cell_type, disease, assay.",
    "get_enriched_coverage:limit": "Maximum values per dimension.",
    "list_journals:limit": "Maximum journals to return, most frequent first.",
    "list_centers:limit": "Maximum centers to return, most frequent first.",
    "list_single_cell_studies:min_evidence": "Minimum independent single-cell measurements, 1-3: matrix, barcode whitelist, R1 length.",
    "list_single_cell_studies:require_matrix": "Only studies with a counted matrix.",
    "get_project_single_cell:all_rows": "List every panel organism in `detections`, bacterial and background hits too.",
    "get_disease_projects:has_fastq": "Has EBI FASTQ links. Only true is accepted; NULL means unknown.",
    "get_disease_projects:has_sra": "Has .sra files. Only true is accepted; NULL means unknown.",
    "get_disease_projects:category": "GARD disease category (rare only). Values: get_disease_facets.",
    "get_disease_projects:specialty": "GARD clinical specialty (rare only). Values: get_disease_facets.",
    "get_disease_projects:group": "GARD disease group (rare only). Values: get_disease_facets.",
    "get_disease_projects:nord_type": "NORD disease type (nord only). Values: get_disease_facets.",
    "get_disease_projects:ancestry": "Submitter-reported ethnicity, verbatim. Values: get_disease_facets.",
    "get_disease_projects:inheritance": "Mode of inheritance (HPO, Orphanet, GARD). Values: get_disease_facets.",
    "get_disease_projects:disease": "Disease name or alias; matches its MONDO id and descendants.",
    "get_disease_projects:assay_category": "Assay category. Values: get_disease_facets.",
    "get_disease_projects:scope": "human_primary (default), patient_derived_model, cell_line or all.",
    "get_longread_projects:archive": f"Archive the study appears in: {_ARCHIVES}.",
    "get_longread_projects:long_read_only": "Every experiment of the study is long-read. NULL when no experiment rows exist.",
    "get_longread_projects:has_exact_chemistry": "At least one run with BAM-header-measured chemistry.",
    "get_singlecell_projects:cell_or_nucleus": "cell or nucleus, read-derived. Values: get_singlecell_facets.",
    "get_singlecell_projects:intervention_kind": "genetic or chemical, from the matched method.",
    "get_singlecell_projects:modality": "Single-cell modality. Values: get_singlecell_facets.",
    "get_perturbation_projects:perturbation_type": "genetic, chemical, both or other.",
    "get_perturbation_projects:confidence": "One tier exactly: high, medium or low.",
    "get_perturbation_projects:min_confidence": "At least this tier: medium keeps high and medium.",
    "get_perturbation_projects:data_availability": "both, matrix_only, fastq_only or neither.",
    "get_perturbation_projects:genetic_subtype": "CRISPR KO/i/a, RNAi, ORF, base/prime editing. Values: get_perturbation_facets.",
    "get_perturbation_projects:compound": "ChEBI-matched compound name. Values: get_perturbation_facets.",
    "get_perturbation_projects:has_control_arm": "The design has a control arm.",
    "get_perturbation_projects:is_pooled": "Pooled screen, from the matched method.",
    "get_spatial_projects:resolution": "single-cell, spot or roi; null rows (no named platform) never match.",
    "get_spatial_projects:technology": "imaging, sequencing or hybrid.",
    "get_spatial_projects:data_availability": "both, matrix_only, fastq_only or neither.",
    "get_search_investigators:q": 'Keywords, or a boolean query such as ("aging" OR aged) immun*.',
    "get_search_investigators:structured": "Parse q as a boolean query even without operators.",
    "get_search_investigators:organism": "Exact scientific name, e.g. Homo sapiens (case-insensitive).",
    "get_search_investigators:library_strategy": "Library strategies (RNA-Seq, ChIP-Seq, WGS, ...); a study matches any of them.",
    "get_search_investigators:library_source": "Library sources (GENOMIC, TRANSCRIPTOMIC, ...), any case; any of them.",
    "get_search_investigators:instrument_model": "Exact instrument models, e.g. Illumina NovaSeq 6000; any of them.",
    "get_search_investigators:platform": "Platform codes such as ILLUMINA, OXFORD_NANOPORE, PACBIO_SMRT.",
    "get_search_investigators:journal": "Exact journal names of the linked paper; any of them.",
    "get_search_investigators:limit": "Maximum investigators to return.",
    "get_study_experiments:limit": "Rows per page; omit for every row.",
    "get_geo_series_samples:limit": "Rows per page; omit for every row.",
    "get_project_enriched:limit": "Rows per page; omit for every row.",
}


# collection project tool -> its shared filter docs
_COLLECTION_DOCS = {
    f"get_{c}_projects": _collection(f"get_{c}_facets")
    for c in (
        "longread",
        "singlecell",
        "perturbation",
        "spatial",
        "country",
        "tissue",
        "disease",
    )
}


def param_doc(tool: str, param: str) -> str | None:
    return (
        MCP_PARAM_DOCS.get(f"{tool}:{param}")
        or _COLLECTION_DOCS.get(tool, {}).get(param)
        or MCP_PARAM_DOCS.get(param)
    )
