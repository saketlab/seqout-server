"""SQL queries for pysradb-server"""

GEO_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
ranked AS (
    SELECT
        accession,
        title,
        summary,
        updated_at,
        ts_rank(tsv, q.q) AS rank,
        'geo'::text AS source
    FROM geo_series, q
    WHERE tsv @@ q.q
),
paged AS (
    SELECT *
    FROM ranked
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    *,
    COUNT(*) OVER () AS total_count
FROM paged
ORDER BY rank DESC, accession DESC
LIMIT 20;
"""


SRA_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
ranked AS (
    SELECT
        accession,
        title,
        abstract AS summary,
        updated_at,
        ts_rank(tsv, q.q) AS rank,
        'sra'::text AS source
    FROM sra_studies, q
    WHERE tsv @@ q.q
),
paged AS (
    SELECT *
    FROM ranked
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    *,
    COUNT(*) OVER () AS total_count
FROM paged
ORDER BY rank DESC, accession DESC
LIMIT 20;
"""



SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
geo AS (
    SELECT
        accession,
        title,
        summary,
        updated_at,
        ts_rank(tsv, q.q) AS rank,
        'geo'::text AS source
    FROM geo_series, q
    WHERE tsv @@ q.q
),
geo_accs AS (
    SELECT accession FROM geo
),
sra AS (
    SELECT
        s.accession,
        s.title,
        s.abstract AS summary,
        s.updated_at,
        ts_rank(s.tsv, q.q) * 0.7 AS rank,
        'sra'::text AS source
    FROM sra_studies s, q
    WHERE s.tsv @@ q.q
      AND NOT EXISTS (
          SELECT 1 FROM geo_accs g WHERE g.accession = s.alias
      )
),
u AS (
    SELECT * FROM geo
    UNION ALL
    SELECT * FROM sra
),
paged AS (
    SELECT *
    FROM u
    WHERE (%s::float8 IS NULL OR (rank, accession) < (%s::float8, %s::text))
)
SELECT
    *,
    COUNT(*) OVER () AS total_count
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