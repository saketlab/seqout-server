"""SQL queries for pysradb-server"""

import math

_EARTH_RADIUS_KM = 6371.0088

STUDY_PUBLICATIONS_BATCH_SQL = """
SELECT accession, publications
FROM study_publications
WHERE accession = ANY(%s);
"""

PMID_VARIANT_BATCH_SQL = """
SELECT accession, pmid FROM (
    SELECT accession, unnest(pubmed_id) AS pmid
        FROM geo_series WHERE accession = ANY(%(acc)s)
    UNION
    SELECT accession, pubmed_id_pysradb
        FROM geo_series WHERE accession = ANY(%(acc)s)
    UNION
    SELECT accession, pubmed_id_pysradb
        FROM sra_studies WHERE accession = ANY(%(acc)s)
        -- pysradb stamps a bogus pmid on private-placeholder mirrors
        -- (title "GEO accession GSExxx is currently private ..."); ~4k of them.
        -- Their real study has no publication yet, so drop the spurious pmid.
        AND title NOT ILIKE 'GEO accession %% is currently private%%'
    UNION
    SELECT study_accession, pubmed_id_pysradb
        FROM ena_studies WHERE study_accession = ANY(%(acc)s)
    UNION
    SELECT accession, pubmed_id
        FROM arrayexpress_experiments WHERE accession = ANY(%(acc)s)
    UNION
    SELECT accession, pubmed_id_pysradb
        FROM arrayexpress_experiments WHERE accession = ANY(%(acc)s)
    UNION
    SELECT accession, pubmed_id_pysradb
        FROM dra_studies WHERE accession = ANY(%(acc)s)
    UNION
    SELECT accession, pubmed_id
        FROM gea_experiments WHERE accession = ANY(%(acc)s)
    UNION
    SELECT accession, pubmed_id_pysradb
        FROM gea_experiments WHERE accession = ANY(%(acc)s)
) t
WHERE pmid IS NOT NULL AND pmid <> ''
ORDER BY accession, pmid;
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

SUBMITTER_BATCH_SQL = """
SELECT canonical_accession AS accession, center_name AS submitter, country_code,
       is_single_cell, single_cell_modality
FROM unified_metadata
WHERE canonical_accession = ANY(%(acc)s);
"""

# GSA's submitter/org live on the linked BioProject, not unified_metadata (which
# has no GSA rows) — same join GSA_PROJECT_SQL uses, batched for result rows.
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
    -- total deferred to /search/facets (filtered by db) — see SEARCH_SQL note.
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
    -- total deferred to /search/facets (filtered by db) — see SEARCH_SQL note.
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
    -- total deferred to /search/facets (filtered by db) — see SEARCH_SQL note.
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
    -- total deferred to /search/facets (filtered by db) — see SEARCH_SQL note.
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
    -- total deferred to /search/facets (filtered by db) — see SEARCH_SQL note.
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
           platforms, 'geo'::text AS source, accession AS dedup_key,
           ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2 AS rank
    FROM geo_series, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
    UNION ALL
    SELECT s.accession, s.title, s.abstract AS summary, s.updated_at,
           s.organisms, s.countries, s.instrument_models, s.library_strategies,
           s.platforms, 'sra'::text, COALESCE(s.alias, s.accession),
           (ts_rank(s.tsv, q.core_q) + ts_rank(s.tsv, q.match_q) * 0.2) * 0.7
    FROM sra_studies s, q
    WHERE (s.tsv @@ q.match_q OR s.tsv @@ q.core_q)
    UNION ALL
    SELECT accession, title, description AS summary, release_date AS updated_at,
           organisms, countries, NULL::text[], NULL::text[], NULL::text[],
           'arrayexpress'::text, COALESCE(alias, accession),
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7
    FROM arrayexpress_experiments, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
    UNION ALL
    -- organisms materialized onto ena_studies_search by transfer.ena_organisms
    -- (DISTINCT ena_samples.scientific_name), so the organism facet includes ENA.
    SELECT accession,
           COALESCE(study_title, study_name, project_name, accession) AS title,
           study_description AS summary,
           COALESCE(last_updated, first_public) AS updated_at,
           organisms, NULL::text[], NULL::text[], NULL::text[], NULL::text[],
           'ena'::text, accession,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7
    FROM ena_studies_search, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
    UNION ALL
    -- title/abstract/organisms enriched from the BioProject and materialized
    -- onto gsa_studies_search by transfer.gsa_search, so the organism facet
    -- includes GSA; library_strategies/platforms stay NULL (per-run aggregation
    -- is too costly for the hot path — as with ENA).
    SELECT gss.accession,
           COALESCE(gss.study_title, gss.project_title, gss.study_name, gss.project_name, gss.accession) AS title,
           COALESCE(gss.study_description, gss.project_summary) AS summary,
           COALESCE(gss.last_updated, gss.first_public) AS updated_at,
           gss.organisms,
           ARRAY['China']::text[], NULL::text[], NULL::text[], NULL::text[],
           'gsa'::text, gss.accession,
           (ts_rank(gss.tsv, q.core_q) + ts_rank(gss.tsv, q.match_q) * 0.2) * 0.7
    FROM gsa_studies_search gss, q
    WHERE (gss.tsv @@ q.match_q OR gss.tsv @@ q.core_q)
    UNION ALL
    -- DRA: the twin holds only DDBJ-exclusive studies, so a mirrored DRP cannot
    -- double-count against its SRA/ENA row. organisms materialized onto
    -- dra_studies_search by transfer.dra_organisms (experiment->sample hop).
    SELECT accession,
           COALESCE(study_title, study_name, project_name, accession) AS title,
           study_description AS summary,
           COALESCE(last_updated, first_public) AS updated_at,
           organisms, ARRAY['Japan']::text[], NULL::text[], NULL::text[], NULL::text[],
           'dra'::text, accession,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7
    FROM dra_studies_search, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
    UNION ALL
    -- GEA: per-sample organisms (transfer.gea_organisms) with the single-column
    -- organism as fallback, so multi-organism studies join the facet fully.
    SELECT accession, title, description AS summary, release_date AS updated_at,
           COALESCE(organisms, CASE WHEN NULLIF(btrim(organism), '') IS NOT NULL
                THEN ARRAY[organism] END)::text[],
           ARRAY['Japan']::text[], NULL::text[], NULL::text[], NULL::text[],
           'gea'::text, accession,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7
    FROM gea_experiments, q
    WHERE (tsv @@ q.match_q OR tsv @@ q.core_q)
),
-- Filter to the requested source BEFORE the dedup. GEO↔SRA twins share a
-- dedup_key (the SRA study carries the GSE as its alias); deduping first can
-- keep the higher-ranked SRA twin, and then a `source = 'geo'` filter would
-- delete the GEO row entirely, undercounting the scoped total. Filtering first
-- dedups only within the requested source (accessions are unique there, so no
-- double-count is reintroduced); the unified db IS NULL path is unchanged.
deduped AS (
    SELECT DISTINCT ON (dedup_key)
        accession, title, summary, updated_at, organisms, countries,
        instrument_models, library_strategies, platforms, source, rank
    FROM all_matches
    WHERE (%(db)s::text IS NULL OR source = %(db)s)
    ORDER BY dedup_key, rank DESC
),
matched AS (
    SELECT * FROM deduped
)
"""

# Each sidebar facet, with the column it unnests/derives and the filter-dimension
# key it represents (so it can be excluded from its own narrowing).
_FACET_BRANCHES = (
    ("organism", "value", "matched, unnest(organisms) value"),
    ("country", "value", "matched, unnest(countries) value"),
    ("library_strategy", "value", "matched, unnest(library_strategies) value"),
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
    if organism:
        dim_clauses["organism"] = "matched.organisms @> ARRAY[%(organism)s]"
        params["organism"] = organism
    if country:
        dim_clauses["country"] = "matched.countries && %(country)s::text[]"
        params["country"] = list(country)
    if library_strategy:
        dim_clauses["library_strategy"] = (
            "matched.library_strategies && %(library_strategy)s::text[]"
        )
        params["library_strategy"] = list(library_strategy)
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


def build_search_match_temp(q=None, q_variants=None, db=None):
    """CREATE TEMP TABLE `matched` (ON COMMIT DROP) holding the q+db match set,
    deduped. This is the single expensive FTS pass; the count and facet queries
    then read this materialized table, so a slow facet can't kill the total and
    the FTS is never computed twice. Caller must run inside a transaction.
    """
    # Only the columns the count + facet + filter queries actually read. Notably
    # NOT title/summary (summary holds full abstracts — tens of MB over a broad
    # match) — those exist in the shared CTE for the result-list path, not here.
    sql = (
        "CREATE TEMP TABLE matched ON COMMIT DROP AS\n"
        + _SEARCH_MATCH_CTE
        + "\nSELECT accession, updated_at, organisms, countries,"
        + " instrument_models, library_strategies, platforms\n"
        + "FROM matched;"
    )
    return sql, {"q": q, "q_variants": q_variants, "db": db}


def build_search_total_over_matched(**filters):
    """Exact result total over the materialized `matched` temp table, with every
    active filter applied (no exclude-self). Cheap — a filtered scan/count, no FTS.
    """
    params = {}
    dim_clauses, always_clauses = _search_filter_clauses(params, **filters)
    conds = list(dim_clauses.values()) + always_clauses
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return f"SELECT COUNT(*)::int AS total FROM matched{where};", params


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
            f" COUNT(*)::int AS count"
            f" FROM {from_clause}{where}"
            f" GROUP BY {value_expr}"
        )

    sql = (
        "WITH facets AS (\n"
        + "\n    UNION ALL\n".join(branches)
        + "\n),\n"
        + "ranked AS (\n"
        + "    SELECT facet, value, count,\n"
        + "           ROW_NUMBER() OVER (PARTITION BY facet"
        + " ORDER BY count DESC, value) AS rn\n"
        + "    FROM facets\n"
        + ")\n"
        + "SELECT facet, value, count FROM ranked WHERE rn <= 1000\n"
        + "ORDER BY facet, count DESC, value;"
    )
    return sql, params


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
    queries — so the list and the sidebar counts always agree.

    Default order is relevance (rank). `sortby` (year|citations|journal) overrides
    it with the same sort expression as build_sorted_search_sql, so filtered and
    unfiltered results order identically. All active filters apply (no
    exclude-self). Offset-paginated, 200/page. total_count is NULL — the exact
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
    else:
        join_clause = ""
        order_by = "ORDER BY paged.rank DESC, paged.accession DESC"

    sql = (
        _SEARCH_MATCH_CTE
        + ", filtered AS (\n"
        + "    SELECT * FROM matched"
        + where
        + "\n"
        + "),\n"
        + "counted AS (\n"
        # total deferred to /search/facets — see SEARCH_SQL note.
        + "    SELECT *, NULL::bigint AS total_count FROM filtered\n"
        + "),\n"
        + "paged AS (\n"
        + "    SELECT * FROM counted\n"
        + "    WHERE (%(cursor_rank)s::float8 IS NULL\n"
        + "           OR (rank, accession) < (%(cursor_rank)s::float8, %(cursor_acc)s::text))\n"
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
    return sql, params


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


GEO_QUERY_ACCESSIONS_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
all_matches AS (
    -- Exact accession match (priority 1)
    SELECT accession,
           100.0::float8 AS rank, 'geo'::text AS source, 1 AS priority
    FROM geo_series
    WHERE UPPER(accession) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) <= %s::int)

    UNION ALL

    -- GSM sample match (priority 2) - uses GIN index
    SELECT g.accession,
           100.0::float8 AS rank, 'geo'::text AS source, 2 AS priority
    FROM geo_series g
    WHERE g.samples_ref @> ARRAY[UPPER(%s)]
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM g.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM g.updated_at) <= %s::int)

    UNION ALL

    -- PRJNA bioproject match (priority 3)
    SELECT g.accession,
           100.0::float8 AS rank, 'geo'::text AS source, 3 AS priority
    FROM geo_series g
    WHERE UPPER(g.bioproject_id) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM g.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM g.updated_at) <= %s::int)

    UNION ALL

    -- Full-text search match (priority 4; includes contributors/departments/organizations in geo_series.tsv)
    SELECT accession,
           ts_rank(tsv, q.q) AS rank, 'geo'::text AS source, 4 AS priority
    FROM geo_series, q
    WHERE tsv @@ q.q
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) <= %s::int)
),
deduped AS (
    SELECT DISTINCT ON (accession)
        accession, rank, source
    FROM all_matches
    ORDER BY accession, priority, rank DESC
)
SELECT
    accession, rank, source
FROM deduped
ORDER BY rank DESC, accession DESC
LIMIT %s;
"""


SRA_QUERY_ACCESSIONS_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
all_matches AS (
    -- Exact SRP/ERP/DRP accession match (priority 1)
    SELECT accession,
           100.0::float8 AS rank, 'sra'::text AS source, 1 AS priority
    FROM sra_studies
    WHERE UPPER(accession) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) <= %s::int)

    UNION ALL

    -- SRX/ERX/DRX experiment match (priority 2)
    SELECT s.accession,
           100.0::float8 AS rank, 'sra'::text AS source, 2 AS priority
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE UPPER(e.accession) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- PRJNA alias match (priority 3)
    SELECT s.accession,
           100.0::float8 AS rank, 'sra'::text AS source, 3 AS priority
    FROM sra_studies s
    WHERE UPPER(s.alias) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- SRS/ERS/DRS sample match (priority 4) - uses GIN index
    SELECT DISTINCT s.accession,
           100.0::float8 AS rank, 'sra'::text AS source, 4 AS priority
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE e.samples @> ARRAY[UPPER(%s)]
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- Submission center name match (priority 5)
    SELECT DISTINCT s.accession,
           50.0::float8 AS rank, 'sra'::text AS source, 5 AS priority
    FROM sra_submissions sub
    JOIN sra_studies s ON s.submission = sub.accession
    WHERE sub.center_name ILIKE '%%' || %s || '%%'
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- Full-text search match (priority 6)
    SELECT accession,
           ts_rank(tsv, q.q) AS rank, 'sra'::text AS source, 6 AS priority
    FROM sra_studies, q
    WHERE tsv @@ q.q
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) <= %s::int)
),
deduped AS (
    SELECT DISTINCT ON (accession)
        accession, rank, source
    FROM all_matches
    ORDER BY accession, priority, rank DESC
)
SELECT
    accession, rank, source
FROM deduped
ORDER BY rank DESC, accession DESC
LIMIT %s;
"""


ALL_QUERY_ACCESSIONS_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
all_matches AS (
    -- GEO exact accession match (priority 1)
    SELECT accession,
           100.0::float8 AS rank, 'geo'::text AS source, 1 AS priority,
           accession AS dedup_key
    FROM geo_series
    WHERE UPPER(accession) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) <= %s::int)

    UNION ALL

    -- GEO GSM sample match (priority 2) - uses GIN index
    SELECT g.accession,
           100.0::float8 AS rank, 'geo'::text AS source, 2 AS priority,
           g.accession AS dedup_key
    FROM geo_series g
    WHERE g.samples_ref @> ARRAY[UPPER(%s)]
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM g.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM g.updated_at) <= %s::int)

    UNION ALL

    -- GEO PRJNA bioproject match (priority 3)
    SELECT g.accession,
           100.0::float8 AS rank, 'geo'::text AS source, 3 AS priority,
           g.accession AS dedup_key
    FROM geo_series g
    WHERE UPPER(g.bioproject_id) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM g.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM g.updated_at) <= %s::int)

    UNION ALL

    -- SRA exact accession match (priority 4)
    SELECT s.accession,
           100.0::float8 AS rank, 'sra'::text AS source, 4 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_studies s
    WHERE UPPER(s.accession) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- SRA SRX experiment match (priority 5)
    SELECT s.accession,
           100.0::float8 AS rank, 'sra'::text AS source, 5 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE UPPER(e.accession) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- SRA PRJNA alias match (priority 6)
    SELECT s.accession,
           100.0::float8 AS rank, 'sra'::text AS source, 6 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_studies s
    WHERE UPPER(s.alias) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- SRA SRS sample match (priority 7) - uses GIN index
    SELECT DISTINCT ON (s.accession)
           s.accession,
           100.0::float8 AS rank, 'sra'::text AS source, 7 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE e.samples @> ARRAY[UPPER(%s)]
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- SRA/ENA submission center name match (priority 8)
    SELECT DISTINCT s.accession,
           50.0::float8 AS rank, 'sra'::text AS source, 8 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_submissions sub
    JOIN sra_studies s ON s.submission = sub.accession
    WHERE sub.center_name ILIKE '%%' || %s || '%%'
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- GEO full-text search (priority 9; includes contributors/departments/organizations in geo_series.tsv)
    SELECT accession,
           ts_rank(tsv, q.q) AS rank, 'geo'::text AS source, 9 AS priority,
           accession AS dedup_key
    FROM geo_series, q
    WHERE tsv @@ q.q
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) <= %s::int)

    UNION ALL

    -- SRA full-text search (priority 10, lower rank multiplier)
    SELECT s.accession,
           ts_rank(s.tsv, q.q) * 0.7 AS rank, 'sra'::text AS source, 10 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_studies s, q
    WHERE s.tsv @@ q.q
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)

    UNION ALL

    -- ArrayExpress exact accession match (priority 11)
    SELECT accession,
           100.0::float8 AS rank, 'arrayexpress'::text AS source, 11 AS priority,
           COALESCE(alias, accession) AS dedup_key
    FROM arrayexpress_experiments
    WHERE UPPER(accession) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) <= %s::int)

    UNION ALL

    -- ArrayExpress ENA experiment/run match (priority 12)
    SELECT DISTINCT e.accession,
           100.0::float8 AS rank, 'arrayexpress'::text AS source, 12 AS priority,
           COALESCE(e.alias, e.accession) AS dedup_key
    FROM arrayexpress_samples s
    JOIN arrayexpress_experiments e ON e.accession = s.experiment_accession
    WHERE (UPPER(s.ena_experiment) = UPPER(%s) OR UPPER(s.ena_run) = UPPER(%s))
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) <= %s::int)

    UNION ALL

    -- ArrayExpress organization name match (priority 13)
    SELECT DISTINCT e.accession,
           50.0::float8 AS rank, 'arrayexpress'::text AS source, 13 AS priority,
           COALESCE(e.alias, e.accession) AS dedup_key
    FROM arrayexpress_experiments e
    CROSS JOIN LATERAL jsonb_array_elements(
        -- organizations is a JSONB 'null' scalar on many rows
        CASE WHEN jsonb_typeof(e.organizations) = 'array'
             THEN e.organizations ELSE '[]'::jsonb END
    ) AS org
    WHERE org->>'name' ILIKE '%%' || %s || '%%'
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) <= %s::int)

    UNION ALL

    -- ArrayExpress full-text search (priority 14)
    SELECT accession,
           ts_rank(tsv, q.q) * 0.7 AS rank, 'arrayexpress'::text AS source, 14 AS priority,
           COALESCE(alias, accession) AS dedup_key
    FROM arrayexpress_experiments, q
    WHERE tsv @@ q.q
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) <= %s::int)
),
deduped AS (
    SELECT DISTINCT ON (dedup_key)
        accession, rank, source
    FROM all_matches
    ORDER BY dedup_key, priority, rank DESC
)
SELECT
    accession, rank, source
FROM deduped
ORDER BY rank DESC, accession DESC
LIMIT %s;
"""


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

# DDBJ-exclusive studies only -- a mirrored DRP is served by PROJECT_SRA_SQL.
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
    -- idx_ena_studies_parent index — a big umbrella has tens of thousands).
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
PRJ_TO_SRA_STUDY_SQL = """
SELECT accession
FROM sra_studies
WHERE bioproject_id = %s
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
# itself (study_accession = PRJNA*/PRJDB*/PRJEB*), with no GEO/SRA/GSA study —
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
# submitter uploaded originals that were never normalised therefore has no
# fastq_ftp at all, even though its original fastq.gz files are already listed in
# ncbi_files -- so the page offered nothing to download while holding the links
# (SRP567218: 44 runs, 0 files shown, 192 present).
#
# Only entries carrying a direct https url qualify. The rest are reachable solely
# via "Use Cloud Data Delivery" s3 alternatives, which are requester-pays and not
# a link we can hand a user; those runs keep showing no FASTQ, which is honest.
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
# (..._R1_001.fastq.gz.1), so a name taken from the URL is wrong for ~92% of the
# fallback entries. ncbi_files already stores the true filename; use it.
#
# Total by construction, never NULL where fastq_ftp is set: the ENA arm is just
# the URL basename, which is byte-for-byte what callers derived themselves, so
# reading this column can never change an ENA run's name. ORDINALITY keeps that
# arm in url order; the fallback arm shares the aggregate's ORDER BY. Do not
# strip the suffix by regex instead -- names like YCS122B_control.1.fastq.gz end
# in a numeric component legitimately, and would be corrupted.
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
PROJECTS_BY_PMID_SQL = """
WITH linked AS (
    SELECT accession,
           CASE WHEN source = 'ae' THEN 'arrayexpress' ELSE source END AS source,
           'study_publications' AS via
    FROM study_publications
    WHERE publications @> %(pmid_json)s::jsonb
    UNION
    SELECT accession, 'geo', 'geo_series.pubmed_id'
    FROM geo_series
    WHERE pubmed_id @> ARRAY[%(pmid)s]::text[] OR pubmed_id_pysradb = %(pmid)s
    UNION
    SELECT accession, 'sra', 'sra_studies.pubmed_id_pysradb'
    FROM sra_studies
    WHERE pubmed_id_pysradb = %(pmid)s
      -- same private-placeholder guard as PMID_VARIANT_BATCH_SQL
      AND title NOT ILIKE 'GEO accession %% is currently private%%'
    UNION
    SELECT study_accession, 'ena', 'ena_studies.pubmed_id_pysradb'
    FROM ena_studies WHERE pubmed_id_pysradb = %(pmid)s
    UNION
    SELECT accession, 'arrayexpress', 'arrayexpress_experiments.pubmed_id'
    FROM arrayexpress_experiments
    WHERE pubmed_id = %(pmid)s OR pubmed_id_pysradb = %(pmid)s
    UNION
    SELECT accession, 'dra', 'dra_studies.pubmed_id_pysradb'
    FROM dra_studies WHERE pubmed_id_pysradb = %(pmid)s
    UNION
    SELECT accession, 'gea', 'gea_experiments.pubmed_id'
    FROM gea_experiments
    WHERE pubmed_id = %(pmid)s OR pubmed_id_pysradb = %(pmid)s
),
-- Collapse multiple link rows for the same accession; study_publications wins as via.
deduped AS (
    SELECT DISTINCT ON (accession) accession, source, via
    FROM linked
    ORDER BY accession, (via <> 'study_publications'), via
)
SELECT l.accession, l.source, l.via,
       COALESCE(g.title, s.title, ae.title, en.study_title, d.title, ge.title)
           AS title,
       -- dra_studies / gea_experiments have no abstract column to fall back on,
       -- so those rows come back with a null summary.
       COALESCE(g.summary, s.abstract, ae.description, en.study_description)
           AS summary,
       COALESCE(g.published_at, s.published_at, ae.release_date, en.first_public,
                d.published_at, ge.release_date) AS published_at
FROM deduped l
LEFT JOIN geo_series               g  ON g.accession        = l.accession
LEFT JOIN sra_studies              s  ON s.accession        = l.accession
LEFT JOIN arrayexpress_experiments ae ON ae.accession       = l.accession
LEFT JOIN ena_studies              en ON en.study_accession = l.accession
LEFT JOIN dra_studies              d  ON d.accession        = l.accession
LEFT JOIN gea_experiments          ge ON ge.accession       = l.accession
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
_AUTHOR_PROJECTS_CTE = """
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
     -- nested loop (loops=N) with `JOIN pm ON pubmed_id @> ARRAY[pm.pmid]`.
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
    -- affiliation — leave institute NULL rather than mislead.
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
    SELECT gs.study_accession, gs.study_title, gs.study_description,
           gs.last_updated, gs.first_public, NULL::text
      FROM gsa_studies gs
     WHERE gs.pubmed_id_pysradb IN (SELECT pmid FROM pm)
    UNION ALL
    -- Split the OR into two arms: a single "col1 IN (..) OR col2 IN (..)" forces
    -- a seq scan over all ~60k AE rows; two arms each hit their own index.
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
# (window runs before LIMIT). The handler paginates + computes institute facets
# in Python from this one result, so the CTE runs once instead of twice.
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

AE_QUERY_ACCESSIONS_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
all_matches AS (
    -- Exact accession match (priority 1)
    SELECT accession,
           100.0::float8 AS rank, 'arrayexpress'::text AS source, 1 AS priority
    FROM arrayexpress_experiments
    WHERE UPPER(accession) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) <= %s::int)

    UNION ALL

    -- ENA experiment/run match (priority 2)
    SELECT DISTINCT e.accession,
           100.0::float8 AS rank, 'arrayexpress'::text AS source, 2 AS priority
    FROM arrayexpress_samples s
    JOIN arrayexpress_experiments e ON e.accession = s.experiment_accession
    WHERE (UPPER(s.ena_experiment) = UPPER(%s) OR UPPER(s.ena_run) = UPPER(%s))
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) <= %s::int)

    UNION ALL

    -- Organization name match (priority 3)
    SELECT DISTINCT e.accession,
           50.0::float8 AS rank, 'arrayexpress'::text AS source, 3 AS priority
    FROM arrayexpress_experiments e
    CROSS JOIN LATERAL jsonb_array_elements(
        -- organizations is a JSONB 'null' scalar on many rows
        CASE WHEN jsonb_typeof(e.organizations) = 'array'
             THEN e.organizations ELSE '[]'::jsonb END
    ) AS org
    WHERE org->>'name' ILIKE '%%' || %s || '%%'
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) <= %s::int)

    UNION ALL

    -- Full-text search match (priority 4)
    SELECT accession,
           ts_rank(tsv, q.q) AS rank, 'arrayexpress'::text AS source, 4 AS priority
    FROM arrayexpress_experiments, q
    WHERE tsv @@ q.q
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) <= %s::int)
),
deduped AS (
    SELECT DISTINCT ON (accession)
        accession, rank, source
    FROM all_matches
    ORDER BY accession, priority, rank DESC
)
SELECT
    accession, rank, source
FROM deduped
ORDER BY rank DESC, accession DESC
LIMIT %s;
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
    have at least one matching row in ontology_samples_v4 (ILIKE substring match).

    `count_by` switches the final SELECT to aggregation mode. Supported values:
    "year", "source", "organism", "country". Returns one row per bucket with
    {bucket, count} instead of the paginated row list.
    """
    params = {}
    cte_parts = []

    include_geo = source in (None, "all", "geo")
    include_sra = source in (None, "all", "sra")
    include_ae = source in (None, "all", "arrayexpress")
    include_ena = source in (None, "all", "ena")
    include_gsa = source in (None, "all", "gsa")

    # Skip sources that cannot satisfy required filters
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

    # Populate shared params
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

    # Geo filter kwargs
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
    def _pub_date_clause(acc_expr: str) -> str:
        conds = [
            f"sp.accession = {acc_expr}",
            "pub->>'pub_date' ~ '^[0-9]{4} [A-Z][a-z]{2} [0-9]{2}$'",
        ]
        if pub_date_after:
            conds.append(
                "to_date(pub->>'pub_date', 'YYYY Mon DD') >= %(pub_date_after)s::date"
            )
        if pub_date_before:
            conds.append(
                "to_date(pub->>'pub_date', 'YYYY Mon DD') <= %(pub_date_before)s::date"
            )
        return (
            "EXISTS (SELECT 1 FROM study_publications sp"
            " CROSS JOIN LATERAL jsonb_array_elements(sp.publications) pub"
            " WHERE " + " AND ".join(conds) + ")"
        )

    has_pub_date = bool(pub_date_after or pub_date_before)

    # --- GEO ---
    if include_geo:
        w = []
        joins = ""
        if geo_cte:
            joins = " INNER JOIN geo_accessions ga ON ga.canonical_accession = g.accession AND ga.source = 'geo'"
        from_q = ", q" if q else ""
        if q:
            w.append("g.tsv @@ q.match_q")
        if organism:
            w.append("g.organisms @> ARRAY[%(organism)s]")
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
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = g.accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = g.accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
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

    # --- SRA ---
    if include_sra:
        w = []
        joins = ""
        if geo_cte:
            joins = " INNER JOIN geo_accessions ga ON ga.canonical_accession = s.accession AND ga.source = 'sra'"
        from_q = ", q" if q else ""
        if q:
            w.append("s.tsv @@ q.match_q")
        if organism:
            w.append("s.organisms @> ARRAY[%(organism)s]")
        if library_strategy or platform:
            ec = ["e.study = s.accession"]
            if library_strategy:
                ec.append("e.library_strategy = %(library_strategy)s")
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
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = s.accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = s.accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
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

    # --- ArrayExpress ---
    if include_ae:
        w = []
        joins = ""
        if geo_cte:
            joins = " INNER JOIN geo_accessions ga ON ga.canonical_accession = ae.accession AND ga.source = 'arrayexpress'"
        from_q = ", q" if q else ""
        if q:
            w.append("ae.tsv @@ q.match_q")
        if organism:
            w.append("ae.organisms @> ARRAY[%(organism)s]")
        if library_strategy:
            w.append("ae.technology = %(library_strategy)s")
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
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = ae.accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = ae.accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
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

    # --- ENA ---
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
            # scalar scientific_name only saw the study-level organism and missed
            # every multi-organism ENA study (44k of them).
            w.append("es.organisms @> ARRAY[%(organism)s]")
        if library_strategy or platform or country:
            ec = ["ee.study_accession = es.study_accession"]
            if library_strategy:
                ec.append("ee.library_strategy = %(library_strategy)s")
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
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = es.study_accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = es.study_accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
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
        # the ~5.4k studies absent from the search table.
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
            # scientific_name missed every multi-organism GSA study (1,194).
            w.append("gs.organisms @> ARRAY[%(organism)s]")
        if library_strategy or platform or country:
            ec = ["ge.study_accession = gs.study_accession"]
            if library_strategy:
                ec.append("ge.library_strategy = %(library_strategy)s")
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
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = gs.study_accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
                " WHERE os.study_accession = gs.study_accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v4 os"
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

    # Prepend query and geo_accessions CTEs when active
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

    # Sort expression and optional join
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
    -- `accession` nor an `organisms` column (transfer.gsa_search enriches them
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
# live in the DRA study wrapping it.
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

# -- Projects mode (studies/series per month) --

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

# -- Experiments mode (samples/experiments per month) --

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

# -- Bases / bytes mode (data volume per month) --

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
            um_conditions.append("u.dominant_scientific_name = %s")
            params.append(organism)
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
        conditions.append("dominant_scientific_name = %s")
        params.append(organism)
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

    facet_cols = ", ".join(dict.fromkeys(col for _, col, _, _, _ in ctes_cfg).keys())
    params: list = [country]
    scoped_where = ""
    if organism:
        scoped_where = "WHERE dominant_scientific_name = %s"
        params.append(organism)

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
        f"totals AS (\n"
        f"    SELECT COUNT(*) AS count\n"
        f"    FROM scoped_base\n"
        f")"
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


SOURCE_TOTALS_SQL = f"""
SELECT
    {_reltuples('geo_series')} AS geo_projects,
    {_reltuples('geo_samples')} AS geo_samples,
    {_reltuples('sra_studies')} AS sra_projects,
    {_reltuples('sra_samples')} AS sra_samples,
    {_reltuples('arrayexpress_experiments')} AS arrayexpress_projects,
    {_reltuples('arrayexpress_samples')} AS arrayexpress_samples,
    {_reltuples('ena_studies')} AS ena_projects,
    {_reltuples('ena_samples')} AS ena_samples,
    {_reltuples('gsa_studies')} AS gsa_projects,
    {_reltuples('gsa_samples')} AS gsa_samples,
    {_reltuples('dra_studies')} AS dra_projects,
    {_reltuples('dra_samples')} AS dra_samples,
    {_reltuples('gea_experiments')} AS gea_projects,
    {_reltuples('gea_samples')} AS gea_samples;
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


def _enriched_summary_ddl() -> str:
    cols = ",\n    ".join(
        f"COUNT({c}) AS {c}_filled, COUNT(DISTINCT {c}) AS {c}_distinct"
        for c in ENRICHED_STAT_COLUMNS
    )
    return (
        "DROP MATERIALIZED VIEW IF EXISTS enriched_summary;\n"
        "CREATE MATERIALIZED VIEW enriched_summary AS\nSELECT\n"
        "    COUNT(*) AS enriched_samples,\n"
        "    COUNT(DISTINCT study_accession) AS enriched_projects,\n"
        f"    {cols}\n"
        "FROM ontology_samples_v4;"
    )


def _enriched_top_values_ddl() -> str:
    union = "\n        UNION ALL\n        ".join(
        f"SELECT '{c}' AS column_name, {c}::text AS value, COUNT(*) AS n "
        f"FROM ontology_samples_v4 WHERE {c} IS NOT NULL AND {c}::text <> '' "
        f"GROUP BY {c}"
        for c in ENRICHED_STAT_COLUMNS
    )
    return (
        "DROP MATERIALIZED VIEW IF EXISTS enriched_column_top_values;\n"
        "CREATE MATERIALIZED VIEW enriched_column_top_values AS\n"
        "SELECT column_name, value, n FROM (\n"
        "    SELECT column_name, value, n,\n"
        "           ROW_NUMBER() OVER (PARTITION BY column_name ORDER BY n DESC) AS rk\n"
        "    FROM (\n        " + union + "\n    ) g\n"
        f") ranked WHERE rk <= {ENRICHED_TOP_VALUES_PER_COLUMN};\n"
        "CREATE INDEX IF NOT EXISTS idx_enriched_top_values_col_n "
        "ON enriched_column_top_values (column_name, n DESC);"
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


def _enriched_crosstab_ddl() -> str:
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
        f"'{b}' AS breakdown_col, {b}::text AS breakdown_val "
        f"FROM base "
        f"WHERE {g} IS NOT NULL AND {g}::text <> '' "
        f"AND {b} IS NOT NULL AND {b}::text <> ''"
        for g in ENRICHED_CROSSTAB_COLUMNS
        for b in ENRICHED_CROSSTAB_COLUMNS
        if g != b
    )
    return (
        "DROP MATERIALIZED VIEW IF EXISTS enriched_crosstab;\n"
        "CREATE MATERIALIZED VIEW enriched_crosstab AS\n"
        "WITH base AS MATERIALIZED (\n"
        f"        SELECT\n            {base_cols}\n"
        "        FROM ontology_samples_v4 os\n"
        "        LEFT JOIN unified_metadata um "
        "ON um.canonical_accession = os.study_accession\n"
        "        LEFT JOIN (SELECT scientific_name, MIN(common_name) AS common_name "
        "FROM common_names GROUP BY scientific_name) cn "
        "ON cn.scientific_name = um.dominant_scientific_name\n"
        "    ),\n"
        "    pairs AS (\n        " + pairs + "\n    ),\n"
        "    counts AS (\n"
        "        SELECT group_col, group_val, breakdown_col, breakdown_val, COUNT(*) AS n\n"
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
        "CREATE INDEX IF NOT EXISTS idx_enriched_crosstab_pair "
        "ON enriched_crosstab (group_col, breakdown_col, group_total DESC);"
    )


ENRICHED_STATS_DDL = (
    _enriched_summary_ddl()
    + "\n\n"
    + _enriched_top_values_ddl()
    + "\n\n"
    + _enriched_crosstab_ddl()
)
ENRICHED_STATS_REFRESH = (
    "REFRESH MATERIALIZED VIEW enriched_summary;\n"
    "REFRESH MATERIALIZED VIEW enriched_column_top_values;\n"
    "REFRESH MATERIALIZED VIEW enriched_crosstab;"
)

ENRICHED_SUMMARY_SQL = "SELECT * FROM enriched_summary;"
ENRICHED_COLUMN_VALUES_SQL = (
    "SELECT value, n FROM enriched_column_top_values "
    "WHERE column_name = %s ORDER BY n DESC LIMIT %s;"
)

ENRICHED_CROSSTAB_SQL = (
    "SELECT group_val, breakdown_val, n, group_total, group_distinct "
    "FROM enriched_crosstab WHERE group_col = %s AND breakdown_col = %s "
    "ORDER BY group_total DESC, group_val, n DESC;"
)


def build_country_accessions_sql(
    country: str,
    organism: str | None = None,
    assay_l2: str | None = None,
    limit: int | None = None,
) -> tuple[str, list]:
    """Return accessions for a country with optional organism/assay filters."""
    conditions = [
        "latitude IS NOT NULL",
        "longitude IS NOT NULL",
        "country = %s",
    ]
    params: list = [country]
    if organism:
        conditions.append("dominant_scientific_name = %s")
        params.append(organism)
    if assay_l2:
        conditions.append("dominant_assay_l2 = %s")
        params.append(assay_l2)
    where = " AND ".join(conditions)
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
_ENRICHED_SAMPLES_NEW_COLS = [
    "development_stage",
    "sample_type",
    "genetic_modification",
    "cell_count",
    "gene_count",
    "cell_count_estimated",
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
SELECT {col_str}
FROM enriched_samples es
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

ENRICHED_STUDY_SQL = """
SELECT st.accession, st.title, st.n_samples,
       NULL AS single_cell_modality
FROM enriched_studies st
WHERE st.accession = %s;
"""


ENRICHED_STUDY_V3_SQL = """
SELECT st.accession, st.title, st.n_samples, st.single_cell_modality
FROM enriched_studies_v4 st
WHERE st.accession = %s;
"""

ONTOLOGY_SAMPLES_V3_SQL = """
SELECT
    sample, age, sex, ethnicity, phenotype,
    cell_type, tissue, strain, disease, assay,
    assay_category, cell_line, treatment,
    development_stage, sample_type, genetic_modification,
    organism, taxid, tissue_primary_site, tissue_site_type,
    cell_count, gene_count, cell_count_estimated,
    disease_ontology_id, disease_ontology_name,
    tissue_ontology_id, tissue_ontology_name,
    cell_type_ontology_id, cell_type_ontology_name,
    assay_ontology_id, assay_ontology_name,
    development_stage_ontology_id, development_stage_ontology_name
FROM ontology_samples_v4
WHERE study_accession = %s
ORDER BY sample;
"""

ENRICHED_SAMPLES_V3_SQL = """
SELECT
    sample, age, sex, ethnicity, phenotype,
    cell_type, tissue, strain, disease, assay,
    assay_category, cell_line, treatment,
    development_stage, sample_type, genetic_modification,
    cell_count, gene_count, cell_count_estimated
FROM enriched_samples_v4
WHERE study_accession = %s
ORDER BY sample;
"""

# Low-confidence tags per sample within a study
LOW_CONFIDENCE_TAGS_SQL = """
SELECT sample_accession, tags
FROM low_confidence
WHERE study_accession = %s;
"""
