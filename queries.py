"""SQL queries for pysradb-server"""

import math

_EARTH_RADIUS_KM = 6371.0088

ONTOLOGY_FIELDS: dict[str, str] = {
    "disease": "mondo",
    "tissue": "uberon",
    "cell_type": "cl",
    "assay": "efo",
    "development_stage": "uberon",
}

DAYS_PER_YEAR = 365.25


def _as_list(value):
    """Scalar -> [scalar], list/tuple -> list; None/""/[] -> None (filter is off)."""
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, (list, tuple, set)):
        vals = [v for v in value if v not in (None, "")]
        return vals or None
    return [value]


STUDY_PUBLICATIONS_BATCH_SQL = """
SELECT accession, publications
FROM study_publications
WHERE accession = ANY(%s);
"""


def _xref_pmid(a: str = "") -> str:
    return f"btrim({a}links->'XREF_LINK'->>'ID')"


def _xref_is_pubmed(a: str = "") -> str:
    return f"upper({a}links->'XREF_LINK'->>'DB') = 'PUBMED'"


_NOT_PRIVATE = "title NOT ILIKE 'GEO accession %% is currently private%%'"

# src_rank encodes publication-source precedence within one accession:
#   0 = the archive's own native pubmed_id (most authoritative)
#   1 = pubmed_id_citation (DOI resolved from the submitter's <Citation>)
#   2 = pubmed_id_pysradb (pysradb's SRA->PubMed elink; can be a citing paper)
# The GROUP BY keeps the lowest rank per (accession, pmid) and ORDER BY makes the
# lowest-rank pmid sort first, so it becomes publications[0] / the scalar pmid.
PMID_VARIANT_BATCH_SQL = f"""
SELECT accession, pmid FROM (
    SELECT accession, pmid, min(src_rank) AS src_rank FROM (
        SELECT accession, unnest(pubmed_id) AS pmid, 0 AS src_rank
            FROM geo_series WHERE accession = ANY(%(acc)s)
        UNION ALL
        SELECT accession, unnest(pubmed_id_citation), 1
            FROM geo_series WHERE accession = ANY(%(acc)s)
        UNION ALL
        SELECT accession, pubmed_id_pysradb, 2
            FROM geo_series WHERE accession = ANY(%(acc)s)
        UNION ALL
        SELECT accession, {_xref_pmid()}, 0
            FROM sra_studies WHERE accession = ANY(%(acc)s)
            AND {_xref_is_pubmed()}
            AND {_NOT_PRIVATE}
        UNION ALL
        SELECT accession, pubmed_id_pysradb, 2
            FROM sra_studies WHERE accession = ANY(%(acc)s)
            AND {_NOT_PRIVATE}
        UNION ALL
        SELECT study_accession, pubmed_id_pysradb, 2
            FROM ena_studies WHERE study_accession = ANY(%(acc)s)
        UNION ALL
        SELECT accession, pubmed_id, 0
            FROM arrayexpress_experiments WHERE accession = ANY(%(acc)s)
        UNION ALL
        SELECT accession, pubmed_id_pysradb, 2
            FROM arrayexpress_experiments WHERE accession = ANY(%(acc)s)
        UNION ALL
        SELECT accession, {_xref_pmid()}, 0
            FROM dra_studies WHERE accession = ANY(%(acc)s)
            AND {_xref_is_pubmed()}
        UNION ALL
        SELECT accession, pubmed_id_pysradb, 2
            FROM dra_studies WHERE accession = ANY(%(acc)s)
        UNION ALL
        SELECT accession, pubmed_id, 0
            FROM gea_experiments WHERE accession = ANY(%(acc)s)
        UNION ALL
        SELECT accession, pubmed_id_pysradb, 2
            FROM gea_experiments WHERE accession = ANY(%(acc)s)
        UNION ALL
        SELECT s.study_accession, jsonb_array_elements_text(p.pubmed_id), 0
            FROM gsa_studies s JOIN gsa_projects p ON p.accession = s.project_name
            WHERE s.study_accession = ANY(%(acc)s)
        UNION ALL
        SELECT study_accession, pubmed_id_pysradb, 2
            FROM gsa_studies WHERE study_accession = ANY(%(acc)s)
    ) u
    WHERE pmid IS NOT NULL AND pmid <> ''
    GROUP BY accession, pmid
) t
ORDER BY accession, src_rank, pmid;
"""

# Rich publication details by pmid, for pmids resolved from the variant tables
# that never made it into study_publications.
PUBMED_BY_PMIDS_BATCH_SQL = """
SELECT pmid, title, journal, doi, pub_date, authors, issn, citation_count,
       journal_h_index, journal_i10_index, journal_2yr_mean_citedness,
       journal_cited_by_count, journal_works_count
FROM pubmed_metadata
WHERE pmid = ANY(%(pmids)s);
"""

# ENA and GSA have no alias source
ALIASES_BATCH_SQL = """
SELECT DISTINCT accession, alias FROM (
    SELECT accession, unnest(alias_all) FROM geo_series WHERE accession = ANY(%(acc)s)
    UNION ALL
    SELECT accession, alias FROM sra_studies WHERE accession = ANY(%(acc)s)
    UNION ALL
    SELECT accession, alias FROM arrayexpress_experiments WHERE accession = ANY(%(acc)s)
    UNION ALL
    SELECT accession, alias FROM dra_studies WHERE accession = ANY(%(acc)s)
    UNION ALL
    SELECT DISTINCT g.accession, d.accession
        FROM gea_experiments g JOIN dra_studies d ON d.bioproject_id = g.bioproject
       WHERE g.accession = ANY(%(acc)s)
    UNION ALL
    SELECT a.acc, um.canonical_accession
        FROM unified_metadata um
        CROSS JOIN LATERAL unnest(alias_values(um.aliases)) AS a(acc)
       WHERE alias_values(um.aliases) && %(acc)s::text[]
         AND a.acc = ANY(%(acc)s)
) u(accession, alias) WHERE alias IS NOT NULL;
"""

SUBMITTER_BATCH_SQL = """
SELECT canonical_accession AS accession, center_name AS submitter, country_code,
       single_cell_status,
       CASE
         WHEN single_cell_status IS NULL OR single_cell_status = 'not_screened'
           THEN NULL
         WHEN single_cell_status IN ('measured_single_cell',
                                     'text_indicates_single_cell')
           THEN TRUE
         ELSE FALSE
       END AS is_single_cell,
       single_cell_modality
FROM unified_metadata
WHERE canonical_accession = ANY(%(acc)s);
"""

# GSA's submitter/org live on the linked BioProject, not unified_metadata (which
# has no GSA rows), same join GSA_PROJECT_SQL uses, batched for result rows.
GSA_SUBMITTER_BATCH_SQL = """
SELECT es.study_accession AS accession,
       NULLIF(btrim(p.submitter), '') AS submitter_person,
       NULLIF(btrim(p.submitting_org), '') AS center_name
FROM gsa_studies es
JOIN gsa_projects p ON p.accession = es.project_name
WHERE es.study_accession = ANY(%(acc)s);
"""

# Authoritative author list for a GEO series
# Used as the author fallback when a series has no linked publication.
GEO_SERIES_CONTRIBUTORS_BATCH_SQL = """
SELECT accession, contributors
FROM geo_series
WHERE accession = ANY(%(acc)s) AND contributors IS NOT NULL;
"""

# Raw submitter-provided <Citation> text (DOI/URL/free text) for a GEO series.
# Last-resort publication display when no candidate resolved to a PMID.
GEO_SERIES_CITATION_BATCH_SQL = """
SELECT accession, citation
FROM geo_series
WHERE accession = ANY(%(acc)s) AND citation IS NOT NULL;
"""

GEO_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    -- Full-text search match (priority 4; includes contributors/departments/organizations in geo_series.tsv)
    SELECT accession, title, summary, updated_at, organisms, countries_iso AS countries,
           ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2 AS rank, 'geo'::text AS source, 4 AS priority, instrument_models, library_strategies
    FROM geo_series, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
),
counted AS (
    -- total deferred to /search/facets (filtered by db); see SEARCH_SQL note.
    SELECT *, NULL::bigint AS total_count FROM all_matches
),
paged AS (
    SELECT *
    FROM counted
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
OFFSET COALESCE(%s::bigint, 0)
LIMIT 200;
"""


SRA_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    -- Full-text search match (priority 6)
    SELECT accession, title, abstract AS summary, updated_at, organisms, countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'sra'::text AS source, 6 AS priority, instrument_models, library_strategies
    FROM sra_studies, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
),
counted AS (
    -- total deferred to /search/facets (filtered by db); see SEARCH_SQL note.
    SELECT *, NULL::bigint AS total_count FROM all_matches
),
paged AS (
    SELECT *
    FROM counted
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
OFFSET COALESCE(%s::bigint, 0)
LIMIT 200;
"""


AE_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    -- Full-text search match (priority 4)
    SELECT accession, title, description AS summary, release_date AS updated_at, organisms, countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'arrayexpress'::text AS source, 4 AS priority, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies
    FROM arrayexpress_experiments, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
),
counted AS (
    -- total deferred to /search/facets (filtered by db); see SEARCH_SQL note.
    SELECT *, NULL::bigint AS total_count FROM all_matches
),
paged AS (
    SELECT *
    FROM counted
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
OFFSET COALESCE(%s::bigint, 0)
LIMIT 200;
"""


ENA_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    -- Full-text search match (priority 4)
    SELECT accession,
           COALESCE(study_title, study_name, project_name, accession) AS title,
           study_description AS summary,
           COALESCE(last_updated, first_public) AS updated_at,
           NULL::text[] AS organisms,
           NULL::text[] AS countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'ena'::text AS source, 4 AS priority,
           NULL::text[] AS instrument_models,
           NULL::text[] AS library_strategies
    FROM ena_studies_search, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
),
counted AS (
    -- total deferred to /search/facets (filtered by db); see SEARCH_SQL note.
    SELECT *, NULL::bigint AS total_count FROM all_matches
),
paged AS (
    SELECT *
    FROM counted
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
OFFSET COALESCE(%s::bigint, 0)
LIMIT 200;
"""


GSA_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    -- Full-text search match (priority 4)
    SELECT accession,
           COALESCE(study_title, project_title, study_name, project_name, accession) AS title,
           COALESCE(study_description, project_summary) AS summary,
           COALESCE(last_updated, first_public) AS updated_at,
           organisms,
           ARRAY['China']::text[] AS countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'gsa'::text AS source, 4 AS priority,
           NULL::text[] AS instrument_models,
           NULL::text[] AS library_strategies
    FROM gsa_studies_search, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
),
counted AS (
    -- total deferred to /search/facets (filtered by db); see SEARCH_SQL note.
    SELECT *, NULL::bigint AS total_count FROM all_matches
),
paged AS (
    SELECT *
    FROM counted
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
OFFSET COALESCE(%s::bigint, 0)
LIMIT 200;
"""

# db=dra. Only the DDBJ-exclusive studies are indexed (see the DRA arm of SEARCH_SQL).
DRA_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    SELECT accession,
           COALESCE(study_title, study_name, project_name, accession) AS title,
           study_description AS summary,
           COALESCE(last_updated, first_public) AS updated_at,
           NULL::text[] AS organisms,
           ARRAY['Japan']::text[] AS countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'dra'::text AS source, 4 AS priority,
           NULL::text[] AS instrument_models,
           NULL::text[] AS library_strategies
    FROM dra_studies_search, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
),
counted AS (
    SELECT *, NULL::bigint AS total_count FROM all_matches
),
paged AS (
    SELECT *
    FROM counted
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
OFFSET COALESCE(%s::bigint, 0)
LIMIT 200;
"""

# db=gea. Like ArrayExpress, the tsv lives on the experiment table itself.
GEA_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    SELECT accession,
           title,
           description AS summary,
           release_date AS updated_at,
           CASE WHEN NULLIF(btrim(organism), '') IS NOT NULL
                THEN ARRAY[organism] END::text[] AS organisms,
           ARRAY['Japan']::text[] AS countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'gea'::text AS source, 4 AS priority,
           NULL::text[] AS instrument_models,
           NULL::text[] AS library_strategies
    FROM gea_experiments, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
),
counted AS (
    SELECT *, NULL::bigint AS total_count FROM all_matches
),
paged AS (
    SELECT *
    FROM counted
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
OFFSET COALESCE(%s::bigint, 0)
LIMIT 200;
"""


SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    -- GEO full-text search (includes contributors/departments/organizations in geo_series.tsv)
    SELECT accession, title, summary, updated_at, organisms, countries_iso AS countries,
           ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2 AS rank, 'geo'::text AS source,
           accession AS dedup_key, instrument_models, library_strategies
    FROM geo_series, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)

    UNION ALL

    -- SRA full-text search (lower rank multiplier)
        SELECT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms, s.countries,
           (ts_rank(s.tsv, q.core_q) + ts_rank(s.tsv, q.match_q) * 0.2) * 0.7 AS rank, 'sra'::text AS source,
           COALESCE(s.alias, s.accession) AS dedup_key, s.instrument_models, s.library_strategies
    FROM sra_studies s, q
    WHERE (s.tsv @@ q.match_q OR s.tsv @@ q.core_q)

    UNION ALL

    -- ArrayExpress full-text search
    SELECT accession, title, description AS summary, release_date AS updated_at, organisms, countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'arrayexpress'::text AS source,
           COALESCE(alias, accession) AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies
    FROM arrayexpress_experiments, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)

    UNION ALL

    -- ENA full-text search
    SELECT accession,
           COALESCE(study_title, study_name, project_name, accession) AS title,
           study_description AS summary,
           COALESCE(last_updated, first_public) AS updated_at,
           NULL::text[] AS organisms, NULL::text[] AS countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'ena'::text AS source,
           accession AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies
    FROM ena_studies_search, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)

    UNION ALL

    -- GSA full-text search (title/abstract/organism enriched from the BioProject
    -- and materialized onto gsa_studies_search by transfer.gsa_search)
    SELECT gss.accession,
           COALESCE(gss.study_title, gss.project_title, gss.study_name, gss.project_name, gss.accession) AS title,
           COALESCE(gss.study_description, gss.project_summary) AS summary,
           COALESCE(gss.last_updated, gss.first_public) AS updated_at,
           gss.organisms,
           ARRAY['China']::text[] AS countries,
           (ts_rank(gss.tsv, q.core_q) + ts_rank(gss.tsv, q.match_q) * 0.2) * 0.7 AS rank, 'gsa'::text AS source,
           gss.accession AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies
    FROM gsa_studies_search gss, q
    WHERE (gss.tsv @@ q.match_q OR gss.tsv @@ q.core_q)

    UNION ALL

    -- dra_studies_search holds only the DDBJ-exclusive studies (is_only_ddbj = 1)
    SELECT accession,
           COALESCE(study_title, study_name, project_name, accession) AS title,
           study_description AS summary,
           COALESCE(last_updated, first_public) AS updated_at,
           NULL::text[] AS organisms, ARRAY['Japan']::text[] AS countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'dra'::text AS source,
           accession AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies
    FROM dra_studies_search, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)

    UNION ALL

    -- GEA (DDBJ E-GEAD) full-text search. Like ArrayExpress the tsv lives on the
    -- experiment table itself; organism is a single column, not an array.
    SELECT accession, title, description AS summary, release_date AS updated_at,
           CASE WHEN NULLIF(btrim(organism), '') IS NOT NULL
                THEN ARRAY[organism] END::text[] AS organisms,
           ARRAY['Japan']::text[] AS countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'gea'::text AS source,
           accession AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies
    FROM gea_experiments, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
),
deduped AS (
    SELECT DISTINCT ON (dedup_key)
        accession, title, summary, updated_at, organisms, countries, rank, source, instrument_models, library_strategies
    FROM all_matches
    ORDER BY dedup_key, rank DESC
),
counted AS (
    -- total deferred to /search/facets so this path returns the top page via a
    -- bounded heapsort instead of materializing the full match set for COUNT.
    SELECT *, NULL::bigint AS total_count FROM deduped
),
paged AS (
    SELECT *
    FROM counted
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
OFFSET COALESCE(%s::bigint, 0)
LIMIT 200;
"""


# Boolean/structured queries
# Every FTS path shares one CTE header: plainto_tsquery core_q + a Neo4j-expanded
# OR-union match_q. Boolean mode swaps that header for a literal to_tsquery on both
# core_q and match_q (the user's exact terms, no expansion). structured_search_sql()
# does the swap on ANY search SQL so the 7-source union body never drifts. Two param
# styles: positional %s (unified/per-source SQL) and named %(q)s (filtered/sorted
# builders). After the swap both tsquery slots read the same q, so positional params
# become (tsq, tsq, ...) and named params just need q = tsq (q_variants unused).
_PLAINTO_HEADER_POS = """    SELECT plainto_tsquery('english', %s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q"""
_TSQUERY_HEADER_POS = """    SELECT to_tsquery('english', %s) AS core_q,
           to_tsquery('english', %s) AS match_q"""
_PLAINTO_HEADER_NAMED = _PLAINTO_HEADER_POS.replace("%s", "%(q)s").replace(
    "unnest(%(q)s::text[])", "unnest(%(q_variants)s::text[])"
)
_TSQUERY_HEADER_NAMED = _TSQUERY_HEADER_POS.replace("%s", "%(q)s")


def structured_search_sql(sql):
    """Swap a search SQL's plainto+expansion CTE header for a literal to_tsquery
    (boolean mode). Handles both positional and named param styles; unchanged
    when the header is absent."""
    return sql.replace(_PLAINTO_HEADER_POS, _TSQUERY_HEADER_POS).replace(
        _PLAINTO_HEADER_NAMED, _TSQUERY_HEADER_NAMED
    )


SEARCH_STRUCTURED_SQL = structured_search_sql(SEARCH_SQL)


_SEARCH_MATCH_CTE = """
WITH q AS (
    SELECT plainto_tsquery('english', %(q)s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%(q_variants)s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    SELECT accession, title, summary, updated_at, organisms,
           countries_iso AS countries, instrument_models, library_strategies,
           platforms, NULL::text[] AS library_sources,
           'geo'::text AS source, accession AS dedup_key,
           ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2 AS rank
    FROM geo_series, q
    WHERE (%(no_q)s OR tsv @@ q.match_q OR tsv @@ q.core_q)
    UNION ALL
    SELECT s.accession, s.title, s.abstract AS summary, s.updated_at,
           s.organisms, s.countries, s.instrument_models, s.library_strategies,
           s.platforms, s.library_sources,
           'sra'::text, COALESCE(s.alias, s.accession),
           (ts_rank(s.tsv, q.core_q) + ts_rank(s.tsv, q.match_q) * 0.2) * 0.7
    FROM sra_studies s, q
    WHERE (%(no_q)s OR s.tsv @@ q.match_q OR s.tsv @@ q.core_q)
    UNION ALL
    SELECT accession, title, description AS summary, release_date AS updated_at,
           organisms, countries, NULL::text[], NULL::text[], NULL::text[],
           NULL::text[],
           'arrayexpress'::text, COALESCE(alias, accession),
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7
    FROM arrayexpress_experiments, q
    WHERE (%(no_q)s OR tsv @@ q.match_q OR tsv @@ q.core_q)
    UNION ALL
    -- organisms materialized onto ena_studies_search by transfer.ena_organisms
    -- (DISTINCT ena_samples.scientific_name), so the organism facet includes ENA.
    SELECT accession,
           COALESCE(study_title, study_name, project_name, accession) AS title,
           study_description AS summary,
           COALESCE(last_updated, first_public) AS updated_at,
           organisms, NULL::text[], NULL::text[], NULL::text[], NULL::text[],
           NULL::text[],
           'ena'::text, accession,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7
    FROM ena_studies_search, q
    WHERE (%(no_q)s OR tsv @@ q.match_q OR tsv @@ q.core_q)
    UNION ALL
    -- title/abstract/organisms enriched from the BioProject and materialized
    -- onto gsa_studies_search by transfer.gsa_search, so the organism facet
    -- includes GSA; library_strategies/platforms stay NULL (per-run aggregation
    -- is too costly for the hot path, as with ENA).
    SELECT gss.accession,
           COALESCE(gss.study_title, gss.project_title, gss.study_name, gss.project_name, gss.accession) AS title,
           COALESCE(gss.study_description, gss.project_summary) AS summary,
           COALESCE(gss.last_updated, gss.first_public) AS updated_at,
           gss.organisms,
           ARRAY['China']::text[], NULL::text[], NULL::text[], NULL::text[],
           NULL::text[],
           'gsa'::text, gss.accession,
           (ts_rank(gss.tsv, q.core_q) + ts_rank(gss.tsv, q.match_q) * 0.2) * 0.7
    FROM gsa_studies_search gss, q
    WHERE (%(no_q)s OR gss.tsv @@ q.match_q OR gss.tsv @@ q.core_q)
    UNION ALL
    -- DRA: the twin holds only DDBJ-exclusive studies, so a mirrored DRP cannot
    -- double-count against its SRA/ENA row. organisms materialized onto
    -- dra_studies_search by transfer.dra_organisms (experiment->sample hop).
    SELECT accession,
           COALESCE(study_title, study_name, project_name, accession) AS title,
           study_description AS summary,
           COALESCE(last_updated, first_public) AS updated_at,
           organisms, ARRAY['Japan']::text[], NULL::text[], NULL::text[], NULL::text[],
           NULL::text[],
           'dra'::text, accession,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7
    FROM dra_studies_search, q
    WHERE (%(no_q)s OR tsv @@ q.match_q OR tsv @@ q.core_q)
    UNION ALL
    -- GEA: per-sample organisms (transfer.gea_organisms) with the single-column
    -- organism as fallback, so multi-organism studies join the facet fully.
    SELECT accession, title, description AS summary, release_date AS updated_at,
           COALESCE(organisms, CASE WHEN NULLIF(btrim(organism), '') IS NOT NULL
                THEN ARRAY[organism] END)::text[],
           ARRAY['Japan']::text[], NULL::text[], NULL::text[], NULL::text[],
           NULL::text[],
           'gea'::text, accession,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7
    FROM gea_experiments, q
    WHERE (%(no_q)s OR tsv @@ q.match_q OR tsv @@ q.core_q)
),
-- Filter to the requested source BEFORE the dedup. GEO↔SRA twins share a
-- dedup_key (the SRA study carries the GSE as its alias); deduping first can
-- keep the higher-ranked SRA twin, and then a source = 'geo' filter would
-- delete the GEO row entirely, undercounting the scoped total. Filtering first
-- dedups only within the requested source (accessions are unique there, so no
-- double-count is reintroduced); the unified db IS NULL path is unchanged.
deduped AS (
    -- dedup_key is carried through because it IS unified_metadata.canonical_accession
    -- (COALESCE(alias, accession) for SRA/AE, accession elsewhere). Some SRA studies
    -- are canonicalised to their GSE alias, so joining unified_metadata on accession
    -- would drop them; the investigator ranking joins on dedup_key instead.
    SELECT DISTINCT ON (dedup_key)
        accession, dedup_key, title, summary, updated_at, organisms, countries,
        instrument_models, library_strategies, platforms, library_sources,
        source, rank
    FROM all_matches
    WHERE (%(db)s::text IS NULL OR source = %(db)s)
    ORDER BY dedup_key, rank DESC
),
matched AS (
    SELECT * FROM deduped
)
"""

# Boolean-mode twin of the match CTE, for the facet/count path, so the header
# total counts the SAME set the boolean result list returns.
_SEARCH_MATCH_CTE_STRUCTURED = structured_search_sql(_SEARCH_MATCH_CTE)

# Each sidebar facet, with the column it unnests/derives and the filter-dimension
# key it represents (so it can be excluded from its own narrowing).
_FACET_BRANCHES = (
    ("organism", "value", "matched, unnest(organisms) value"),
    ("country", "value", "matched, unnest(countries) value"),
    ("library_strategy", "value", "matched, unnest(library_strategies) value"),
    ("library_source", "value", "matched, unnest(library_sources) value"),
    ("instrument_model", "value", "matched, unnest(instrument_models) value"),
    ("platform", "value", "matched, unnest(platforms) value"),
    (
        "journal",
        "sp.publications->0->>'journal'",
        "matched JOIN study_publications sp ON sp.accession = matched.accession",
    ),
)


def _search_filter_clauses(
    params,
    organism=None,
    country=None,
    library_strategy=None,
    library_source=None,
    instrument_model=None,
    platform=None,
    journal=None,
    multi_platform=False,
    year_from=None,
    year_to=None,
    date_from=None,
    date_to=None,
):
    """Build sidebar-filter SQL over the `matched` CTE columns.

    Mutates `params` with the bound values and returns
    (dim_clauses: {dimension -> sql}, always_clauses: [sql]). `dim_clauses` is
    keyed by facet dimension so a facet can exclude its own clause (exclude-self
    narrowing); `always_clauses` (year range) apply everywhere. Multi-value
    filters accept lists and match on array overlap / ANY.
    """
    dim_clauses = {}
    organism = _as_list(organism)
    if organism:
        # case-insensitive: unnest so "mus musculus" matches "Mus musculus".
        dim_clauses["organism"] = (
            "EXISTS (SELECT 1 FROM unnest(matched.organisms) o"
            " WHERE lower(o) = ANY(SELECT lower(x)"
            " FROM unnest(%(organism)s::text[]) x))"
        )
        params["organism"] = organism
    if country:
        dim_clauses["country"] = "matched.countries && %(country)s::text[]"
        params["country"] = list(country)
    if library_strategy:
        dim_clauses["library_strategy"] = (
            "matched.library_strategies && %(library_strategy)s::text[]"
        )
        params["library_strategy"] = list(library_strategy)
    if library_source:
        # case-insensitive multi-value: stored values are UPPERCASE canonical
        # (GENOMIC, TRANSCRIPTOMIC, ...) but users type any case.
        dim_clauses["library_source"] = (
            "EXISTS (SELECT 1 FROM unnest(matched.library_sources) ls"
            " WHERE lower(ls) = ANY(SELECT lower(x)"
            " FROM unnest(%(library_source)s::text[]) x))"
        )
        params["library_source"] = list(library_source)
    if instrument_model:
        dim_clauses["instrument_model"] = (
            "matched.instrument_models && %(instrument_model)s::text[]"
        )
        params["instrument_model"] = list(instrument_model)
    if platform or multi_platform:
        platform_parts = []
        if platform:
            platform_parts.append("matched.platforms && %(platform)s::text[]")
            params["platform"] = list(platform)
        if multi_platform:
            platform_parts.append("array_length(matched.platforms, 1) >= 2")
        dim_clauses["platform"] = "(" + " AND ".join(platform_parts) + ")"
    if journal:
        dim_clauses["journal"] = (
            "EXISTS (SELECT 1 FROM study_publications sp2"
            " WHERE sp2.accession = matched.accession"
            " AND sp2.publications->0->>'journal' = ANY(%(journal)s))"
        )
        params["journal"] = list(journal)

    always_clauses = []
    # day-level range wins when given; year_* stays for the structured endpoint.
    if date_from is not None:
        always_clauses.append("matched.updated_at::date >= %(date_from)s")
        params["date_from"] = date_from
    elif year_from is not None:
        always_clauses.append("EXTRACT(YEAR FROM matched.updated_at) >= %(year_from)s")
        params["year_from"] = year_from
    if date_to is not None:
        always_clauses.append("matched.updated_at::date <= %(date_to)s")
        params["date_to"] = date_to
    elif year_to is not None:
        always_clauses.append("EXTRACT(YEAR FROM matched.updated_at) <= %(year_to)s")
        params["year_to"] = year_to

    return dim_clauses, always_clauses


def build_search_match_temp(q=None, q_variants=None, db=None, structured=False):
    """CREATE TEMP TABLE `matched` (ON COMMIT DROP) holding the q+db match set,
    deduped. This is the single expensive FTS pass; the count and facet queries
    then read this materialized table, so a slow facet can't kill the total and
    the FTS is never computed twice. Caller must run inside a transaction.

    structured=True: q is already a to_tsquery string (boolean mode); count the
    literal terms with no expansion, so the total matches the boolean result list.
    """
    cte = _SEARCH_MATCH_CTE_STRUCTURED if structured else _SEARCH_MATCH_CTE
    # Only the columns the count + facet + filter queries actually read.
    # title/summary stay in the shared CTE for the result-list path.
    # rank is kept because facets order by summed relevance.
    sql = (
        "CREATE TEMP TABLE matched ON COMMIT DROP AS\n"
        + cte
        + "\nSELECT accession, dedup_key, rank, updated_at, organisms, countries,"
        + " instrument_models, library_strategies, platforms, library_sources\n"
        + "FROM matched;"
    )
    # no_q is a SQL literal: "FALSE OR tsv @@ q" folds to "tsv @@ q" at plan
    # time, keeping the FTS index in generic/prepared plans too. "TRUE OR ..."
    # folds to TRUE for the query-less full scan.
    sql = sql.replace("%(no_q)s", "TRUE" if not q else "FALSE")
    return sql, {"q": q, "q_variants": q_variants, "db": db}


def build_search_total_over_matched(**filters):
    """Exact result total over the materialized `matched` temp table, with every
    active filter applied (no exclude-self). Filtered scan/count with no FTS.
    """
    params = {}
    dim_clauses, always_clauses = _search_filter_clauses(params, **filters)
    conds = list(dim_clauses.values()) + always_clauses
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return f"SELECT COUNT(*)::int AS total FROM matched{where};", params


def build_search_max_rank_over_matched():
    """Top relevance rank over the whole q+db match set, BEFORE sidebar filters.

    The "Low relevance" badge on a result card is thresholded against this, so it
    has to be the query's own ceiling. Computing it over the FILTERED set would
    defeat the badge exactly where it matters: filter an SMA search down to
    Arabidopsis and every remaining row is a weak match, so the filtered max is
    itself weak and nothing looks unusual. `matched` is the pre-filter set (the
    filters live in the count/facet queries), which is what we want.
    """
    return "SELECT MAX(rank)::float8 AS max_rank FROM matched;", {}


def build_search_facets_over_matched(**filters):
    """Filter-aware sidebar facet counts (exclude-self narrowing) over the
    materialized `matched` temp table.

    Each facet is counted over the match set narrowed by every *other* active
    sidebar filter plus the year range, so selecting one filter narrows the
    others' counts (standard faceted search). Returns (sql, params); top-1000
    values per facet.
    """
    params = {}
    dim_clauses, always_clauses = _search_filter_clauses(params, **filters)

    branches = []
    for facet, value_expr, from_clause in _FACET_BRANCHES:
        conds = [c for dim, c in dim_clauses.items() if dim != facet]
        conds += always_clauses
        conds.append(f"{value_expr} IS NOT NULL")
        conds.append(f"{value_expr} <> ''")
        where = " WHERE " + " AND ".join(conds)
        branches.append(
            f"SELECT '{facet}'::text AS facet, {value_expr} AS value,"
            f" COUNT(*)::int AS count,"
            f" COALESCE(SUM(matched.rank), 0)::float8 AS score"
            f" FROM {from_clause}{where}"
            f" GROUP BY {value_expr}"
        )

    # Ordered by summed match rank. A query can match many studies
    # in one organism for an unrelated reason, and those matches all score near
    # zero. Summing the ranks keeps the ordering count-weighted, so a big
    # well-matched value still beats a single lucky hit. rank is NULL on a
    # query-less search, so score collapses to 0 there and the count tiebreak
    # carries the order.
    sql = (
        "WITH facets AS (\n"
        + "\n    UNION ALL\n".join(branches)
        + "\n),\n"
        + "ranked AS (\n"
        + "    SELECT facet, value, count, score,\n"
        + "           ROW_NUMBER() OVER (PARTITION BY facet"
        + " ORDER BY score DESC, count DESC, value) AS rn\n"
        + "    FROM facets\n"
        + ")\n"
        + "SELECT facet, value, count, score FROM ranked WHERE rn <= 1000\n"
        + "ORDER BY facet, score DESC, count DESC, value;"
    )
    return sql, params


_RANK_KEYSET_WHERE = (
    "    WHERE (%(cursor_rank)s::float8 IS NULL\n"
    "           OR (rank, accession) < (%(cursor_rank)s::float8, %(cursor_acc)s::text))\n"
)


def build_search_results(
    q=None,
    q_variants=None,
    db=None,
    cursor_rank=None,
    cursor_acc=None,
    offset=None,
    sortby=None,
    order="desc",
    **filters,
):
    """Paginated result list over the same filtered match set as the facet/total
    queries, so the list and the sidebar counts always agree.

    Default order is relevance (rank). `sortby` (year|citations|journal) overrides
    it with the same sort expression as build_sorted_search_sql, so filtered and
    unfiltered results order identically. All active filters apply (no
    exclude-self). Offset-paginated, 200/page. total_count is NULL; the exact
    total is deferred to /search/facets. Returns (sql, params) with %(name)s
    params and the same column layout as SEARCH_SQL.
    """
    params = {
        "q": q,
        "q_variants": q_variants,
        "db": db,
        "cursor_rank": cursor_rank,
        "cursor_acc": cursor_acc,
        "offset": offset,
    }
    dim_clauses, always_clauses = _search_filter_clauses(params, **filters)
    conds = list(dim_clauses.values()) + always_clauses
    where = (" WHERE " + " AND ".join(conds)) if conds else ""

    # Sort mirrors build_sorted_search_sql. Offset paginates (the keyset cursor
    # below is only exercised by the unsorted geo path), so re-ordering the final
    # SELECT is all that's needed.
    if sortby in ("citations", "journal", "year"):
        direction = "DESC" if order == "desc" else "ASC"
        if sortby == "citations":
            sort_expr = "COALESCE((sp.publications->0->>'citation_count')::int, 0)"
            join_clause = (
                " LEFT JOIN study_publications sp ON sp.accession = paged.accession"
            )
        elif sortby == "journal":
            sort_expr = "COALESCE(sp.publications->0->>'journal', '')"
            join_clause = (
                " LEFT JOIN study_publications sp ON sp.accession = paged.accession"
            )
        else:  # year
            sort_expr = "COALESCE(paged.updated_at::date, '1970-01-01'::date)"
            join_clause = ""
        order_by = (
            f"ORDER BY {sort_expr} {direction} NULLS LAST, paged.accession {direction}"
        )
        paged_where = _RANK_KEYSET_WHERE
    elif not q:
        # query-less: rank is NULL on every row, so a (rank, accession) keyset
        # guard is always-true and paging never advances (infinite loop). Page by
        # accession alone, the real sort order when all ranks are NULL.
        join_clause = ""
        order_by = "ORDER BY paged.accession DESC"
        paged_where = (
            "    WHERE (%(cursor_acc)s::text IS NULL OR accession < %(cursor_acc)s)\n"
        )
    else:
        join_clause = ""
        order_by = "ORDER BY paged.rank DESC, paged.accession DESC"
        paged_where = _RANK_KEYSET_WHERE

    sql = (
        _SEARCH_MATCH_CTE
        + ", filtered AS (\n"
        + "    SELECT * FROM matched"
        + where
        + "\n"
        + "),\n"
        + "counted AS (\n"
        # total deferred to /search/facets; see SEARCH_SQL note.
        + "    SELECT *, NULL::bigint AS total_count FROM filtered\n"
        + "),\n"
        + "paged AS (\n"
        + "    SELECT * FROM counted\n"
        + paged_where
        + ")\n"
        + "SELECT paged.accession, paged.title, paged.summary, paged.updated_at,\n"
        + "       paged.organisms, paged.countries, paged.rank, paged.source,\n"
        + "       paged.total_count, paged.instrument_models, paged.library_strategies\n"
        + "FROM paged"
        + join_clause
        + "\n"
        + order_by
        + "\n"
        + "OFFSET COALESCE(%(offset)s::bigint, 0)\n"
        + "LIMIT 200;"
    )
    # no_q inlined as a literal so "FALSE OR tsv @@ q" folds to the FTS index
    # scan at plan time (no bind-param plan surprises); see build_search_match_temp.
    sql = sql.replace("%(no_q)s", "TRUE" if not q else "FALSE")
    return sql, params


_RESULTS_PAGE_TAIL = "\nOFFSET COALESCE(%(offset)s::bigint, 0)\nLIMIT 200;"


def strip_result_paging(sql):
    """Drop build_search_results' page tail, returning every match for downloads.

    Raises if the tail moved: a silent no-op would cap every download at the page
    size and look like a working download.
    """
    if _RESULTS_PAGE_TAIL not in sql:
        raise ValueError("result paging tail not found; strip_result_paging is stale")
    return sql.replace(_RESULTS_PAGE_TAIL, ";")


# Per-kind dispatch (vs. one giant UNION CTE) so each lookup gets its own plan;
# bind-param values can't be constant-folded across union branches in a prepared
# statement. Column layout matches SEARCH_SQL so rows flow straight into responses.
_SRA_FIELDS = (
    "s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,"
    " s.countries, 100.0::float8 AS rank, 'sra'::text AS source,"
    " s.instrument_models, s.library_strategies"
)
_GEO_FIELDS = (
    "g.accession, g.title, g.summary, g.updated_at, g.organisms,"
    " g.countries_iso AS countries, 100.0::float8 AS rank, 'geo'::text AS source,"
    " g.instrument_models, g.library_strategies"
)
_AE_FIELDS = (
    "ae.accession, ae.title, ae.description AS summary,"
    " ae.release_date AS updated_at, ae.organisms, ae.countries,"
    " 100.0::float8 AS rank, 'arrayexpress'::text AS source,"
    " NULL::text[] AS instrument_models, NULL::text[] AS library_strategies"
)
_ENA_FIELDS = (
    "es.accession,"
    " COALESCE(es.study_title, es.study_name, es.project_name, es.accession) AS title,"
    " es.study_description AS summary,"
    " COALESCE(es.last_updated, es.first_public) AS updated_at,"
    " NULL::text[] AS organisms, NULL::text[] AS countries,"
    " 100.0::float8 AS rank, 'ena'::text AS source,"
    " NULL::text[] AS instrument_models, NULL::text[] AS library_strategies"
)
_DRA_FIELDS = (
    "ds.accession,"
    " COALESCE(ds.study_title, ds.study_name, ds.project_name, ds.accession) AS title,"
    " ds.study_description AS summary,"
    " COALESCE(ds.last_updated, ds.first_public) AS updated_at,"
    " NULL::text[] AS organisms, ARRAY['Japan']::text[] AS countries,"
    " 100.0::float8 AS rank, 'dra'::text AS source,"
    " NULL::text[] AS instrument_models, NULL::text[] AS library_strategies"
)
_GEA_FIELDS = (
    "ge.accession, ge.title, ge.description AS summary,"
    " ge.release_date AS updated_at,"
    " CASE WHEN NULLIF(btrim(ge.organism), '') IS NOT NULL"
    " THEN ARRAY[ge.organism] END::text[] AS organisms,"
    " ARRAY['Japan']::text[] AS countries,"
    " 100.0::float8 AS rank, 'gea'::text AS source,"
    " NULL::text[] AS instrument_models, NULL::text[] AS library_strategies"
)
_GSA_FIELDS = (
    "es.accession,"
    " COALESCE(es.study_title, es.project_title, es.study_name, es.project_name, es.accession) AS title,"
    " COALESCE(es.study_description, es.project_summary) AS summary,"
    " COALESCE(es.last_updated, es.first_public) AS updated_at,"
    " es.organisms, ARRAY['China']::text[] AS countries,"
    " 100.0::float8 AS rank, 'gsa'::text AS source,"
    " NULL::text[] AS instrument_models, NULL::text[] AS library_strategies"
)

SEARCH_BY_ACCESSION_SQL_BY_KIND: dict[str, str] = {
    "gse": (
        f"SELECT {_GEO_FIELDS} FROM geo_series g WHERE g.accession = %(acc)s LIMIT 1"
    ),
    "gsm": (
        f"SELECT {_GEO_FIELDS} FROM geo_series g"
        f" WHERE g.samples_ref @> ARRAY[%(acc)s] LIMIT 1"
    ),
    # A pattern match resolving to nothing returns empty, never full-text search.
    # DDBJ-exclusive DRPs need their own arm; dra_studies_search holds exactly those.
    "srp": (
        f"SELECT {_SRA_FIELDS} FROM sra_studies s WHERE s.accession = %(acc)s"
        f" UNION ALL "
        f"SELECT {_DRA_FIELDS} FROM dra_studies_search ds"
        f" WHERE ds.accession = %(acc)s"
        f" LIMIT 2"
    ),
    "srx": (
        f"SELECT {_SRA_FIELDS} FROM sra_experiments e"
        f" JOIN sra_studies s ON s.accession = e.study"
        f" WHERE e.accession = %(acc)s LIMIT 1"
    ),
    "srs": (
        f"SELECT DISTINCT ON (s.accession) {_SRA_FIELDS} FROM sra_experiments e"
        f" JOIN sra_studies s ON s.accession = e.study"
        f" WHERE e.samples @> ARRAY[%(acc)s]"
        f" ORDER BY s.accession LIMIT 1"
    ),
    # ena_experiments.study_accession holds the BioProject ID (PRJ*), not an
    # SRP/ERP/DRP; the joins below match it against sra_studies.bioproject_id
    # and ena_studies_search.accession (also PRJ-keyed).
    "srr": (
        f"SELECT {_SRA_FIELDS} FROM ena_experiments ee"
        f" JOIN sra_studies s ON s.bioproject_id = ee.study_accession"
        f" WHERE ee.run_accession = %(acc)s"
        f" UNION ALL "
        f"SELECT {_ENA_FIELDS} FROM ena_experiments ee"
        f" JOIN ena_studies_search es ON es.accession = ee.study_accession"
        f" WHERE ee.run_accession = %(acc)s"
        f"   AND NOT EXISTS (SELECT 1 FROM sra_studies ss"
        f"                   WHERE ss.bioproject_id = ee.study_accession)"
        f" UNION ALL "
        f"SELECT {_GSA_FIELDS} FROM gsa_experiments ee"
        f" JOIN gsa_studies_search es ON es.accession = ee.study_accession"
        f" WHERE ee.run_accession = %(acc)s"
        f" LIMIT 3"
    ),
    "sam": (
        f"(SELECT DISTINCT ON (s.accession) {_SRA_FIELDS} FROM ena_experiments ee"
        f" JOIN sra_studies s ON s.bioproject_id = ee.study_accession"
        f" WHERE ee.sample_accession = %(acc)s"
        f" ORDER BY s.accession)"
        f" UNION ALL "
        f"(SELECT DISTINCT ON (es.accession) {_ENA_FIELDS} FROM ena_experiments ee"
        f" JOIN ena_studies_search es ON es.accession = ee.study_accession"
        f" WHERE ee.sample_accession = %(acc)s"
        f"   AND NOT EXISTS (SELECT 1 FROM sra_studies ss"
        f"                   WHERE ss.bioproject_id = ee.study_accession)"
        f" ORDER BY es.accession)"
        f" UNION ALL "
        f"(SELECT DISTINCT ON (es.accession) {_GSA_FIELDS} FROM gsa_experiments ee"
        f" JOIN gsa_studies_search es ON es.accession = ee.study_accession"
        f" WHERE ee.sample_accession = %(acc)s"
        f" ORDER BY es.accession)"
        f" LIMIT 3"
    ),
    # E-GEAD now classifies as "gea" and routes here; the union below stays as a
    # belt-and-suspenders in case an E-GEAD ever reaches the "ae" arm.
    "gea": (
        f"SELECT {_GEA_FIELDS} FROM gea_experiments ge"
        f" WHERE ge.accession = %(acc)s LIMIT 1"
    ),
    # without this arm every E-GEAD accession is a silent zero
    "ae": (
        f"SELECT {_AE_FIELDS} FROM arrayexpress_experiments ae"
        f" WHERE UPPER(ae.accession) = %(acc)s"
        f" UNION ALL "
        f"SELECT {_GEA_FIELDS} FROM gea_experiments ge"
        f" WHERE UPPER(ge.accession) = %(acc)s"
        f" LIMIT 2"
    ),
    "prj": (
        f"SELECT {_SRA_FIELDS} FROM sra_studies s"
        f" WHERE s.bioproject_id = %(acc)s"
        f" UNION ALL "
        f"SELECT {_GEO_FIELDS} FROM geo_series g"
        f" WHERE g.bioproject_id = %(acc)s"
        f" UNION ALL "
        f"SELECT {_ENA_FIELDS} FROM ena_studies_search es"
        f" WHERE es.accession = %(acc)s"
        f" UNION ALL "
        f"SELECT {_GSA_FIELDS} FROM gsa_studies_search es"
        f" LEFT JOIN gsa_studies gs ON gs.study_accession = es.accession"
        f" WHERE es.accession = %(acc)s OR gs.project_name = %(acc)s"
        f" LIMIT 4"
    ),
}


PROJECT_GEO_SQL = """
SELECT
    accession,
    alias_all AS alias,
    title,
    summary,
    overall_design,
    pubmed_id,
    samples_ref,
    series_type,
    relation,
    supplementary_data,
    published_at,
    updated_at,
    coords_2d,
    coords_3d,
    neighbors,
    organisms,
    (SELECT jsonb_agg(DISTINCT jsonb_build_object(
        'organization', c.organization,
        'department', c.department,
        'formatted_address', gc.geo_formatted_address,
        'latitude', gc.geo_latitude,
        'longitude', gc.geo_longitude,
        'city', gc.geo_city,
        'district', gc.geo_district,
        'state', gc.geo_state,
        'postcode', gc.geo_postcode,
        'country', gc.geo_country,
        'country_code', gc.geo_country_code,
        'place_name', gc.geo_place_name
     ))
     FROM geo_contributors c
     LEFT JOIN geo_geocode gc ON gc.address_key =
        c.organization || '|' || COALESCE(c.department,'') || '|' ||
        COALESCE(c.address_line,'') || '|' || COALESCE(c.address_city,'') || '|' ||
        COALESCE(c.address_state,'') || '|' || COALESCE(c.address_country,'')
     WHERE c.accession = geo_series.accession
    ) AS center
FROM geo_series
WHERE accession = %s;
"""
PROJECT_SRA_SQL = """
SELECT
    s.accession,
    s.alias,
    s.title,
    s.abstract,
    s.submission,
    s.study_type,
    s.external_id,
    s.links,
    s.published_at,
    s.updated_at,
    s.coords_2d,
    s.coords_3d,
    s.neighbors,
    s.organisms,
    jsonb_build_object(
        'organization', sub.center_name,
        'formatted_address', COALESCE(sg.formatted_address, eg.formatted_address),
        'latitude', COALESCE(sg.latitude, eg.latitude),
        'longitude', COALESCE(sg.longitude, eg.longitude),
        'city', COALESCE(sg.city, eg.city),
        'district', COALESCE(sg.district, eg.district),
        'state', COALESCE(sg.state, eg.state),
        'postcode', COALESCE(sg.postcode, eg.postcode),
        'country', COALESCE(sg.country, eg.country),
        'country_code', sg.country_code,
        'place_name', sg.place_name
    ) AS center
FROM sra_studies s
LEFT JOIN sra_submissions sub ON sub.accession = s.submission
LEFT JOIN sra_geocode sg ON sg.center_name = sub.center_name
LEFT JOIN ena_geocode eg ON eg.center_name = sub.center_name
WHERE s.accession = %s;
"""

# DDBJ-exclusive studies. A mirrored DRP is served by PROJECT_SRA_SQL.
# Shaped like PROJECT_SRA_SQL so the SRA project page renders it unchanged.
PROJECT_DRA_SQL = """
SELECT
    d.accession,
    d.alias,
    d.title,
    d.abstract,
    d.submission,
    d.study_type,
    d.external_id,
    d.links,
    d.published_at,
    d.updated_at,
    d.coords_2d,
    d.coords_3d,
    d.neighbors,
    (SELECT array_agg(DISTINCT sm.scientific_name)
     FROM dra_experiments e
     JOIN dra_samples sm ON sm.accession = ANY (e.samples)
     WHERE e.study = d.accession
       AND sm.scientific_name IS NOT NULL) AS organisms,
    jsonb_build_object(
        'organization', COALESCE(sub.center_name, d.center_name)
    ) AS center
FROM dra_studies d
LEFT JOIN dra_submissions sub ON sub.accession = d.submission
WHERE d.accession = %s;
"""

PROJECT_ENA_SQL = """
SELECT
    es.study_accession AS accession,
    NULL::text AS alias,
    es.study_title AS title,
    es.study_description AS abstract,
    NULL::text AS submission,
    NULL::text AS study_type,
    NULL::text AS external_id,
    NULL::jsonb AS links,
    NULL::date AS published_at,
    es.last_updated AS updated_at,
    -- Most ENA projects have no embedding of their own and inherit one from the
    -- SRA/GEO study they wrap (_graft_linked_project); the minority that do have
    -- one must use it, and the graft leaves a populated value alone.
    es.coords_2d,
    es.coords_3d,
    es.neighbors,
    -- Prefer the per-sample organism set (populated by transfer.ena_organisms);
    -- fall back to the study-level scientific_name where a study has no samples,
    -- so this is never worse than the old scalar (no blanking).
    COALESCE(
        es.organisms,
        CASE
            WHEN NULLIF(btrim(es.scientific_name), '') IS NOT NULL
            THEN ARRAY[es.scientific_name]
        END
    )::text[] AS organisms,
    NULL::jsonb AS center,
    -- ENA/DDBJ study hierarchy: a study may nest under an umbrella BioProject
    -- (parent), and umbrella projects may have many children. Child-of is a
    -- single cheap join; parent-of is a capped preview + total (needs the
    -- idx_ena_studies_parent index for large umbrella projects).
    es.parent_study_accession AS parent_accession,
    parent.study_title AS parent_title,
    (
        SELECT count(*) FROM ena_studies ch
        WHERE ch.parent_study_accession = es.study_accession
    ) AS children_total,
    (
        SELECT jsonb_agg(c)
        FROM (
            SELECT ch.study_accession AS accession, ch.study_title AS title
            FROM ena_studies ch
            WHERE ch.parent_study_accession = es.study_accession
            ORDER BY ch.study_accession
            LIMIT 24
        ) c
    ) AS children
FROM ena_studies es
LEFT JOIN ena_studies parent
    ON parent.study_accession = es.parent_study_accession
WHERE es.study_accession = %s
LIMIT 1;
"""

PROJECT_GSA_SQL = """
SELECT
    es.study_accession AS accession,
    NULL::text AS alias,
    es.project_name AS bioproject_id,
    -- open GSA (CRA*) studies often leave study_title blank and carry the title
    -- on the linked BioProject; fall back to it (mirrors the abstract below).
    COALESCE(NULLIF(es.study_title, ''), p.title) AS title,
    -- prefer the linked BioProject abstract (rich) over the study's own
    -- description, which for GSA-Human is often just a disease/scope phrase.
    COALESCE(NULLIF(p.summary, ''), es.study_description) AS abstract,
    NULL::text AS submission,
    NULL::text AS study_type,
    NULL::text AS external_id,
    NULL::jsonb AS links,
    NULL::date AS published_at,
    es.last_updated AS updated_at,
    es.coords_2d,
    es.coords_3d,
    es.neighbors,
    -- Per-sample organism set (transfer.gsa_organisms); fall back to the study
    -- scientific_name where a study has no samples (no blanking).
    COALESCE(
        es.organisms,
        CASE
            WHEN es.scientific_name IS NOT NULL AND es.scientific_name <> ''
            THEN ARRAY[es.scientific_name]
        END
    )::text[] AS organisms,
    -- GSA records a single submitting person, not an author roster; the linked
    -- BioProject carries them along with the organisation. Both live on the join
    -- below and were previously discarded.
    CASE WHEN NULLIF(btrim(p.submitter), '') IS NOT NULL
         THEN ARRAY[p.submitter]
    END::text[] AS authors,
    NULLIF(btrim(p.submitting_org), '') AS center_name,
    CASE WHEN NULLIF(btrim(p.submitting_org), '') IS NOT NULL
         THEN jsonb_build_array(jsonb_build_object(
                  'organization', p.submitting_org))
    END AS center
FROM gsa_studies es
LEFT JOIN gsa_projects p ON p.accession = es.project_name
WHERE es.study_accession = %s
LIMIT 1;
"""

STUDY_EXPERIMENTS_SQL = """
SELECT
    accession,
    title,
    design_description,
    library_layout,
    library_name,
    library_selection,
    library_source,
    library_strategy,
    samples,
    platform,
    instrument_model,
    submission
FROM sra_experiments
WHERE study = %s;
"""

# dra_experiments mirrors sra_experiments column for column (shared INSDC schema).
DRA_STUDY_EXPERIMENTS_SQL = """
SELECT
    accession,
    title,
    design_description,
    library_layout,
    library_name,
    library_selection,
    library_source,
    library_strategy,
    samples,
    platform,
    instrument_model,
    submission
FROM dra_experiments
WHERE study = %s;
"""

ENA_STUDY_EXPERIMENTS_SQL = """
SELECT DISTINCT ON (e.experiment_accession)
    e.experiment_accession AS accession,
    e.experiment_title AS title,
    NULL::text AS design_description,
    e.library_layout,
    e.library_name,
    e.library_selection,
    e.library_source,
    e.library_strategy,
    CASE
        WHEN e.sample_accession IS NOT NULL THEN ARRAY[e.sample_accession]::text[]
        ELSE ARRAY[]::text[]
    END AS samples,
    e.instrument_platform AS platform,
    e.instrument_model,
    NULL::text AS submission
FROM ena_experiments e
WHERE e.study_accession = UPPER(%s)
ORDER BY e.experiment_accession, e.run_accession NULLS LAST;
"""

GSA_STUDY_EXPERIMENTS_SQL = """
SELECT DISTINCT ON (e.experiment_accession)
    e.experiment_accession AS accession,
    e.experiment_title AS title,
    NULL::text AS design_description,
    e.library_layout,
    e.library_name,
    e.library_selection,
    e.library_source,
    e.library_strategy,
    CASE
        WHEN e.sample_accession IS NOT NULL THEN ARRAY[e.sample_accession]::text[]
        ELSE ARRAY[]::text[]
    END AS samples,
    e.instrument_platform AS platform,
    e.instrument_model,
    NULL::text AS submission
FROM gsa_experiments e
WHERE e.study_accession = UPPER(%s)
ORDER BY e.experiment_accession, e.run_accession NULLS LAST;
"""

SAMPLE_SQL = """
SELECT
    accession,
    alias,
    description,
    title,
    scientific_name,
    taxon_id,
    external_id,
    attributes_json,
    submission,
    links
FROM sra_samples
WHERE accession = %s;
"""

ENA_SAMPLE_SQL = """
SELECT
    s.sample_accession AS accession,
    s.sample_alias AS alias,
    s.sample_description AS description,
    s.sample_title AS title,
    s.scientific_name,
    s.tax_id AS taxon_id,
    s.secondary_sample_accession AS external_id,
    COALESCE(
        (
            SELECT jsonb_object_agg(kv.key, kv.value)
            FROM jsonb_each_text(
                jsonb_strip_nulls(
                    to_jsonb(s) - ARRAY[
                        'sample_accession',
                        'secondary_sample_accession',
                        'sample_alias',
                        'sample_title',
                        'sample_description',
                        'tax_id',
                        'scientific_name',
                        'study_accession'
                    ]::text[]
                )
            ) AS kv
        ),
        '{}'::jsonb
    ) AS attributes_json,
    s.submission_accession AS submission,
    NULL::jsonb AS links
FROM ena_samples s
-- sample_accession is uppercase for every row and callers uppercase the input,
-- so match the column directly and hit the ena_samples_pkey; the UPPER() wrap
-- would force the redundant functional index instead.
WHERE s.sample_accession = %s
LIMIT 1;
"""

GSA_SAMPLE_SQL = """
SELECT
    s.sample_accession AS accession,
    s.sample_alias AS alias,
    s.sample_description AS description,
    s.sample_title AS title,
    s.scientific_name,
    NULL::text AS taxon_id,
    NULL::text AS external_id,
    COALESCE(
        (
            SELECT jsonb_object_agg(kv.key, kv.value)
            FROM jsonb_each_text(
                jsonb_strip_nulls(
                    to_jsonb(s) - ARRAY[
                        'sample_accession',
                        'sample_alias',
                        'sample_title',
                        'sample_description',
                        'scientific_name',
                        'study_accession'
                    ]::text[]
                )
            ) AS kv
        ),
        '{}'::jsonb
    ) AS attributes_json,
    NULL::text AS submission,
    NULL::jsonb AS links
FROM gsa_samples s
WHERE UPPER(s.sample_accession) = UPPER(%s)
LIMIT 1;
"""

BATCH_SAMPLES_SQL = """
SELECT
    accession,
    alias,
    description,
    title,
    scientific_name,
    taxon_id,
    external_id,
    attributes_json
FROM sra_samples
WHERE accession = ANY(%s);
"""

# Batched ENA fallback for _fetch_samples_batch. Mirrors ENA_SAMPLE_SQL and
# matches an (already-uppercased) list on the plain ena_samples_pkey.
BATCH_ENA_SAMPLES_SQL = """
SELECT
    s.sample_accession AS accession,
    s.sample_alias AS alias,
    s.sample_description AS description,
    s.sample_title AS title,
    s.scientific_name,
    s.tax_id AS taxon_id,
    s.secondary_sample_accession AS external_id,
    COALESCE(
        (
            SELECT jsonb_object_agg(kv.key, kv.value)
            FROM jsonb_each_text(
                jsonb_strip_nulls(
                    to_jsonb(s) - ARRAY[
                        'sample_accession',
                        'secondary_sample_accession',
                        'sample_alias',
                        'sample_title',
                        'sample_description',
                        'tax_id',
                        'scientific_name',
                        'study_accession'
                    ]::text[]
                )
            ) AS kv
        ),
        '{}'::jsonb
    ) AS attributes_json,
    s.submission_accession AS submission,
    NULL::jsonb AS links
FROM ena_samples s
WHERE s.sample_accession = ANY(%s);
"""

BATCH_GSA_SAMPLES_SQL = """
SELECT
    s.sample_accession AS accession,
    s.sample_alias AS alias,
    s.sample_description AS description,
    s.sample_title AS title,
    s.scientific_name,
    NULL::text AS taxon_id,
    NULL::text AS external_id,
    COALESCE(
        (
            SELECT jsonb_object_agg(kv.key, kv.value)
            FROM jsonb_each_text(
                jsonb_strip_nulls(
                    to_jsonb(s) - ARRAY[
                        'sample_accession',
                        'sample_alias',
                        'sample_title',
                        'sample_description',
                        'scientific_name',
                        'study_accession'
                    ]::text[]
                )
            ) AS kv
        ),
        '{}'::jsonb
    ) AS attributes_json,
    NULL::text AS submission,
    NULL::jsonb AS links
FROM gsa_samples s
WHERE UPPER(s.sample_accession) = ANY(%s);
"""

# last-resort batch fallback; mirrored DDBJ samples are already in sra_samples
BATCH_DRA_SAMPLES_SQL = """
SELECT
    accession,
    alias,
    description,
    title,
    scientific_name,
    taxon_id,
    external_id,
    attributes_json,
    submission,
    links
FROM dra_samples
WHERE UPPER(accession) = ANY(%s);
"""

COMMON_NAME_BY_SCIENTIFIC_NAME_SQL = """
SELECT
    tax_id,
    scientific_name,
    common_name
FROM common_names
WHERE LOWER(scientific_name) = LOWER(%s)
ORDER BY common_name;
"""

# Batch variant: one round-trip for a whole facet list. %s is a list of
# already-lowercased names matched against LOWER(scientific_name).
COMMON_NAMES_BY_SCIENTIFIC_NAMES_SQL = """
SELECT
    tax_id,
    scientific_name,
    common_name
FROM common_names
WHERE LOWER(scientific_name) = ANY(%s)
ORDER BY scientific_name, common_name;
"""

GEO_SERIES_SAMPLES_SQL = """
SELECT s.*
FROM geo_samples s
JOIN geo_series g
  ON s.accession = ANY (g.samples_ref)
WHERE g.accession = %s
"""

GEO_SERIES_NEIGHBORS_COORDS_SQL = """
SELECT
    coords_3d,
    neighbors
FROM geo_series
WHERE accession = %s;
"""

BIO_PROJECT_GEO_SERIES_SQL = """
SELECT accession FROM geo_series WHERE bioproject_id = %s;
"""

# Get SRA study accession from sample accession
# Caller must pass accession already uppercased
SRA_SAMPLE_TO_STUDY_SQL = """
SELECT DISTINCT s.accession
FROM sra_experiments e
JOIN sra_studies s ON s.accession = e.study
WHERE e.samples @> ARRAY[%s]
LIMIT 1;
"""

# Get GEO series accession from GSM sample accession
# Caller must pass accession already uppercased
GEO_SAMPLE_TO_SERIES_SQL = """
SELECT accession
FROM geo_series
WHERE samples_ref @> ARRAY[%s]
LIMIT 1;
"""

# Get SRA study accession from experiment accession
# Caller must pass accession already uppercased
SRA_EXPERIMENT_TO_STUDY_SQL = """
SELECT study
FROM sra_experiments
WHERE accession = %s
LIMIT 1;
"""

# Get GEO series by PRJNA bioproject_id
PRJNA_TO_GEO_SERIES_SQL = """
SELECT accession
FROM geo_series
WHERE bioproject_id = %s
LIMIT 1;
"""

# Get SRA study by PRJ accession (for PRJNA, PRJDA, PRJEB, etc.)
# some submissions get two BioProject accessions; sra_studies has only the
# SRA XML one, bioproject_sra_links maps the other
PRJ_TO_SRA_STUDY_SQL = """
WITH q AS (SELECT %s::text AS prj)
SELECT accession FROM sra_studies, q WHERE bioproject_id = q.prj
UNION ALL
SELECT canonical_study FROM bioproject_sra_links, q
WHERE bioproject = q.prj AND canonical_study IS NOT NULL
LIMIT 1;
"""

# GSA (CNCB-NGDC) BioProjects (PRJCA*) map to a CRA/HRA study via project_name.
# Only searchable studies are in gsa_studies_search, matching the search shortcut.
PRJ_TO_GSA_STUDY_SQL = """
SELECT accession
FROM gsa_studies_search
WHERE project_name = %s
LIMIT 1;
"""

# ENA (incl. the DDBJ mirror) stores some BioProjects keyed by the PRJ accession
# itself (study_accession = PRJNA*/PRJDB*/PRJEB*), with no GEO/SRA/GSA study,
# e.g. assembly-only projects. The project page is /p/<PRJ>, so resolve to self.
PRJ_TO_ENA_STUDY_SQL = """
SELECT study_accession AS accession
FROM ena_studies
WHERE study_accession = %s
LIMIT 1;
"""

# Studies belonging to an SRA/ENA/DDBJ submission accession (SRA######, ERA######,
# DRA######). Most submissions wrap a single study, but some batch thousands, so
# this returns a list. The submission is recorded two ways: directly on the study
# (sra_studies.submission), and on the experiments of studies that carry no
# submission of their own (sra_experiments.submission -> study); UNION covers both.
# %s is bound twice.
SUBMISSION_TO_STUDIES_SQL = """
WITH studies AS (
    SELECT accession FROM sra_studies WHERE submission = %s
    UNION
    SELECT study AS accession FROM sra_experiments
    WHERE submission = %s AND study IS NOT NULL
)
SELECT s.accession, s.title, s.abstract, s.published_at
FROM studies st
JOIN sra_studies s ON s.accession = st.accession
ORDER BY s.published_at DESC NULLS LAST, s.accession;
"""

# Sample detail queries

# Get a single SRA experiment by accession (SRX/DRX/ERX)
# Caller must pass accession already uppercased
SRA_EXPERIMENT_SQL = """
SELECT
    accession,
    title,
    design_description,
    library_layout,
    library_name,
    library_selection,
    library_source,
    library_strategy,
    samples,
    platform,
    instrument_model,
    submission,
    study
FROM sra_experiments
WHERE accession = %s
LIMIT 1;
"""

# Get a single ENA experiment by accession
# Caller must pass accession already uppercased
ENA_EXPERIMENT_SQL = """
SELECT DISTINCT ON (e.experiment_accession)
    e.experiment_accession AS accession,
    e.experiment_title AS title,
    NULL::text AS design_description,
    e.library_layout,
    e.library_name,
    e.library_selection,
    e.library_source,
    e.library_strategy,
    CASE
        WHEN e.sample_accession IS NOT NULL THEN ARRAY[e.sample_accession]::text[]
        ELSE ARRAY[]::text[]
    END AS samples,
    e.instrument_platform AS platform,
    e.instrument_model,
    NULL::text AS submission,
    e.study_accession AS study
FROM ena_experiments e
WHERE e.experiment_accession = %s
ORDER BY e.experiment_accession, e.run_accession NULLS LAST
LIMIT 1;
"""

# Get a single GSA experiment by accession
# Caller must pass accession already uppercased
GSA_EXPERIMENT_SQL = """
SELECT DISTINCT ON (e.experiment_accession)
    e.experiment_accession AS accession,
    e.experiment_title AS title,
    NULL::text AS design_description,
    e.library_layout,
    e.library_name,
    e.library_selection,
    e.library_source,
    e.library_strategy,
    CASE
        WHEN e.sample_accession IS NOT NULL THEN ARRAY[e.sample_accession]::text[]
        ELSE ARRAY[]::text[]
    END AS samples,
    e.instrument_platform AS platform,
    e.instrument_model,
    NULL::text AS submission,
    e.study_accession AS study
FROM gsa_experiments e
WHERE e.experiment_accession = %s
ORDER BY e.experiment_accession, e.run_accession NULLS LAST
LIMIT 1;
"""

# Get a single GEO sample by accession (GSM)
# Caller must pass accession already uppercased
GEO_SAMPLE_SQL = """
SELECT s.*
FROM geo_samples s
WHERE s.accession = %s
LIMIT 1;
"""

# ENA only mirrors runs that NCBI normalised into an .sra object. A run whose
# submitter uploaded originals that were never normalised has no fastq_ftp, even
# though its original fastq.gz files are already listed in ncbi_files.
#
# Direct https URLs qualify. The rest are requester-pays s3 alternatives from
# "Use Cloud Data Delivery"; those runs keep showing no FASTQ.
#
# Every query reading fastq_ftp off sra_run_download_links needs this, or the
# same run gains files on one page and stays empty on another.
_ORIGINAL_FASTQ_JOIN = """
LEFT JOIN LATERAL (
    SELECT
        string_agg(x.url,      ';' ORDER BY x.filename) AS fastq_ftp,
        string_agg(x.size,     ';' ORDER BY x.filename) AS fastq_bytes,
        string_agg(x.md5,      ';' ORDER BY x.filename) AS fastq_md5,
        string_agg(x.filename, ';' ORDER BY x.filename) AS fastq_filenames
    FROM (
        SELECT f->>'filename' AS filename,
               f->>'url'      AS url,
               f->>'size'     AS size,
               f->>'md5'      AS md5
        FROM jsonb_array_elements(
                 -- ncbi_files can be a JSONB 'null' scalar, not an array
                 CASE WHEN jsonb_typeof(dl.ncbi_files) = 'array'
                      THEN dl.ncbi_files ELSE '[]'::jsonb END
             ) AS f
        WHERE f->>'supertype' = 'Original'
          AND f->>'semantic_name' = 'fastq'
          AND COALESCE(f->>'url', '') LIKE 'https://%%'
          AND COALESCE(f->>'size', '') ~ '^[0-9]+$'
          AND COALESCE(f->>'filename', '') <> ''
    ) x
) orig ON NULLIF(dl.fastq_ftp, '') IS NULL
"""

# All four switch on fastq_ftp together, never column-by-column: the lists are
# positional, so taking urls from ENA and bytes from ncbi_files would silently
# mis-pair them. A run with ENA FASTQ is left exactly as it was.
_FASTQ_FTP_EXPR = (
    "CASE WHEN NULLIF(dl.fastq_ftp, '') IS NOT NULL "
    "THEN dl.fastq_ftp ELSE orig.fastq_ftp END"
)
_FASTQ_BYTES_EXPR = (
    "CASE WHEN NULLIF(dl.fastq_ftp, '') IS NOT NULL "
    "THEN dl.fastq_bytes ELSE orig.fastq_bytes END"
)
_FASTQ_MD5_EXPR = (
    "CASE WHEN NULLIF(dl.fastq_ftp, '') IS NOT NULL "
    "THEN dl.fastq_md5 ELSE orig.fastq_md5 END"
)

# NCBI's object keys carry a version suffix the real file does not have
# (..._R1_001.fastq.gz.1), so a name taken from the URL is wrong for
# fallback entries. ncbi_files already stores the true filename; use it.
#
# Total by construction, never NULL where fastq_ftp is set: the ENA arm is just
# the URL basename, which is byte-for-byte what callers derived themselves, so
# reading this column can never change an ENA run's name. ORDINALITY keeps that
# arm in url order; the fallback arm shares the aggregate's ORDER BY. Keep regex
# out of this; names like YCS122B_control.1.fastq.gz end in a numeric component
# legitimately, and would be corrupted.
_FASTQ_FILENAMES_EXPR = """
CASE WHEN NULLIF(dl.fastq_ftp, '') IS NOT NULL
     THEN (SELECT string_agg(split_part(u, '/', -1), ';' ORDER BY ord)
           FROM unnest(string_to_array(dl.fastq_ftp, ';'))
                WITH ORDINALITY AS t(u, ord))
     ELSE orig.fastq_filenames END
"""

# Get runs for a specific experiment accession
# Caller must pass accession already uppercased
EXPERIMENT_RUNS_SQL = f"""
SELECT
    dl.run_accession,
    dl.experiment_accession,
    dl.library_layout,
    {_FASTQ_FTP_EXPR} AS fastq_ftp,
    {_FASTQ_BYTES_EXPR} AS fastq_bytes,
    {_FASTQ_MD5_EXPR} AS fastq_md5,
    {_FASTQ_FILENAMES_EXPR} AS fastq_filenames,
    dl.sra_ftp,
    dl.sra_bytes,
    dl.sra_md5,
    dl.ncbi_sra_url,
    dl.ncbi_sra_url_aws,
    dl.ncbi_sra_normalized_url,
    dl.ncbi_sra_normalized_bytes,
    dl.ncbi_sra_lite_url,
    dl.ncbi_sra_lite_bytes,
    dl.ncbi_sra_lite_s3_url,
    dl.ncbi_sra_lite_gs_url,
    COALESCE(ena.run_alias, gsa.run_alias) AS run_alias
FROM sra_run_download_links dl
LEFT JOIN ena_experiments ena ON ena.run_accession = dl.run_accession
LEFT JOIN gsa_experiments gsa ON gsa.run_accession = dl.run_accession
{_ORIGINAL_FASTQ_JOIN}
WHERE dl.experiment_accession = %s
ORDER BY dl.run_accession;
"""

# Get ENA experiment accession from sample accession
# Caller must pass accession already uppercased
ENA_SAMPLE_TO_EXPERIMENT_SQL = """
SELECT DISTINCT experiment_accession
FROM ena_experiments
WHERE sample_accession = %s
LIMIT 1;
"""

# Get GSA experiment accession from sample accession
# Caller must pass accession already uppercased
GSA_SAMPLE_TO_EXPERIMENT_SQL = """
SELECT DISTINCT experiment_accession
FROM gsa_experiments
WHERE sample_accession = %s
LIMIT 1;
"""

# Get SRA experiment accession from sample accession
# Caller must pass accession already uppercased
SRA_SAMPLE_TO_EXPERIMENT_SQL = """
SELECT accession
FROM sra_experiments
WHERE samples @> ARRAY[%s]
LIMIT 1;
"""

# Get ENA study accession from experiment accession
# Caller must pass accession already uppercased
ENA_EXPERIMENT_TO_STUDY_SQL = """
SELECT DISTINCT study_accession AS study
FROM ena_experiments
WHERE experiment_accession = %s
LIMIT 1;
"""

# Get GSA study accession from experiment accession
# Caller must pass accession already uppercased
GSA_EXPERIMENT_TO_STUDY_SQL = """
SELECT DISTINCT study_accession AS study
FROM gsa_experiments
WHERE experiment_accession = %s
LIMIT 1;
"""

# Distinct samples for an SRA study (caller must uppercase the accession).
STUDY_SAMPLES_SRA_SQL = """
SELECT DISTINCT
    s.accession,
    s.title,
    s.scientific_name,
    s.taxon_id,
    s.alias
FROM sra_samples s
JOIN sra_experiments e ON s.accession = ANY(e.samples)
WHERE e.study = %s
ORDER BY s.accession
LIMIT 1000;
"""

STUDY_SAMPLES_DRA_SQL = """
SELECT DISTINCT
    s.accession,
    s.title,
    s.scientific_name,
    s.taxon_id,
    s.alias
FROM dra_samples s
JOIN dra_experiments e ON s.accession = ANY(e.samples)
WHERE e.study = %s
ORDER BY s.accession
LIMIT 1000;
"""

DRA_SAMPLE_SQL = """
SELECT
    accession,
    alias,
    title,
    description,
    scientific_name,
    taxon_id,
    external_id,
    attributes_json,
    submission,
    links
FROM dra_samples
WHERE accession = %s;
"""

DRA_EXPERIMENT_SQL = """
SELECT
    accession,
    title,
    design_description,
    library_layout,
    library_name,
    library_selection,
    library_source,
    library_strategy,
    samples,
    platform,
    instrument_model,
    study,
    submission
FROM dra_experiments
WHERE accession = %s;
"""

DRA_EXPERIMENT_TO_STUDY_SQL = """
SELECT study
FROM dra_experiments
WHERE accession = %s
LIMIT 1;
"""

DRA_SAMPLE_TO_STUDY_SQL = """
SELECT DISTINCT e.study AS accession
FROM dra_experiments e
WHERE e.samples @> ARRAY[%s]
LIMIT 1;
"""

# Inverse of _enrich_with_publications(): a pmid can live only in a repo's own column.
PROJECTS_BY_PMID_SQL = f"""
WITH linked AS (
    SELECT accession,
           CASE WHEN source = 'ae' THEN 'arrayexpress' ELSE source END AS source,
           'study_publications' AS via
    FROM study_publications
    WHERE publications @> %(pmid_json)s::jsonb
    UNION
    SELECT accession, 'geo', 'geo_series.pubmed_id'
    FROM geo_series WHERE pubmed_id @> ARRAY[%(pmid)s]::text[]
    UNION
    SELECT accession, 'geo', 'geo_series.pubmed_id_pysradb'
    FROM geo_series WHERE pubmed_id_pysradb = %(pmid)s
    UNION
    SELECT accession, 'sra', 'sra_studies.pubmed_id_pysradb'
    FROM sra_studies
    WHERE pubmed_id_pysradb = %(pmid)s
      AND {_NOT_PRIVATE}
    UNION
    SELECT accession, 'sra', 'sra_studies.links.XREF_LINK'
    FROM sra_studies
    WHERE {_xref_pmid()} = %(pmid)s
      AND {_xref_is_pubmed()}
      AND {_NOT_PRIVATE}
    UNION
    SELECT study_accession, 'ena', 'ena_studies.pubmed_id_pysradb'
    FROM ena_studies WHERE pubmed_id_pysradb = %(pmid)s
    UNION
    SELECT accession, 'arrayexpress', 'arrayexpress_experiments.pubmed_id'
    FROM arrayexpress_experiments WHERE pubmed_id = %(pmid)s
    UNION
    SELECT accession, 'arrayexpress', 'arrayexpress_experiments.pubmed_id_pysradb'
    FROM arrayexpress_experiments WHERE pubmed_id_pysradb = %(pmid)s
    UNION
    SELECT accession, 'dra', 'dra_studies.pubmed_id_pysradb'
    FROM dra_studies WHERE pubmed_id_pysradb = %(pmid)s
    UNION
    SELECT accession, 'dra', 'dra_studies.links.XREF_LINK'
    FROM dra_studies
    WHERE {_xref_pmid()} = %(pmid)s AND {_xref_is_pubmed()}
    UNION
    SELECT accession, 'gea', 'gea_experiments.pubmed_id'
    FROM gea_experiments WHERE pubmed_id = %(pmid)s
    UNION
    SELECT accession, 'gea', 'gea_experiments.pubmed_id_pysradb'
    FROM gea_experiments WHERE pubmed_id_pysradb = %(pmid)s
    UNION
    SELECT s.study_accession, 'gsa', 'gsa_projects.pubmed_id'
    FROM gsa_studies s JOIN gsa_projects p ON p.accession = s.project_name
    WHERE p.pubmed_id @> jsonb_build_array(%(pmid)s::text)
    UNION
    SELECT study_accession, 'gsa', 'gsa_studies.pubmed_id_pysradb'
    FROM gsa_studies WHERE pubmed_id_pysradb = %(pmid)s
),
-- Collapse multiple link rows for the same accession; study_publications wins as via.
deduped AS (
    SELECT DISTINCT ON (accession) accession, source, via
    FROM linked
    ORDER BY accession, (via <> 'study_publications'), via
)
SELECT l.accession, l.source, l.via,
       COALESCE(g.title, s.title, ae.title, en.study_title, d.title, ge.title,
                gs.study_title) AS title,
       -- dra_studies / gea_experiments have no abstract column to fall back on,
       -- so those rows come back with a null summary.
       COALESCE(g.summary, s.abstract, ae.description, en.study_description,
                gs.study_description) AS summary,
       COALESCE(g.published_at, s.published_at, ae.release_date, en.first_public,
                d.published_at, ge.release_date, gs.first_public) AS published_at
FROM deduped l
LEFT JOIN geo_series               g  ON g.accession        = l.accession
LEFT JOIN sra_studies              s  ON s.accession        = l.accession
LEFT JOIN arrayexpress_experiments ae ON ae.accession       = l.accession
LEFT JOIN ena_studies              en ON en.study_accession = l.accession
LEFT JOIN dra_studies              d  ON d.accession        = l.accession
LEFT JOIN gea_experiments          ge ON ge.accession       = l.accession
LEFT JOIN gsa_studies              gs ON gs.study_accession = l.accession
ORDER BY l.source, l.accession;
"""

PUBLICATION_BY_PMID_SQL = """
SELECT pmid, title, journal, doi, pub_date, authors, citation_count
FROM pubmed_metadata
WHERE pmid = %s;
"""

# Case-insensitive DOI lookup; consider an index on LOWER(doi) if this becomes hot.
PUBLICATION_BY_DOI_SQL = """
SELECT pmid, title, journal, doi, pub_date, authors, citation_count
FROM pubmed_metadata
WHERE LOWER(doi) = LOWER(%s)
LIMIT 1;
"""

SIMILAR_STUDIES_SQL = """
WITH base AS (
    SELECT neighbors FROM geo_series   WHERE accession = %(acc)s
    UNION ALL
    SELECT neighbors FROM sra_studies  WHERE accession = %(acc)s
),
expanded AS (
    SELECT
        (n->>'accession')::text AS accession,
        COALESCE((n->>'distance')::float, (n->>'score')::float, 0.0) AS distance
    -- neighbors is jsonb[] (a Postgres array), not a jsonb array: jsonb_array_elements() errors on it
    FROM base, unnest(base.neighbors) AS n
    ORDER BY 2 ASC
    LIMIT %(limit)s
)
SELECT
    e.accession, e.distance,
    COALESCE(g.title, s.title, ae.title) AS title,
    COALESCE(g.organisms, s.organisms, ae.organisms) AS organisms,
    CASE
        WHEN g.accession  IS NOT NULL THEN 'geo'
        WHEN s.accession  IS NOT NULL THEN 'sra'
        WHEN ae.accession IS NOT NULL THEN 'arrayexpress'
    END AS source
FROM expanded e
LEFT JOIN geo_series              g  ON g.accession  = e.accession
LEFT JOIN sra_studies             s  ON s.accession  = e.accession
LEFT JOIN arrayexpress_experiments ae ON ae.accession = e.accession
ORDER BY e.distance ASC;
"""

LIST_LIBRARY_STRATEGIES_SQL = """
SELECT library_strategy AS value, COUNT(*)::bigint AS count
FROM sra_experiments
WHERE library_strategy IS NOT NULL AND library_strategy <> ''
GROUP BY library_strategy
ORDER BY count DESC, value;
"""

LIST_INSTRUMENT_MODELS_SQL = """
SELECT instrument_model AS value, COUNT(*)::bigint AS count
FROM sra_experiments
WHERE instrument_model IS NOT NULL AND instrument_model <> ''
GROUP BY instrument_model
ORDER BY count DESC, value;
"""

LIST_JOURNALS_SQL = """
SELECT journal AS value, COUNT(*)::bigint AS count
FROM pubmed_metadata
WHERE journal IS NOT NULL AND journal <> ''
GROUP BY journal
ORDER BY count DESC, value
LIMIT %s;
"""

LIST_CENTERS_SQL = """
SELECT center_name AS value, COUNT(*)::bigint AS count
FROM (
    SELECT center_name FROM sra_submissions WHERE center_name IS NOT NULL AND center_name <> ''
    UNION ALL
    SELECT center_name FROM ena_studies     WHERE center_name IS NOT NULL AND center_name <> ''
    UNION ALL
    SELECT center_name FROM gsa_studies     WHERE center_name IS NOT NULL AND center_name <> ''
) c
GROUP BY center_name
ORDER BY count DESC, value
LIMIT %s;
"""

# Author substring search; caller supplies the ILIKE pattern with %-wildcards.
SEARCH_AUTHORS_SQL = """
WITH matched AS (
    SELECT pmid, title, journal, doi, pub_date, authors, citation_count
    FROM pubmed_metadata
    WHERE authors ILIKE %s
    ORDER BY citation_count DESC NULLS LAST
    LIMIT %s
)
SELECT
    m.pmid, m.title, m.journal, m.doi, m.pub_date, m.authors, m.citation_count,
    COALESCE(
        jsonb_agg(DISTINCT jsonb_build_object('accession', c.accession, 'source', c.source))
        FILTER (WHERE c.accession IS NOT NULL),
        '[]'::jsonb
    ) AS studies
FROM matched m
LEFT JOIN cross_ref_pubmed c ON c.pubmed_id = m.pmid
GROUP BY m.pmid, m.title, m.journal, m.doi, m.pub_date, m.authors, m.citation_count
ORDER BY m.citation_count DESC NULLS LAST;
"""

# Reverse-resolve an author's pmids to projects via per-source pubmed_id columns
_AUTHOR_PROJECTS_CTE = f"""
WITH pm AS MATERIALIZED (
    SELECT pmid FROM pubmed_metadata WHERE authors ~* %(re)s
),
u AS (
    -- @> uses the GIN index on the text[] column; unnest ... IN would seq-scan.
    -- institute: best-effort affiliation per source, shown per card + summarized.
    -- GEO stores it as organizations text[]; noise (incl. "GEO") is filtered by
    -- the _INSTITUTE_NOISE blocklist downstream, not here.
    SELECT gs.accession, gs.title, gs.summary, gs.updated_at, gs.published_at,
           array_to_string(gs.organizations, '; ') AS institute
      FROM geo_series gs
     -- Array-overlap against the whole pmid set: one GIN scan, vs a per-pmid
     -- nested loop per pmid.
     WHERE gs.pubmed_id && ARRAY(SELECT pmid FROM pm)
    UNION ALL
    SELECT gs.accession, gs.title, gs.summary, gs.updated_at, gs.published_at,
           array_to_string(gs.organizations, '; ')
      FROM geo_series gs
     WHERE gs.pubmed_id_pysradb IN (SELECT pmid FROM pm)
    UNION ALL
    -- GEO contributor names, findable even with no linked publication.
    SELECT gs.accession, gs.title, gs.summary, gs.updated_at, gs.published_at,
           array_to_string(gs.organizations, '; ')
      FROM geo_series gs
     WHERE geo_contrib_text(gs.contributors) ~* %(re)s
    UNION ALL
    -- SRA center_project_name / ENA center_name are submitting-center metadata
    -- (often a sequencing vendor, e.g. "Illumina Inc."), not an author
    -- affiliation, leave institute NULL rather than mislead.
    SELECT s.accession, s.title, s.abstract, s.updated_at, s.published_at,
           NULL::text
      FROM sra_studies s
     WHERE s.pubmed_id_pysradb IN (SELECT pmid FROM pm)
    UNION ALL
    SELECT es.study_accession, es.study_title, es.study_description,
           es.last_updated, es.first_public, NULL::text
      FROM ena_studies es
     WHERE es.pubmed_id_pysradb IN (SELECT pmid FROM pm)
    UNION ALL
    -- Arms stay split per column: each has its own index, an OR would seq-scan.
    SELECT ae.accession, ae.title, ae.description,
           ae.release_date, ae.release_date,
           COALESCE(NULLIF(ae.contact_affiliation, ''), NULLIF(ae.organizations_text, ''))
      FROM arrayexpress_experiments ae
     WHERE ae.pubmed_id IN (SELECT pmid FROM pm)
    UNION ALL
    SELECT ae.accession, ae.title, ae.description,
           ae.release_date, ae.release_date,
           COALESCE(NULLIF(ae.contact_affiliation, ''), NULLIF(ae.organizations_text, ''))
      FROM arrayexpress_experiments ae
     WHERE ae.pubmed_id_pysradb IN (SELECT pmid FROM pm)
    UNION ALL
    SELECT ge.accession, ge.title, ge.description,
           ge.updated_at, ge.release_date, ge.contact_affiliation
      FROM gea_experiments ge
     WHERE ge.pubmed_id IN (SELECT pmid FROM pm)
    UNION ALL
    SELECT ge.accession, ge.title, ge.description,
           ge.updated_at, ge.release_date, ge.contact_affiliation
      FROM gea_experiments ge
     WHERE ge.pubmed_id_pysradb IN (SELECT pmid FROM pm)
    UNION ALL
    SELECT d.accession, d.title, d.abstract, d.updated_at, d.published_at,
           NULL::text
      FROM dra_studies d
     WHERE d.pubmed_id_pysradb IN (SELECT pmid FROM pm)
    UNION ALL
    SELECT d.accession, d.title, d.abstract, d.updated_at, d.published_at,
           NULL::text
      FROM dra_studies d
     WHERE {_xref_pmid("d.")} IN (SELECT pmid FROM pm)
       AND {_xref_is_pubmed("d.")}
    UNION ALL
    SELECT gs.study_accession, gs.study_title, gs.study_description,
           gs.last_updated, gs.first_public, p.submitting_org
      FROM gsa_studies gs LEFT JOIN gsa_projects p ON p.accession = gs.project_name
     WHERE gs.pubmed_id_pysradb IN (SELECT pmid FROM pm)
    UNION ALL
    SELECT gs.study_accession, gs.study_title, gs.study_description,
           gs.last_updated, gs.first_public, p.submitting_org
      FROM gsa_studies gs JOIN gsa_projects p ON p.accession = gs.project_name
     -- ?| takes the whole pmid set in one pass; a correlated
     -- jsonb_array_elements_text EXISTS re-hashes pm per project row.
     WHERE p.pubmed_id ?| ARRAY(SELECT pmid FROM pm)
),
-- Dedup by accession; full_total is the real count before LIMIT.
d AS (
    SELECT DISTINCT ON (accession) accession, title, summary, updated_at,
           published_at, institute
      FROM u
     ORDER BY accession
)
"""

# Full deduped match set (capped). full_total is the real count before the cap
# because the window runs before LIMIT. The handler paginates and computes
# institute facets in Python from this one result, so the CTE runs once.
SEARCH_AUTHOR_PROJECTS_SQL = (
    _AUTHOR_PROJECTS_CTE
    + """
SELECT accession, title, summary, updated_at, published_at,
       NULLIF(btrim(institute), '') AS institute,
       count(*) OVER () AS full_total
FROM d
ORDER BY published_at DESC NULLS LAST, accession
LIMIT %(cap)s;
"""
)

PROJECT_AE_SQL = """
SELECT
    ae.accession,
    ae.title,
    ae.description AS summary,
    ae.protocols_description AS overall_design,
    ae.organisms,
    ae.coords_2d,
    ae.coords_3d,
    ae.neighbors,
    CASE WHEN ae.pubmed_id IS NOT NULL THEN ARRAY[ae.pubmed_id] ELSE NULL END AS pubmed_id,
    NULL AS samples_ref,
    ae.study_type AS series_type,
    CASE
        WHEN jsonb_typeof(ae.external_links) = 'array' THEN
            (SELECT jsonb_agg(
                jsonb_build_object(
                    '@type',
                    CASE
                        WHEN link->>'type' = 'ENA' THEN 'ENA'
                        WHEN link->>'type' = 'GEO' THEN 'GEO'
                        WHEN link->>'type' = 'gxa' THEN 'ArrayExpress Atlas'
                        ELSE link->>'type'
                    END,
                    '@target',
                    CASE
                        WHEN link->>'type' = 'ENA' THEN 'https://www.ebi.ac.uk/ena/browser/view/' || (link->>'url')
                        WHEN link->>'type' = 'GEO' THEN 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=' || (link->>'url')
                        WHEN link->>'type' = 'gxa' THEN 'https://www.ebi.ac.uk/gxa/experiments/' || (link->>'url')
                        ELSE link->>'url'
                    END
                )
            )
            FROM jsonb_array_elements(ae.external_links) AS link)
        ELSE NULL
    END AS relation,
    -- AE stores processed archives in processed_data_files and the raw-data
    -- archive inside each sample's comment[arrayexpress ftp file] attribute (one
    -- experiment-level file repeated across samples). Show both, not either/or.
    NULLIF(
        COALESCE(
            CASE WHEN jsonb_typeof(ae.processed_data_files) = 'array'
                  AND jsonb_array_length(ae.processed_data_files) > 0
                 THEN ae.processed_data_files END,
            '[]'::jsonb
        )
        ||
        COALESCE(
            (SELECT jsonb_agg(DISTINCT jsonb_build_object(
                'url', s.attributes_json->>'comment[arrayexpress ftp file]',
                'path', regexp_replace(s.attributes_json->>'comment[arrayexpress ftp file]', '^.*/', '')
             ))
             FROM arrayexpress_samples s
             WHERE s.experiment_accession = ae.accession
               AND s.attributes_json ? 'comment[arrayexpress ftp file]'),
            '[]'::jsonb
        ),
        '[]'::jsonb
    ) AS supplementary_data,
    ae.release_date AS published_at,
    ae.release_date AS updated_at,
    ae.alias,
    ae.publication_title,
    ae.publication_authors,
    ae.publication_doi,
    ae.external_links,
    ae.experimental_factors_detail,
    ae.authors,
    ae.organizations,
    ae.assay_count,
    ae.technology,
    ae.assay_by_molecule,
    ae.minseqe_score,
    (SELECT jsonb_agg(DISTINCT jsonb_build_object(
        'organization', aeg.organization,
        'formatted_address', aeg.geo_formatted_address,
        'latitude', aeg.geo_latitude,
        'longitude', aeg.geo_longitude,
        'city', aeg.geo_city,
        'district', aeg.geo_district,
        'state', aeg.geo_state,
        'postcode', aeg.geo_postcode,
        'country', aeg.geo_country,
        'place_name', aeg.geo_place_name
     ))
     FROM jsonb_array_elements(
              CASE WHEN jsonb_typeof(ae.organizations) = 'array'
                   THEN ae.organizations ELSE '[]'::jsonb END
          ) AS org
     LEFT JOIN arrayexpress_geocode aeg ON aeg.organization = org->>'name'
    ) AS center
FROM arrayexpress_experiments ae
WHERE ae.accession = %s;
"""

PROJECT_GEA_SQL = """
SELECT
    g.accession,
    g.title,
    g.description AS summary,
    g.protocols_description AS overall_design,
    -- Per-sample organism set (transfer.gea_organisms); fall back to the
    -- experiment-level organism scalar where a study has no samples.
    COALESCE(
        g.organisms,
        CASE WHEN NULLIF(btrim(g.organism), '') IS NOT NULL
             THEN ARRAY[g.organism] END
    ) AS organisms,
    g.coords_2d,
    g.coords_3d,
    g.neighbors,
    CASE WHEN COALESCE(NULLIF(g.pubmed_id, ''), NULLIF(g.pubmed_id_pysradb, '')) IS NOT NULL
         THEN ARRAY[COALESCE(NULLIF(g.pubmed_id, ''), g.pubmed_id_pysradb)]
    END AS pubmed_id,
    NULL AS samples_ref,
    g.study_type AS series_type,
    NULL AS relation,
    g.data_files AS supplementary_data,
    g.release_date AS published_at,
    g.updated_at,
    (SELECT array_agg(DISTINCT d.accession)
     FROM dra_studies d
     WHERE d.bioproject_id = g.bioproject) AS alias,
    g.bioproject,
    g.experimental_factors,
    g.experimental_factor_types,
    CASE WHEN NULLIF(btrim(g.contact_affiliation), '') IS NOT NULL
         THEN jsonb_build_array(jsonb_build_object(
                  'organization', g.contact_affiliation))
    END AS center
FROM gea_experiments g
WHERE g.accession = %s;
"""

GEA_EXPERIMENT_SAMPLES_SQL = """
SELECT
    id,
    experiment_accession,
    source_name,
    organism,
    taxon_id,
    organism_part,
    tissue,
    cell_type,
    genotype,
    strain,
    sex,
    age,
    biosample,
    sample_title,
    sample_description,
    library_strategy,
    library_source,
    library_selection,
    library_layout,
    instrument_model,
    dra_experiment,
    dra_run,
    attributes_json
FROM gea_samples
WHERE experiment_accession = %s
ORDER BY id
"""

AE_EXPERIMENT_SAMPLES_SQL = """
SELECT
    id,
    experiment_accession,
    source_name,
    organism,
    organism_part,
    cell_type,
    genotype,
    sample_source_name,
    sample_title,
    sample_description,
    library_strategy,
    library_source,
    library_selection,
    ena_experiment,
    ena_run,
    attributes_json
FROM arrayexpress_samples
WHERE experiment_accession = %s;
"""


def _geo_radius_bounds(
    lat: float, lng: float, radius_km: float
) -> dict[str, float | bool]:
    angular_radius = radius_km / _EARTH_RADIUS_KM
    lat_delta = math.degrees(angular_radius)
    lat_min = max(-90.0, lat - lat_delta)
    lat_max = min(90.0, lat + lat_delta)

    if (
        radius_km >= (math.pi * _EARTH_RADIUS_KM / 2)
        or lat_min <= -90.0
        or lat_max >= 90.0
        or abs(math.cos(math.radians(lat))) < 1e-12
    ):
        return {
            "lat_min": lat_min,
            "lat_max": lat_max,
            "all_longitudes": True,
        }

    lng_delta = math.degrees(
        math.asin(min(1.0, math.sin(angular_radius) / math.cos(math.radians(lat))))
    )
    lng_min = lng - lng_delta
    lng_max = lng + lng_delta
    crosses_antimeridian = lng_min < -180.0 or lng_max > 180.0
    if crosses_antimeridian:
        lng_min = ((lng_min + 180.0) % 360.0) - 180.0
        lng_max = ((lng_max + 180.0) % 360.0) - 180.0

    return {
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lng_min": lng_min,
        "lng_max": lng_max,
        "all_longitudes": False,
        "crosses_antimeridian": crosses_antimeridian,
    }


def _build_geo_cte(params, assay_l1=None, assay_l2=None, **geo):
    """Return a CTE string for geo_accessions from unified_metadata, or None."""
    conditions = []
    if assay_l1:
        conditions.append("dominant_assay_l1 = %(assay_l1)s")
        params["assay_l1"] = assay_l1
    if assay_l2:
        conditions.append("dominant_assay_l2 = %(assay_l2)s")
        params["assay_l2"] = assay_l2
    if geo.get("geo_country"):
        conditions.append("country ILIKE %(geo_country)s")
        params["geo_country"] = geo["geo_country"]
    if geo.get("geo_country_code"):
        conditions.append("country_code ILIKE %(geo_country_code)s")
        params["geo_country_code"] = geo["geo_country_code"]
    if geo.get("geo_country_code_iso2"):
        conditions.append("country_code_iso2 ILIKE %(geo_country_code_iso2)s")
        params["geo_country_code_iso2"] = geo["geo_country_code_iso2"]
    if geo.get("geo_city"):
        conditions.append("city ILIKE %(geo_city)s")
        params["geo_city"] = geo["geo_city"]
    if geo.get("geo_state"):
        conditions.append("state ILIKE %(geo_state)s")
        params["geo_state"] = geo["geo_state"]
    if geo.get("geo_district"):
        conditions.append("district ILIKE %(geo_district)s")
        params["geo_district"] = geo["geo_district"]
    if geo.get("geo_postcode"):
        conditions.append("postcode = %(geo_postcode)s")
        params["geo_postcode"] = geo["geo_postcode"]
    if geo.get("geo_lat") is not None and geo.get("geo_lng") is not None:
        geo_lat = float(geo["geo_lat"])
        geo_lng = float(geo["geo_lng"])
        params["geo_lat"] = geo_lat
        params["geo_lng"] = geo_lng
        conditions.append("latitude BETWEEN -90 AND 90")
        conditions.append("longitude BETWEEN -180 AND 180")
        if geo.get("geo_radius_km"):
            geo_radius_km = float(geo["geo_radius_km"])
            bounds = _geo_radius_bounds(geo_lat, geo_lng, geo_radius_km)
            params["geo_radius_km"] = geo_radius_km
            params["geo_lat_min"] = bounds["lat_min"]
            params["geo_lat_max"] = bounds["lat_max"]
            conditions.append("latitude BETWEEN %(geo_lat_min)s AND %(geo_lat_max)s")
            if not bounds["all_longitudes"]:
                params["geo_lng_min"] = bounds["lng_min"]
                params["geo_lng_max"] = bounds["lng_max"]
                if bounds["crosses_antimeridian"]:
                    conditions.append(
                        "(longitude >= %(geo_lng_min)s OR longitude <= %(geo_lng_max)s)"
                    )
                else:
                    conditions.append(
                        "longitude BETWEEN %(geo_lng_min)s AND %(geo_lng_max)s"
                    )
            conditions.append(
                "(2 * 6371.0088 * asin(sqrt(LEAST(1.0,"
                " power(sin(radians(latitude - %(geo_lat)s) / 2), 2)"
                " + cos(radians(%(geo_lat)s)) * cos(radians(latitude))"
                " * power(sin(radians(longitude - %(geo_lng)s) / 2), 2)"
                ")))) <= %(geo_radius_km)s"
            )
        else:
            conditions.append(
                "ROUND(latitude::numeric, 2) = ROUND(%(geo_lat)s::numeric, 2)"
                " AND ROUND(longitude::numeric, 2) = ROUND(%(geo_lng)s::numeric, 2)"
            )
    if not conditions:
        return None
    where = " AND ".join(conditions)
    return (
        f"geo_accessions AS ("
        f"SELECT DISTINCT canonical_accession, source"
        f" FROM unified_metadata"
        f" WHERE {where})"
    )


def build_structured_search(
    q=None,
    q_variants=None,
    organism=None,
    library_strategy=None,
    platform=None,
    country=None,
    center=None,
    year_from=None,
    year_to=None,
    source=None,
    cursor_rank=None,
    cursor_acc=None,
    journal=None,
    instrument_model=None,
    geo_country=None,
    geo_country_code=None,
    geo_country_code_iso2=None,
    geo_city=None,
    geo_state=None,
    geo_district=None,
    geo_postcode=None,
    geo_lat=None,
    geo_lng=None,
    geo_radius_km=None,
    assay_l1=None,
    assay_l2=None,
    multi_platform=False,
    published_after=None,
    published_before=None,
    pub_date_after=None,
    pub_date_before=None,
    sample_tissue=None,
    sample_disease=None,
    sample_cell_type=None,
    count_by=None,
):
    """Build SQL + params for structured search across GEO, SRA, ArrayExpress, ENA.

    Returns (sql_string, params_dict) using %(name)s named parameters.

    `published_after`/`published_before` accept ISO date strings (YYYY-MM-DD) and
    filter the source's publication-date column (geo.published_at,
    sra.published_at, ae.release_date, ena.first_public).

    `sample_tissue`/`sample_disease`/`sample_cell_type` filter to studies that
    have at least one matching row in ontology_samples_v5 (ILIKE substring match).

    `count_by` switches the final SELECT to aggregation mode. Supported values:
    "year", "source", "organism", "country". Returns one row per bucket with
    {bucket, count}.
    """
    params = {}
    cte_parts = []

    organism = _as_list(organism)
    library_strategy = _as_list(library_strategy)

    include_geo = source in (None, "all", "geo")
    include_sra = source in (None, "all", "sra")
    include_ae = source in (None, "all", "arrayexpress")
    include_ena = source in (None, "all", "ena")
    include_gsa = source in (None, "all", "gsa")

    if library_strategy:
        include_geo = False
    if platform:
        include_geo = False
        include_ae = False
    if country:
        include_ae = False
    if instrument_model:
        include_ae = False
        include_ena = False
        include_gsa = False
    if multi_platform:
        include_ae = False
        include_ena = False
        include_gsa = False

    if q:
        params["q"] = q
        params["q_variants"] = q_variants or [q]
    if organism:
        params["organism"] = organism
    if library_strategy:
        params["library_strategy"] = library_strategy
    if platform:
        params["platform"] = platform
    if country:
        params["country"] = country
    if center:
        params["center"] = center
    if year_from is not None:
        params["year_from"] = year_from
    if year_to is not None:
        params["year_to"] = year_to
    if journal:
        params["journal"] = journal
    if instrument_model:
        params["instrument_model"] = instrument_model
    if published_after:
        params["published_after"] = published_after
    if published_before:
        params["published_before"] = published_before
    if pub_date_after:
        params["pub_date_after"] = pub_date_after
    if pub_date_before:
        params["pub_date_before"] = pub_date_before
    if sample_tissue:
        params["sample_tissue"] = f"%{sample_tissue}%"
    if sample_disease:
        params["sample_disease"] = f"%{sample_disease}%"
    if sample_cell_type:
        params["sample_cell_type"] = f"%{sample_cell_type}%"

    geo_kwargs = dict(
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
    )
    has_geo_filter = any(v is not None for v in geo_kwargs.values())
    has_um_filter = has_geo_filter or assay_l1 or assay_l2

    # When unified_metadata filters are active, use it as the driving table:
    # build a geo_accessions CTE and INNER JOIN each source table to it.
    geo_cte = None
    if has_um_filter:
        geo_cte = _build_geo_cte(
            params, assay_l1=assay_l1, assay_l2=assay_l2, **geo_kwargs
        )

    # pub_date is free-text mixed-granularity; only full "YYYY Mon DD" rows are
    # parseable, so year/month-only pubs and unlinked studies are excluded.
    # study_pub_dates holds them parsed and indexed.
    def _pub_date_clause(acc_expr: str) -> str:
        conds = [f"spd.accession = {acc_expr}"]
        if pub_date_after:
            conds.append("spd.pub_date >= %(pub_date_after)s::date")
        if pub_date_before:
            conds.append("spd.pub_date <= %(pub_date_before)s::date")
        return (
            "EXISTS (SELECT 1 FROM study_pub_dates spd"
            " WHERE " + " AND ".join(conds) + ")"
        )

    has_pub_date = bool(pub_date_after or pub_date_before)

    if include_geo:
        w = []
        joins = ""
        if geo_cte:
            joins = " INNER JOIN geo_accessions ga ON ga.canonical_accession = g.accession AND ga.source = 'geo'"
        from_q = ", q" if q else ""
        if q:
            w.append("g.tsv @@ q.match_q")
        if organism:
            w.append("g.organisms && %(organism)s::text[]")
        if country:
            w.append(
                "EXISTS (SELECT 1 FROM geo_contributors gc"
                " WHERE gc.accession = g.accession"
                " AND gc.address_country = %(country)s)"
            )
        if center:
            w.append(
                "EXISTS (SELECT 1 FROM geo_contributors gc2"
                " WHERE gc2.accession = g.accession"
                " AND gc2.organization ILIKE '%%' || %(center)s || '%%')"
            )
        if year_from is not None:
            w.append("g.published_at >= make_date(%(year_from)s, 1, 1)")
        if year_to is not None:
            w.append("g.published_at <= make_date(%(year_to)s, 12, 31)")
        if published_after:
            w.append("g.published_at >= %(published_after)s::date")
        if published_before:
            w.append("g.published_at <= %(published_before)s::date")
        if journal:
            w.append(
                "EXISTS (SELECT 1 FROM study_publications sp"
                " WHERE sp.accession = g.accession"
                " AND sp.publications->0->>'journal' = %(journal)s)"
            )
        if has_pub_date:
            w.append(_pub_date_clause("g.accession"))
        if instrument_model:
            w.append("g.instrument_models @> ARRAY[%(instrument_model)s]")
        if multi_platform:
            w.append("array_length(g.platforms, 1) >= 2")
        if sample_tissue:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = g.accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = g.accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = g.accession"
                " AND os.cell_type ILIKE %(sample_cell_type)s)"
            )

        rank = (
            "ts_rank(g.tsv, q.core_q) + ts_rank(g.tsv, q.match_q) * 0.2"
            if q
            else "COALESCE(EXTRACT(EPOCH FROM g.published_at), 0)"
        )
        where = (" WHERE " + " AND ".join(w)) if w else ""
        cte_parts.append(
            f"SELECT g.accession, g.title, g.summary,"
            f" g.updated_at, g.organisms, g.countries_iso AS countries,"
            f" {rank} AS rank, 'geo'::text AS source,"
            f" g.accession AS dedup_key, g.instrument_models, g.library_strategies, g.platforms"
            f" FROM geo_series g{joins}{from_q}{where}"
        )

    if include_sra:
        w = []
        joins = ""
        if geo_cte:
            joins = " INNER JOIN geo_accessions ga ON ga.canonical_accession = s.accession AND ga.source = 'sra'"
        from_q = ", q" if q else ""
        if q:
            w.append("s.tsv @@ q.match_q")
        if organism:
            w.append("s.organisms && %(organism)s::text[]")
        if library_strategy or platform:
            ec = ["e.study = s.accession"]
            if library_strategy:
                ec.append("e.library_strategy = ANY(%(library_strategy)s)")
            if platform:
                ec.append("e.platform = %(platform)s")
            w.append(
                "EXISTS (SELECT 1 FROM sra_experiments e"
                " WHERE " + " AND ".join(ec) + ")"
            )
        if country:
            w.append("s.countries @> ARRAY[%(country)s]")
        if center:
            w.append(
                "EXISTS (SELECT 1 FROM sra_submissions sub"
                " WHERE sub.accession = s.submission"
                " AND sub.center_name ILIKE '%%' || %(center)s || '%%')"
            )
        if year_from is not None:
            w.append("s.published_at >= make_date(%(year_from)s, 1, 1)")
        if year_to is not None:
            w.append("s.published_at <= make_date(%(year_to)s, 12, 31)")
        if published_after:
            w.append("s.published_at >= %(published_after)s::date")
        if published_before:
            w.append("s.published_at <= %(published_before)s::date")
        if journal:
            w.append(
                "EXISTS (SELECT 1 FROM study_publications sp"
                " WHERE sp.accession = s.accession"
                " AND sp.publications->0->>'journal' = %(journal)s)"
            )
        if has_pub_date:
            w.append(_pub_date_clause("s.accession"))
        if instrument_model:
            w.append("s.instrument_models @> ARRAY[%(instrument_model)s]")
        if multi_platform:
            w.append("array_length(s.platforms, 1) >= 2")
        if sample_tissue:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = s.accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = s.accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = s.accession"
                " AND os.cell_type ILIKE %(sample_cell_type)s)"
            )

        rank = (
            "(ts_rank(s.tsv, q.core_q) + ts_rank(s.tsv, q.match_q) * 0.2) * 0.7"
            if q
            else "COALESCE(EXTRACT(EPOCH FROM s.published_at), 0)"
        )
        where = (" WHERE " + " AND ".join(w)) if w else ""
        cte_parts.append(
            f"SELECT s.accession, s.title, s.abstract AS summary,"
            f" s.updated_at, s.organisms, s.countries,"
            f" {rank} AS rank, 'sra'::text AS source,"
            f" COALESCE(s.alias, s.accession) AS dedup_key, s.instrument_models, s.library_strategies, s.platforms"
            f" FROM sra_studies s{joins}{from_q}{where}"
        )

    if include_ae:
        w = []
        joins = ""
        if geo_cte:
            # unified_metadata stores this source as 'ae'
            joins = " INNER JOIN geo_accessions ga ON ga.canonical_accession = ae.accession AND ga.source = 'ae'"
        from_q = ", q" if q else ""
        if q:
            w.append("ae.tsv @@ q.match_q")
        if organism:
            w.append("ae.organisms && %(organism)s::text[]")
        if library_strategy:
            w.append("ae.technology = ANY(%(library_strategy)s)")
        if center:
            w.append("ae.organizations::text ILIKE '%%' || %(center)s || '%%'")
        if year_from is not None:
            w.append("ae.release_date >= make_date(%(year_from)s, 1, 1)")
        if year_to is not None:
            w.append("ae.release_date <= make_date(%(year_to)s, 12, 31)")
        if published_after:
            w.append("ae.release_date >= %(published_after)s::date")
        if published_before:
            w.append("ae.release_date <= %(published_before)s::date")
        if sample_tissue:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = ae.accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = ae.accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = ae.accession"
                " AND os.cell_type ILIKE %(sample_cell_type)s)"
            )
        if journal:
            w.append(
                "EXISTS (SELECT 1 FROM study_publications sp"
                " WHERE sp.accession = ae.accession"
                " AND sp.publications->0->>'journal' = %(journal)s)"
            )
        if has_pub_date:
            w.append(_pub_date_clause("ae.accession"))

        rank = (
            "(ts_rank(ae.tsv, q.core_q) + ts_rank(ae.tsv, q.match_q) * 0.2) * 0.7"
            if q
            else "COALESCE(EXTRACT(EPOCH FROM ae.release_date), 0)"
        )
        where = (" WHERE " + " AND ".join(w)) if w else ""
        cte_parts.append(
            f"SELECT ae.accession, ae.title, ae.description AS summary,"
            f" ae.release_date AS updated_at, ae.organisms, ae.countries,"
            f" {rank} AS rank, 'arrayexpress'::text AS source,"
            f" COALESCE(ae.alias, ae.accession) AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies, NULL::text[] AS platforms"
            f" FROM arrayexpress_experiments ae{joins}{from_q}{where}"
        )

    if include_ena:
        w = []
        joins = ""
        if geo_cte:
            joins = " INNER JOIN geo_accessions ga ON ga.canonical_accession = es.study_accession AND ga.source = 'ena'"
        from_q = ", q" if q else ""
        ena_tsv = (
            "to_tsvector('english',"
            " coalesce(es.study_title, '') || ' ' ||"
            " coalesce(es.study_description, ''))"
        )
        if q:
            w.append(f"{ena_tsv} @@ q.match_q")
        if organism:
            # GIN-indexed array containment (idx_ena_studies_organisms_gin); the
            # scalar scientific_name only sees the study-level organism and misses
            # multi-organism ENA studies.
            w.append("es.organisms && %(organism)s::text[]")
        if library_strategy or platform or country:
            ec = ["ee.study_accession = es.study_accession"]
            if library_strategy:
                ec.append("ee.library_strategy = ANY(%(library_strategy)s)")
            if platform:
                ec.append("ee.instrument_platform = %(platform)s")
            if country:
                ec.append("ee.country = %(country)s")
            w.append(
                "EXISTS (SELECT 1 FROM ena_experiments ee"
                " WHERE " + " AND ".join(ec) + ")"
            )
        if center:
            w.append("es.center_name ILIKE '%%' || %(center)s || '%%'")
        if year_from is not None:
            w.append("es.first_public >= make_date(%(year_from)s, 1, 1)")
        if year_to is not None:
            w.append("es.first_public <= make_date(%(year_to)s, 12, 31)")
        if published_after:
            w.append("es.first_public >= %(published_after)s::date")
        if published_before:
            w.append("es.first_public <= %(published_before)s::date")
        if sample_tissue:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = es.study_accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = es.study_accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = es.study_accession"
                " AND os.cell_type ILIKE %(sample_cell_type)s)"
            )
        if journal:
            w.append(
                "EXISTS (SELECT 1 FROM study_publications sp"
                " WHERE sp.accession = es.study_accession"
                " AND sp.publications->0->>'journal' = %(journal)s)"
            )
        if has_pub_date:
            w.append(_pub_date_clause("es.study_accession"))

        rank = (
            f"(ts_rank({ena_tsv}, q.core_q) + ts_rank({ena_tsv}, q.match_q) * 0.2) * 0.7"
            if q
            else "COALESCE(EXTRACT(EPOCH FROM es.first_public), 0)"
        )
        where = (" WHERE " + " AND ".join(w)) if w else ""
        cte_parts.append(
            f"SELECT es.study_accession AS accession,"
            f" es.study_title AS title,"
            f" es.study_description AS summary,"
            f" es.first_public AS updated_at,"
            f" COALESCE(es.organisms, CASE WHEN es.scientific_name IS NOT NULL"
            f" THEN ARRAY[es.scientific_name] END) AS organisms,"
            f" NULL::text[] AS countries,"
            f" {rank} AS rank, 'ena'::text AS source,"
            f" es.study_accession AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies, NULL::text[] AS platforms"
            f" FROM ena_studies es{joins}{from_q}{where}"
        )

    if include_gsa:
        w = []
        # LEFT JOIN the enriched search table so q matches the BioProject/sample
        # text materialized onto gsa_studies_search by transfer.gsa_search, not
        # just the thin study_title/description. LEFT so non-q filters still see
        # studies absent from the search table.
        joins = (
            " LEFT JOIN gsa_studies_search gss ON gss.accession = gs.study_accession"
        )
        if geo_cte:
            joins += " INNER JOIN geo_accessions ga ON ga.canonical_accession = gs.study_accession AND ga.source = 'gsa'"
        from_q = ", q" if q else ""
        gsa_tsv = "gss.tsv"
        if q:
            w.append(f"{gsa_tsv} @@ q.match_q")
        if organism:
            # GIN-indexed containment (idx_gsa_studies_organisms_gin); the scalar
            # scientific_name missed multi-organism GSA studies.
            w.append("gs.organisms && %(organism)s::text[]")
        if library_strategy or platform or country:
            ec = ["ge.study_accession = gs.study_accession"]
            if library_strategy:
                ec.append("ge.library_strategy = ANY(%(library_strategy)s)")
            if platform:
                ec.append("ge.instrument_platform = %(platform)s")
            if country:
                ec.append("ge.country = %(country)s")
            w.append(
                "EXISTS (SELECT 1 FROM gsa_experiments ge"
                " WHERE " + " AND ".join(ec) + ")"
            )
        if center:
            w.append("gs.center_name ILIKE '%%' || %(center)s || '%%'")
        if year_from is not None:
            w.append("gs.first_public >= make_date(%(year_from)s, 1, 1)")
        if year_to is not None:
            w.append("gs.first_public <= make_date(%(year_to)s, 12, 31)")
        if published_after:
            w.append("gs.first_public >= %(published_after)s::date")
        if published_before:
            w.append("gs.first_public <= %(published_before)s::date")
        if sample_tissue:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = gs.study_accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = gs.study_accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v5 os"
                " WHERE os.study_accession = gs.study_accession"
                " AND os.cell_type ILIKE %(sample_cell_type)s)"
            )
        if journal:
            w.append(
                "EXISTS (SELECT 1 FROM study_publications sp"
                " WHERE sp.accession = gs.study_accession"
                " AND sp.publications->0->>'journal' = %(journal)s)"
            )
        if has_pub_date:
            w.append(_pub_date_clause("gs.study_accession"))

        rank = (
            f"(ts_rank({gsa_tsv}, q.core_q) + ts_rank({gsa_tsv}, q.match_q) * 0.2) * 0.7"
            if q
            else "COALESCE(EXTRACT(EPOCH FROM gs.first_public), 0)"
        )
        where = (" WHERE " + " AND ".join(w)) if w else ""
        cte_parts.append(
            f"SELECT gs.study_accession AS accession,"
            f" COALESCE(gs.study_title, gss.project_title, gs.study_name, gs.project_name, gs.study_accession) AS title,"
            f" COALESCE(gs.study_description, gss.project_summary) AS summary,"
            f" gs.first_public AS updated_at,"
            f" COALESCE(gs.organisms, CASE WHEN gs.scientific_name IS NOT NULL"
            f" THEN ARRAY[gs.scientific_name] END) AS organisms,"
            f" ARRAY['China']::text[] AS countries,"
            f" {rank} AS rank, 'gsa'::text AS source,"
            f" gs.study_accession AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies, NULL::text[] AS platforms"
            f" FROM gsa_studies gs{joins}{from_q}{where}"
        )

    if not cte_parts:
        return (
            "SELECT NULL AS accession, NULL AS title, NULL AS summary,"
            " NULL AS updated_at, NULL::text[] AS organisms,"
            " NULL::text[] AS countries,"
            " NULL::float8 AS rank, NULL AS source,"
            " 0::bigint AS total_count, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies WHERE FALSE",
            {},
        )

    all_matches = "\n    UNION ALL\n    ".join(cte_parts)

    if cursor_rank is not None and cursor_acc is not None:
        params["cursor_rank"] = cursor_rank
        params["cursor_acc"] = cursor_acc
        cursor_where = (
            "WHERE (rank, accession) < (%(cursor_rank)s::float8, %(cursor_acc)s::text)"
        )
    else:
        cursor_where = ""

    query_cte = ""
    if q:
        query_cte = """q AS (
    SELECT plainto_tsquery('english', %(q)s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%(q_variants)s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
"""

    query_cte_prefix = query_cte
    geo_cte_prefix = f"{geo_cte},\n" if geo_cte else ""

    _COUNT_BUCKETS = {
        "year": "EXTRACT(YEAR FROM updated_at)::int",
        "source": "source",
        "organism": "unnest(organisms)",
        "country": "unnest(countries)",
        "library_strategy": "unnest(library_strategies)",
        "instrument_model": "unnest(instrument_models)",
    }
    if count_by:
        bucket_expr = _COUNT_BUCKETS.get(count_by)
        if not bucket_expr:
            raise ValueError(
                f"Unsupported count_by={count_by!r}; expected one of {list(_COUNT_BUCKETS)}"
            )
        sql = f"""\
WITH {query_cte_prefix}{geo_cte_prefix}all_matches AS (
    {all_matches}
),
deduped AS (
    SELECT DISTINCT ON (dedup_key)
        accession, updated_at, organisms, countries, source,
        library_strategies, instrument_models
    FROM all_matches
    ORDER BY dedup_key, rank DESC
)
SELECT bucket, COUNT(*)::bigint AS count
FROM (SELECT {bucket_expr} AS bucket FROM deduped) buckets
WHERE bucket IS NOT NULL
GROUP BY bucket
ORDER BY count DESC, bucket
LIMIT 200;"""
        return sql, params

    sql = f"""\
WITH {query_cte_prefix}{geo_cte_prefix}all_matches AS (
    {all_matches}
),
deduped AS (
    SELECT DISTINCT ON (dedup_key)
        accession, title, summary, updated_at, organisms, countries, rank, source, instrument_models, library_strategies
    FROM all_matches
    ORDER BY dedup_key, rank DESC
),
counted AS (
    SELECT *, COUNT(*) OVER () AS total_count FROM deduped
),
paged AS (
    SELECT *
    FROM counted
    {cursor_where}
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
OFFSET COALESCE(%(offset)s::bigint, 0)
LIMIT 200;"""

    params["offset"] = None
    return sql, params


def build_sorted_search_sql(
    q,
    sortby,
    order="desc",
    db=None,
    cursor_sort=None,
    cursor_acc=None,
    q_variants=None,
    offset=None,
):
    """Build search SQL sorted by citations, journal, or year.

    Returns (sql_string, params_dict) using %(name)s named parameters.
    """
    params = {"q": q, "q_variants": q_variants or [q]}
    cte_parts = []

    if db in (None, "geo"):
        cte_parts.append(
            "SELECT accession, title, summary, updated_at, organisms, countries_iso AS countries,"
            " ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2 AS rank, 'geo'::text AS source,"
            " accession AS dedup_key, instrument_models, library_strategies"
            " FROM geo_series, q"
            " WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)"
        )

    if db in (None, "sra"):
        cte_parts.append(
            "SELECT s.accession, s.title, s.abstract AS summary, s.updated_at,"
            " s.organisms, s.countries,"
            " (ts_rank(s.tsv, q.core_q) + ts_rank(s.tsv, q.match_q) * 0.2) * 0.7 AS rank, 'sra'::text AS source,"
            " COALESCE(s.alias, s.accession) AS dedup_key, s.instrument_models, s.library_strategies"
            " FROM sra_studies s, q"
            " WHERE (s.tsv @@ q.match_q OR s.tsv @@ q.core_q)"
        )

    if db in (None, "arrayexpress"):
        cte_parts.append(
            "SELECT accession, title, description AS summary,"
            " release_date AS updated_at, organisms, countries,"
            " (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'arrayexpress'::text AS source,"
            " COALESCE(alias, accession) AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies"
            " FROM arrayexpress_experiments, q"
            " WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)"
        )

    if db in (None, "ena"):
        cte_parts.append(
            "SELECT accession,"
            " COALESCE(study_title, study_name, project_name, accession) AS title,"
            " study_description AS summary,"
            " COALESCE(last_updated, first_public) AS updated_at,"
            " NULL::text[] AS organisms, NULL::text[] AS countries,"
            " (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'ena'::text AS source,"
            " accession AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies"
            " FROM ena_studies_search, q"
            " WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)"
        )

    if db in (None, "gsa"):
        cte_parts.append(
            "SELECT accession,"
            " COALESCE(study_title, project_title, study_name, project_name, accession) AS title,"
            " COALESCE(study_description, project_summary) AS summary,"
            " COALESCE(last_updated, first_public) AS updated_at,"
            " organisms, ARRAY['China']::text[] AS countries,"
            " (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'gsa'::text AS source,"
            " accession AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies"
            " FROM gsa_studies_search, q"
            " WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)"
        )

    if not cte_parts:
        return (
            "SELECT NULL AS accession, NULL AS title, NULL AS summary,"
            " NULL AS updated_at, NULL::text[] AS organisms,"
            " NULL::text[] AS countries,"
            " NULL::float8 AS rank, NULL AS source,"
            " NULL::text[] AS instrument_models, NULL::text[] AS library_strategies,"
            " NULL AS sort_val, 0::bigint AS total_count WHERE FALSE",
            {},
        )

    all_matches = "\n    UNION ALL\n    ".join(cte_parts)

    if sortby == "citations":
        sort_expr = "COALESCE((sp.publications->0->>'citation_count')::int, 0)"
        join_clause = (
            "\n    LEFT JOIN study_publications sp ON sp.accession = d.accession"
        )
        cursor_cast = "::int"
    elif sortby == "journal":
        sort_expr = "COALESCE(sp.publications->0->>'journal', '')"
        join_clause = (
            "\n    LEFT JOIN study_publications sp ON sp.accession = d.accession"
        )
        cursor_cast = ""
    else:  # year
        sort_expr = "COALESCE(d.updated_at::date, '1970-01-01'::date)"
        join_clause = ""
        cursor_cast = "::date"

    direction = "DESC" if order == "desc" else "ASC"
    comp = "<" if order == "desc" else ">"

    if cursor_sort is not None and cursor_acc is not None:
        params["cursor_sort"] = cursor_sort
        params["cursor_acc"] = cursor_acc
        cursor_where = (
            f"WHERE (sort_val, accession) {comp} "
            f"(%(cursor_sort)s{cursor_cast}, %(cursor_acc)s)"
        )
    else:
        cursor_where = ""

    sql = f"""\
WITH q AS (
    SELECT plainto_tsquery('english', %(q)s) AS core_q,
           (
               SELECT string_agg('(' || term_q::text || ')', ' | ')::tsquery
               FROM (
                   SELECT plainto_tsquery('english', term) AS term_q
                   FROM unnest(%(q_variants)s::text[]) AS terms(term)
                   WHERE btrim(term) <> ''
               ) parsed_terms
               WHERE term_q::text <> ''
           ) AS match_q
),
all_matches AS (
    {all_matches}
),
deduped AS (
    SELECT DISTINCT ON (dedup_key)
        accession, title, summary, updated_at, organisms, countries, rank, source, instrument_models, library_strategies
    FROM all_matches
    ORDER BY dedup_key, rank DESC
),
sorted AS (
    SELECT d.*,
           {sort_expr} AS sort_val
    FROM deduped d{join_clause}
),
counted AS (
    SELECT *, COUNT(*) OVER () AS total_count FROM sorted
),
paged AS (
    SELECT *
    FROM counted
    {cursor_where}
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, instrument_models, library_strategies,
    sort_val, total_count
FROM paged
ORDER BY sort_val {direction} NULLS LAST, accession {direction}
OFFSET COALESCE(%(offset)s::bigint, 0)
LIMIT 200;"""

    params["offset"] = offset
    return sql, params


RELATED_SQL = """\
WITH forward_geo AS (
    SELECT target_accession AS accession,
           link_type,
           'cross_ref_geo' AS source
    FROM cross_ref_geo
    WHERE geo_accession = %(acc)s
),
forward_ae AS (
    SELECT target_accession AS accession,
           link_type,
           'cross_ref_ae' AS source
    FROM cross_ref_ae
    WHERE ae_accession = %(acc)s
),
reverse_geo AS (
    SELECT geo_accession AS accession,
           link_type || ' (reverse)' AS link_type,
           'cross_ref_geo' AS source
    FROM cross_ref_geo
    WHERE target_accession = %(acc)s
),
reverse_ae AS (
    SELECT ae_accession AS accession,
           link_type || ' (reverse)' AS link_type,
           'cross_ref_ae' AS source
    FROM cross_ref_ae
    WHERE target_accession = %(acc)s
),
forward_ena AS (
    SELECT target_accession AS accession,
           link_type,
           'cross_ref_ena' AS source
    FROM cross_ref_ena
    WHERE ena_accession = %(acc)s
),
reverse_ena AS (
    SELECT ena_accession AS accession,
           link_type || ' (reverse)' AS link_type,
           'cross_ref_ena' AS source
    FROM cross_ref_ena
    WHERE target_accession = %(acc)s
),
sra_alias AS (
    SELECT alias AS accession,
           'SRA alias' AS link_type,
           'sra_studies' AS source
    FROM sra_studies
    WHERE accession = %(acc)s
      AND alias LIKE 'GSE%%'
),
sra_reverse AS (
    SELECT accession,
           'SRA study' AS link_type,
           'sra_studies' AS source
    FROM sra_studies
    WHERE alias = %(acc)s
),
egeod_to_gse AS (
    SELECT 'GSE' || substring(%(acc)s FROM 'E-GEOD-(\\d+)') AS accession,
           'E-GEOD naming' AS link_type,
           'naming_convention' AS source
    WHERE %(acc)s LIKE 'E-GEOD-%%'
      AND EXISTS (
          SELECT 1 FROM geo_series
          WHERE accession = 'GSE' || substring(%(acc)s FROM 'E-GEOD-(\\d+)')
      )
),
gse_to_egeod AS (
    SELECT 'E-GEOD-' || substring(%(acc)s FROM 'GSE(\\d+)') AS accession,
           'E-GEOD naming' AS link_type,
           'naming_convention' AS source
    WHERE %(acc)s LIKE 'GSE%%'
      AND EXISTS (
          SELECT 1 FROM arrayexpress_experiments
          WHERE accession = 'E-GEOD-' || substring(%(acc)s FROM 'GSE(\\d+)')
      )
),
pubmed_siblings AS (
    SELECT DISTINCT p2.accession,
           'PubMed:' || p1.pubmed_id AS link_type,
           'pubmed' AS source
    FROM cross_ref_pubmed p1
    JOIN cross_ref_pubmed p2
      ON p1.pubmed_id = p2.pubmed_id
    WHERE p1.accession = %(acc)s
      AND p2.accession != %(acc)s
),
ena_two_hop AS (
    SELECT DISTINCT cf2.target_accession AS accession,
           cf2.link_type,
           'cross_ref_ena_2hop' AS source
    FROM cross_ref_ena cf1
    JOIN cross_ref_ena cf2 ON cf1.ena_accession = cf2.ena_accession
    WHERE cf1.target_accession = %(acc)s
      AND cf2.target_accession != %(acc)s
),
geo_two_hop AS (
    SELECT DISTINCT cf2.target_accession AS accession,
           cf2.link_type,
           'cross_ref_geo_2hop' AS source
    FROM cross_ref_geo cf1
    JOIN cross_ref_geo cf2 ON cf1.geo_accession = cf2.geo_accession
    WHERE cf1.target_accession = %(acc)s
      AND cf2.target_accession != %(acc)s
),
ae_two_hop AS (
    SELECT DISTINCT cf2.target_accession AS accession,
           cf2.link_type,
           'cross_ref_ae_2hop' AS source
    FROM cross_ref_ae cf1
    JOIN cross_ref_ae cf2 ON cf1.ae_accession = cf2.ae_accession
    WHERE cf1.target_accession = %(acc)s
      AND cf2.target_accession != %(acc)s
)
SELECT accession, link_type, source FROM forward_geo
UNION ALL
SELECT accession, link_type, source FROM forward_ae
UNION ALL
SELECT accession, link_type, source FROM forward_ena
UNION ALL
SELECT accession, link_type, source FROM reverse_geo
UNION ALL
SELECT accession, link_type, source FROM reverse_ae
UNION ALL
SELECT accession, link_type, source FROM reverse_ena
UNION ALL
SELECT accession, link_type, source FROM sra_alias
UNION ALL
SELECT accession, link_type, source FROM sra_reverse
UNION ALL
SELECT accession, link_type, source FROM egeod_to_gse
UNION ALL
SELECT accession, link_type, source FROM gse_to_egeod
UNION ALL
SELECT accession, link_type, source FROM pubmed_siblings
UNION ALL
SELECT accession, link_type, source FROM ena_two_hop
UNION ALL
SELECT accession, link_type, source FROM geo_two_hop
UNION ALL
SELECT accession, link_type, source FROM ae_two_hop;
"""


BULK_PROJECT_METADATA_SQL = """
WITH input AS (
    SELECT accession, MIN(ord)::int AS ord
    FROM UNNEST(%s::text[]) WITH ORDINALITY AS t(accession, ord)
    GROUP BY accession
),
matched AS (
    SELECT
        g.accession,
        g.title,
        g.summary AS description,
        g.organisms
    FROM geo_series g
    JOIN input i ON i.accession = g.accession

    UNION ALL

    SELECT
        s.accession,
        s.title,
        s.abstract AS description,
        s.organisms
    FROM sra_studies s
    JOIN input i ON i.accession = s.accession

    UNION ALL

    SELECT
        ae.accession,
        ae.title,
        ae.description,
        ae.organisms
    FROM arrayexpress_experiments ae
    JOIN input i ON i.accession = ae.accession

    UNION ALL

    -- GSA title/organisms come off the search table; gsa_studies has neither an
    -- accession nor organisms columns (transfer.gsa_search enriches them
    -- from the BioProject). Same COALESCE ladder search uses, so the graph and
    -- the result rows agree on a study's name.
    SELECT
        gss.accession,
        COALESCE(gss.study_title, gss.project_title, gss.study_name,
                 gss.project_name, gss.accession) AS title,
        COALESCE(gss.study_description, gss.project_summary) AS description,
        gss.organisms
    FROM gsa_studies_search gss
    JOIN input i ON i.accession = gss.accession

    UNION ALL

    -- DDBJ-exclusive studies only: a DRP that SRA mirrors is already covered by
    -- sra_studies above and would otherwise match twice.
    SELECT
        ds.accession,
        ds.title,
        ds.abstract AS description,
        NULL::text[] AS organisms
    FROM dra_studies ds
    JOIN input i ON i.accession = ds.accession
    WHERE ds.is_only_ddbj = 1

    UNION ALL

    -- E-GEAD lives in gea_experiments, not arrayexpress_experiments, and stores
    -- one organism as text rather than an array.
    SELECT
        gea.accession,
        gea.title,
        gea.description,
        CASE
            WHEN gea.organism IS NULL THEN NULL::text[]
            ELSE ARRAY[gea.organism]
        END AS organisms
    FROM gea_experiments gea
    JOIN input i ON i.accession = gea.accession
)
SELECT
    m.accession,
    m.title,
    m.description,
    m.organisms
FROM input i
JOIN matched m ON m.accession = i.accession
ORDER BY i.ord;
"""


# Resolve study_accession(s) used in sra_run_download_links for a given
# SRP/ERP/DRP accession.
# Resolve a study/project accession to every accession the download links may be
# keyed under. Cross-archive links go BOTH ways: an SRP -> its BioProject, and a
# PRJ* -> the SRA study it wraps (so ENA project pages, addressed by PRJ*, find
# the FASTQ/BAM rows that live under the SRP). Single %s: the input accession.
# The same two hops run against dra_studies, since a DDBJ-exclusive DRP is absent
# from sra_studies. E-GEAD needs a third: it names only a BioProject, whose reads
# live in the DRA study wrapping it. bioproject_sra_links covers submissions that
# got two BioProject accessions, where sra_studies has only the SRA XML one.
_RESOLVE_DL_STUDY = """
WITH input AS (SELECT %s::text AS acc),
candidates AS (
    SELECT acc FROM input
    UNION
    SELECT s.bioproject_id FROM sra_studies s, input
    WHERE s.accession = input.acc AND s.bioproject_id IS NOT NULL
    UNION
    SELECT s.accession FROM sra_studies s, input
    WHERE s.bioproject_id = input.acc
    UNION
    SELECT d.bioproject_id FROM dra_studies d, input
    WHERE d.accession = input.acc AND d.bioproject_id IS NOT NULL
    UNION
    SELECT d.accession FROM dra_studies d, input
    WHERE d.bioproject_id = input.acc
    UNION
    SELECT d.accession
    FROM gea_experiments g
    JOIN dra_studies d ON d.bioproject_id = g.bioproject
    CROSS JOIN input
    WHERE g.accession = input.acc
    UNION
    SELECT l.canonical_study FROM bioproject_sra_links l, input
    WHERE l.bioproject = input.acc AND l.canonical_study IS NOT NULL
)
"""

STUDY_RUNS_SUMMARY_SQL = (
    _RESOLVE_DL_STUDY
    + f"""
SELECT
    COUNT(*) AS total_runs,
    COUNT(*) FILTER (WHERE dl.library_layout = 'PAIRED') AS paired_runs,
    COUNT(*) FILTER (WHERE dl.library_layout = 'SINGLE') AS single_runs,
    COALESCE(SUM(
        CASE WHEN {_FASTQ_BYTES_EXPR} IS NOT NULL AND {_FASTQ_BYTES_EXPR} != ''
             THEN (
                 SELECT SUM(v::bigint)
                 FROM unnest(string_to_array({_FASTQ_BYTES_EXPR}, ';')) AS v
                 WHERE v ~ '^[0-9]+$'
             )
             ELSE 0
        END
    ), 0) AS total_fastq_bytes
FROM sra_run_download_links dl
{_ORIGINAL_FASTQ_JOIN}
WHERE dl.study_accession IN (SELECT acc FROM candidates);
"""
)

STUDY_RUNS_SQL = (
    _RESOLVE_DL_STUDY
    + f"""
SELECT
    dl.run_accession,
    dl.experiment_accession,
    dl.library_layout,
    {_FASTQ_FTP_EXPR} AS fastq_ftp,
    {_FASTQ_BYTES_EXPR} AS fastq_bytes,
    {_FASTQ_MD5_EXPR} AS fastq_md5,
    {_FASTQ_FILENAMES_EXPR} AS fastq_filenames,
    dl.sra_ftp,
    dl.sra_bytes,
    dl.sra_md5,
    dl.ncbi_sra_url,
    dl.ncbi_sra_url_aws,
    dl.ncbi_sra_normalized_url,
    dl.ncbi_sra_normalized_bytes,
    dl.ncbi_sra_lite_url,
    dl.ncbi_sra_lite_bytes,
    dl.ncbi_sra_lite_s3_url,
    dl.ncbi_sra_lite_gs_url,
    COALESCE(ena.run_alias, gsa.run_alias) AS run_alias
FROM sra_run_download_links dl
LEFT JOIN ena_experiments ena ON ena.run_accession = dl.run_accession
LEFT JOIN gsa_experiments gsa ON gsa.run_accession = dl.run_accession
{_ORIGINAL_FASTQ_JOIN}
WHERE dl.study_accession IN (SELECT acc FROM candidates)
ORDER BY dl.experiment_accession, dl.run_accession;
"""
)

STUDY_RUNS_PREVIEW_SQL = (
    _RESOLVE_DL_STUDY
    + f"""
SELECT
    dl.run_accession,
    dl.experiment_accession,
    dl.library_layout,
    {_FASTQ_FTP_EXPR} AS fastq_ftp,
    {_FASTQ_BYTES_EXPR} AS fastq_bytes,
    {_FASTQ_MD5_EXPR} AS fastq_md5,
    {_FASTQ_FILENAMES_EXPR} AS fastq_filenames,
    dl.sra_ftp,
    dl.sra_bytes,
    dl.sra_md5,
    dl.ncbi_sra_url,
    dl.ncbi_sra_url_aws,
    dl.ncbi_sra_normalized_url,
    dl.ncbi_sra_normalized_bytes,
    dl.ncbi_sra_lite_url,
    dl.ncbi_sra_lite_bytes,
    dl.ncbi_sra_lite_s3_url,
    dl.ncbi_sra_lite_gs_url,
    COALESCE(ena.run_alias, gsa.run_alias) AS run_alias
FROM sra_run_download_links dl
LEFT JOIN ena_experiments ena ON ena.run_accession = dl.run_accession
LEFT JOIN gsa_experiments gsa ON gsa.run_accession = dl.run_accession
{_ORIGINAL_FASTQ_JOIN}
WHERE dl.study_accession IN (SELECT acc FROM candidates)
ORDER BY dl.experiment_accession, dl.run_accession
LIMIT 500;
"""
)

# GSA runs come from gsa_experiments; download links (fastq only, no sra/ncbi)
# are LEFT JOINed from sra_run_download_links.
GSA_STUDY_RUNS_SUMMARY_SQL = """
SELECT
    COUNT(*) AS total_runs,
    COUNT(*) FILTER (WHERE ge.library_layout = 'PAIRED') AS paired_runs,
    COUNT(*) FILTER (WHERE ge.library_layout = 'SINGLE') AS single_runs,
    COALESCE(SUM(
        CASE WHEN dl.fastq_bytes IS NOT NULL AND dl.fastq_bytes != ''
             THEN (
                 SELECT SUM(v::bigint)
                 FROM unnest(string_to_array(dl.fastq_bytes, ';')) AS v
                 WHERE v ~ '^[0-9]+$'
             )
             ELSE 0
        END
    ), 0) AS total_fastq_bytes
FROM gsa_experiments ge
LEFT JOIN sra_run_download_links dl ON dl.run_accession = ge.run_accession
WHERE ge.study_accession = %s;
"""

GSA_STUDY_RUNS_PREVIEW_SQL = """
SELECT
    ge.run_accession,
    ge.experiment_accession,
    ge.library_layout,
    dl.fastq_ftp,
    dl.fastq_bytes,
    dl.fastq_md5,
    NULL::text AS sra_ftp,
    NULL::text AS sra_bytes,
    NULL::text AS sra_md5,
    NULL::text AS ncbi_sra_url,
    NULL::text AS ncbi_sra_url_aws,
    NULL::text AS ncbi_sra_normalized_url,
    NULL::bigint AS ncbi_sra_normalized_bytes,
    NULL::text AS ncbi_sra_lite_url,
    NULL::bigint AS ncbi_sra_lite_bytes,
    NULL::text AS ncbi_sra_lite_s3_url,
    NULL::text AS ncbi_sra_lite_gs_url,
    ge.run_alias
FROM gsa_experiments ge
LEFT JOIN sra_run_download_links dl ON dl.run_accession = ge.run_accession
WHERE ge.study_accession = %s
ORDER BY ge.experiment_accession, ge.run_accession
LIMIT 500;
"""

# Unbounded GSA runs, for the full TSV export. Derived from the preview rather
# than copied, so the column list can't drift between the two.
GSA_STUDY_RUNS_SQL = GSA_STUDY_RUNS_PREVIEW_SQL.replace("\nLIMIT 500;", ";")

# Same columns as STUDY_RUNS_PREVIEW_SQL plus dl.study_accession; filtered to one run.
RUN_DOWNLOAD_SQL = f"""
SELECT
    dl.run_accession,
    dl.experiment_accession,
    dl.study_accession,
    dl.library_layout,
    {_FASTQ_FTP_EXPR} AS fastq_ftp,
    {_FASTQ_BYTES_EXPR} AS fastq_bytes,
    {_FASTQ_MD5_EXPR} AS fastq_md5,
    {_FASTQ_FILENAMES_EXPR} AS fastq_filenames,
    dl.sra_ftp,
    dl.sra_bytes,
    dl.sra_md5,
    dl.ncbi_sra_url,
    dl.ncbi_sra_url_aws,
    dl.ncbi_sra_normalized_url,
    dl.ncbi_sra_normalized_bytes,
    dl.ncbi_sra_lite_url,
    dl.ncbi_sra_lite_bytes,
    dl.ncbi_sra_lite_s3_url,
    dl.ncbi_sra_lite_gs_url,
    COALESCE(ena.run_alias, gsa.run_alias) AS run_alias
FROM sra_run_download_links dl
LEFT JOIN ena_experiments ena ON ena.run_accession = dl.run_accession
LEFT JOIN gsa_experiments gsa ON gsa.run_accession = dl.run_accession
{_ORIGINAL_FASTQ_JOIN}
WHERE dl.run_accession = %s;
"""

# BAM files: original submitted BAM files from ncbi_files JSONB

STUDY_BAMS_SQL = (
    _RESOLVE_DL_STUDY
    + """
SELECT
    dl.run_accession,
    dl.experiment_accession,
    f->>'filename'                    AS filename,
    f->>'url'                         AS url,
    f->>'size'                        AS size,
    f->>'md5'                         AS md5,
    f->>'semantic_name'               AS semantic_name,
    alt.https_url,
    alt.s3_url,
    COUNT(*) OVER()                   AS total_bams,
    COALESCE(SUM((f->>'size')::bigint) OVER(), 0)::text AS total_bam_bytes
FROM sra_run_download_links dl,
     -- ncbi_files can be a JSONB 'null' scalar, not an array
     jsonb_array_elements(
         CASE WHEN jsonb_typeof(dl.ncbi_files) = 'array'
              THEN dl.ncbi_files ELSE '[]'::jsonb END
     ) AS f,
     LATERAL (
         SELECT
             MAX(a->>'url') FILTER (
                 WHERE a->>'access_type' = 'anonymous'
                   AND a->>'url' LIKE 'https://%%'
             ) AS https_url,
             MAX(a->>'url') FILTER (
                 WHERE a->>'url' LIKE 's3://%%'
             ) AS s3_url
         FROM jsonb_array_elements(
                  CASE WHEN jsonb_typeof(f->'alternatives') = 'array'
                       THEN f->'alternatives' ELSE '[]'::jsonb END
              ) AS a
     ) alt
WHERE dl.study_accession IN (SELECT acc FROM candidates)
  AND f->>'supertype' = 'Original'
  AND f->>'filename' LIKE '%%.bam'
ORDER BY dl.experiment_accession, dl.run_accession;
"""
)

# Growth stats: monthly counts per database

DATABASE_GROWTH_MONTHLY_SQL = """
SELECT db, TO_CHAR(month, 'YYYY-MM') AS month, count
FROM database_growth_monthly
WHERE metric = %s
  AND (%s::text IS NULL OR db = %s::text)
ORDER BY db, month;
"""

DATABASE_VOLUME_GROWTH_MONTHLY_SQL = """
SELECT db, TO_CHAR(month, 'YYYY-MM') AS month, count
FROM database_volume_growth_monthly
WHERE %s::text IS NULL OR db = %s::text
ORDER BY db, month;
"""

# Projects mode (studies/series per month)

GROWTH_PROJECTS_GEO_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', published_at), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM geo_series
WHERE published_at IS NOT NULL
GROUP BY DATE_TRUNC('month', published_at)
ORDER BY month;
"""

GROWTH_PROJECTS_SRA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', published_at), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM sra_studies
WHERE published_at IS NOT NULL
GROUP BY DATE_TRUNC('month', published_at)
ORDER BY month;
"""

GROWTH_PROJECTS_AE_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', release_date), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM arrayexpress_experiments
WHERE release_date IS NOT NULL
GROUP BY DATE_TRUNC('month', release_date)
ORDER BY month;
"""

GROWTH_PROJECTS_ENA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', first_public), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM ena_studies
WHERE first_public IS NOT NULL
GROUP BY DATE_TRUNC('month', first_public)
ORDER BY month;
"""

GROWTH_PROJECTS_GSA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', first_public), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM gsa_studies
WHERE first_public IS NOT NULL
GROUP BY DATE_TRUNC('month', first_public)
ORDER BY month;
"""

GROWTH_PROJECTS_DRA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', published_at), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM dra_studies
WHERE published_at IS NOT NULL
GROUP BY DATE_TRUNC('month', published_at)
ORDER BY month;
"""

GROWTH_PROJECTS_GEA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', release_date), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM gea_experiments
WHERE release_date IS NOT NULL
GROUP BY DATE_TRUNC('month', release_date)
ORDER BY month;
"""

# Experiments mode (samples/experiments per month)

GROWTH_EXPERIMENTS_GEO_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', published_at), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM geo_samples
WHERE published_at IS NOT NULL
GROUP BY DATE_TRUNC('month', published_at)
ORDER BY month;
"""

GROWTH_EXPERIMENTS_SRA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', s.published_at), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM sra_experiments e
JOIN sra_studies s ON s.accession = e.study
WHERE s.published_at IS NOT NULL
GROUP BY DATE_TRUNC('month', s.published_at)
ORDER BY month;
"""

GROWTH_EXPERIMENTS_AE_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', e.release_date), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM arrayexpress_samples s
JOIN arrayexpress_experiments e ON e.accession = s.experiment_accession
WHERE e.release_date IS NOT NULL
GROUP BY DATE_TRUNC('month', e.release_date)
ORDER BY month;
"""

GROWTH_EXPERIMENTS_ENA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', first_public), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM ena_experiments
WHERE first_public IS NOT NULL
GROUP BY DATE_TRUNC('month', first_public)
ORDER BY month;
"""

GROWTH_EXPERIMENTS_GSA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', first_public), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM gsa_experiments
WHERE first_public IS NOT NULL
GROUP BY DATE_TRUNC('month', first_public)
ORDER BY month;
"""

GROWTH_EXPERIMENTS_DRA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', s.published_at), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM dra_experiments e
JOIN dra_studies s ON s.accession = e.study
WHERE s.published_at IS NOT NULL
GROUP BY DATE_TRUNC('month', s.published_at)
ORDER BY month;
"""

# GEA's sample table is the experiment's assay rows, mirroring arrayexpress_samples.
GROWTH_EXPERIMENTS_GEA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', e.release_date), 'YYYY-MM') AS month,
       COUNT(*) AS count
FROM gea_samples g
JOIN gea_experiments e ON e.accession = g.experiment_accession
WHERE e.release_date IS NOT NULL
GROUP BY DATE_TRUNC('month', e.release_date)
ORDER BY month;
"""

# Bases / bytes mode (data volume per month)

GROWTH_BASES_ENA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', first_public), 'YYYY-MM') AS month,
       SUM(base_count::bigint) AS count
FROM ena_experiments
WHERE first_public IS NOT NULL
  AND base_count IS NOT NULL
GROUP BY DATE_TRUNC('month', first_public)
ORDER BY month;
"""

GROWTH_BYTES_SRA_FASTQ_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', s.published_at), 'YYYY-MM') AS month,
       SUM(
           split_part(dl.fastq_bytes, ';', 1)::bigint +
           COALESCE(NULLIF(split_part(dl.fastq_bytes, ';', 2), '')::bigint, 0) +
           COALESCE(NULLIF(split_part(dl.fastq_bytes, ';', 3), '')::bigint, 0)
       ) AS count
FROM sra_run_download_links dl
JOIN sra_studies s ON s.accession = dl.study_accession
WHERE dl.fastq_bytes IS NOT NULL
  AND dl.fastq_bytes <> ''
  AND s.published_at IS NOT NULL
GROUP BY DATE_TRUNC('month', s.published_at)
ORDER BY month;
"""

GROWTH_BYTES_SRA_SRA_SQL = """
SELECT TO_CHAR(DATE_TRUNC('month', s.published_at), 'YYYY-MM') AS month,
       SUM(
           CASE
               WHEN dl.sra_bytes NOT LIKE '%;%' THEN dl.sra_bytes::bigint
               ELSE (SELECT SUM(x::bigint) FROM unnest(string_to_array(dl.sra_bytes, ';')) x)
           END
       ) AS count
FROM sra_run_download_links dl
JOIN sra_studies s ON s.accession = dl.study_accession
WHERE dl.sra_bytes IS NOT NULL
  AND dl.sra_bytes <> ''
  AND s.published_at IS NOT NULL
GROUP BY DATE_TRUNC('month', s.published_at)
ORDER BY month;
"""

# Organism growth: monthly sample/experiment counts filtered by organism

GROWTH_ORGANISM_SQL = """
SELECT TO_CHAR(month, 'YYYY-MM') AS month, db, count
FROM organism_growth_monthly
WHERE organism = %s
ORDER BY db, month;
"""

# Platform growth: monthly counts filtered by platform

GROWTH_PLATFORM_SQL = """
SELECT TO_CHAR(month, 'YYYY-MM') AS month, db, SUM(count) AS count
FROM platform_growth_monthly
WHERE platform = %s
GROUP BY month, db
ORDER BY db, month;
"""

GROWTH_PLATFORM_INSTRUMENT_SQL = """
SELECT TO_CHAR(month, 'YYYY-MM') AS month, db, SUM(count) AS count
FROM platform_growth_monthly
WHERE platform = %s AND instrument_model = %s
GROUP BY month, db
ORDER BY db, month;
"""

GROWTH_PLATFORM_PROJECTS_SQL = """
SELECT TO_CHAR(month, 'YYYY-MM') AS month, db, count
FROM platform_growth_projects_monthly
WHERE platform = %s
ORDER BY db, month;
"""

PLATFORM_TOTALS_SQL = """
SELECT platform, SUM(count) AS total,
       SUM(CASE WHEN db = 'geo' THEN count ELSE 0 END) AS geo,
       SUM(CASE WHEN db = 'sra' THEN count ELSE 0 END) AS sra,
       SUM(CASE WHEN db = 'ena' THEN count ELSE 0 END) AS ena,
       SUM(CASE WHEN db = 'gsa' THEN count ELSE 0 END) AS gsa,
       SUM(CASE WHEN db = 'dra' THEN count ELSE 0 END) AS dra,
       SUM(CASE WHEN db = 'gea' THEN count ELSE 0 END) AS gea
FROM platform_growth_monthly
GROUP BY platform
ORDER BY total DESC;
"""

PLATFORM_INSTRUMENTS_SQL = """
SELECT instrument_model, SUM(count) AS total
FROM platform_growth_monthly
WHERE platform = %s
  AND instrument_model IS NOT NULL
  AND instrument_model != ''
  AND instrument_model != 'unspecified'
GROUP BY instrument_model
ORDER BY total DESC;
"""

PLATFORM_FILTERS_SQL = """
WITH platform_studies AS MATERIALIZED (
    SELECT u.dominant_scientific_name, u.dominant_assay_l2, u.country
    FROM sra_studies s
    JOIN unified_metadata u ON u.canonical_accession = COALESCE(s.alias, s.accession)
    WHERE s.platforms @> ARRAY[%s]
)
SELECT 'organism' AS filter_type, dominant_scientific_name AS value, COUNT(*) AS count
FROM platform_studies
WHERE dominant_scientific_name IS NOT NULL
GROUP BY dominant_scientific_name
HAVING COUNT(*) >= 5

UNION ALL

SELECT 'assay', dominant_assay_l2, COUNT(*)
FROM platform_studies
WHERE dominant_assay_l2 IS NOT NULL AND dominant_assay_l2 != ''
GROUP BY dominant_assay_l2

UNION ALL

SELECT 'country', country, COUNT(*)
FROM platform_studies
WHERE country IS NOT NULL AND country != ''
GROUP BY country
HAVING COUNT(*) >= 5

ORDER BY filter_type, count DESC;
"""


def _organism_membership_clause(alias: str = "") -> str:
    """Match studies where an organism is dominant or present in organism_counts."""
    q = f"{alias}." if alias else ""
    return f"({q}dominant_scientific_name = %s OR {q}organism_counts ? %s)"


def build_platform_growth_filtered_sql(
    platform: str,
    mode: str = "experiments",
    instrument_model: str | None = None,
    organism: str | None = None,
    assay: str | None = None,
    country: str | None = None,
) -> tuple[str, list]:
    """Build live query for platform growth with cross-filters.

    Uses unified_metadata for organism/assay/country matching (same source as
    the global contributions card). Cross-filtered queries only cover SRA/GEO
    (sra_experiments + sra_studies). ENA is excluded because unified_metadata
    does not reliably cover ENA experiments for cross-dimensional filtering.
    """
    params: list = []
    exp_conditions = ["e.platform = %s", "st.published_at IS NOT NULL"]
    params.append(platform)

    if instrument_model:
        exp_conditions.append("e.instrument_model = %s")
        params.append(instrument_model)

    # Join to unified_metadata for cross-filters (COALESCE for single index probe)
    um_join = ""
    um_conditions: list[str] = []
    if organism or assay or country:
        um_join = " JOIN unified_metadata u ON u.canonical_accession = COALESCE(st.alias, st.accession)"
        if organism:
            um_conditions.append(_organism_membership_clause("u"))
            params.extend([organism, organism])
        if assay:
            um_conditions.append("u.dominant_assay_l2 = %s")
            params.append(assay)
        if country:
            um_conditions.append("u.country = %s")
            params.append(country)

    where = " AND ".join(exp_conditions + um_conditions)

    if mode == "projects":
        select = "COUNT(DISTINCT st.accession)"
    else:
        select = "COUNT(*)"

    sql = f"""
    SELECT TO_CHAR(DATE_TRUNC('month', st.published_at), 'YYYY-MM') AS month,
           CASE WHEN st.alias LIKE 'GSE%%' THEN 'geo' ELSE 'sra' END AS db,
           {select} AS count
    FROM sra_experiments e
    JOIN sra_studies st ON st.accession = e.study
    {um_join}
    WHERE {where}
    GROUP BY month, db
    ORDER BY db, month
    """
    return sql, params


# List all distinct organisms across databases

LIST_ORGANISMS_SQL = """
SELECT DISTINCT organism
FROM organism_growth_monthly
WHERE organism IS NOT NULL AND organism != ''
ORDER BY organism;
"""

LIST_ORGANISMS_WITH_COMMON_NAMES_SQL = """
SELECT DISTINCT ogm.organism AS scientific_name,
       cn.common_name
FROM organism_growth_monthly ogm
LEFT JOIN common_names cn ON LOWER(cn.scientific_name) = LOWER(ogm.organism)
WHERE ogm.organism IS NOT NULL AND ogm.organism != ''
ORDER BY ogm.organism;
"""


# Organism experiment totals: total experiments per organism per database

ORGANISM_TOTALS_SQL = """
SELECT o.organism, o.db, o.count, cn.common_name
FROM (
    SELECT organism, db, SUM(count) AS count
    FROM organism_growth_monthly
    GROUP BY organism, db
) o
LEFT JOIN common_names cn ON cn.scientific_name = o.organism
ORDER BY o.organism, o.db;
"""

ORGANISM_TOTALS_YEAR_SQL = """
SELECT o.organism, o.db, o.count, cn.common_name
FROM (
    SELECT organism, db, SUM(count) AS count
    FROM organism_growth_monthly
    WHERE month >= make_date(%s, 1, 1) AND month <= make_date(%s, 12, 31)
    GROUP BY organism, db
) o
LEFT JOIN common_names cn ON cn.scientific_name = o.organism
ORDER BY o.organism, o.db;
"""

ORGANISM_SEARCH_SQL = """
WITH candidates AS (
    SELECT DISTINCT organism
    FROM organism_growth_monthly
    WHERE organism ILIKE %s
    UNION
    SELECT scientific_name AS organism
    FROM common_names
    WHERE common_name ILIKE %s
      AND scientific_name IN (SELECT DISTINCT organism FROM organism_growth_monthly)
)
SELECT c.organism, SUM(ogm.count) AS total, cn.common_name
FROM candidates c
JOIN organism_growth_monthly ogm ON ogm.organism = c.organism
LEFT JOIN common_names cn ON cn.scientific_name = c.organism
GROUP BY c.organism, cn.common_name
ORDER BY total DESC
LIMIT %s;
"""

# Global contributions: aggregated lat/lng from unified_metadata

_GLOBAL_CONTRIBUTIONS_OPTIONAL_COLS = ["place_type", "address_type"]
_global_contributions_available_cols: set[str] = set()


def _build_global_contributions_template(available_cols: set[str]) -> str:
    """Build the template, omitting MODE()/SELECT for columns not in PG."""
    base_cols = [
        "ROUND(latitude::numeric, 2) AS lat",
        "ROUND(longitude::numeric, 2) AS lng",
        "source",
        "n_experiments",
        "n_samples",
        "country",
        "country_code",
        "city",
        "state",
        "place_name",
        "short_label",
        "center_name",
        "dominant_scientific_name",
    ]
    mode_lines = [
        "MODE() WITHIN GROUP (ORDER BY country) AS country",
        "MODE() WITHIN GROUP (ORDER BY country_code) AS country_code",
        "MODE() WITHIN GROUP (ORDER BY city) AS city",
        "MODE() WITHIN GROUP (ORDER BY state) AS state",
        "MODE() WITHIN GROUP (ORDER BY place_name) AS place_name",
    ]
    select_cols = [
        "a.country",
        "a.country_code",
        "a.city",
        "a.state",
        "a.place_name",
    ]
    for col in _GLOBAL_CONTRIBUTIONS_OPTIONAL_COLS:
        if col in available_cols:
            base_cols.append(col)
            mode_lines.append(f"MODE() WITHIN GROUP (ORDER BY {col}) AS {col}")
            select_cols.append(f"a.{col}")
        else:
            select_cols.append(f"NULL AS {col}")
    mode_lines.append("MODE() WITHIN GROUP (ORDER BY short_label) AS short_label")
    mode_lines.append("MODE() WITHIN GROUP (ORDER BY center_name) AS center_name")
    select_cols.extend(["a.short_label", "a.center_name"])

    base_block = ",\n        ".join(base_cols)
    mode_block = ",\n        ".join(mode_lines)
    select_block = ", ".join(select_cols)

    return f"""
WITH filtered AS MATERIALIZED (
    SELECT
        {base_block}
    FROM unified_metadata
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
      AND __WHERE__
),
location_agg AS (
    SELECT
        lat,
        lng,
        COUNT(*) AS n_projects,
        COALESCE(SUM(n_experiments), 0)::bigint AS n_experiments,
        COALESCE(SUM(n_samples), 0)::bigint AS n_samples,
        COUNT(*) FILTER (WHERE source = 'geo') AS n_geo,
        COUNT(*) FILTER (WHERE source = 'sra') AS n_sra,
        COUNT(*) FILTER (WHERE source = 'arrayexpress') AS n_ae,
        COUNT(*) FILTER (WHERE source = 'ena') AS n_ena,
        COUNT(*) FILTER (WHERE source = 'gsa') AS n_gsa,
        {mode_block}
    FROM filtered
    GROUP BY 1, 2
),
organism_ranked AS (
    SELECT
        lat,
        lng,
        dominant_scientific_name AS organism,
        COUNT(*) AS cnt,
        ROW_NUMBER() OVER (
            PARTITION BY lat, lng
            ORDER BY COUNT(*) DESC
        ) AS rn
    FROM filtered
    WHERE dominant_scientific_name IS NOT NULL
    GROUP BY 1, 2, dominant_scientific_name
),
organism_top5 AS (
    SELECT lat, lng,
        jsonb_agg(jsonb_build_object('name', organism, 'count', cnt) ORDER BY cnt DESC) AS top_organisms
    FROM organism_ranked
    WHERE rn <= 5
    GROUP BY lat, lng
)
SELECT
    a.lat, a.lng, a.n_projects, a.n_experiments, a.n_samples,
    a.n_geo, a.n_sra, a.n_ae, a.n_ena, a.n_gsa,
    {select_block},
    COALESCE(o.top_organisms, '[]'::jsonb) AS top_organisms
FROM location_agg a
LEFT JOIN organism_top5 o ON a.lat = o.lat AND a.lng = o.lng
ORDER BY a.n_projects DESC;
"""


_GLOBAL_CONTRIBUTIONS_TEMPLATE = _build_global_contributions_template(
    {"place_type", "address_type"}
)


def build_global_contributions_sql(
    organism: str | None = None,
    assay_l1: str | None = None,
    assay_l2: str | None = None,
    place_type: str | None = None,
    address_type: str | None = None,
) -> tuple[str, list]:
    """Build the global contributions SQL with optional filters.

    Filter values go into the params list (safe from injection).
    The template already has ``latitude IS NOT NULL AND longitude IS NOT NULL``
    in the WHERE clause, so we only add column-level filters here.
    """
    conditions: list[str] = ["TRUE"]
    params: list = []
    if organism:
        conditions.append(_organism_membership_clause())
        params.extend([organism, organism])
    if assay_l1:
        conditions.append("dominant_assay_l1 = %s")
        params.append(assay_l1)
    if assay_l2:
        conditions.append("dominant_assay_l2 = %s")
        params.append(assay_l2)
    if place_type and "place_type" in _global_contributions_available_cols:
        conditions.append("place_type = %s")
        params.append(place_type)
    if address_type and "address_type" in _global_contributions_available_cols:
        conditions.append("address_type = %s")
        params.append(address_type)
    where = " AND ".join(conditions)
    sql = _GLOBAL_CONTRIBUTIONS_TEMPLATE.replace("__WHERE__", where)
    return sql, params


def init_global_contributions_schema(pg_conn):
    """Probe unified_metadata for optional columns and rebuild template."""
    global _GLOBAL_CONTRIBUTIONS_TEMPLATE, _global_contributions_available_cols
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'unified_metadata' AND column_name = ANY(%s)",
            (_GLOBAL_CONTRIBUTIONS_OPTIONAL_COLS,),
        )
        _global_contributions_available_cols = {r[0] for r in cur.fetchall()}
    _GLOBAL_CONTRIBUTIONS_TEMPLATE = _build_global_contributions_template(
        _global_contributions_available_cols
    )


def _build_global_contributions_filters_sql(available_cols: set[str]) -> str:
    ctes = [
        ("organism_opts", "dominant_scientific_name", "organism", "LIMIT 200"),
        ("assay_l1_opts", "dominant_assay_l1", "assay_l1", ""),
        ("assay_l2_opts", "dominant_assay_l2", "assay_l2", ""),
    ]
    for col in _GLOBAL_CONTRIBUTIONS_OPTIONAL_COLS:
        if col in available_cols:
            ctes.append((f"{col}_opts", col, col, ""))

    base_cols = ", ".join(col for _, col, _, _ in ctes)
    cte_parts = [
        "base AS MATERIALIZED (\n"
        f"    SELECT {base_cols}\n"
        "    FROM unified_metadata\n"
        "    WHERE latitude IS NOT NULL AND longitude IS NOT NULL\n"
        ")"
    ]
    select_parts = []
    for cte_name, col, kind, limit in ctes:
        cte_parts.append(
            f"{cte_name} AS (\n"
            f"    SELECT {col} AS value, COUNT(*) AS count\n"
            f"    FROM base\n"
            f"    WHERE {col} IS NOT NULL\n"
            f"    GROUP BY 1 ORDER BY count DESC\n"
            f"    {limit}\n"
            f")"
        )
        select_parts.append(f"SELECT '{kind}' AS kind, value, count FROM {cte_name}")

    return (
        "WITH "
        + ",\n".join(cte_parts)
        + "\n"
        + "\nUNION ALL\n".join(select_parts)
        + ";"
    )


GLOBAL_CONTRIBUTIONS_FILTERS_SQL = _build_global_contributions_filters_sql(
    {"place_type", "address_type"}
)


def init_global_contributions_filters_sql() -> str:
    """Rebuild filters SQL using detected available columns. Returns new SQL."""
    global GLOBAL_CONTRIBUTIONS_FILTERS_SQL
    GLOBAL_CONTRIBUTIONS_FILTERS_SQL = _build_global_contributions_filters_sql(
        _global_contributions_available_cols
    )
    return GLOBAL_CONTRIBUTIONS_FILTERS_SQL


def build_country_filters_sql(
    country: str,
    organism: str | None = None,
) -> tuple[str, list]:
    """Build filters SQL scoped to a specific country (and optionally organism).

    Organism CTE is always scoped to country only (never self-filtered).
    Assay and other CTEs are scoped to country + organism when organism is given.
    A totals CTE returns the overall count for the selected scope.
    """
    cols = _global_contributions_available_cols or {"place_type", "address_type"}

    ctes_cfg: list[tuple[str, str, str, str, str]] = [
        (
            "organism_opts",
            "dominant_scientific_name",
            "organism",
            "LIMIT 200",
            "country_base",
        ),
        ("assay_l1_opts", "dominant_assay_l1", "assay_l1", "", "scoped_base"),
        ("assay_l2_opts", "dominant_assay_l2", "assay_l2", "", "scoped_base"),
    ]
    for col in _GLOBAL_CONTRIBUTIONS_OPTIONAL_COLS:
        if col in cols:
            ctes_cfg.append((f"{col}_opts", col, col, "", "scoped_base"))

    base_cols = list(dict.fromkeys(col for _, col, _, _, _ in ctes_cfg).keys())
    if organism:
        base_cols.append("organism_counts")
    facet_cols = ", ".join(base_cols)
    params: list = [country]
    scoped_where = ""
    if organism:
        scoped_where = f"WHERE {_organism_membership_clause()}"
        params.extend([organism, organism])

    cte_parts = [
        "country_base AS MATERIALIZED (\n"
        f"    SELECT {facet_cols}\n"
        "    FROM unified_metadata\n"
        "    WHERE latitude IS NOT NULL AND longitude IS NOT NULL\n"
        "      AND country = %s\n"
        "),\n"
        "scoped_base AS MATERIALIZED (\n"
        "    SELECT *\n"
        "    FROM country_base\n"
        f"    {scoped_where}\n"
        ")"
    ]
    select_parts = []

    for cte_name, col, kind, limit, source in ctes_cfg:
        cte_parts.append(
            f"{cte_name} AS (\n"
            f"    SELECT {col} AS value, COUNT(*) AS count\n"
            f"    FROM {source}\n"
            f"    WHERE {col} IS NOT NULL\n"
            f"    GROUP BY 1 ORDER BY count DESC\n"
            f"    {limit}\n"
            f")"
        )
        select_parts.append(f"SELECT '{kind}' AS kind, value, count FROM {cte_name}")

    cte_parts.append(
        f"totals AS (\n    SELECT COUNT(*) AS count\n    FROM scoped_base\n)"
    )
    select_parts.append("SELECT 'total' AS kind, NULL AS value, count FROM totals")

    sql = (
        "WITH "
        + ",\n".join(cte_parts)
        + "\n"
        + "\nUNION ALL\n".join(select_parts)
        + ";"
    )
    return sql, params


# Spec is (table, column, distinct, where). ENA mirrors the whole INSDC space,
# so sra_exp/ena_exp are prefix-partitioned (SRX vs ERX/DRX) to avoid emitting
# the same /e URL twice.
SITEMAP_TABLES = {
    "geo": ("geo_series", "accession", False, None),
    "sra": ("sra_studies", "accession", False, None),
    "arrayexpress": ("arrayexpress_experiments", "accession", False, None),
    "ena": ("ena_studies", "study_accession", False, None),
    "sra_exp": (
        "sra_experiments",
        "accession",
        False,
        "accession >= 'SRX' AND accession < 'SRY'",
    ),
    "ena_exp": (
        "ena_experiments",
        "experiment_accession",
        True,
        "experiment_accession >= 'DRX' AND experiment_accession < 'ERY'",
    ),
    "gsa": ("gsa_studies", "study_accession", False, None),
    "gsa_exp": ("gsa_experiments", "experiment_accession", True, None),
    "run": ("sra_run_download_links", "run_accession", False, None),
    "geo_sample": ("geo_samples", "accession", False, None),
    "sra_sample": ("sra_samples", "accession", False, None),
    "ena_sample": ("ena_samples", "sample_accession", False, None),
    "gsa_sample": ("gsa_samples", "sample_accession", False, None),
}


def _sitemap_accessions_sql(
    table: str, col: str, distinct: bool, where: str | None
) -> str:
    select = (
        f"SELECT DISTINCT {col} AS accession"
        if distinct
        else f"SELECT {col} AS accession"
    )
    where_clause = f" WHERE {where}" if where else ""
    return f"{select} FROM {table}{where_clause} ORDER BY {col} LIMIT %s OFFSET %s;"


def _sitemap_count_expr(table: str, col: str, distinct: bool, where: str | None) -> str:
    reltuples = (
        f"(SELECT reltuples::bigint FROM pg_class WHERE oid = '{table}'::regclass)"
    )
    if not distinct:
        return reltuples
    return (
        "(SELECT COALESCE("
        "(SELECT CASE WHEN s.n_distinct >= 0 THEN s.n_distinct "
        "ELSE -s.n_distinct * c.reltuples END "
        f"FROM pg_stats s WHERE s.schemaname = 'public' AND s.tablename = '{table}' "
        f"AND s.attname = '{col}'), c.reltuples)::bigint "
        f"FROM pg_class c WHERE c.oid = '{table}'::regclass)"
    )


def _sitemap_keyset_sql(table: str, col: str, distinct: bool, where: str | None) -> str:
    # Keyset page from a precomputed boundary: O(limit) at any depth, vs OFFSET.
    select = (
        f"SELECT DISTINCT {col} AS accession"
        if distinct
        else f"SELECT {col} AS accession"
    )
    extra = f" AND {where}" if where else ""
    return f"{select} FROM {table} WHERE {col} >= %s{extra} ORDER BY {col} LIMIT %s;"


SITEMAP_ACCESSIONS_SQL = {
    key: _sitemap_accessions_sql(*spec) for key, spec in SITEMAP_TABLES.items()
}

SITEMAP_KEYSET_SQL = {
    key: _sitemap_keyset_sql(*spec) for key, spec in SITEMAP_TABLES.items()
}

SITEMAP_PAGE_SIZE = 50000
SITEMAP_BOUND_LOOKUP_SQL = (
    "SELECT start_accession FROM sitemap_page_bounds WHERE source = %s AND page = %s;"
)
SITEMAP_SOURCE_BUILT_SQL = (
    "SELECT 1 FROM sitemap_page_bounds WHERE source = %s LIMIT 1;"
)
# Accession count = max(page)*PAGE_SIZE + 1 so the frontend's ceil(count/PAGE_SIZE)
# reproduces the exact page count.
SITEMAP_BOUNDS_COUNTS_SQL = (
    "SELECT source, MAX(page) * %s + 1 AS count "
    "FROM sitemap_page_bounds GROUP BY source;"
)

SITEMAP_COUNTS_SQL = (
    "SELECT "
    + ", ".join(
        f"{_sitemap_count_expr(*spec)} AS {key}" for key, spec in SITEMAP_TABLES.items()
    )
    + ";"
)


# reltuples estimates (not exact)
def _reltuples(table: str) -> str:
    return f"(SELECT reltuples::bigint FROM pg_class WHERE oid = '{table}'::regclass)"


# Cross-archive project overlap
PROJECT_OVERLAP_SQL = """
SELECT dbs, projects, n_dbs, built_at
FROM project_overlap
ORDER BY projects DESC;
"""


SOURCE_TOTALS_SQL = f"""
SELECT
    {_reltuples("geo_series")} AS geo_projects,
    {_reltuples("geo_samples")} AS geo_samples,
    {_reltuples("sra_studies")} AS sra_projects,
    {_reltuples("sra_samples")} AS sra_samples,
    {_reltuples("arrayexpress_experiments")} AS arrayexpress_projects,
    {_reltuples("arrayexpress_samples")} AS arrayexpress_samples,
    {_reltuples("ena_studies")} AS ena_projects,
    {_reltuples("ena_samples")} AS ena_samples,
    {_reltuples("gsa_studies")} AS gsa_projects,
    {_reltuples("gsa_samples")} AS gsa_samples,
    {_reltuples("dra_studies")} AS dra_projects,
    {_reltuples("dra_samples")} AS dra_samples,
    {_reltuples("gea_experiments")} AS gea_projects,
    {_reltuples("gea_samples")} AS gea_samples;
"""

LAST_UPDATED_SQL = """
SELECT
    (SELECT MAX(updated_at)::timestamptz FROM geo_series
        WHERE updated_at::timestamptz <= now()) AS geo,
    (SELECT MAX(published_at)::timestamptz FROM sra_submissions
        WHERE published_at::timestamptz <= now()) AS sra,
    (SELECT MAX(release_date)::timestamptz FROM arrayexpress_experiments
        WHERE release_date::timestamptz <= now()) AS arrayexpress,
    (SELECT MAX(COALESCE(last_updated, first_public))::timestamptz FROM ena_studies
        WHERE COALESCE(last_updated, first_public)::timestamptz <= now()) AS ena,
    (SELECT MAX(COALESCE(last_updated, first_public))::timestamptz FROM gsa_studies
        WHERE COALESCE(last_updated, first_public)::timestamptz <= now()) AS gsa,
    (SELECT MAX(COALESCE(updated_at, published_at))::timestamptz FROM dra_studies
        WHERE COALESCE(updated_at, published_at)::timestamptz <= now()) AS dra,
    (SELECT MAX(COALESCE(updated_at, release_date))::timestamptz FROM gea_experiments
        WHERE COALESCE(updated_at, release_date)::timestamptz <= now()) AS gea;
"""

ENRICHED_STAT_COLUMNS = [
    "tissue",
    "cell_type",
    "disease",
    "assay",
    "assay_category",
    "sex",
    "organism",
    "development_stage",
    "sample_type",
    "strain",
    "treatment",
    "cell_line",
    "ethnicity",
    "phenotype",
    "genetic_modification",
    "tissue_primary_site",
    "tissue_site_type",
]
ENRICHED_TOP_VALUES_PER_COLUMN = 100


def _enriched_summary_ddl(suffix: str = "") -> str:
    cols = ",\n    ".join(
        f"COUNT({c}) AS {c}_filled, COUNT(DISTINCT {c}) AS {c}_distinct"
        for c in ENRICHED_STAT_COLUMNS
    )
    return (
        f"DROP MATERIALIZED VIEW IF EXISTS enriched_summary{suffix};\n"
        f"CREATE MATERIALIZED VIEW enriched_summary{suffix} AS\nSELECT\n"
        "    COUNT(*) AS enriched_samples,\n"
        "    COUNT(DISTINCT study_accession) AS enriched_projects,\n"
        f"    {cols}\n"
        "FROM ontology_samples_v5;"
    )


def _enriched_top_values_ddl(suffix: str = "") -> str:
    union = "\n        UNION ALL\n        ".join(
        f"SELECT '{c}' AS column_name, {c}::text AS value, COUNT(*) AS n "
        f"FROM ontology_samples_v5 WHERE {c} IS NOT NULL AND {c}::text <> '' "
        f"GROUP BY {c}"
        for c in ENRICHED_STAT_COLUMNS
    )
    return (
        f"DROP MATERIALIZED VIEW IF EXISTS enriched_column_top_values{suffix};\n"
        f"CREATE MATERIALIZED VIEW enriched_column_top_values{suffix} AS\n"
        "SELECT column_name, value, n FROM (\n"
        "    SELECT column_name, value, n,\n"
        "           ROW_NUMBER() OVER (PARTITION BY column_name ORDER BY n DESC) AS rk\n"
        "    FROM (\n        " + union + "\n    ) g\n"
        f") ranked WHERE rk <= {ENRICHED_TOP_VALUES_PER_COLUMN};\n"
        f"CREATE INDEX IF NOT EXISTS idx_enriched_top_values_col_n{suffix} "
        f"ON enriched_column_top_values{suffix} (column_name, n DESC);"
    )


ENRICHED_CROSSTAB_COLUMNS = [
    "organism",
    "tissue",
    "cell_type",
    "disease",
    "assay",
    "development_stage",
    "sample_type",
    "sex",
    "ethnicity",
    "phenotype",
    "genetic_modification",
]
ENRICHED_CROSSTAB_TOP_GROUPS = 100
ENRICHED_CROSSTAB_TOP_BREAKDOWN = 50


def _enriched_crosstab_ddl(suffix: str = "") -> str:
    base_cols = ",\n            ".join(
        (
            "COALESCE(cn.common_name, um.dominant_scientific_name) AS organism"
            if c == "organism"
            else f"os.{c}"
        )
        for c in ENRICHED_CROSSTAB_COLUMNS
    )
    pairs = "\n        UNION ALL\n        ".join(
        f"SELECT '{g}' AS group_col, {g}::text AS group_val, "
        f"'{b}' AS breakdown_col, {b}::text AS breakdown_val, COUNT(*) AS n "
        f"FROM base "
        f"WHERE {g} IS NOT NULL AND {g}::text <> '' "
        f"AND {b} IS NOT NULL AND {b}::text <> '' "
        f"GROUP BY {g}, {b}"
        for g in ENRICHED_CROSSTAB_COLUMNS
        for b in ENRICHED_CROSSTAB_COLUMNS
        if g != b
    )
    return (
        f"DROP MATERIALIZED VIEW IF EXISTS enriched_crosstab{suffix};\n"
        f"CREATE MATERIALIZED VIEW enriched_crosstab{suffix} AS\n"
        "WITH base AS MATERIALIZED (\n"
        f"        SELECT\n            {base_cols}\n"
        "        FROM ontology_samples_v5 os\n"
        "        LEFT JOIN unified_metadata um "
        "ON um.canonical_accession = os.study_accession\n"
        "        LEFT JOIN (SELECT scientific_name, MIN(common_name) AS common_name "
        "FROM common_names GROUP BY scientific_name) cn "
        "ON cn.scientific_name = um.dominant_scientific_name\n"
        "    ),\n"
        "    pairs AS (\n        " + pairs + "\n    ),\n"
        "    counts AS (\n"
        "        SELECT group_col, group_val, breakdown_col, breakdown_val, SUM(n) AS n\n"
        "        FROM pairs GROUP BY 1, 2, 3, 4\n"
        "    ),\n"
        "    grp AS (\n"
        "        SELECT group_col, group_val, breakdown_col,\n"
        "               SUM(n) AS group_total, COUNT(*) AS group_distinct\n"
        "        FROM counts GROUP BY 1, 2, 3\n"
        "    ),\n"
        "    ranked AS (\n"
        "        SELECT c.group_col, c.group_val, c.breakdown_col, c.breakdown_val,\n"
        "               c.n, g.group_total, g.group_distinct,\n"
        "               ROW_NUMBER() OVER (PARTITION BY c.group_col, c.group_val,\n"
        "                   c.breakdown_col ORDER BY c.n DESC) AS brk,\n"
        "               DENSE_RANK() OVER (PARTITION BY c.group_col, c.breakdown_col\n"
        "                   ORDER BY g.group_total DESC, c.group_val) AS grk\n"
        "        FROM counts c JOIN grp g USING (group_col, group_val, breakdown_col)\n"
        "    )\n"
        "SELECT group_col, group_val, breakdown_col, breakdown_val,\n"
        "       n, group_total, group_distinct\n"
        f"FROM ranked WHERE brk <= {ENRICHED_CROSSTAB_TOP_BREAKDOWN} "
        f"AND grk <= {ENRICHED_CROSSTAB_TOP_GROUPS};\n"
        f"CREATE INDEX IF NOT EXISTS idx_enriched_crosstab_pair{suffix} "
        f"ON enriched_crosstab{suffix} (group_col, breakdown_col, group_total DESC);"
    )


# ontology_samples_v5 has no source column; read the archive off the accession
ENRICHED_SOURCE_CASE = """CASE
            WHEN study_accession LIKE 'GSE%' THEN 'geo'
            WHEN study_accession LIKE 'SRP%' OR study_accession LIKE 'PRJNA%' THEN 'sra'
            WHEN study_accession LIKE 'ERP%' OR study_accession LIKE 'PRJEB%' THEN 'ena'
            WHEN study_accession LIKE 'DRP%' OR study_accession LIKE 'PRJDB%' THEN 'dra'
            WHEN study_accession LIKE 'CRA%' OR study_accession LIKE 'HRA%' THEN 'gsa'
            WHEN study_accession LIKE 'E-GEAD-%' THEN 'gea'
            WHEN study_accession LIKE 'E-%' THEN 'arrayexpress'
            ELSE 'other'
        END"""


ENRICHED_COVERAGE_COLUMNS = ("organism", "assay", "assay_category")


def _enriched_coverage_ddl(suffix: str = "") -> str:
    """Enriched project/sample counts per archive, organism and assay."""
    dims = "\n".join(
        "UNION ALL\n"
        f"SELECT '{c}', {c},\n"
        "       COUNT(DISTINCT study_accession), COUNT(*)\n"
        f"FROM base WHERE {c} IS NOT NULL AND {c} <> ''\n"
        f"GROUP BY {c}"
        for c in ENRICHED_COVERAGE_COLUMNS
    )
    cols = ", ".join(ENRICHED_COVERAGE_COLUMNS)
    return (
        f"DROP MATERIALIZED VIEW IF EXISTS enriched_coverage{suffix};\n"
        f"CREATE MATERIALIZED VIEW enriched_coverage{suffix} AS\n"
        "WITH base AS MATERIALIZED (\n"
        f"        SELECT study_accession, {cols},\n"
        f"               {ENRICHED_SOURCE_CASE} AS source\n"
        "        FROM ontology_samples_v5\n"
        "    )\n"
        "SELECT 'source' AS dimension, source AS value,\n"
        "       COUNT(DISTINCT study_accession) AS projects, COUNT(*) AS samples\n"
        "FROM base GROUP BY source\n"
        f"{dims};"
    )


# true counts; the API returns only a top slice
ENRICHED_COVERAGE_TOTALS_SQL = """
SELECT dimension, COUNT(*) AS n FROM enriched_coverage GROUP BY dimension;
"""

ENRICHED_COVERAGE_SQL = """
SELECT dimension, value, projects, samples
FROM enriched_coverage
WHERE dimension = %s
ORDER BY samples DESC, value
LIMIT %s;
"""


# rebuild_enriched_stats.sh reads this list
ENRICHED_STATS_VIEWS = (
    "enriched_summary",
    "enriched_column_top_values",
    "enriched_crosstab",
    "enriched_coverage",
)


def enriched_stats_ddl(suffix: str = "") -> str:
    """DDL for the enriched-stats matviews, optionally under a suffix."""
    return (
        _enriched_summary_ddl(suffix)
        + "\n\n"
        + _enriched_top_values_ddl(suffix)
        + "\n\n"
        + _enriched_crosstab_ddl(suffix)
        + "\n\n"
        + _enriched_coverage_ddl(suffix)
    )


ENRICHED_STATS_DDL = enriched_stats_ddl()

# rare disease (GARD)

DISEASE_COLLECTIONS = {
    "rare": {
        "prefix": "rare_disease",
        "label": "catalogue_diseases",
        "label_col": "gard_names",
        "tags": {
            "category": "categories",
            "specialty": "specialties",
            "group": "groups",
        },
        "catalogue": ("gard_disease", "gard_name"),
    },
    "nord": {
        "prefix": "nord_disease",
        "label": "catalogue_diseases",
        "label_col": "nord_names",
        "tags": {"nord_type": "nord_types"},
        "catalogue": ("nord_disease", "nord_name"),
    },
}

_SCALAR_FILTERS = ("assay_category",)

_BOOL_FILTERS = ("has_fastq", "has_sra")

_ARRAY_FILTERS = {
    "ancestry": "ancestries",
    "organism": "organisms",
    "inheritance": "inheritance",
}

SORTABLE = frozenset(
    {
        "cells",
        "n_samples",
        "n_diseases",
        "title",
        "study_accession",
        "assay_category",
        "organism",
    }
)

SCOPES = {
    # human material that is neither an immortalized line nor a derived model
    "human_primary": ("n_human_primary", "r.n_human_primary > 0"),
    # organoid / iPSC / ESC / xenograft: patient-derived, but not a patient cohort
    "patient_derived_model": ("n_model", "r.n_model > 0"),
    "cell_line": ("n_cell_line", "r.n_cell_line > 0"),
    "all": ("n_samples", "TRUE"),
}
DEFAULT_SCOPE = "human_primary"

_ALIAS_NORM = "btrim(regexp_replace(lower({}), '[^a-z0-9]+', ' ', 'g'))"

DISEASE_MONDO_SET = f"""(
    SELECT coalesce(array_agg(DISTINCT m), '{{}}') FROM (
        SELECT unnest(a.mondo_ids) AS m FROM disease_alias a
         WHERE a.alias = {_ALIAS_NORM.format("%(disease)s::text")}
        UNION
        SELECT c.descendant_id FROM disease_alias a
          JOIN ontology_closure c
            ON c.ontology = 'mondo' AND c.ancestor_id = ANY(a.mondo_ids)
         WHERE a.alias = {_ALIAS_NORM.format("%(disease)s::text")}
    ) x
)"""

DISEASE_ALIAS_LOOKUP_SQL = f"""
SELECT display_name, alias, mondo_ids, sources,
       COUNT(*) OVER () AS total_count
FROM disease_alias
-- the %% operator, not similarity(): only the operator uses
-- idx_disease_alias_trgm; both forms select the same
-- rows. Every literal percent is doubled; psycopg scans comments for
-- placeholders too
WHERE alias %% {_ALIAS_NORM.format("%(q)s::text")}
   OR alias LIKE {_ALIAS_NORM.format("%(q)s::text")} || '%%'
ORDER BY (alias = {_ALIAS_NORM.format("%(q)s::text")}) DESC,
         similarity(alias, {_ALIAS_NORM.format("%(q)s::text")}) DESC,
         length(alias)
LIMIT %(limit)s;
"""

# Upper-ontology terms every disease inherits; they say nothing about the study.
UPPER_ONTOLOGY_TERMS = [
    "entity",
    "continuant",
    "specifically dependent continuant",
    "realizable entity",
    "disposition",
    "disease",
    "human disease",
]


def build_disease_summary_sql(collection: str) -> str:
    """Corpus-wide totals for one disease collection."""
    prefix = DISEASE_COLLECTIONS[collection]["prefix"]
    return f"""
SELECT count(*)                        AS studies,
       sum(n_samples)                  AS samples,
       sum(cells)                      AS cells,
       count(*) FILTER (WHERE reads_single_cell > 0) AS studies_single_cell,
       count(*) FILTER (WHERE cells IS NOT NULL)     AS studies_cells_measured,
       count(*) FILTER (WHERE 'Homo sapiens' = ANY(organisms)) AS studies_human,
       count(*) FILTER (WHERE n_human_primary > 0)   AS studies_human_primary,
       count(*) FILTER (WHERE n_model > 0)           AS studies_model,
       count(*) FILTER (WHERE n_cell_line > 0)       AS studies_cell_line,
       -- reported ethnicity, not inferred ancestry; see the matview comment
       count(*) FILTER (WHERE ancestries IS NOT NULL) AS studies_with_ancestry,
       sum(stated_male)                AS stated_male,
       sum(stated_female)              AS stated_female,
       sum(stated_missing)             AS stated_missing,
       sum(reads_male)                 AS reads_male,
       sum(reads_female)               AS reads_female
FROM {prefix}_study;
"""  # noqa: S608


def build_disease_facets_sql(collection: str) -> str:
    """Facet counts. A value no study carries is absent, not zero."""
    prefix = DISEASE_COLLECTIONS[collection]["prefix"]
    return (  # noqa: S608
        f"SELECT facet, value, studies FROM {prefix}_facet "
        "ORDER BY facet, studies DESC;"
    )


def _disease_where(collection: str) -> str:
    """Filter clauses shared by the page query and the count that reports it."""
    coll = DISEASE_COLLECTIONS[collection]
    return (
        "".join(
            f"\n      AND (%({param})s::text IS NULL"
            f"\n           OR r.{col} @> ARRAY[%({param})s::text])"
            for param, col in {**coll["tags"], **_ARRAY_FILTERS}.items()
        )
        + "".join(
            f"\n      AND (%({c})s::text IS NULL OR r.{c} = %({c})s::text)"
            for c in _SCALAR_FILTERS
        )
        + "".join(
            f"\n      AND (%({c})s::boolean IS NOT TRUE OR r.{c})"
            for c in _BOOL_FILTERS
        )
        + "\n      AND (%(disease)s::text IS NULL"
        f"\n           OR r.resolved_mondo_ids && {DISEASE_MONDO_SET})"
    )


def build_disease_projects_sql(
    collection: str,
    sort: str = "cells",
    descending: bool = True,
    scope: str = DEFAULT_SCOPE,
) -> str:
    """
    One page of studies, filtered and sorted.

    Both the column list and the ORDER BY are assembled from module constants;
    `sort` is validated against SORTABLE and never reaches the SQL as text.
    """
    coll = DISEASE_COLLECTIONS[collection]
    prefix, label, label_col = coll["prefix"], coll["label"], coll["label_col"]
    catalogue, name_col = coll["catalogue"]

    tag_cols = "".join(f", r.{c}" for c in coll["tags"].values())
    scope_col, scope_where = SCOPES[scope]
    direction = "DESC NULLS LAST" if descending else "ASC NULLS LAST"

    # qualified: the outer FROM joins three relations that all carry study_accession
    def order_by(alias: str) -> str:
        return f"{alias}.{sort} {direction}, {alias}.study_accession"

    return f"""
WITH page AS (
    SELECT r.study_accession, r.title, r.organism, r.organisms,
           r.n_samples, r.n_diseases,
           r.cells, r.assay_category, r.stated_male, r.stated_female,
           r.stated_missing, r.reads_male, r.reads_female,
           r.mondo_ids,
           r.disease_names AS diseases,
           r.ancestries, r.inheritance,
           r.has_fastq, r.has_sra, r.n_fastq_runs, r.n_sra_runs,
           r.n_human_primary, r.n_model, r.n_cell_line, r.n_nonhuman,
           r.n_unknown_organism, r.cells_human_primary,
           r.{scope_col} AS n_samples_in_scope,
           r.{label_col} AS {label}_direct{tag_cols}
    FROM {prefix}_study r
    WHERE {scope_where}{_disease_where(collection)}
    ORDER BY {order_by("r")}
    LIMIT %(limit)s OFFSET %(offset)s
),
page_mondo AS (
    SELECT p.study_accession, m FROM page p, unnest(p.mondo_ids) m
),
page_mondo_closed AS (
    SELECT study_accession, m FROM page_mondo
    UNION
    SELECT pm.study_accession, c.ancestor_id
      FROM page_mondo pm
      JOIN ontology_closure c
        ON c.ontology = 'mondo' AND c.descendant_id = pm.m
),
cat_names AS (
    SELECT pmc.study_accession, array_agg(DISTINCT cat.{name_col}) AS names
      FROM page_mondo_closed pmc
      JOIN {catalogue} cat ON cat.mondo_id = pmc.m
     GROUP BY 1
),
anc AS (
    SELECT os.study_accession, array_agg(DISTINCT e->>'name') AS ancestors
    FROM ontology_samples_v5 os,
         LATERAL jsonb_array_elements(os.disease_ancestors::jsonb) e
    WHERE os.study_accession IN (SELECT study_accession FROM page)
      AND os.disease_ancestors LIKE '[%%'
      AND e->>'name' <> ALL (%(upper_ontology)s::text[])
    GROUP BY 1
)
SELECT p.*, cat_names.names AS {label}, anc.ancestors
FROM page p
LEFT JOIN cat_names ON cat_names.study_accession = p.study_accession
LEFT JOIN anc ON anc.study_accession = p.study_accession
ORDER BY {order_by("p")};
"""  # noqa: S608


def build_disease_count_sql(collection: str, scope: str = DEFAULT_SCOPE) -> str:
    """
    How many studies match the filters, not how many are on this page.
    """
    return f"""
SELECT count(*) AS total FROM {DISEASE_COLLECTIONS[collection]["prefix"]}_study r
WHERE {SCOPES[scope][1]}{_disease_where(collection)};
"""  # noqa: S608


def disease_projects_params(collection: str) -> tuple[str, ...]:
    """Filter parameter names this collection's projects query binds."""
    return (
        *DISEASE_COLLECTIONS[collection]["tags"],
        *_ARRAY_FILTERS,
        *_SCALAR_FILTERS,
        *_BOOL_FILTERS,
        "disease",
    )


ENRICHED_SUMMARY_SQL = "SELECT * FROM enriched_summary_v5;"
ENRICHED_COLUMN_VALUES_SQL = (
    "SELECT value, n FROM enriched_column_top_values_v5 "
    "WHERE column_name = %s ORDER BY n DESC LIMIT %s;"
)

ENRICHED_CROSSTAB_SQL = (
    "SELECT group_val, breakdown_val, n, group_total, group_distinct "
    "FROM enriched_crosstab_v5 WHERE group_col = %s AND breakdown_col = %s "
    "ORDER BY group_total DESC, group_val, n DESC;"
)


def _country_scope_conditions(
    country: str,
    organism: str | None = None,
    assay_l2: str | None = None,
    alias: str = "",
) -> tuple[str, list]:
    """WHERE clause scoping unified_metadata to one country on the map."""
    q = f"{alias}." if alias else ""
    conditions = [
        f"{q}latitude IS NOT NULL",
        f"{q}longitude IS NOT NULL",
        f"{q}country = %s",
    ]
    params: list = [country]
    if organism:
        conditions.append(_organism_membership_clause(alias))
        params.extend([organism, organism])
    if assay_l2:
        conditions.append(f"{q}dominant_assay_l2 = %s")
        params.append(assay_l2)
    return " AND ".join(conditions), params


def build_country_accessions_sql(
    country: str,
    organism: str | None = None,
    assay_l2: str | None = None,
    limit: int | None = None,
) -> tuple[str, list]:
    """Return accessions for a country with optional organism/assay filters."""
    where, params = _country_scope_conditions(country, organism, assay_l2)
    sql = (
        f"SELECT DISTINCT canonical_accession"
        f" FROM unified_metadata"
        f" WHERE {where}"
        f" ORDER BY canonical_accession"
    )
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    return sql + ";", params


# Senior-author attribution: use the last publication author by convention.
# GEO contributor order is submitter order, not author order, so PI ranking uses
# publications only and reports reduced coverage.
_PI_SENIOR_AUTHORS_CTE = """
authors AS (
    SELECT p.canonical_accession, p.pmid, p.center_name,
           btrim(
               (string_to_array(m.authors, ','))[
                   array_length(string_to_array(m.authors, ','), 1)
               ]
           ) AS pi
    FROM proj p
    JOIN pubmed_metadata m ON m.pmid = p.pmid
    WHERE m.authors IS NOT NULL AND btrim(m.authors) <> ''
)"""

# Investigator attribution, shared by the country ranking and the search ranking
# so the two can't drift. Expects a preceding proj CTE exposing
# (canonical_accession, pmid, center_name). An investigator is any author of the
# linked publication; projects with no pmid fall back to their contributor list.
_PI_AUTHORS_CTE = """
authors AS (
    -- investigators from the linked publication
    SELECT p.canonical_accession, p.pmid, p.center_name, btrim(a.author) AS pi
    FROM proj p
    JOIN pubmed_metadata m ON m.pmid = p.pmid
    CROSS JOIN LATERAL unnest(string_to_array(m.authors, ',')) AS a(author)
    UNION ALL
    -- fallback to GEO contributors when the project has no linked pmid
    SELECT p.canonical_accession, NULL::text AS pmid, p.center_name, btrim(c.name) AS pi
    FROM proj p
    JOIN geo_series g ON g.accession = p.canonical_accession
    CROSS JOIN LATERAL unnest(g.contributors) AS c(name)
    WHERE p.pmid IS NULL
    UNION ALL
    -- fallback to ArrayExpress authors when the project has no linked pmid
    SELECT p.canonical_accession, NULL::text AS pmid, p.center_name, btrim(c.name) AS pi
    FROM proj p
    JOIN arrayexpress_experiments x ON x.accession = p.canonical_accession
    CROSS JOIN LATERAL unnest(x.authors) AS c(name)
    WHERE p.pmid IS NULL
)"""

# Ranking tail for _PI_AUTHORS_CTE. Authors are unnested, so project/paper counts
# use DISTINCT. covered_projects is the distinct projects that yielded at least
# one investigator.
_PI_RANK_TAIL = """
SELECT
    -- group case-insensitively; one person deposited under several spellings
    -- otherwise ranks as several. the commonest mixed-case variant keeps
    -- McDonald and O'Brien, with initcap only when every variant is uniform.
    COALESCE(
        mode() WITHIN GROUP (ORDER BY a.pi)
            FILTER (WHERE a.pi <> lower(a.pi) AND a.pi <> upper(a.pi)),
        initcap(min(a.pi))
    ) AS pi,
    count(DISTINCT a.canonical_accession) AS n_projects,
    count(DISTINCT a.pmid) AS n_papers,
    mode() WITHIN GROUP (ORDER BY a.center_name) AS center_name,
    (SELECT count(DISTINCT canonical_accession) FROM authors) AS covered_projects,
    -- projects left after country/filters, i.e. the honest denominator for
    -- covered_projects. Without it a country query reports the *global* match
    -- count and the coverage ratio looks far worse than it is.
    (SELECT count(*) FROM proj) AS scoped_projects
FROM authors a
WHERE a.pi LIKE '%% %%'
GROUP BY lower(a.pi)
ORDER BY n_projects DESC, 1
LIMIT {limit};
"""


def build_country_pis_sql(
    country: str,
    organism: str | None = None,
    assay_l2: str | None = None,
    limit: int = 200,
) -> tuple[str, list]:
    """Rank investigators for a country, counting every author of a linked
    publication. Projects with no linked pmid fall back to their contributor
    list: GEO series contributors or ArrayExpress authors.
    """
    where, params = _country_scope_conditions(country, organism, assay_l2, alias="u")
    params.append(limit)
    return (
        f"""
WITH proj AS (
    SELECT u.canonical_accession, u.pmid, u.center_name
    FROM unified_metadata u
    WHERE {where}
),
{_PI_AUTHORS_CTE}
{_PI_RANK_TAIL.format(limit="%s")}""",
        params,
    )


def build_search_investigators_sql(
    limit: int = 50,
    country: str | None = None,
    senior_only: bool = True,
    **filters,
) -> tuple[str, dict]:
    """Rank investigators across the materialized `matched` temp table."""
    params: dict = {}
    dim_clauses, always_clauses = _search_filter_clauses(params, **filters)
    conds = list(dim_clauses.values()) + always_clauses
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    country_where = ""
    if country:
        country_where = " WHERE u.country = %(pi_country)s"
        params["pi_country"] = country
    params["pi_limit"] = limit
    return (
        f"""
WITH scoped AS (
    SELECT accession, dedup_key FROM matched{where}
),
proj AS (
    SELECT DISTINCT u.canonical_accession, u.pmid, u.center_name
    FROM scoped s
    JOIN unified_metadata u
      ON u.canonical_accession IN (s.dedup_key, s.accession)
    {country_where}
),
{_PI_SENIOR_AUTHORS_CTE if senior_only else _PI_AUTHORS_CTE}
{_PI_RANK_TAIL.format(limit="%(pi_limit)s")}""",
        params,
    )


_ENRICHED_SAMPLES_BASE_COLS = [
    "sample",
    "age",
    "sex",
    "ethnicity",
    "phenotype",
    "cell_type",
    "tissue",
    "strain",
    "disease",
    "assay",
    "cell_line",
    "treatment",
]
# cell_count/gene_count are deliberately absent: they are NULL on every row of
# enriched_samples too, and come from pentimento_sample via the lateral below.
_ENRICHED_SAMPLES_NEW_COLS = [
    "development_stage",
    "sample_type",
    "genetic_modification",
]


def build_enriched_samples_sql(pg_conn) -> str:
    """Build ENRICHED_SAMPLES_SQL, including new columns only if they exist in PG."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'enriched_samples' AND column_name = ANY(%s)",
            (_ENRICHED_SAMPLES_NEW_COLS,),
        )
        existing_new = {r[0] for r in cur.fetchall()}

    cols = list(_ENRICHED_SAMPLES_BASE_COLS)
    for c in _ENRICHED_SAMPLES_NEW_COLS:
        if c in existing_new:
            cols.append(c)
        else:
            cols.append(f"NULL AS {c}")

    col_str = ", ".join(f"es.{c}" if " " not in c else c for c in cols)
    return f"""
SELECT {col_str},
       pc.cells AS cell_count, pc.genes AS gene_count,
       (pc.sample_accession IS NOT NULL) AS pentimento_screened,
       pc.unfiltered
FROM enriched_samples es{_pentimento_counts_lateral("es.sample")}
WHERE es.study_accession = %s
ORDER BY es.sample;
"""


ENRICHED_SAMPLES_SQL = """
SELECT es.sample, es.age, es.sex, es.ethnicity, es.phenotype,
       es.cell_type, es.tissue, es.strain, es.disease, es.assay,
       es.cell_line, es.treatment,
       NULL AS development_stage, NULL AS sample_type, NULL AS genetic_modification
FROM enriched_samples es
WHERE es.study_accession = %s
ORDER BY es.sample;
"""

# st.n_samples counts enrichment input rows, which are per GEO channel: a
# two-colour array study reports double its sample count. Count the rows we
# actually serve instead; study_accession is indexed, so it is free.
ENRICHED_STUDY_SQL = """
SELECT st.accession, st.title,
       (SELECT count(*) FROM enriched_samples es
        WHERE es.study_accession = st.accession) AS n_samples,
       NULL AS single_cell_modality
FROM enriched_studies st
WHERE st.accession = %s;
"""


# n_samples counted from the served rows; see ENRICHED_STUDY_SQL above.
ENRICHED_STUDY_V4_SQL = """
SELECT st.accession, st.title, st.single_cell_modality,
       (SELECT count(*) FROM ontology_samples_v5 os
        WHERE os.study_accession = st.accession) AS n_samples
FROM enriched_studies_v5 st
WHERE st.accession = %s;
"""


# One row per matrix; per-matrix quantiles differ.
# _f_ columns are filtered at min_counts. The unfiltered pair counts ambient
# barcodes in raw droplet space, which is_raw_barcode_space flags.
SC_STATS_SAMPLES_SQL = """
SELECT sample_accession AS sample,
       file,
       n_cells,
       n_barcodes,
       is_raw_barcode_space,
       species        AS matrix_species,
       n_mito_genes,
       min_counts,
       n_cells_filtered,
       ncount_median,
       nfeature_median,
       ncount_f_median,
       nfeature_f_median,
       ncount_f_mean,
       nfeature_f_mean,
       pct_mito_f_median
FROM sc_stats_matrix
WHERE sample_accession = ANY(%(samples)s)
ORDER BY sample_accession, file
"""

# medians of per-matrix medians, not per-cell figures
SC_STATS_STUDY_SQL = """
SELECT s.accession,
       s.state,
       s.n_matrices,
       s.n_failures,
       s.species                AS matrix_species,
       s.any_raw_barcode_space,
       m.n_matrices_measured,
       m.median_ncount,
       m.median_nfeature,
       m.median_pct_mito
FROM sc_stats_study s
LEFT JOIN LATERAL (
    SELECT count(*) FILTER (WHERE ncount_f_median IS NOT NULL) AS n_matrices_measured,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ncount_f_median)   AS median_ncount,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY nfeature_f_median) AS median_nfeature,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY pct_mito_f_median) AS median_pct_mito
    FROM sc_stats_matrix x WHERE x.accession = s.accession
) m ON TRUE
WHERE s.accession = %(acc)s
"""


# Backs /stats/tissue-microbes. The matview carries its own studies >= 2 and
# screened >= 200 gates; see sql/tissue_microbe_rates.sql.
TISSUE_MICROBE_RATES_SQL = """
SELECT tissue, uberon, organism, class, is_background, is_endogenous,
       screened, positives, positives_confirmed, studies, rate_pct
FROM tissue_microbe_rates
ORDER BY tissue, organism
"""


SC_QUALITY_BY_YEAR_SQL = """
SELECT year, technology, evidence, n_matrices, n_studies,
       p25_ncount, median_ncount, p75_ncount,
       p25_nfeature, median_nfeature, p75_nfeature
FROM sc_quality_by_year
ORDER BY technology, year
"""


SC_QUALITY_SAMPLES_SQL = """
SELECT year, technology, ncount_f_median AS ncount, nfeature_f_median AS nfeature
FROM (
    SELECT year, technology, ncount_f_median, nfeature_f_median,
           row_number() OVER (
               PARTITION BY year, technology
               ORDER BY md5(accession || '/' || file)
           ) AS rn
    FROM sc_quality_matrix
) t
WHERE rn <= %(per_cell)s
ORDER BY year, technology
"""


def _pentimento_counts_lateral(sample_expr: str) -> str:
    """Per-sample matrix counts for a study page."""
    return f"""
LEFT JOIN LATERAL (
    SELECT p.sample_accession, p.cells, p.genes, p.unfiltered
    FROM pentimento_sample p
    WHERE p.sample_accession = {sample_expr}
    LIMIT 1
) pc ON TRUE"""


ONTOLOGY_SAMPLES_V4_SQL = f"""
SELECT
    os.sample, os.age, os.sex, os.ethnicity, os.phenotype,
    os.cell_type, os.tissue, os.strain, os.disease, os.assay,
    os.assay_category, os.cell_line, os.treatment,
    os.development_stage, os.sample_type, os.genetic_modification,
    os.organism, os.taxid, os.tissue_primary_site, os.tissue_site_type,
    pc.cells AS cell_count, pc.genes AS gene_count, pc.unfiltered,
    os.disease_ontology_id, os.disease_ontology_name,
    os.tissue_ontology_id, os.tissue_ontology_name,
    os.cell_type_ontology_id, os.cell_type_ontology_name,
    os.assay_ontology_id, os.assay_ontology_name,
    os.development_stage_ontology_id, os.development_stage_ontology_name
FROM ontology_samples_v5 os{_pentimento_counts_lateral("os.sample")}
WHERE os.study_accession = %s
ORDER BY os.sample;
"""
# Low-confidence tags per sample within a study
LOW_CONFIDENCE_TAGS_SQL = """
SELECT sample_accession, tags
FROM low_confidence
WHERE study_accession = %s;
"""


_SAMPLE_SEARCH_LIKE_COLS = frozenset(
    {
        "tissue",
        "disease",
        "cell_type",
        "assay",
        "assay_category",
        "phenotype",
        "treatment",
        "development_stage",
        "sample_type",
        "genetic_modification",
        "strain",
        "cell_line",
        "ethnicity",
        "tissue_primary_site",
    }
)

# ILIKE '%male%' would also match 'female'.
_SAMPLE_SEARCH_EXACT_COLS = frozenset({"organism", "sex", "taxid", "study_accession"})

_SAMPLE_SEARCH_ONTOLOGY_COLS = {
    f"{field}_ontology_id": (field, ontology)
    for field, ontology in ONTOLOGY_FIELDS.items()
}

_SAMPLE_SEARCH_RANGE_COLS = {
    "age_min_days": ("age_days", ">="),
    "age_max_days": ("age_days", "<="),
}

# Every pentimento_sample filter: key -> (clause over alias `p`, param value).
# The value callable is None for flags that bind nothing. Matrix counts live
# here; ontology_samples_v5 cell_count/gene_count are NULL placeholders.
_SAMPLE_SEARCH_PENTIMENTO = {
    # `has_matrix` is the PROOF tier: a cell-by-gene matrix was parsed for this
    # sample. Prefer it over single_cell_only when you want data you can
    # reanalyse without realignment.
    #
    # single_cell_only is a read-derived call (10x barcode-whitelist hits) and
    # needs FASTQ plus a completed preflightx scan, so it is NULL on most
    # verified single-cell samples. The two filters answer different questions
    # and neither subsumes the other.
    "has_matrix": ("p.cells > 0", None),
    "single_cell_only": ("p.assay_is_single_cell IS TRUE", None),
    "has_viral_reads": ("p.has_viral_reads IS TRUE", None),
    "has_bacterial_reads": ("p.has_bacterial_reads IS TRUE", None),
    "hpv_type": ("p.hpv_top_type ILIKE %(f_hpv_type)s", lambda v: f"%{v}%"),
    "min_cell_count": ("p.cells >= %(f_min_cell_count)s", int),
    "max_cell_count": ("p.cells <= %(f_max_cell_count)s", int),
    "min_gene_count": ("p.genes >= %(f_min_gene_count)s", int),
    "max_gene_count": ("p.genes <= %(f_max_gene_count)s", int),
}

_SAMPLE_SEARCH_MICROBE_KEYS = frozenset(
    {
        "microbe",
        "microbe_class",
        "microbe_min_breadth",
        "microbe_min_kmer_mass",
        "microbe_validated_only",
        "microbe_tier",
        "microbe_exclude_background",
    }
)

# Resolved by semi-join against the small pentimento tables, not by a column compare
# microbe_tier only narrows a microbe match, so it must not by itself pull the
# pentimento join into an otherwise plain sample search
_SAMPLE_SEARCH_MICROBE_SELECTORS = _SAMPLE_SEARCH_MICROBE_KEYS - {
    "microbe_tier",
    "microbe_exclude_background",
}
_SAMPLE_SEARCH_PENTIMENTO_KEYS = _SAMPLE_SEARCH_MICROBE_KEYS | set(
    _SAMPLE_SEARCH_PENTIMENTO
)

SAMPLE_SEARCH_FILTERS = frozenset(
    set(_SAMPLE_SEARCH_LIKE_COLS)
    | set(_SAMPLE_SEARCH_EXACT_COLS)
    | set(_SAMPLE_SEARCH_ONTOLOGY_COLS)
    | set(_SAMPLE_SEARCH_RANGE_COLS)
    | _SAMPLE_SEARCH_PENTIMENTO_KEYS
)

_MICROBE_KINGDOM_CASE = """CASE m.class
             WHEN 'virus'    THEN 'viral'
             WHEN 'pathogen' THEN 'bacterial'
             WHEN 'culture'  THEN 'bacterial'
             WHEN 'kitome'   THEN 'bacterial_background'
             WHEN 'flora'    THEN 'bacterial_background'
             WHEN 'parasite' THEN 'eukaryotic_parasite'
             WHEN 'spike'    THEN 'spike_in_control'
             WHEN 'animalPV' THEN 'negative_control'
             WHEN 'vector'   THEN 'vector'
             ELSE 'other'
           END"""

_MICROBE_VALIDATED_EXPR = (
    "(m.is_validated_viral OR m.is_viral_evidence OR m.is_validated_bacterial)"
)

# is_background is a PREVALENCE flag, not a contamination call: any organism seen
# across more than a threshold fraction of runs trips it. In scRNA-seq no microbe
# is expected at all, so prevalence is a poor proxy for artifact -- it catches
# M. tuberculosis at 98.7% genome breadth alongside reagent kitome. So it is
# surfaced as a label, never used to filter rows out. Kept as an opt-in predicate
# for callers that explicitly ask to drop prevalence-flagged taxa.
_MICROBE_NOT_BACKGROUND = "m.is_background IS NOT TRUE"

# The upstream flags gate on breadth of coverage, and a single fraction is not
# comparable across kingdoms: the median viral genome is 8.8 kb and the median
# bacterial one 2.2 Mb, so is_validated_bacterial's 30% asks for 652 kb where the
# viral tiers ask for 2.6 kb. Bacteria get a relaxed tier of their own at 1%,
# which is the p99 of bacterial breadth and where the noise floor stops falling.
_MICROBE_LOW_BREADTH = "m.breadth_frac >= 0.01"
# The is_validated_viral / is_viral_evidence flags carry a taxon-aware policy:
# endogenous retroviruses (MMTV, MLV, HIV1) are held out of the evidence tier and
# need the 50% gate, and herpesviruses are excluded from both flags entirely --
# 94 rows sit above 50% breadth with neither flag set, EBV at 0.87 among them, and
# pentimento_sample.flags is the only place those calls surfaced. Backstop on
# breadth so a herpesvirus at 87% genome coverage is not reported as nothing.
_MICROBE_VIRAL_HIGH_BREADTH = "(m.class = 'virus' AND m.breadth_frac >= 0.50)"
_MICROBE_BACTERIAL_EVIDENCE = (
    f"(m.class IN ('pathogen', 'culture') AND {_MICROBE_LOW_BREADTH})"
)
_MICROBE_EVIDENCE_EXPR = (
    f"({_MICROBE_VALIDATED_EXPR} OR {_MICROBE_BACTERIAL_EVIDENCE}"
    f" OR {_MICROBE_VIRAL_HIGH_BREADTH})"
)
# confirmed is the strict flag only; is_viral_evidence (20-50% breadth) is evidence
_MICROBE_CONFIRMED_EXPR = (
    f"(m.is_validated_viral OR m.is_validated_bacterial"
    f" OR {_MICROBE_VIRAL_HIGH_BREADTH})"
)

_MICROBE_ROW_CAP = 50

# Sorting on these means joining pentimento_sample into the page CTE, because
# the page is cut before the LATERALs run. Everything else sorts on its own
# ontology_samples_v5 column.
_PENTIMENTO_SORT_COLS = {"cell_count": "cells", "gene_count": "genes"}
SAMPLE_SEARCH_SORTABLE = frozenset(
    {"sample", "study_accession", "age_days"} | set(_PENTIMENTO_SORT_COLS)
)

_SAMPLE_SEARCH_SELECT = """
    s.sample, s.study_accession, s.organism, s.taxid,
    s.age, s.age_days, s.sex, s.ethnicity,
    s.phenotype, s.cell_type, s.tissue, s.strain,
    s.disease, s.assay, s.assay_category, s.cell_line,
    s.treatment, s.development_stage, s.sample_type, s.genetic_modification,
    s.tissue_primary_site, s.tissue_site_type, s.disease_ontology_id, s.disease_ontology_name,
    s.tissue_ontology_id, s.tissue_ontology_name, s.cell_type_ontology_id, s.cell_type_ontology_name,
    s.assay_ontology_id, s.assay_ontology_name, s.development_stage_ontology_id, s.development_stage_ontology_name
""".strip()


def _pentimento_sort_page(
    select: str,
    sort: str,
    col: str,
    where: str,
    direction: str,
    tiebreak: str,
    measured_only: bool = False,
) -> str:
    """Page CTE body for a sort on a pentimento matrix count.

    NULLS LAST puts unmeasured rows last in both directions, so measured rows form
    a prefix. The measured branch joins pentimento_sample directly, and the
    unmeasured tail is left to an anti-join.

    The measured branch joins pentimento_sample directly: a duplicate
    sample_accession would fan the page out past the count query, and
    idx_pentimento_sample_sample_uniq now forbids one. Each branch is capped at
    offset+limit and the outer ORDER BY re-sorts at most 2*(offset+limit) rows,
    so correctness never relies on UNION ALL preserving branch order.

    `measured_only` drops the second branch entirely. A pentimento_sample filter
    and a missing pentimento_sample row are contradictory, so that branch is
    guaranteed empty. Postgres cannot prove it and scans for it anyway."""
    if measured_only:
        return f"""
    SELECT {select},
           pv.{col} AS {sort}
    FROM pentimento_sample pv
    JOIN ontology_samples_v5 s ON s.sample = pv.sample_accession
    WHERE {where}
    ORDER BY pv.{col} {direction} NULLS LAST{tiebreak}
    LIMIT %(limit)s OFFSET %(offset)s"""
    outer_tiebreak = tiebreak.replace("s.", "")
    return f"""
    SELECT * FROM (
        (SELECT {select},
                pv.{col} AS {sort}
         FROM pentimento_sample pv
         JOIN ontology_samples_v5 s ON s.sample = pv.sample_accession
         WHERE {where}
         ORDER BY pv.{col} {direction} NULLS LAST{tiebreak}
         LIMIT %(page_end)s)
        UNION ALL
        (SELECT {select},
                NULL::bigint AS {sort}
         FROM ontology_samples_v5 s
         WHERE {where}
           AND NOT EXISTS (SELECT 1 FROM pentimento_sample pn
                           WHERE pn.sample_accession = s.sample)
         ORDER BY{tiebreak.lstrip(",")}
         LIMIT %(page_end)s)
    ) u
    ORDER BY {sort} {direction} NULLS LAST{outer_tiebreak}
    LIMIT %(limit)s OFFSET %(offset)s"""


def _sample_search_where(filters: dict, include_descendants: bool) -> tuple[str, dict]:
    """WHERE body + params for a cross-project sample search; unknown keys raise."""
    clauses: list[str] = []
    params: dict = {}
    for key, value in filters.items():
        if value is None or value == "":
            continue
        if key not in SAMPLE_SEARCH_FILTERS:
            raise ValueError(f"unknown sample filter: {key}")
        if key in _SAMPLE_SEARCH_PENTIMENTO_KEYS:
            continue
        pkey = f"f_{key}"
        if key in _SAMPLE_SEARCH_LIKE_COLS:
            clauses.append(f"s.{key} ILIKE %({pkey})s")
            params[pkey] = f"%{value}%"
        elif key in _SAMPLE_SEARCH_EXACT_COLS:
            if key == "taxid":
                clauses.append(f"s.{key} = %({pkey})s")
            else:
                clauses.append(f"lower(s.{key}::text) = lower(%({pkey})s::text)")
            params[pkey] = value
        elif key in _SAMPLE_SEARCH_ONTOLOGY_COLS:
            col, ontology = _SAMPLE_SEARCH_ONTOLOGY_COLS[key]
            if include_descendants:
                clauses.append(
                    f"s.{col}_ontology_id = ANY(ARRAY("
                    f"SELECT descendant_id FROM ontology_closure"
                    f" WHERE ontology = %({pkey}_onto)s"
                    f" AND ancestor_id = %({pkey})s"
                    f" UNION SELECT %({pkey})s))"
                )
                params[f"{pkey}_onto"] = ontology
            else:
                clauses.append(f"s.{col}_ontology_id = %({pkey})s")
            params[pkey] = value
        elif key in _SAMPLE_SEARCH_RANGE_COLS:
            col, op = _SAMPLE_SEARCH_RANGE_COLS[key]
            clauses.append(f"s.{col} {op} %({pkey})s")
            params[pkey] = value
        else:
            raise ValueError(f"unhandled sample filter bucket: {key}")
    return (" AND ".join(clauses) if clauses else "TRUE"), params


def _microbe_conds(filters: dict, params: dict) -> str:
    """The detection-matching predicate, shared by the LATERAL and the semi-join."""
    conds: list[str] = []
    if filters.get("microbe_exclude_background"):
        conds.append(_MICROBE_NOT_BACKGROUND)
    if filters.get("microbe"):
        conds.append("m.organism ILIKE %(f_microbe)s")
        params["f_microbe"] = f"%{filters['microbe']}%"
    if filters.get("microbe_class"):
        conds.append("m.class = %(f_microbe_class)s")
        params["f_microbe_class"] = filters["microbe_class"]
    if filters.get("microbe_min_breadth") is not None:
        conds.append("m.breadth_frac >= %(f_microbe_min_breadth)s::real")
        params["f_microbe_min_breadth"] = filters["microbe_min_breadth"]
    if filters.get("microbe_min_kmer_mass") is not None:
        conds.append("m.kmer_mass >= %(f_microbe_min_kmer_mass)s::double precision")
        params["f_microbe_min_kmer_mass"] = filters["microbe_min_kmer_mass"]
    # Without a breadth floor `microbe_class=pathogen` matches 370k samples of
    # trace k-mer noise, so the default tier is low_breadth, not `any`.
    tier = filters.get("microbe_tier") or (
        "high_breadth" if filters.get("microbe_validated_only") else "low_breadth"
    )
    if tier == "high_breadth":
        conds.append(_MICROBE_CONFIRMED_EXPR)
    elif tier == "low_breadth":
        conds.append(_MICROBE_EVIDENCE_EXPR)
    # background is no longer an unconditional cond, so this list can be empty
    return " AND ".join(conds) if conds else "TRUE"


def _is_set(value) -> bool:
    """A filter counts as set unless it is absent. `is`-comparisons, not
    truthiness: min_cell_count=0 is a real bound, and `0 in (None, "", False)`
    is True in Python."""
    return value is not None and value is not False and value != ""


def _sample_semi(inner: str) -> str:
    """Wrap a sub-select as a cohort-narrowing semi-join over `s.sample`.

    `= ANY(ARRAY(...))` builds the small side once. `IN (...)` can let the planner
    drive the semi-join from ontology_samples_v5 and probe pentimento once per
    ontology row."""
    return f"\n  AND s.sample = ANY(ARRAY(\n    {inner}\n  ))"


def _microbe_semi(filters: dict, params: dict) -> str:
    """Semi-join over microbe detections."""
    if not _wants_microbes(filters):
        return ""
    # DISTINCT + NOT NULL: unnest emits both accessions per run, with repeats and
    # NULLs, so the raw array is larger than the represented sample set and would
    # be materialised twice per request.
    return _sample_semi(f"""SELECT DISTINCT x FROM (
        SELECT unnest(ARRAY[r.gsm_accession, r.sample_accession]) AS x
        FROM pentimento_run r
        JOIN pentimento_microbe m ON m.run_accession = r.run_accession
        WHERE {_microbe_conds(filters, params)}
    ) t WHERE x IS NOT NULL""")


def _pentimento_semi(filters: dict, params: dict) -> str:
    """Semi-join over the 85k-row pentimento_sample flags and matrix counts."""
    conds = []
    for key, (clause, to_param) in _SAMPLE_SEARCH_PENTIMENTO.items():
        value = filters.get(key)
        if not _is_set(value):
            continue
        conds.append(clause)
        if to_param is not None:
            params[f"f_{key}"] = to_param(value)
    if not conds:
        return ""
    return _sample_semi(
        f"""SELECT p.sample_accession FROM pentimento_sample p
    WHERE {" AND ".join(conds)}"""
    )


def _microbe_lateral(filters: dict, params: dict) -> str:
    """LATERAL attaching microbial detections to one sample. Aggregates over every
    matching detection; only `microbes` is capped, per `microbes_truncated`."""
    conds = _microbe_conds(filters, params)
    return f"""
LEFT JOIN LATERAL (
    SELECT
        COUNT(*)                                    AS n_detections,
        MAX(d.breadth_frac)                         AS max_breadth_frac,
        SUM(d.reads)                                AS reads,
        SUM(d.kmer_mass)                            AS kmer_mass,
        COUNT(*) > {_MICROBE_ROW_CAP}               AS microbes_truncated,
        jsonb_agg(
            jsonb_build_object(
                'organism', d.organism, 'class', d.class, 'kingdom', d.kingdom,
                'run_accession', d.run_accession,
                'reads', d.reads, 'kmer_mass', d.kmer_mass,
                'n_unitigs', d.n_unitigs, 'breadth_frac', d.breadth_frac,
                'covered_bp', d.covered_bp, 'ref_bp', d.ref_bp,
                'is_validated_viral', d.is_validated_viral,
                'is_viral_evidence', d.is_viral_evidence,
                'is_validated_bacterial', d.is_validated_bacterial,
                -- prevalence label; nothing filters on it, so the client must see it
                'is_background', d.is_background
            ) ORDER BY d.kmer_mass DESC NULLS LAST
        ) FILTER (WHERE d.rn <= {_MICROBE_ROW_CAP}) AS microbes
    FROM (
        SELECT m.organism, m.class, m.reads, m.kmer_mass, m.n_unitigs,
               m.covered_bp, m.ref_bp, m.breadth_frac, r.run_accession,
               m.is_validated_viral, m.is_viral_evidence,
               m.is_validated_bacterial, m.is_background,
               {_MICROBE_KINGDOM_CASE} AS kingdom,
               ROW_NUMBER() OVER (
                   ORDER BY m.kmer_mass DESC NULLS LAST
               ) AS rn
        FROM pentimento_run r
        JOIN pentimento_microbe m ON m.run_accession = r.run_accession
        WHERE (r.gsm_accession = s.sample OR r.sample_accession = s.sample)
          AND {conds}
    ) d
) mq ON TRUE"""


def _wants_pentimento_sample(filters: dict) -> bool:
    """True if any filter is resolved against pentimento_sample itself."""
    return any(
        _is_set(filters.get(k))
        for k in _SAMPLE_SEARCH_PENTIMENTO
        if k not in _SAMPLE_SEARCH_MICROBE_KEYS
    )


def _wants_microbes(filters: dict) -> bool:
    return any(
        filters.get(k) not in (None, "", False)
        for k in _SAMPLE_SEARCH_MICROBE_SELECTORS
    )


# LIMIT 1: redundant under idx_pentimento_sample_sample_uniq, but it keeps the
# planner on a correlated index probe.
_PENTIMENTO_SAMPLE_LATERAL = """
LEFT JOIN LATERAL (
    SELECT p.sample_accession, p.cells, p.genes, p.unfiltered, p.assay_display,
           p.assay_is_single_cell,
           p.hpv_top_type, p.hpv_ambiguous, p.has_viral_reads,
           p.has_viral_evidence,
           p.has_bacterial_reads, p.viral_kmer_mass, p.bacterial_kmer_mass
    FROM pentimento_sample p
    WHERE p.sample_accession = s.sample
    LIMIT 1
) ps ON TRUE"""


def _sample_search_filter(filters: dict, include_descendants: bool) -> tuple[str, dict]:
    """The whole WHERE for the cohort, shared by the page query and its count.

    FROM is ontology_samples_v5 alone; the pentimento filters appear here as
    semi-joins so they narrow that scan before output decoration."""
    where, params = _sample_search_where(filters, include_descendants)
    where += _pentimento_semi(filters, params) + _microbe_semi(filters, params)
    return where, params


def build_enriched_sample_search_sql(
    filters: dict,
    sort: str = "sample",
    order: str = "asc",
    limit: int = 100,
    offset: int = 0,
    include_descendants: bool = True,
) -> tuple[str, dict]:
    """Cross-project sample cohort search over ontology_samples_v5.

    Every pentimento filter, including `microbe*`, flags, and matrix-count bounds,
    also restricts the cohort, so `total` shrinks with them. Pair with the count
    builder, which applies the identical WHERE."""
    if sort not in SAMPLE_SEARCH_SORTABLE:
        raise ValueError(f"unsortable column: {sort}")
    direction = "DESC" if str(order).lower() == "desc" else "ASC"

    where, params = _sample_search_filter(filters, include_descendants)
    joins = _PENTIMENTO_SAMPLE_LATERAL
    microbe_cols = ""
    if _wants_microbes(filters):
        joins += _microbe_lateral(filters, params)
        microbe_cols = """,
       mq.n_detections      AS microbe_n_detections,
       mq.max_breadth_frac  AS microbe_max_breadth_frac,
       mq.reads             AS microbe_reads,
       mq.kmer_mass         AS microbe_kmer_mass,
       mq.microbes_truncated,
       mq.microbes"""
    params["limit"] = limit
    params["offset"] = offset
    # (sample, study_accession) is unique; sample alone is not, so without the
    # full tiebreak two pages can repeat or skip a row.
    tiebreak = "".join(
        f", s.{c} ASC" for c in ("sample", "study_accession") if c != sort
    )

    def order_by(expr: str) -> str:
        return f"ORDER BY {expr} {direction} NULLS LAST{tiebreak}"

    # The page is cut before the LATERALs run, so they decorate at most `limit`
    # rows matched by filters.
    if sort in _PENTIMENTO_SORT_COLS:
        measured_only = _wants_pentimento_sample(filters)
        if not measured_only:
            params["page_end"] = offset + limit
        page_body = _pentimento_sort_page(
            _SAMPLE_SEARCH_SELECT,
            sort,
            _PENTIMENTO_SORT_COLS[sort],
            where,
            direction,
            tiebreak,
            measured_only=measured_only,
        )
    else:
        page_body = f"""
    SELECT {_SAMPLE_SEARCH_SELECT}
    FROM ontology_samples_v5 s
    WHERE {where}
    {order_by(f"s.{sort}")}
    LIMIT %(limit)s OFFSET %(offset)s"""
    sql = f"""
WITH page AS ({page_body}
)
SELECT {_SAMPLE_SEARCH_SELECT},
       ps.cells, ps.genes,
       (ps.sample_accession IS NOT NULL) AS pentimento_screened,
       ps.unfiltered, ps.assay_display AS pentimento_assay,
       ps.assay_is_single_cell, ps.hpv_top_type, ps.hpv_ambiguous,
       ps.has_viral_reads, ps.has_viral_evidence, ps.has_bacterial_reads,
       ps.viral_kmer_mass, ps.bacterial_kmer_mass{microbe_cols}
FROM page s{joins}
{order_by(f"s.{sort}")};
"""
    return sql, params


def build_enriched_sample_count_sql(
    filters: dict, include_descendants: bool = True
) -> tuple[str, dict]:
    """Cohort total. Same WHERE as the page, none of its per-row decoration."""
    where, params = _sample_search_filter(filters, include_descendants)
    return (
        f"SELECT COUNT(*)::bigint AS total\nFROM ontology_samples_v5 s\nWHERE {where};",
        params,
    )


#### Pentimento: single-cell counts, read-derived calls, microbial flags #####
PENTIMENTO_RESOLVE_SQL = """
WITH candidates AS (
    SELECT geo_accession AS gse, 1 AS pri
    FROM cross_ref_geo
    WHERE target_accession = %(acc)s
    UNION ALL
    SELECT alias AS gse, 1 AS pri
    FROM sra_studies
    WHERE accession = %(acc)s AND alias LIKE 'GSE%%'
    UNION ALL
    SELECT study_accession AS gse, 2 AS pri
    FROM pentimento_study
    WHERE aliases @> to_jsonb(%(acc)s::text)
)
SELECT c.gse
FROM candidates c
JOIN pentimento_study p ON p.study_accession = c.gse
GROUP BY c.gse
ORDER BY MIN(c.pri), c.gse
LIMIT 1;
"""

# Last resort when the archives link the accession to no GSE at all
PENTIMENTO_RESOLVE_GSM_SQL = """
SELECT s.study_accession, COUNT(*) AS n
FROM pentimento_run r
JOIN pentimento_sample s ON s.sample_accession = r.gsm_accession
WHERE r.study_accession = %s
GROUP BY s.study_accession
ORDER BY n DESC
LIMIT 1;
"""

PENTIMENTO_SAMPLE_CELLS_SQL = """
WITH s AS (
    SELECT sample_accession, cells, genes, nnz, unfiltered
    FROM pentimento_sample
    WHERE study_accession = %(acc)s
    ORDER BY sample_accession
    LIMIT %(limit)s
), x AS (
    SELECT sample_accession,
           array_agg(DISTINCT experiment_accession
                     ORDER BY experiment_accession) AS experiment_accessions
    FROM (
        SELECT s.sample_accession, r.experiment_accession
        FROM s JOIN pentimento_run r ON r.gsm_accession = s.sample_accession
        WHERE r.experiment_accession IS NOT NULL
        UNION ALL
        SELECT s.sample_accession, r.experiment_accession
        FROM s JOIN pentimento_run r ON r.sample_accession = s.sample_accession
        WHERE r.experiment_accession IS NOT NULL
    ) u
    GROUP BY sample_accession
)
SELECT s.sample_accession, s.cells, s.genes, s.nnz, s.unfiltered,
       COALESCE(x.experiment_accessions, '{}'::text[]) AS experiment_accessions,
       (SELECT p.n_samples_detailed FROM pentimento_study p
        WHERE p.study_accession = %(acc)s) AS n_samples_total
FROM s
LEFT JOIN x ON x.sample_accession = s.sample_accession
ORDER BY s.sample_accession;
"""

# The pentimento_study projection, shared by the study endpoint and the
# single-cell summary. pentimento_study is rebuilt whole by the ingestion side
# and gains columns; two hand-maintained copies of this list would drift
# silently, so it lives in one place.
_PENTIMENTO_STUDY_COLS = """p.study_accession, p.source, p.title, p.study_cells,
       p.cells_unfiltered, p.single_cell_file_format, p.n_samples_reported,
       p.n_samples_detailed, p.sample_cells_total, p.unassigned_cells,
       p.sample_breakdown_complete, p.any_unfiltered, p.n_runs_linked,
       p.n_runs_preflightx, p.n_runs_measurable, p.has_metadata, p.has_celltype,
       p.has_donor, p.has_demographics"""

PENTIMENTO_STUDY_SQL = f"""
SELECT {_PENTIMENTO_STUDY_COLS}, p.flags
FROM pentimento_study p
WHERE p.study_accession = %s;
"""

# ORDER BY keeps LIMIT/OFFSET paging stable
PENTIMENTO_SAMPLES_SQL = """
SELECT sample_accession, cells, genes, nnz, file_format, unfiltered,
       n_runs, n_runs_measurable,
       species_called, species_confidence, species_hit_reads,
       species_hit_fraction, species_runner_up, species_margin,
       species_ambiguous, species_mislabel,
       sex_verdict, sex_confidence, sex_reads_scanned, y_hits, xist_hits,
       y_xist_ratio, sex_panel_status, sex_mixed_suspected, deep_n_reads,
       assay, assay_display, assay_is_single_cell,
       calls_ambiguous, flags, n_flags, hpv_top_type, hpv_ambiguous,
       has_viral_reads, has_viral_evidence, has_bacterial_reads,
       viral_kmer_mass, bacterial_kmer_mass
FROM pentimento_sample
WHERE study_accession = %s
ORDER BY sample_accession
LIMIT %s OFFSET %s;
"""

# Single-cell status and summary
#
# These queries keep raw columns from inviting the opposite reading:
#
# 1. `unified_metadata.single_cell_status` is a cached label recomputed by the
#    recall_fix recipe in seqout-ingestion. The endpoint returns status alongside
#    the evidence.
#
# 2. The evidence sources are independent and attempted only when the required
#    inputs exist: the matrix route needs supplementary files, and the read routes
#    need FASTQ plus a preflightx scan. A study with a single evidence source is
#    usually showing the only measurement that could be made. A counted matrix is
#    proof on its own.
#
# 3. `n_runs_measurable = 0` means the quantifier NEVER RAN. It does not mean
#    "clean". An empty `flags` list with n_runs_measurable > 0 is the "measured,
#    found nothing" case. The handler emits an explicit microbe_measured bool so
#    a caller cannot collapse the two.
# SELECT-list fragment interpolated into both single-study queries. It owns its
# trailing comma so call sites cannot disagree about it.
SINGLE_CELL_EVIDENCE_COLS = """
  (SELECT count(*) FROM pentimento_sample s
    WHERE s.study_accession = %(acc)s AND s.cells > 0)          AS samples_with_matrix,
  -- Runs reach a study by EITHER its own accession or its GSM link. The two
  -- evidence tables do not share an accession space: pentimento_sample is 100%
  -- GSE, pentimento_run is mostly SRP (9,144 SRP vs 908 GSE on the barcode
  -- call). Matching on study_accession alone misses every GEO study whose reads
  -- are filed under an SRP, which is most of them -- it undercounted studies
  -- carrying BOTH kinds of evidence 7-fold, 783 against a true 5,721.
  EXISTS (SELECT 1 FROM pentimento_run r
           LEFT JOIN pentimento_sample s2 ON s2.sample_accession = r.gsm_accession
           WHERE (r.study_accession = %(acc)s OR s2.study_accession = %(acc)s)
             AND r.assay_is_single_cell)                        AS barcode_whitelist_hit,
  EXISTS (SELECT 1 FROM pentimento_run r
           LEFT JOIN pentimento_sample s2 ON s2.sample_accession = r.gsm_accession
           WHERE (r.study_accession = %(acc)s OR s2.study_accession = %(acc)s)
             AND r.bulk_vs_sc = 'single_cell')                  AS r1_length_single_cell,
  -- pentimento's own count of runs it could resolve for the study. Do NOT
  -- substitute a join to sra_run_download_links: that table is keyed by SRP and
  -- most matrix studies are GSE, so the join silently matches nothing and every
  -- study looks read-less.
  COALESCE((SELECT p2.n_runs_linked FROM pentimento_study p2
             WHERE p2.study_accession = %(acc)s), 0)            AS n_runs_linked_ev,
  -- WHERE each kind of evidence actually lives. A GEO study's matrix sits under
  -- its GSE while its reads sit under an SRP, so a caller that wants to fetch
  -- the reads needs the read-side accession, not the one it asked about.
  (SELECT array_agg(DISTINCT r.study_accession)
     FROM pentimento_run r
     LEFT JOIN pentimento_sample s3 ON s3.sample_accession = r.gsm_accession
    WHERE (r.study_accession = %(acc)s OR s3.study_accession = %(acc)s)
      AND (r.assay_is_single_cell OR r.bulk_vs_sc = 'single_cell'))
                                                                AS reads_accessions,
  (SELECT array_agg(DISTINCT s4.study_accession)
     FROM pentimento_sample s4
    WHERE s4.study_accession = %(acc)s AND s4.cells > 0)        AS matrix_accessions,
"""

# Anchor on ACCESSION so exactly one row always comes back. pentimento_study has
# studies missing from unified_metadata; anchoring on the unified table returns
# 404 for studies this endpoint exists to answer for. `in_unified` / `in_pentimento` tell
# the caller which side knows about it. "nothing has measured this" is an answer.
SINGLE_CELL_STATUS_SQL = f"""
SELECT u.canonical_accession, u.source, u.single_cell_status, u.is_single_cell,
       (u.canonical_accession IS NOT NULL)                      AS in_unified,
       EXISTS (SELECT 1 FROM pentimento_study p
                WHERE p.study_accession = %(acc)s)              AS in_pentimento,
       {SINGLE_CELL_EVIDENCE_COLS.rstrip().rstrip(",")}
FROM (SELECT 1) AS _one
LEFT JOIN unified_metadata u ON u.canonical_accession = %(acc)s;
"""

SINGLE_CELL_SUMMARY_SQL = f"""
SELECT {_PENTIMENTO_STUDY_COLS},
       p.flags,
       u.single_cell_status,
       {SINGLE_CELL_EVIDENCE_COLS}
       (SELECT count(DISTINCT m.organism) FROM pentimento_microbe m
          JOIN pentimento_run r ON r.run_accession = m.run_accession
         WHERE r.study_accession = %(acc)s
           AND m.is_viral_evidence)                             AS n_viral_organisms,
       (SELECT count(DISTINCT m.organism) FROM pentimento_microbe m
          JOIN pentimento_run r ON r.run_accession = m.run_accession
         WHERE r.study_accession = %(acc)s
           AND m.is_validated_bacterial)                        AS n_bacterial_organisms
FROM pentimento_study p
LEFT JOIN unified_metadata u ON u.canonical_accession = p.study_accession
WHERE p.study_accession = %(acc)s;
"""

_SINGLE_CELL_EV_CTE = """
ev AS (
  SELECT study_accession,
         max(CASE WHEN src = 'matrix'  THEN 1 ELSE 0 END) AS matrix,
         max(CASE WHEN src = 'barcode' THEN 1 ELSE 0 END) AS barcode,
         max(CASE WHEN src = 'r1len'   THEN 1 ELSE 0 END) AS r1len
  FROM (
    SELECT study_accession, 'matrix'  AS src FROM pentimento_sample WHERE cells > 0
    UNION ALL
    -- COALESCE folds a run filed under an SRP onto the GSE its GSM belongs to,
    -- so one study yields ONE row instead of a GSE row holding the matrix and a
    -- separate SRP row holding the read call.
    SELECT COALESCE(s2.study_accession, r.study_accession), 'barcode'
      FROM pentimento_run r
      LEFT JOIN pentimento_sample s2 ON s2.sample_accession = r.gsm_accession
     WHERE r.assay_is_single_cell
    UNION ALL
    SELECT COALESCE(s2.study_accession, r.study_accession), 'r1len'
      FROM pentimento_run r
      LEFT JOIN pentimento_sample s2 ON s2.sample_accession = r.gsm_accession
     WHERE r.bulk_vs_sc = 'single_cell'
  ) t
  GROUP BY study_accession
)"""

SINGLE_CELL_STUDIES_SQL = f"""
WITH {_SINGLE_CELL_EV_CTE}
SELECT ev.study_accession, ev.matrix::bool AS counted_matrix,
       ev.barcode::bool AS barcode_whitelist_hit,
       ev.r1len::bool AS r1_length_single_cell,
       ev.matrix + ev.barcode + ev.r1len AS n_independent_evidence,
       -- same four-way classification _evidence_block applies per study, so a
       -- listed row and a fetched one agree
       CASE
         WHEN ev.matrix = 1 AND (ev.barcode = 1 OR ev.r1len = 1)
           THEN 'matrix_and_reads'
         WHEN ev.matrix = 1 AND COALESCE(p.n_runs_linked, 0) > 0
           THEN 'matrix_reads_unscanned'
         WHEN ev.matrix = 1 THEN 'matrix_only'
         ELSE 'reads_only'
       END AS kind,
       p.study_cells, p.any_unfiltered, u.single_cell_status
FROM ev
LEFT JOIN pentimento_study p ON p.study_accession = ev.study_accession
LEFT JOIN unified_metadata u ON u.canonical_accession = ev.study_accession
WHERE ev.matrix + ev.barcode + ev.r1len >= %(min_evidence)s
  AND (NOT %(require_matrix)s OR ev.matrix = 1)
ORDER BY ev.study_accession
LIMIT %(limit)s OFFSET %(offset)s;
"""

PENTIMENTO_SAMPLE_TISSUE_SQL = """
SELECT sample, tissue
FROM ontology_samples_v5
WHERE study_accession = %(acc)s AND sample = ANY(%(samples)s);
"""

SINGLE_CELL_OVERVIEW_SQL = f"""
WITH {_SINGLE_CELL_EV_CTE},
classified AS (
  SELECT ev.study_accession,
         CASE
           WHEN ev.matrix = 1
                AND (ev.barcode = 1 OR ev.r1len = 1
                     OR COALESCE(p.n_runs_linked, 0) > 0)  THEN 'fastq_and_matrix'
           WHEN ev.matrix = 1                              THEN 'matrix_only'
           ELSE 'fastq_only'
         END AS kind,
         COALESCE(p.n_samples_detailed, p.n_samples_reported) AS n_samples,
         COALESCE(p.study_cells, 0) AS n_cells
  FROM ev
  LEFT JOIN pentimento_study p ON p.study_accession = ev.study_accession
),
tissue_organism AS (
  SELECT os.study_accession,
         COALESCE(os.tissue_ontology_name, os.tissue) AS tissue,
         os.organism
  FROM ontology_samples_v5 os
  JOIN classified c ON c.study_accession = os.study_accession
)
SELECT
  agg.n_studies, agg.n_samples, agg.n_cells,
  (SELECT json_agg(x) FROM (
     SELECT kind, count(*) AS n_studies,
            -- a zero total counts as uncounted, same as a missing row
            count(*) FILTER (WHERE n_samples > 0) AS n_studies_counted,
            COALESCE(sum(n_samples), 0) AS n_samples
     FROM classified GROUP BY kind
   ) x)                                            AS by_kind,
  (SELECT json_agg(x) FROM (
     SELECT tissue, count(DISTINCT study_accession) AS n_studies
     FROM tissue_organism WHERE tissue IS NOT NULL
     GROUP BY 1 ORDER BY n_studies DESC LIMIT 15
   ) x)                                            AS top_tissues,
  (SELECT json_agg(x) FROM (
     SELECT organism, count(DISTINCT study_accession) AS n_studies
     FROM tissue_organism WHERE organism IS NOT NULL
     GROUP BY 1 ORDER BY n_studies DESC LIMIT 15
   ) x)                                            AS top_organisms
FROM (
  SELECT count(*) AS n_studies, sum(n_samples) AS n_samples, sum(n_cells) AS n_cells
  FROM classified
) agg;
"""

# Per (sample, organism) detections for one study. The project grid badged from a
# hardcoded 24-taxon list, so organisms outside it -- M. tuberculosis at 98% genome
# breadth among them -- rendered as "none detected". This drives the badges and the
# CSV from the real panel instead. LEFT JOIN so a screened sample with nothing found
# still emits a row: "screened, nothing found" and "never screened" are different
# answers and the grid has to tell them apart.
PENTIMENTO_STUDY_DETECTIONS_SQL = f"""
WITH runs AS (
  -- pentimento_run keys on the SRP, pentimento_sample on the GSE, so accept
  -- either namespace rather than making the caller resolve first
  SELECT COALESCE(gsm_accession, sample_accession) AS sample, run_accession
  FROM pentimento_run
  WHERE study_accession = %(study)s
    AND (gsm_accession IS NOT NULL OR sample_accession IS NOT NULL)
  UNION
  SELECT ps.sample_accession, pr.run_accession
  FROM pentimento_sample ps
  JOIN pentimento_run pr ON pr.gsm_accession = ps.sample_accession
  WHERE ps.study_accession = %(study)s
  UNION
  SELECT ps.sample_accession, pr.run_accession
  FROM pentimento_sample ps
  JOIN pentimento_run pr ON pr.sample_accession = ps.sample_accession
  WHERE ps.study_accession = %(study)s
)
SELECT r.sample,
       m.organism,
       m.class,
       CASE WHEN m.organism IS NULL THEN NULL
            ELSE {_MICROBE_KINGDOM_CASE} END                    AS kingdom,
       CASE WHEN m.organism IS NULL THEN NULL
            WHEN bool_or({_MICROBE_CONFIRMED_EXPR}) THEN 'high_breadth'
            ELSE 'low_breadth' END                              AS tier,
       count(DISTINCT m.run_accession) FILTER (
         WHERE m.organism IS NOT NULL)                          AS n_runs,
       max(m.breadth_frac)                                      AS breadth_frac,
       max(m.ref_bp)                                            AS ref_bp,
       max(m.covered_bp)                                        AS covered_bp,
       sum(m.reads)                                             AS reads,
       sum(m.kmer_mass)                                         AS kmer_mass,
       sum(m.n_unitigs)                                         AS n_unitigs,
       -- prevalence label, carried so the UI can mark rather than hide the row
       bool_or(m.is_background)                                 AS is_background
FROM runs r
LEFT JOIN pentimento_microbe m ON m.run_accession = r.run_accession
  AND (%(all_rows)s OR {_MICROBE_EVIDENCE_EXPR})
GROUP BY 1, 2, 3, 4
ORDER BY r.sample, max(m.breadth_frac) DESC NULLS LAST;
"""

PENTIMENTO_MICROBES_SQL = f"""
SELECT r.run_accession, r.gsm_accession, r.sample_accession, r.study_accession,
       r.measurable, r.total_reads,
       m.organism, m.class, m.reads, m.kmer_mass, m.n_unitigs,
       m.ref_bp, m.covered_bp, m.breadth_frac, m.is_background,
       m.is_validated_viral, m.is_viral_evidence, m.is_validated_bacterial,
       {_MICROBE_KINGDOM_CASE} AS kingdom
FROM pentimento_run r
LEFT JOIN pentimento_microbe m ON m.run_accession = r.run_accession
  AND (NOT %(exclude_background)s OR {_MICROBE_NOT_BACKGROUND})
  AND (%(kind)s = 'all'
       OR (%(kind)s = 'viral'     AND m.class = 'virus')
       OR (%(kind)s = 'bacterial' AND m.class IN ('pathogen', 'culture'))
       OR (%(kind)s = 'both'      AND m.class IN ('virus','pathogen','culture')))
  AND (%(min_breadth)s::real IS NULL OR m.breadth_frac >= %(min_breadth)s::real)
  AND (%(min_kmer_mass)s::double precision IS NULL
       OR m.kmer_mass >= %(min_kmer_mass)s::double precision)
  AND (NOT %(validated_only)s OR {_MICROBE_VALIDATED_EXPR})
WHERE (r.gsm_accession = %(acc)s OR r.sample_accession = %(acc)s)
ORDER BY m.kmer_mass DESC NULLS LAST, m.breadth_frac DESC NULLS LAST
LIMIT %(limit)s;
"""


PENTIMENTO_OVERVIEW_SQL = f"""
WITH rollup AS (
  -- one pass over pentimento_run. min/max answer "none, or more than one"
  -- without the sort count(DISTINCT) forces, and GROUPING SETS gets the study
  -- count in the same scan; a sample can span studies, so min(study_accession)
  -- would undercount.
  SELECT COALESCE(gsm_accession, sample_accession) AS sample,
         study_accession,
         grouping(study_accession) AS is_sample_row,
         bool_or(measurable) AS measurable,
         min(sex_verdict) FILTER (WHERE sex_verdict IN ('male','female')) AS sex_lo,
         max(sex_verdict) FILTER (WHERE sex_verdict IN ('male','female')) AS sex_hi,
         min(assay) AS assay_lo,
         max(assay) AS assay_hi,
         min(read_end) AS end_lo,
         max(read_end) AS end_hi
  FROM pentimento_run
  WHERE gsm_accession IS NOT NULL OR sample_accession IS NOT NULL
  GROUP BY GROUPING SETS ((1), (2))
),
per_sample AS (SELECT * FROM rollup WHERE is_sample_row = 1),
scanned AS (
  SELECT count(*) FILTER (WHERE is_sample_row = 1) AS n_samples,
         count(*) FILTER (WHERE is_sample_row = 1 AND measurable) AS n_measurable,
         count(*) FILTER (WHERE is_sample_row = 0
                          AND study_accession IS NOT NULL) AS n_studies
  FROM rollup
),
sex AS (
  -- runs that disagree are their own bucket, never folded into one sex
  SELECT CASE WHEN sex_lo IS NULL     THEN 'no_verdict'
              WHEN sex_lo <> sex_hi   THEN 'conflicting'
              ELSE sex_lo END AS verdict,
         count(*) AS n_samples
  FROM per_sample GROUP BY 1
),
assay AS (
  SELECT CASE WHEN assay_lo IS NULL     THEN 'No call'
              WHEN assay_lo <> assay_hi THEN 'Mixed'
              ELSE assay_lo END AS assay,
         count(*) AS n_samples
  FROM per_sample GROUP BY 1
),
hits AS (
  -- only virus/pathogen/culture clear the evidence bar, so the shared kingdom
  -- map collapses to viral/bacterial here; reusing it keeps that a consequence
  -- of the WHERE rather than a second class->kingdom truth
  SELECT m.class, m.organism, r.read_end,
         {_MICROBE_KINGDOM_CASE} AS kingdom,
         {_MICROBE_CONFIRMED_EXPR} AS confirmed,
         COALESCE(r.gsm_accession, r.sample_accession) AS sample
  FROM pentimento_microbe m
  JOIN pentimento_run r ON r.run_accession = m.run_accession
  WHERE {_MICROBE_EVIDENCE_EXPR}
    AND (r.gsm_accession IS NOT NULL OR r.sample_accession IS NOT NULL)
),
-- bucketed here rather than in the client the way assay_groups is: read_end is
-- an open column, so a closed CASE is what stops a new value vanishing silently
sample_read_end AS (
  SELECT sample,
         CASE WHEN end_lo = end_hi AND end_lo IN ('3''', '5''', 'full-length')
              THEN end_lo ELSE 'no_call' END AS read_end
  FROM per_sample
),
-- What the low-breadth cut is worth, measured rather than asserted: kitome and
-- flora are reagent/skin background at bacterial genome scale, so the rate at
-- which they clear the same bar is this tier's noise floor. Scanned here so the
-- number moves with the data instead of living in a caption.
calibration AS (
  SELECT count(*) FILTER (WHERE class IN ('kitome', 'flora'))  AS background_rows,
         count(*) FILTER (WHERE class IN ('kitome', 'flora')
                          AND {_MICROBE_LOW_BREADTH})           AS background_pass,
         count(*) FILTER (WHERE class IN ('pathogen', 'culture')) AS target_rows,
         count(*) FILTER (WHERE class IN ('pathogen', 'culture')
                          AND {_MICROBE_LOW_BREADTH})           AS target_pass,
         -- E. coli (vector/plasmid) and M. hyorhinis (culture contaminant) are
         -- the two most artifact-prone organisms on the panel; if they carry the
         -- tier, the headline is an artifact count wearing a biology label
         count(*) FILTER (WHERE class IN ('pathogen', 'culture')
                          AND {_MICROBE_LOW_BREADTH}
                          AND organism IN ('Escherichia_coli',
                                           'Mycoplasma_hyorhinis'))
                                                                AS target_pass_artifact_prone
  FROM pentimento_microbe m
  WHERE class IN ('kitome', 'flora', 'pathogen', 'culture')
),
-- one row per (sample, kingdom) so a sample with two hits is not counted twice
hit_sample AS (
  SELECT sample, kingdom, bool_or(confirmed) AS confirmed
  FROM hits GROUP BY 1, 2
),
-- a hit COUNT per read end is not a rate: 3' and 5' have very different numbers
-- of screened samples, so the denominator has to travel with the numerator
read_end_rate AS (
  SELECT k.kingdom, sre.read_end,
         count(*) AS n_screened,
         count(hs.sample) AS n_samples,
         count(*) FILTER (WHERE hs.confirmed) AS n_samples_confirmed
  FROM sample_read_end sre
  CROSS JOIN (SELECT unnest(ARRAY['viral', 'bacterial']) AS kingdom) k
  LEFT JOIN hit_sample hs ON hs.sample = sre.sample AND hs.kingdom = k.kingdom
  GROUP BY 1, 2
),
-- assay_lo = assay_hi is the agreement test; split samples drop out
sc_sample AS (
  SELECT sample, assay_lo AS assay
  FROM per_sample
  WHERE assay_lo IS NOT NULL AND assay_lo = assay_hi
    AND assay_lo <> 'Not single-cell'
),
-- one tissue per sample; one filed under two studies stacks twice
sample_tissue AS (
  SELECT sc.sample, sc.assay,
         min(COALESCE(os.tissue_ontology_name, os.tissue)) AS tissue,
         min(os.study_accession) AS study_accession
  FROM sc_sample sc
  JOIN ontology_samples_v5 os ON os.sample = sc.sample
  WHERE COALESCE(os.tissue_ontology_name, os.tissue) IS NOT NULL
  GROUP BY 1, 2
),
-- count studies; plate-based studies file one sample per well and dominate
-- a sample count
tissue_cell AS (
  SELECT assay, tissue, count(DISTINCT study_accession) AS n_studies
  FROM sample_tissue GROUP BY 1, 2
),
top_assay AS (
  SELECT assay, sum(n_studies) AS n_studies
  FROM tissue_cell GROUP BY 1 ORDER BY 2 DESC LIMIT 8
),
-- eight matches the palette width
top_tissue AS (
  SELECT tissue FROM tissue_cell
  GROUP BY 1 ORDER BY sum(n_studies) DESC LIMIT 8
),
-- top per kingdom, not overall: viruses outnumber bacteria and a flat limit
-- leaves one bacterial row on the chart. No QUALIFY in Postgres, so rn needs
-- its own level; a CTE keeps the final SELECT flat like the two above.
top_microbe AS (
  SELECT organism, class, kingdom, count(DISTINCT sample) AS n_samples,
         count(DISTINCT sample) FILTER (WHERE confirmed) AS n_samples_confirmed,
         row_number() OVER (PARTITION BY kingdom
                            ORDER BY count(DISTINCT sample) DESC) AS rn
  FROM hits GROUP BY organism, class, kingdom
)
SELECT
  scanned.n_samples, scanned.n_measurable, scanned.n_studies,
  (SELECT count(DISTINCT sample) FROM hits WHERE confirmed)
                                                        AS n_samples_with_hit,
  (SELECT count(DISTINCT sample) FROM hits)             AS n_samples_with_evidence,
  (SELECT json_agg(x) FROM (
     SELECT verdict, n_samples FROM sex ORDER BY n_samples DESC
   ) x)                                                 AS sex,
  -- every count below is the relaxed total with its confirmed subset alongside,
  -- so a chart can show both without a second round trip
  (SELECT json_agg(x) FROM (
     SELECT class, count(DISTINCT sample) AS n_samples,
            count(DISTINCT sample) FILTER (WHERE confirmed) AS n_samples_confirmed,
            count(DISTINCT organism) AS n_organisms
     FROM hits GROUP BY class ORDER BY n_samples DESC
   ) x)                                                 AS microbe_by_class,
  (SELECT json_agg(x) FROM (
     SELECT organism, class, kingdom, n_samples, n_samples_confirmed
     FROM top_microbe WHERE rn <= 12 ORDER BY n_samples DESC
   ) x)                                                 AS top_microbes,
  (SELECT json_agg(x) FROM (
     SELECT kingdom, read_end, n_screened, n_samples, n_samples_confirmed
     FROM read_end_rate
   ) x)                                                 AS microbe_by_read_end,
  (SELECT to_json(c) FROM calibration c)                AS low_breadth_calibration,
  (SELECT json_agg(x) FROM (
     SELECT assay, n_samples FROM assay
     WHERE assay NOT IN ('No call', 'Not single-cell', 'Mixed')
     ORDER BY n_samples DESC LIMIT 12
   ) x)                                                 AS by_assay,
  -- the shares that partition every scanned sample. Runs that disagree about
  -- being single-cell answer "unknown", so Mixed sits with No call.
  (SELECT json_agg(x) FROM (
     SELECT CASE WHEN assay IN ('No call', 'Mixed') THEN 'no_call'
                 WHEN assay = 'Not single-cell' THEN 'not_single_cell'
                 ELSE 'single_cell' END AS group_name,
            sum(n_samples) AS n_samples
     FROM assay GROUP BY 1
   ) x)                                                 AS assay_groups,
  (SELECT json_agg(x) FROM (
     SELECT tc.assay, tc.tissue, tc.n_studies
     FROM tissue_cell tc
     WHERE tc.assay IN (SELECT assay FROM top_assay)
       AND tc.tissue IN (SELECT tissue FROM top_tissue)
     ORDER BY tc.n_studies DESC
   ) x)                                                 AS assay_by_tissue
FROM scanned;
"""


if __name__ == "__main__":
    sql, p = build_enriched_sample_search_sql({"sex": "female"})
    assert "lower(s.sex::text) = lower(%(f_sex)s::text)" in sql, sql
    assert p["f_sex"] == "female", p
    assert "ILIKE" not in sql.split("WHERE")[1].split("ORDER BY")[0], sql

    sql, p = build_enriched_sample_search_sql({"tissue": "liver"})
    assert "s.tissue ILIKE %(f_tissue)s" in sql and p["f_tissue"] == "%liver%", p

    sql, p = build_enriched_sample_search_sql({"disease_ontology_id": "MONDO:0005061"})
    assert "ontology_closure" in sql and p["f_disease_ontology_id_onto"] == "mondo", p
    sql, p = build_enriched_sample_search_sql(
        {"disease_ontology_id": "MONDO:0005061"}, include_descendants=False
    )
    assert "ontology_closure" not in sql, sql

    try:
        build_enriched_sample_search_sql({"nonsense": "x"})
        raise AssertionError("unknown filter should raise")
    except ValueError as e:
        assert "nonsense" in str(e), e

    try:
        build_enriched_sample_search_sql({"tissue": "liver"}, sort="sample; DROP TABLE")
        raise AssertionError("bad sort should raise")
    except ValueError:
        pass

    sql, p = build_enriched_sample_search_sql(
        {
            "tissue": "cervi",
            "assay": "single cell rna",
            "microbe": "papillomavirus",
            "single_cell_only": True,
        },
        sort="cell_count",
        order="desc",
    )
    assert "LEFT JOIN LATERAL" in sql, sql
    assert p["f_microbe"] == "%papillomavirus%", p
    assert "p.assay_is_single_cell IS TRUE" in sql, sql
    assert "AS pentimento_screened" in sql, sql
    assert "mq.microbes_truncated" in sql, sql
    tail = " DESC NULLS LAST, s.sample ASC, s.study_accession ASC"
    # single_cell_only is a pentimento_sample filter, so "has no pentimento_sample
    # row" is contradictory and the unmeasured branch is dropped entirely.
    assert f"ORDER BY pv.cells{tail}" in sql, sql
    assert "UNION ALL" not in sql, sql
    assert "NOT EXISTS (SELECT 1 FROM pentimento_sample pn" not in sql, sql
    assert "page_end" not in sql, sql

    # Without such a filter the unmeasured tail is reachable, so both branches
    # are emitted, each capped, and re-sorted over the small union.
    both, bp = build_enriched_sample_search_sql(
        {"tissue": "cervi"}, sort="cell_count", order="desc"
    )
    assert f"ORDER BY pv.cells{tail}" in both, both  # measured branch
    assert "NULL::bigint AS cell_count" in both, both  # unmeasured branch
    assert "NOT EXISTS (SELECT 1 FROM pentimento_sample pn" in both, both
    assert both.count("LIMIT %(page_end)s") == 2, both  # both capped
    assert bp["page_end"] == bp["offset"] + bp["limit"], bp
    assert (  # outer re-sort
        "ORDER BY cell_count DESC NULLS LAST, sample ASC, study_accession ASC" in both
    ), both
    assert f"ORDER BY s.cell_count{tail}" in both, both  # final page order
    # measured branch joins pentimento directly (uniqueness is enforced by index)
    assert "FROM pentimento_sample pv" in sql, sql
    # the sort column is not repeated as its own tiebreak
    by_study, _ = build_enriched_sample_search_sql(
        {"tissue": "liver"}, sort="study_accession"
    )
    assert "ORDER BY s.study_accession ASC NULLS LAST, s.sample ASC\n" in by_study, (
        by_study
    )
    assert "FROM pentimento_sample pv" not in by_study, by_study

    # matrix counts come from pentimento, never from the all-NULL ontology columns
    for key, (clause, _to_param) in _SAMPLE_SEARCH_PENTIMENTO.items():
        csql, cparams = build_enriched_sample_count_sql({key: 100})
        assert clause in csql and "pentimento_sample p" in csql, (key, csql)
        assert "s.cell_count" not in csql and "s.gene_count" not in csql, (key, csql)
    # 0 is a real bound; False/None/"" mean the filter is absent
    assert "p.cells >=" in build_enriched_sample_count_sql({"min_cell_count": 0})[0]
    for absent in (None, False, ""):
        assert (
            "pentimento"
            not in build_enriched_sample_count_sql(
                {"min_cell_count": absent, "tissue": "liver"}
            )[0]
        ), absent
    assert "s.cell_count" not in _SAMPLE_SEARCH_SELECT, _SAMPLE_SEARCH_SELECT
    assert "s.gene_count" not in _SAMPLE_SEARCH_SELECT, _SAMPLE_SEARCH_SELECT

    lateral = _microbe_lateral({"microbe": "papillomavirus"}, {})
    assert f"FILTER (WHERE d.rn <= {_MICROBE_ROW_CAP})" in lateral, lateral
    assert "COUNT(*)" in lateral, lateral
    assert "LIMIT" not in lateral, lateral

    sql, _ = build_enriched_sample_search_sql({"tissue": "liver"})
    assert "pentimento_microbe" not in sql, sql
    assert "microbe_n_detections" not in sql, sql
    assert sql.count("LEFT JOIN LATERAL") == 1, sql

    assert _as_list("Homo sapiens") == ["Homo sapiens"]
    assert _as_list(["Homo sapiens", "Mus musculus"]) == [
        "Homo sapiens",
        "Mus musculus",
    ]
    assert _as_list(None) is None and _as_list([]) is None and _as_list("") is None
    s3, p3 = build_structured_search(organism="Homo sapiens")
    assert p3["organism"] == ["Homo sapiens"], p3

    gpsql, gpp = build_global_contributions_sql(organism="Homo sapiens")
    assert "organism_counts ? %s" in gpsql and gpp == [
        "Homo sapiens",
        "Homo sapiens",
    ], (gpsql, gpp)
    cpsql, cpp = build_country_accessions_sql("United States", organism="Homo sapiens")
    assert "organism_counts ? %s" in cpsql and cpp == [
        "United States",
        "Homo sapiens",
        "Homo sapiens",
    ], (cpsql, cpp)
    psql, pp = build_platform_growth_filtered_sql("ILLUMINA", organism="Homo sapiens")
    assert "u.organism_counts ? %s" in psql and pp == [
        "ILLUMINA",
        "Homo sapiens",
        "Homo sapiens",
    ], (psql, pp)

    fp = {}
    dim, _ = _search_filter_clauses(fp, organism=["Homo sapiens", "Mus musculus"])
    assert "unnest(%(organism)s::text[])" in dim["organism"], dim
    assert fp["organism"] == ["Homo sapiens", "Mus musculus"], fp
    fp2 = {}
    _search_filter_clauses(fp2, organism="Homo sapiens")
    assert fp2["organism"] == ["Homo sapiens"], fp2

    for param in ("organism", "library_strategy"):
        s4, p4 = build_structured_search(**{param: ["RNA-Seq", "ATAC-seq"]})
        assert isinstance(p4[param], list), (param, p4)
        scalar_eq = f"= %({param})s"
        assert scalar_eq not in s4, (
            f"{param} binds a list but {scalar_eq!r} is still a scalar compare"
        )
    s5, _ = build_structured_search(library_strategy=["RNA-Seq"])
    assert "= ANY(%(library_strategy)s)" in s5, s5

    assert "COUNT(*) OVER () AS total_count" in DISEASE_ALIAS_LOOKUP_SQL

    assert "LEFT JOIN pentimento_sample" not in sql, sql
    assert "FROM pentimento_sample p" in sql and "LIMIT 1\n) ps ON TRUE" in sql, sql

    page, pp = build_enriched_sample_search_sql(
        {"tissue": "cervi", "microbe": "papillomavirus"}
    )
    csql, cp = build_enriched_sample_count_sql(
        {"tissue": "cervi", "microbe": "papillomavirus"}
    )
    assert csql.startswith("SELECT COUNT(*)::bigint AS total"), csql
    assert "jsonb_agg" not in csql and "LATERAL" not in csql, csql
    assert "LIMIT %(limit)s" not in csql, csql
    assert cp["f_microbe"] == pp["f_microbe"] == "%papillomavirus%", (cp, pp)
    assert cp["f_tissue"] == pp["f_tissue"], (cp, pp)
    csql2, _ = build_enriched_sample_count_sql({"tissue": "liver"})
    assert "pentimento" not in csql2, csql2

    assert "COUNT(*) OVER ()" not in page, page
    assert page.index("LIMIT %(limit)s") < page.index("LEFT JOIN LATERAL"), page
    for f, table in (
        ({"microbe": "HPV"}, "pentimento_microbe"),
        ({"single_cell_only": True}, "pentimento_sample"),
        ({"hpv_type": "16"}, "pentimento_sample"),
    ):
        c, _ = build_enriched_sample_count_sql(f)
        assert "AND s.sample = ANY(ARRAY(" in c and table in c, (f, c)

    assert set(_SAMPLE_SEARCH_ONTOLOGY_COLS) == {
        f"{f}_ontology_id" for f in ONTOLOGY_FIELDS
    }
    assert _MICROBE_KINGDOM_CASE in PENTIMENTO_MICROBES_SQL
    assert _MICROBE_KINGDOM_CASE in PENTIMENTO_OVERVIEW_SQL
    assert _MICROBE_VALIDATED_EXPR in PENTIMENTO_MICROBES_SQL
    assert _MICROBE_VALIDATED_EXPR in PENTIMENTO_OVERVIEW_SQL
    # the one not-background predicate, or search and sample detail disagree
    assert _MICROBE_NOT_BACKGROUND in PENTIMENTO_MICROBES_SQL
    # prevalence must never silently drop rows; only an explicit opt-in filters
    assert _MICROBE_NOT_BACKGROUND not in _microbe_conds({"microbe": "HPV"}, {})
    assert _MICROBE_NOT_BACKGROUND in _microbe_conds(
        {"microbe": "HPV", "microbe_exclude_background": True}, {}
    )
    assert not _wants_microbes({"microbe_exclude_background": True})
    assert _microbe_conds({"microbe_tier": "any"}, {}) == "TRUE"
    # every microbe payload carries the prevalence label, sample search included
    _p, _ = build_enriched_sample_search_sql({"microbe": "HPV"})
    assert "'is_background', d.is_background" in _p, _p

    # read_end is bucketed in SQL, so the client never sees a raw pipeline value
    assert "'mixed'" not in PENTIMENTO_OVERVIEW_SQL

    for _sql in (_PENTIMENTO_SAMPLE_LATERAL, PENTIMENTO_SAMPLES_SQL):
        assert "has_viral_evidence" in _sql, _sql

    # a herpesvirus above the strict breadth gate carries neither viral flag
    assert _MICROBE_VIRAL_HIGH_BREADTH in _MICROBE_EVIDENCE_EXPR
    assert _MICROBE_VIRAL_HIGH_BREADTH in _MICROBE_CONFIRMED_EXPR

    # a bare microbe filter must carry a breadth floor, or microbe_class=pathogen
    # matches every trace k-mer hit in the table
    assert _MICROBE_EVIDENCE_EXPR in _microbe_conds({"microbe": "HPV"}, {})
    assert _MICROBE_CONFIRMED_EXPR in _microbe_conds(
        {"microbe": "HPV", "microbe_tier": "high_breadth"}, {}
    )
    assert _MICROBE_CONFIRMED_EXPR in _microbe_conds(
        {"microbe": "HPV", "microbe_validated_only": True}, {}
    )
    _any = _microbe_conds({"microbe": "HPV", "microbe_tier": "any"}, {})
    assert "breadth_frac" not in _any and "is_validated" not in _any, _any
    # the tier alone must not drag the microbe join into a plain sample search
    assert not _wants_microbes({"microbe_tier": "low_breadth"})
    assert _wants_microbes({"microbe": "HPV"})
    # sex_verdict is NULL when a run was never assessed; the overview keeps
    # that bucket so the denominator stays visible
    assert "'no_verdict'" in PENTIMENTO_OVERVIEW_SQL
    assert "'conflicting'" in PENTIMENTO_OVERVIEW_SQL

    print("OK: cohort search + structured multi-value self-checks pass")
