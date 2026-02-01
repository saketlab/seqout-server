"""SQL queries for pysradb-server"""

GEO_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
exact_match AS (
    SELECT
        accession,
        title,
        summary,
        updated_at,
        organisms,
        100.0::float8 AS rank,
        'geo'::text AS source
    FROM geo_series
    WHERE UPPER(accession) = UPPER(%s)
),
gsm_match AS (
    SELECT
        g.accession,
        g.title,
        g.summary,
        g.updated_at,
        g.organisms,
        100.0::float8 AS rank,
        'geo'::text AS source
    FROM geo_series g
    WHERE EXISTS (
        SELECT 1 FROM unnest(g.samples_ref) AS sample_acc
        WHERE UPPER(sample_acc) = UPPER(%s)
    )
      AND g.accession NOT IN (SELECT accession FROM exact_match)
),
fts_match AS (
    SELECT
        accession,
        title,
        summary,
        updated_at,
        organisms,
        ts_rank(tsv, q.q) AS rank,
        'geo'::text AS source
    FROM geo_series, q
    WHERE tsv @@ q.q
      AND accession NOT IN (SELECT accession FROM exact_match)
      AND accession NOT IN (SELECT accession FROM gsm_match)
),
ranked AS (
    SELECT * FROM exact_match
    UNION ALL
    SELECT * FROM gsm_match
    UNION ALL
    SELECT * FROM fts_match
),
counted AS (
    SELECT *, COUNT(*) OVER () AS total_count FROM ranked
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
exact_match AS (
    SELECT
        accession,
        title,
        abstract AS summary,
        updated_at,
        organisms,
        100.0::float8 AS rank,
        'sra'::text AS source
    FROM sra_studies
    WHERE UPPER(accession) = UPPER(%s)
),
srx_match AS (
    SELECT
        s.accession,
        s.title,
        s.abstract AS summary,
        s.updated_at,
        s.organisms,
        100.0::float8 AS rank,
        'sra'::text AS source
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE UPPER(e.accession) = UPPER(%s)
      AND s.accession NOT IN (SELECT accession FROM exact_match)
),
fts_match AS (
    SELECT
        accession,
        title,
        abstract AS summary,
        updated_at,
        organisms,
        ts_rank(tsv, q.q) AS rank,
        'sra'::text AS source
    FROM sra_studies, q
    WHERE tsv @@ q.q
      AND accession NOT IN (SELECT accession FROM exact_match)
      AND accession NOT IN (SELECT accession FROM srx_match)
),
ranked AS (
    SELECT * FROM exact_match
    UNION ALL
    SELECT * FROM srx_match
    UNION ALL
    SELECT * FROM fts_match
),
counted AS (
    SELECT *, COUNT(*) OVER () AS total_count FROM ranked
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
geo_exact AS (
    SELECT
        accession,
        title,
        summary,
        updated_at,
        organisms,
        100.0::float8 AS rank,
        'geo'::text AS source
    FROM geo_series
    WHERE UPPER(accession) = UPPER(%s)
),
gsm_match AS (
    SELECT
        g.accession,
        g.title,
        g.summary,
        g.updated_at,
        g.organisms,
        100.0::float8 AS rank,
        'geo'::text AS source
    FROM geo_series g
    WHERE EXISTS (
        SELECT 1 FROM unnest(g.samples_ref) AS sample_acc
        WHERE UPPER(sample_acc) = UPPER(%s)
    )
      AND g.accession NOT IN (SELECT accession FROM geo_exact)
),
sra_exact AS (
    SELECT
        s.accession,
        s.title,
        s.abstract AS summary,
        s.updated_at,
        s.organisms,
        100.0::float8 AS rank,
        'sra'::text AS source
    FROM sra_studies s
    WHERE UPPER(s.accession) = UPPER(%s)
      AND NOT EXISTS (
          SELECT 1 FROM geo_exact g WHERE g.accession = s.alias
      )
      AND NOT EXISTS (
          SELECT 1 FROM gsm_match g WHERE g.accession = s.alias
      )
),
srx_match AS (
    SELECT
        s.accession,
        s.title,
        s.abstract AS summary,
        s.updated_at,
        s.organisms,
        100.0::float8 AS rank,
        'sra'::text AS source
    FROM sra_experiments e
    JOIN sra_studies s ON s.accession = e.study
    WHERE UPPER(e.accession) = UPPER(%s)
      AND s.accession NOT IN (SELECT accession FROM sra_exact)
      AND NOT EXISTS (
          SELECT 1 FROM geo_exact g WHERE g.accession = s.alias
      )
      AND NOT EXISTS (
          SELECT 1 FROM gsm_match g WHERE g.accession = s.alias
      )
),
geo AS (
    SELECT
        accession,
        title,
        summary,
        updated_at,
        organisms,
        ts_rank(tsv, q.q) AS rank,
        'geo'::text AS source
    FROM geo_series, q
    WHERE tsv @@ q.q
      AND accession NOT IN (SELECT accession FROM geo_exact)
      AND accession NOT IN (SELECT accession FROM gsm_match)
),
geo_accs AS (
    SELECT accession FROM geo
    UNION
    SELECT accession FROM geo_exact
    UNION
    SELECT accession FROM gsm_match
),
sra AS (
    SELECT
        s.accession,
        s.title,
        s.abstract AS summary,
        s.updated_at,
        s.organisms,
        ts_rank(s.tsv, q.q) * 0.7 AS rank,
        'sra'::text AS source
    FROM sra_studies s, q
    WHERE s.tsv @@ q.q
      AND s.accession NOT IN (SELECT accession FROM sra_exact)
      AND s.accession NOT IN (SELECT accession FROM srx_match)
      AND NOT EXISTS (
          SELECT 1 FROM geo_accs g WHERE g.accession = s.alias
      )
),
u AS (
    SELECT * FROM geo_exact
    UNION ALL
    SELECT * FROM gsm_match
    UNION ALL
    SELECT * FROM sra_exact
    UNION ALL
    SELECT * FROM srx_match
    UNION ALL
    SELECT * FROM geo
    UNION ALL
    SELECT * FROM sra
),
counted AS (
    SELECT *, COUNT(*) OVER () AS total_count FROM u
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


PROJECT_GEO_SQL = """
SELECT
    accession,
    title,
    summary,
    overall_design,
    pubmed_id,
    samples_ref,
    series_type,
    relation,
    supplementary_data,
    published_at,
    updated_at
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
    published_at,
    updated_at
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

BIO_PROJECT_GEO_SERIES_SQL = """
SELECT accession FROM geo_series WHERE bioproject_id = %s;
"""
