"""SQL queries for pysradb-server"""

GEO_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
)
SELECT
    accession,
    title,
    summary,
    updated_at,
    ts_rank(tsv, q.q) AS rank,
    'geo'::text AS source
FROM geo_series, q
WHERE tsv @@ q.q
ORDER BY rank DESC
LIMIT %s OFFSET %s;
"""

SRA_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
)
SELECT
    accession,
    title,
    abstract AS summary,
    NULL::date AS published_at,
    ts_rank(tsv, q.q) AS rank,
    'sra'::text AS source
FROM sra_studies, q
WHERE tsv @@ q.q
ORDER BY rank DESC
LIMIT %s OFFSET %s;
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
        NULL::date AS published_at,
        ts_rank(s.tsv, q.q) * 0.7 AS rank,
        'sra'::text AS source
    FROM sra_studies s, q
    WHERE s.tsv @@ q.q
      AND NOT EXISTS (
          SELECT 1
          FROM geo_accs g
          WHERE g.accession = s.alias
      )
)
SELECT *
FROM (
    SELECT * FROM geo
    UNION ALL
    SELECT * FROM sra
) u
ORDER BY rank DESC
LIMIT %s OFFSET %s;
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
    study_type
FROM sra_studies
WHERE accession = %s;
"""
