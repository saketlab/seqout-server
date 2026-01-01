"""SQL queries for pysradb-server"""

GEO_SEARCH_SQL = """
WITH q AS (
    SELECT websearch_to_tsquery('english', %s) AS q
)
SELECT
    accession,
    title,
    summary,
    published_at,
    ts_rank(tsv, q.q) AS rank,
    'geo'::text AS source
FROM geo_series, q
WHERE tsv @@ q.q
ORDER BY rank DESC
LIMIT %s OFFSET %s;
"""

SRA_SEARCH_SQL = """
WITH q AS (
    SELECT websearch_to_tsquery('english', %s) AS q
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
    SELECT websearch_to_tsquery('english', %s) AS q
),
geo AS (
    SELECT
        accession,
        title,
        summary,
        published_at,
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
