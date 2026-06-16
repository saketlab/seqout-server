"""SQL queries for pysradb-server"""

STUDY_PUBLICATIONS_BATCH_SQL = """
SELECT accession, publications
FROM study_publications
WHERE accession = ANY(%s);
"""

SUBMITTER_BATCH_SQL = """
SELECT canonical_accession AS accession, center_name AS submitter, country_code,
       is_single_cell, single_cell_modality
FROM unified_metadata
WHERE canonical_accession = ANY(%(acc)s);
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
    WHERE tsv @@ q.match_q
),
counted AS (
    SELECT *, COUNT(*) OVER () AS total_count FROM all_matches
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
    WHERE tsv @@ q.match_q
),
counted AS (
    SELECT *, COUNT(*) OVER () AS total_count FROM all_matches
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
    WHERE tsv @@ q.match_q
),
counted AS (
    SELECT *, COUNT(*) OVER () AS total_count FROM all_matches
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
    WHERE tsv @@ q.match_q
),
counted AS (
    SELECT *, COUNT(*) OVER () AS total_count FROM all_matches
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
    WHERE tsv @@ q.match_q

    UNION ALL

    -- SRA full-text search (lower rank multiplier)
        SELECT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms, s.countries,
           (ts_rank(s.tsv, q.core_q) + ts_rank(s.tsv, q.match_q) * 0.2) * 0.7 AS rank, 'sra'::text AS source,
           COALESCE(s.alias, s.accession) AS dedup_key, s.instrument_models, s.library_strategies
    FROM sra_studies s, q
    WHERE s.tsv @@ q.match_q

    UNION ALL

    -- ArrayExpress full-text search
    SELECT accession, title, description AS summary, release_date AS updated_at, organisms, countries,
           (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'arrayexpress'::text AS source,
           COALESCE(alias, accession) AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies
    FROM arrayexpress_experiments, q
    WHERE tsv @@ q.match_q

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
    WHERE tsv @@ q.match_q
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
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    accession, title, summary, updated_at, organisms, countries, rank, source, total_count, instrument_models, library_strategies
FROM paged
ORDER BY rank DESC, accession DESC
LIMIT 200;
"""


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

SEARCH_BY_ACCESSION_SQL_BY_KIND: dict[str, str] = {
    "gse": (
        f"SELECT {_GEO_FIELDS} FROM geo_series g WHERE g.accession = %(acc)s LIMIT 1"
    ),
    "gsm": (
        f"SELECT {_GEO_FIELDS} FROM geo_series g"
        f" WHERE g.samples_ref @> ARRAY[%(acc)s] LIMIT 1"
    ),
    "srp": (
        f"SELECT {_SRA_FIELDS} FROM sra_studies s WHERE s.accession = %(acc)s LIMIT 1"
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
        f" LIMIT 2"
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
        f" LIMIT 2"
    ),
    "ae": (
        f"SELECT {_AE_FIELDS} FROM arrayexpress_experiments ae"
        f" WHERE UPPER(ae.accession) = %(acc)s LIMIT 1"
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
        f" LIMIT 3"
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
    CROSS JOIN LATERAL jsonb_array_elements(e.organizations) AS org
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
    (SELECT string_agg(DISTINCT contributor_name, ', ' ORDER BY contributor_name)
     FROM (
        SELECT btrim(COALESCE(
            to_jsonb(c)->>'contributor',
            to_jsonb(c)->>'contributor_name',
            to_jsonb(c)->>'contact_name',
            to_jsonb(c)->>'name',
            to_jsonb(c)->>'author'
        )) AS contributor_name
        FROM geo_contributors c
        WHERE c.accession = geo_series.accession
     ) contributors
     WHERE contributor_name IS NOT NULL AND contributor_name <> ''
    ) AS geo_contributor_authors,
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
    NULL::real[] AS coords_2d,
    NULL::real[] AS coords_3d,
    NULL::jsonb AS neighbors,
    NULL::text[] AS organisms,
    NULL::jsonb AS center
FROM ena_studies es
WHERE UPPER(es.study_accession) = UPPER(%s)
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
            SELECT jsonb_agg(
                jsonb_build_object('tag', kv.key, 'value', kv.value)
                ORDER BY kv.key
            )
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
        '[]'::jsonb
    ) AS attributes_json,
    s.submission_accession AS submission,
    NULL::jsonb AS links
FROM ena_samples s
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

COMMON_NAME_BY_SCIENTIFIC_NAME_SQL = """
SELECT
    tax_id,
    scientific_name,
    common_name
FROM common_names
WHERE LOWER(scientific_name) = LOWER(%s)
ORDER BY common_name;
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

# ---------------------------------------------------------------------------
# Sample detail queries
# ---------------------------------------------------------------------------

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

# Get a single GEO sample by accession (GSM)
# Caller must pass accession already uppercased
GEO_SAMPLE_SQL = """
SELECT s.*
FROM geo_samples s
WHERE s.accession = %s
LIMIT 1;
"""

# Get runs for a specific experiment accession
# Caller must pass accession already uppercased
EXPERIMENT_RUNS_SQL = """
SELECT
    dl.run_accession,
    dl.experiment_accession,
    dl.library_layout,
    dl.fastq_ftp,
    dl.fastq_bytes,
    dl.fastq_md5,
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
    ena.run_alias
FROM sra_run_download_links dl
LEFT JOIN ena_experiments ena ON ena.run_accession = dl.run_accession
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

# Publication -> studies reverse lookup.
STUDIES_BY_PMID_SQL = """
SELECT DISTINCT accession, source
FROM cross_ref_pubmed
WHERE pubmed_id = %s
ORDER BY source, accession;
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

# Similar studies via the precomputed neighbors JSONB (2D embedding nearest neighbors).
# Looks up the source accession in geo_series OR sra_studies, then enriches each
# neighbor with title/organisms/source from whichever archive owns it.
# Params: %(acc)s (source accession), %(limit)s (neighbor count).
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
    FROM base, jsonb_array_elements(base.neighbors) AS n
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

PROJECT_AE_SQL = """
SELECT
    ae.accession,
    ae.title,
    ae.description AS summary,
    ae.protocols_description AS overall_design,
    ae.organisms,
    CASE WHEN ae.pubmed_id IS NOT NULL THEN ARRAY[ae.pubmed_id] ELSE NULL END AS pubmed_id,
    NULL AS samples_ref,
    ae.study_type AS series_type,
    CASE
        WHEN ae.external_links IS NOT NULL THEN
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
    ae.processed_data_files AS supplementary_data,
    ae.release_date AS published_at,
    ae.release_date AS updated_at,
    NULL::real[] AS coords_2d,
    NULL::real[] AS coords_3d,
    NULL::jsonb AS neighbors,
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
     FROM jsonb_array_elements(ae.organizations) AS org
     LEFT JOIN arrayexpress_geocode aeg ON aeg.organization = org->>'name'
    ) AS center
FROM arrayexpress_experiments ae
WHERE ae.accession = %s;
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
    CROSS JOIN LATERAL jsonb_array_elements(e.organizations) AS org
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
        params["geo_lat"] = geo["geo_lat"]
        params["geo_lng"] = geo["geo_lng"]
        if geo.get("geo_radius_km"):
            params["geo_radius_km"] = geo["geo_radius_km"]
            conditions.append(
                "(6371 * acos(LEAST(1.0, cos(radians(%(geo_lat)s)) * cos(radians(latitude))"
                " * cos(radians(longitude) - radians(%(geo_lng)s))"
                " + sin(radians(%(geo_lat)s)) * sin(radians(latitude))))) <= %(geo_radius_km)s"
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
    have at least one matching row in ontology_samples_v3 (ILIKE substring match).

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
    if multi_platform:
        include_ae = False
        include_ena = False

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
        if instrument_model:
            w.append("g.instrument_models @> ARRAY[%(instrument_model)s]")
        if multi_platform:
            w.append("array_length(g.platforms, 1) >= 2")
        if sample_tissue:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = g.accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = g.accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
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
        if instrument_model:
            w.append("s.instrument_models @> ARRAY[%(instrument_model)s]")
        if multi_platform:
            w.append("array_length(s.platforms, 1) >= 2")
        if sample_tissue:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = s.accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = s.accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
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
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = ae.accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = ae.accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = ae.accession"
                " AND os.cell_type ILIKE %(sample_cell_type)s)"
            )
        if journal:
            w.append(
                "EXISTS (SELECT 1 FROM study_publications sp"
                " WHERE sp.accession = ae.accession"
                " AND sp.publications->0->>'journal' = %(journal)s)"
            )

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
            w.append("es.scientific_name = %(organism)s")
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
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = es.study_accession"
                " AND os.tissue ILIKE %(sample_tissue)s)"
            )
        if sample_disease:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = es.study_accession"
                " AND os.disease ILIKE %(sample_disease)s)"
            )
        if sample_cell_type:
            w.append(
                "EXISTS (SELECT 1 FROM ontology_samples_v3 os"
                " WHERE os.study_accession = es.study_accession"
                " AND os.cell_type ILIKE %(sample_cell_type)s)"
            )
        if journal:
            w.append(
                "EXISTS (SELECT 1 FROM study_publications sp"
                " WHERE sp.accession = es.study_accession"
                " AND sp.publications->0->>'journal' = %(journal)s)"
            )

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
            f" CASE WHEN es.scientific_name IS NOT NULL"
            f" THEN ARRAY[es.scientific_name] END AS organisms,"
            f" NULL::text[] AS countries,"
            f" {rank} AS rank, 'ena'::text AS source,"
            f" es.study_accession AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies, NULL::text[] AS platforms"
            f" FROM ena_studies es{joins}{from_q}{where}"
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
        accession, updated_at, organisms, countries, source
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
LIMIT 200;"""

    return sql, params


def build_sorted_search_sql(
    q,
    sortby,
    order="desc",
    db=None,
    cursor_sort=None,
    cursor_acc=None,
    q_variants=None,
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
            " WHERE tsv @@ q.match_q"
        )

    if db in (None, "sra"):
        cte_parts.append(
            "SELECT s.accession, s.title, s.abstract AS summary, s.updated_at,"
            " s.organisms, s.countries,"
            " (ts_rank(s.tsv, q.core_q) + ts_rank(s.tsv, q.match_q) * 0.2) * 0.7 AS rank, 'sra'::text AS source,"
            " COALESCE(s.alias, s.accession) AS dedup_key, s.instrument_models, s.library_strategies"
            " FROM sra_studies s, q"
            " WHERE s.tsv @@ q.match_q"
        )

    if db in (None, "arrayexpress"):
        cte_parts.append(
            "SELECT accession, title, description AS summary,"
            " release_date AS updated_at, organisms, countries,"
            " (ts_rank(tsv, q.core_q) + ts_rank(tsv, q.match_q) * 0.2) * 0.7 AS rank, 'arrayexpress'::text AS source,"
            " COALESCE(alias, accession) AS dedup_key, NULL::text[] AS instrument_models, NULL::text[] AS library_strategies"
            " FROM arrayexpress_experiments, q"
            " WHERE tsv @@ q.match_q"
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
            " WHERE tsv @@ q.match_q"
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
LIMIT 200;"""

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
_RESOLVE_DL_STUDY = """
WITH candidates AS (
    SELECT %s::text AS acc
    UNION
    SELECT bioproject_id FROM sra_studies
    WHERE accession = %s AND bioproject_id IS NOT NULL
)
"""

STUDY_RUNS_SUMMARY_SQL = (
    _RESOLVE_DL_STUDY
    + """
SELECT
    COUNT(*) AS total_runs,
    COUNT(*) FILTER (WHERE library_layout = 'PAIRED') AS paired_runs,
    COUNT(*) FILTER (WHERE library_layout = 'SINGLE') AS single_runs,
    COALESCE(SUM(
        CASE WHEN fastq_bytes IS NOT NULL AND fastq_bytes != ''
             THEN (
                 SELECT SUM(v::bigint)
                 FROM unnest(string_to_array(fastq_bytes, ';')) AS v
                 WHERE v ~ '^[0-9]+$'
             )
             ELSE 0
        END
    ), 0) AS total_fastq_bytes
FROM sra_run_download_links
WHERE study_accession IN (SELECT acc FROM candidates);
"""
)

STUDY_RUNS_SQL = (
    _RESOLVE_DL_STUDY
    + """
SELECT
    dl.run_accession,
    dl.experiment_accession,
    dl.library_layout,
    dl.fastq_ftp,
    dl.fastq_bytes,
    dl.fastq_md5,
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
    ena.run_alias
FROM sra_run_download_links dl
LEFT JOIN ena_experiments ena ON ena.run_accession = dl.run_accession
WHERE dl.study_accession IN (SELECT acc FROM candidates)
ORDER BY dl.experiment_accession, dl.run_accession;
"""
)

STUDY_RUNS_PREVIEW_SQL = (
    _RESOLVE_DL_STUDY
    + """
SELECT
    dl.run_accession,
    dl.experiment_accession,
    dl.library_layout,
    dl.fastq_ftp,
    dl.fastq_bytes,
    dl.fastq_md5,
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
    ena.run_alias
FROM sra_run_download_links dl
LEFT JOIN ena_experiments ena ON ena.run_accession = dl.run_accession
WHERE dl.study_accession IN (SELECT acc FROM candidates)
ORDER BY dl.experiment_accession, dl.run_accession
LIMIT 500;
"""
)

# Same columns as STUDY_RUNS_PREVIEW_SQL plus dl.study_accession; filtered to one run.
RUN_DOWNLOAD_SQL = """
SELECT
    dl.run_accession,
    dl.experiment_accession,
    dl.study_accession,
    dl.library_layout,
    dl.fastq_ftp,
    dl.fastq_bytes,
    dl.fastq_md5,
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
    ena.run_alias
FROM sra_run_download_links dl
LEFT JOIN ena_experiments ena ON ena.run_accession = dl.run_accession
WHERE dl.run_accession = %s;
"""

# ---------------------------------------------------------------------------
# BAM files: original submitted BAM files from ncbi_files JSONB
# ---------------------------------------------------------------------------

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
     jsonb_array_elements(dl.ncbi_files) AS f,
     LATERAL (
         SELECT
             MAX(a->>'url') FILTER (
                 WHERE a->>'access_type' = 'anonymous'
                   AND a->>'url' LIKE 'https://%%'
             ) AS https_url,
             MAX(a->>'url') FILTER (
                 WHERE a->>'url' LIKE 's3://%%'
             ) AS s3_url
         FROM jsonb_array_elements(f->'alternatives') AS a
     ) alt
WHERE dl.study_accession IN (SELECT acc FROM candidates)
  AND f->>'supertype' = 'Original'
  AND f->>'filename' LIKE '%%.bam'
ORDER BY dl.experiment_accession, dl.run_accession;
"""
)

# ---------------------------------------------------------------------------
# Growth stats: monthly counts per database
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Organism growth: monthly sample/experiment counts filtered by organism
# ---------------------------------------------------------------------------

GROWTH_ORGANISM_SQL = """
SELECT TO_CHAR(month, 'YYYY-MM') AS month, db, count
FROM organism_growth_monthly
WHERE organism = %s
ORDER BY db, month;
"""

# ---------------------------------------------------------------------------
# Platform growth: monthly counts filtered by platform
# ---------------------------------------------------------------------------

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
       SUM(CASE WHEN db = 'ena' THEN count ELSE 0 END) AS ena
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


# ---------------------------------------------------------------------------
# List all distinct organisms across databases
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Organism experiment totals: total experiments per organism per database
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Global contributions: aggregated lat/lng from unified_metadata
# ---------------------------------------------------------------------------

_GLOBAL_CONTRIBUTIONS_OPTIONAL_COLS = ["place_type", "address_type"]
_global_contributions_available_cols: set[str] = set()


def _build_global_contributions_template(available_cols: set[str]) -> str:
    """Build the template, omitting MODE()/SELECT for columns not in PG."""
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
            mode_lines.append(f"MODE() WITHIN GROUP (ORDER BY {col}) AS {col}")
            select_cols.append(f"a.{col}")
        else:
            select_cols.append(f"NULL AS {col}")
    mode_lines.append("MODE() WITHIN GROUP (ORDER BY short_label) AS short_label")
    mode_lines.append("MODE() WITHIN GROUP (ORDER BY center_name) AS center_name")
    select_cols.extend(["a.short_label", "a.center_name"])

    mode_block = ",\n        ".join(mode_lines)
    select_block = ", ".join(select_cols)

    return f"""
WITH location_agg AS (
    SELECT
        ROUND(latitude::numeric, 2) AS lat,
        ROUND(longitude::numeric, 2) AS lng,
        COUNT(*) AS n_projects,
        COALESCE(SUM(n_experiments), 0)::bigint AS n_experiments,
        COALESCE(SUM(n_samples), 0)::bigint AS n_samples,
        COUNT(*) FILTER (WHERE source = 'geo') AS n_geo,
        COUNT(*) FILTER (WHERE source = 'sra') AS n_sra,
        COUNT(*) FILTER (WHERE source = 'arrayexpress') AS n_ae,
        COUNT(*) FILTER (WHERE source = 'ena') AS n_ena,
        {mode_block}
    FROM unified_metadata
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
      AND __WHERE__
    GROUP BY 1, 2
),
organism_ranked AS (
    SELECT
        ROUND(latitude::numeric, 2) AS lat,
        ROUND(longitude::numeric, 2) AS lng,
        dominant_scientific_name AS organism,
        COUNT(*) AS cnt,
        ROW_NUMBER() OVER (
            PARTITION BY ROUND(latitude::numeric, 2), ROUND(longitude::numeric, 2)
            ORDER BY COUNT(*) DESC
        ) AS rn
    FROM unified_metadata
    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
      AND __WHERE__
      AND dominant_scientific_name IS NOT NULL
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
    a.n_geo, a.n_sra, a.n_ae, a.n_ena,
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
    # __WHERE__ appears in 2 CTEs, so each param placeholder is duplicated
    return sql, params * 2


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

    cte_parts = []
    select_parts = []
    for cte_name, col, kind, limit in ctes:
        cte_parts.append(
            f"{cte_name} AS (\n"
            f"    SELECT {col} AS value, COUNT(*) AS count\n"
            f"    FROM unified_metadata\n"
            f"    WHERE {col} IS NOT NULL\n"
            f"      AND latitude IS NOT NULL AND longitude IS NOT NULL\n"
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

    ctes_cfg: list[tuple[str, str, str, str, bool]] = [
        ("organism_opts", "dominant_scientific_name", "organism", "LIMIT 200", False),
        ("assay_l1_opts", "dominant_assay_l1", "assay_l1", "", True),
        ("assay_l2_opts", "dominant_assay_l2", "assay_l2", "", True),
    ]
    for col in _GLOBAL_CONTRIBUTIONS_OPTIONAL_COLS:
        if col in cols:
            ctes_cfg.append((f"{col}_opts", col, col, "", True))

    organism_clause = "      AND dominant_scientific_name = %s\n" if organism else ""

    params: list = []
    cte_parts = []
    select_parts = []

    for cte_name, col, kind, limit, use_organism in ctes_cfg:
        extra = organism_clause if use_organism else ""
        cte_parts.append(
            f"{cte_name} AS (\n"
            f"    SELECT {col} AS value, COUNT(*) AS count\n"
            f"    FROM unified_metadata\n"
            f"    WHERE {col} IS NOT NULL\n"
            f"      AND latitude IS NOT NULL AND longitude IS NOT NULL\n"
            f"      AND country = %s\n"
            + extra
            + f"    GROUP BY 1 ORDER BY count DESC\n"
            f"    {limit}\n"
            f")"
        )
        params.append(country)
        if use_organism and organism:
            params.append(organism)
        select_parts.append(f"SELECT '{kind}' AS kind, value, count FROM {cte_name}")

    totals_where = "country = %s"
    params_for_totals = [country]
    if organism:
        totals_where += " AND dominant_scientific_name = %s"
        params_for_totals.append(organism)

    cte_parts.append(
        f"totals AS (\n"
        f"    SELECT COUNT(*) AS count\n"
        f"    FROM unified_metadata\n"
        f"    WHERE latitude IS NOT NULL AND longitude IS NOT NULL\n"
        f"      AND {totals_where}\n"
        f")"
    )
    params.extend(params_for_totals)
    select_parts.append("SELECT 'total' AS kind, NULL AS value, count FROM totals")

    sql = (
        "WITH "
        + ",\n".join(cte_parts)
        + "\n"
        + "\nUNION ALL\n".join(select_parts)
        + ";"
    )
    return sql, params


SITEMAP_TABLES = {
    "geo": ("geo_series", "accession", False),
    "sra": ("sra_studies", "accession", False),
    "arrayexpress": ("arrayexpress_experiments", "accession", False),
    "ena": ("ena_studies", "study_accession", False),
    "sra_exp": ("sra_experiments", "accession", False),
    "ena_exp": ("ena_experiments", "experiment_accession", True),
    "run": ("sra_run_download_links", "run_accession", False),
    "geo_sample": ("geo_samples", "accession", False),
    "sra_sample": ("sra_samples", "accession", False),
    "ena_sample": ("ena_samples", "sample_accession", True),
}


def _sitemap_accessions_sql(table: str, col: str, distinct: bool) -> str:
    select = (
        f"SELECT DISTINCT {col} AS accession"
        if distinct
        else f"SELECT {col} AS accession"
    )
    return f"{select} FROM {table} ORDER BY {col} LIMIT %s OFFSET %s;"


def _sitemap_count_expr(table: str, col: str, distinct: bool) -> str:
    count = f"COUNT(DISTINCT {col})" if distinct else "COUNT(*)"
    return f"(SELECT {count} FROM {table})"


SITEMAP_ACCESSIONS_SQL = {
    key: _sitemap_accessions_sql(*spec) for key, spec in SITEMAP_TABLES.items()
}

SITEMAP_COUNTS_SQL = (
    "SELECT "
    + ", ".join(
        f"{_sitemap_count_expr(*spec)} AS {key}" for key, spec in SITEMAP_TABLES.items()
    )
    + ";"
)

SOURCE_TOTALS_SQL = """
SELECT
    (SELECT COUNT(*) FROM geo_series) AS geo_projects,
    (SELECT COUNT(*) FROM geo_samples) AS geo_samples,
    (SELECT COUNT(*) FROM sra_studies) AS sra_projects,
    (SELECT COUNT(*) FROM sra_samples) AS sra_samples,
    (SELECT COUNT(*) FROM arrayexpress_experiments) AS arrayexpress_projects,
    (SELECT COUNT(*) FROM arrayexpress_samples) AS arrayexpress_samples,
    (SELECT COUNT(*) FROM ena_studies) AS ena_projects,
    (SELECT COUNT(*) FROM ena_samples) AS ena_samples;
"""

LAST_UPDATED_SQL = """
SELECT
    (SELECT MAX(updated_at)::timestamptz FROM geo_series) AS geo,
    (SELECT MAX(updated_at)::timestamptz FROM sra_studies) AS sra,
    (SELECT MAX(release_date)::timestamptz FROM arrayexpress_experiments) AS arrayexpress,
    (SELECT MAX(COALESCE(last_updated, first_public))::timestamptz FROM ena_studies) AS ena;
"""


def build_country_accessions_sql(
    country: str,
    organism: str | None = None,
    assay_l2: str | None = None,
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
        f" ORDER BY canonical_accession;"
    )
    return sql, params


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


# Default (base columns only + NULL placeholders) — overridden at startup by build_enriched_samples_sql()
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

# ── Ontology-enriched metadata (v3) ─────────────────────────────────────────

ENRICHED_STUDY_V3_SQL = """
SELECT st.accession, st.title, st.n_samples, st.single_cell_modality
FROM enriched_studies_v3 st
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
FROM ontology_samples_v3
WHERE study_accession = %s
ORDER BY sample;
"""

# Fallback when ontology_samples_v3 has no rows yet (e.g. skeleton-only cell-count studies)
ENRICHED_SAMPLES_V3_SQL = """
SELECT
    sample, age, sex, ethnicity, phenotype,
    cell_type, tissue, strain, disease, assay,
    assay_category, cell_line, treatment,
    development_stage, sample_type, genetic_modification,
    cell_count, gene_count, cell_count_estimated
FROM enriched_samples_v3
WHERE study_accession = %s
ORDER BY sample;
"""

# Low-confidence tags per sample within a study
LOW_CONFIDENCE_TAGS_SQL = """
SELECT sample_accession, tags
FROM low_confidence
WHERE study_accession = %s;
"""
