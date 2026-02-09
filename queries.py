"""SQL queries for pysradb-server"""

GEO_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
all_matches AS (
    -- Exact accession match (priority 1)
    SELECT accession, title, summary, updated_at, organisms,
           100.0::float8 AS rank, 'geo'::text AS source, 1 AS priority
    FROM geo_series
    WHERE UPPER(accession) = UPPER(%s)

    UNION ALL

    -- GSM sample match (priority 2) - uses GIN index
    SELECT g.accession, g.title, g.summary, g.updated_at, g.organisms,
           100.0::float8 AS rank, 'geo'::text AS source, 2 AS priority
    FROM geo_series g
    WHERE g.samples_ref @> ARRAY[UPPER(%s)]

    UNION ALL

    -- PRJNA bioproject match (priority 3)
    SELECT g.accession, g.title, g.summary, g.updated_at, g.organisms,
           100.0::float8 AS rank, 'geo'::text AS source, 3 AS priority
    FROM geo_series g
    WHERE UPPER(g.bioproject_id) = UPPER(%s)

    UNION ALL

    -- Full-text search match (priority 4)
    SELECT accession, title, summary, updated_at, organisms,
           ts_rank(tsv, q.q) AS rank, 'geo'::text AS source, 4 AS priority
    FROM geo_series, q
    WHERE tsv @@ q.q
),
deduped AS (
    SELECT DISTINCT ON (accession)
        accession, title, summary, updated_at, organisms, rank, source
    FROM all_matches
    ORDER BY accession, priority, rank DESC
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
    accession, title, summary, updated_at, organisms, rank, source, total_count
FROM paged
ORDER BY rank DESC, accession DESC
LIMIT 20;
"""


SRA_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
all_matches AS (
    -- Exact SRP/ERP/DRP accession match (priority 1)
    SELECT accession, title, abstract AS summary, updated_at, organisms,
           100.0::float8 AS rank, 'sra'::text AS source, 1 AS priority
    FROM sra_studies
    WHERE UPPER(accession) = UPPER(%s)

    UNION ALL

    -- SRX/ERX/DRX experiment match (priority 2)
    SELECT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,
           100.0::float8 AS rank, 'sra'::text AS source, 2 AS priority
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE UPPER(e.accession) = UPPER(%s)

    UNION ALL

    -- PRJNA alias match (priority 3)
    SELECT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,
           100.0::float8 AS rank, 'sra'::text AS source, 3 AS priority
    FROM sra_studies s
    WHERE UPPER(s.alias) = UPPER(%s)

    UNION ALL

    -- SRS/ERS/DRS sample match (priority 4) - uses GIN index
    SELECT DISTINCT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,
           100.0::float8 AS rank, 'sra'::text AS source, 4 AS priority
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE e.samples @> ARRAY[UPPER(%s)]

    UNION ALL

    -- Full-text search match (priority 5)
    SELECT accession, title, abstract AS summary, updated_at, organisms,
           ts_rank(tsv, q.q) AS rank, 'sra'::text AS source, 5 AS priority
    FROM sra_studies, q
    WHERE tsv @@ q.q
),
deduped AS (
    SELECT DISTINCT ON (accession)
        accession, title, summary, updated_at, organisms, rank, source
    FROM all_matches
    ORDER BY accession, priority, rank DESC
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
    accession, title, summary, updated_at, organisms, rank, source, total_count
FROM paged
ORDER BY rank DESC, accession DESC
LIMIT 20;
"""


SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
all_matches AS (
    -- GEO exact accession match (priority 1)
    SELECT accession, title, summary, updated_at, organisms,
           100.0::float8 AS rank, 'geo'::text AS source, 1 AS priority,
           accession AS dedup_key
    FROM geo_series
    WHERE UPPER(accession) = UPPER(%s)

    UNION ALL

    -- GEO GSM sample match (priority 2) - uses GIN index
    SELECT g.accession, g.title, g.summary, g.updated_at, g.organisms,
           100.0::float8 AS rank, 'geo'::text AS source, 2 AS priority,
           g.accession AS dedup_key
    FROM geo_series g
    WHERE g.samples_ref @> ARRAY[UPPER(%s)]

    UNION ALL

    -- GEO PRJNA bioproject match (priority 3)
    SELECT g.accession, g.title, g.summary, g.updated_at, g.organisms,
           100.0::float8 AS rank, 'geo'::text AS source, 3 AS priority,
           g.accession AS dedup_key
    FROM geo_series g
    WHERE UPPER(g.bioproject_id) = UPPER(%s)

    UNION ALL

    -- SRA exact accession match (priority 4)
    SELECT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,
           100.0::float8 AS rank, 'sra'::text AS source, 4 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_studies s
    WHERE UPPER(s.accession) = UPPER(%s)

    UNION ALL

    -- SRA SRX experiment match (priority 5)
    SELECT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,
           100.0::float8 AS rank, 'sra'::text AS source, 5 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE UPPER(e.accession) = UPPER(%s)

    UNION ALL

    -- SRA PRJNA alias match (priority 6)
    SELECT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,
           100.0::float8 AS rank, 'sra'::text AS source, 6 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_studies s
    WHERE UPPER(s.alias) = UPPER(%s)

    UNION ALL

    -- SRA SRS sample match (priority 7) - uses GIN index
    SELECT DISTINCT ON (s.accession)
           s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,
           100.0::float8 AS rank, 'sra'::text AS source, 7 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE e.samples @> ARRAY[UPPER(%s)]

    UNION ALL

    -- GEO full-text search (priority 8)
    SELECT accession, title, summary, updated_at, organisms,
           ts_rank(tsv, q.q) AS rank, 'geo'::text AS source, 8 AS priority,
           accession AS dedup_key
    FROM geo_series, q
    WHERE tsv @@ q.q

    UNION ALL

    -- SRA full-text search (priority 9, lower rank multiplier)
    SELECT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,
           ts_rank(s.tsv, q.q) * 0.7 AS rank, 'sra'::text AS source, 9 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_studies s, q
    WHERE s.tsv @@ q.q
),
deduped AS (
    SELECT DISTINCT ON (dedup_key)
        accession, title, summary, updated_at, organisms, rank, source
    FROM all_matches
    ORDER BY dedup_key, priority, rank DESC
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
    accession, title, summary, updated_at, organisms, rank, source, total_count
FROM paged
ORDER BY rank DESC, accession DESC
LIMIT 20;
"""


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

    -- Full-text search match (priority 4)
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

    -- Full-text search match (priority 5)
    SELECT accession,
           ts_rank(tsv, q.q) AS rank, 'sra'::text AS source, 5 AS priority
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

    -- GEO full-text search (priority 8)
    SELECT accession,
           ts_rank(tsv, q.q) AS rank, 'geo'::text AS source, 8 AS priority,
           accession AS dedup_key
    FROM geo_series, q
    WHERE tsv @@ q.q
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM updated_at) <= %s::int)

    UNION ALL

    -- SRA full-text search (priority 9, lower rank multiplier)
    SELECT s.accession,
           ts_rank(s.tsv, q.q) * 0.7 AS rank, 'sra'::text AS source, 9 AS priority,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_studies s, q
    WHERE s.tsv @@ q.q
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM s.updated_at) <= %s::int)
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
    alias,
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
    organisms
FROM geo_series
WHERE accession = %s;
"""
PROJECT_SRA_SQL = """
SELECT
    accession,
    alias,
    title,
    abstract,
    submission,
    study_type,
    external_id,
    links,
    published_at,
    updated_at,
    coords_2d,
    coords_3d,
    neighbors,
    organisms
FROM sra_studies
WHERE accession = %s;
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
SRA_SAMPLE_TO_STUDY_SQL = """
SELECT DISTINCT s.accession
FROM sra_experiments e
JOIN sra_studies s ON s.accession = e.study
WHERE e.samples @> ARRAY[UPPER(%s)]
LIMIT 1;
"""

# Get GEO series accession from GSM sample accession
GEO_SAMPLE_TO_SERIES_SQL = """
SELECT accession
FROM geo_series
WHERE samples_ref @> ARRAY[UPPER(%s)]
LIMIT 1;
"""

# Get SRA study accession from experiment accession
SRA_EXPERIMENT_TO_STUDY_SQL = """
SELECT study
FROM sra_experiments
WHERE accession = UPPER(%s)
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
