"""SQL queries for pysradb-server"""

GEO_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
all_matches AS (
    -- Full-text search match (priority 4; includes contributors/departments/organizations in geo_series.tsv)
    SELECT accession, title, summary, updated_at, organisms,
           ts_rank(tsv, q.q) AS rank, 'geo'::text AS source, 4 AS priority
    FROM geo_series, q
    WHERE tsv @@ q.q
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
    -- Full-text search match (priority 6)
    SELECT accession, title, abstract AS summary, updated_at, organisms,
           ts_rank(tsv, q.q) AS rank, 'sra'::text AS source, 6 AS priority
    FROM sra_studies, q
    WHERE tsv @@ q.q
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
    accession, title, summary, updated_at, organisms, rank, source, total_count
FROM paged
ORDER BY rank DESC, accession DESC
LIMIT 20;
"""


AE_SEARCH_SQL = """
WITH q AS (
    SELECT plainto_tsquery('english', %s) AS q
),
all_matches AS (
    -- Full-text search match (priority 4)
    SELECT accession, title, description AS summary, release_date AS updated_at, organisms,
           ts_rank(tsv, q.q) AS rank, 'arrayexpress'::text AS source, 4 AS priority
    FROM arrayexpress_experiments, q
    WHERE tsv @@ q.q
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
    -- GEO full-text search (includes contributors/departments/organizations in geo_series.tsv)
    SELECT accession, title, summary, updated_at, organisms,
           ts_rank(tsv, q.q) AS rank, 'geo'::text AS source,
           accession AS dedup_key
    FROM geo_series, q
    WHERE tsv @@ q.q

    UNION ALL

    -- SRA full-text search (lower rank multiplier)
    SELECT s.accession, s.title, s.abstract AS summary, s.updated_at, s.organisms,
           ts_rank(s.tsv, q.q) * 0.7 AS rank, 'sra'::text AS source,
           COALESCE(s.alias, s.accession) AS dedup_key
    FROM sra_studies s, q
    WHERE s.tsv @@ q.q

    UNION ALL

    -- ArrayExpress full-text search
    SELECT accession, title, description AS summary, release_date AS updated_at, organisms,
           ts_rank(tsv, q.q) * 0.7 AS rank, 'arrayexpress'::text AS source,
           accession AS dedup_key
    FROM arrayexpress_experiments, q
    WHERE tsv @@ q.q
),
deduped AS (
    SELECT DISTINCT ON (dedup_key)
        accession, title, summary, updated_at, organisms, rank, source
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
           accession AS dedup_key
    FROM arrayexpress_experiments
    WHERE UPPER(accession) = UPPER(%s)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM release_date) <= %s::int)

    UNION ALL

    -- ArrayExpress ENA experiment/run match (priority 12)
    SELECT DISTINCT e.accession,
           100.0::float8 AS rank, 'arrayexpress'::text AS source, 12 AS priority,
           e.accession AS dedup_key
    FROM arrayexpress_samples s
    JOIN arrayexpress_experiments e ON e.accession = s.experiment_accession
    WHERE (UPPER(s.ena_experiment) = UPPER(%s) OR UPPER(s.ena_run) = UPPER(%s))
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) <= %s::int)

    UNION ALL

    -- ArrayExpress organization name match (priority 13)
    SELECT DISTINCT e.accession,
           50.0::float8 AS rank, 'arrayexpress'::text AS source, 13 AS priority,
           e.accession AS dedup_key
    FROM arrayexpress_experiments e
    CROSS JOIN LATERAL jsonb_array_elements(e.organizations) AS org
    WHERE org->>'name' ILIKE '%%' || %s || '%%'
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) >= %s::int)
      AND (%s::int IS NULL OR EXTRACT(YEAR FROM e.release_date) <= %s::int)

    UNION ALL

    -- ArrayExpress full-text search (priority 14)
    SELECT accession,
           ts_rank(tsv, q.q) * 0.7 AS rank, 'arrayexpress'::text AS source, 14 AS priority,
           accession AS dedup_key
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
    NULL AS alias,
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


def build_structured_search(
    q=None,
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
):
    """Build SQL + params for structured search across GEO, SRA, ArrayExpress, ENA.

    Returns (sql_string, params_dict) using %(name)s named parameters.
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

    # Populate shared params
    if q:
        params["q"] = q
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

    # --- GEO ---
    if include_geo:
        w = []
        if q:
            w.append("g.tsv @@ plainto_tsquery('english', %(q)s)")
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

        rank = (
            "ts_rank(g.tsv, plainto_tsquery('english', %(q)s))"
            if q
            else "COALESCE(EXTRACT(EPOCH FROM g.published_at), 0)"
        )
        where = (" WHERE " + " AND ".join(w)) if w else ""
        cte_parts.append(
            f"SELECT g.accession, g.title, g.summary,"
            f" g.updated_at, g.organisms,"
            f" {rank} AS rank, 'geo'::text AS source,"
            f" g.accession AS dedup_key"
            f" FROM geo_series g{where}"
        )

    # --- SRA ---
    if include_sra:
        w = []
        if q:
            w.append("s.tsv @@ plainto_tsquery('english', %(q)s)")
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

        rank = (
            "ts_rank(s.tsv, plainto_tsquery('english', %(q)s)) * 0.7"
            if q
            else "COALESCE(EXTRACT(EPOCH FROM s.published_at), 0)"
        )
        where = (" WHERE " + " AND ".join(w)) if w else ""
        cte_parts.append(
            f"SELECT s.accession, s.title, s.abstract AS summary,"
            f" s.updated_at, s.organisms,"
            f" {rank} AS rank, 'sra'::text AS source,"
            f" COALESCE(s.alias, s.accession) AS dedup_key"
            f" FROM sra_studies s{where}"
        )

    # --- ArrayExpress ---
    if include_ae:
        w = []
        if q:
            w.append("ae.tsv @@ plainto_tsquery('english', %(q)s)")
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

        rank = (
            "ts_rank(ae.tsv, plainto_tsquery('english', %(q)s)) * 0.7"
            if q
            else "COALESCE(EXTRACT(EPOCH FROM ae.release_date), 0)"
        )
        where = (" WHERE " + " AND ".join(w)) if w else ""
        cte_parts.append(
            f"SELECT ae.accession, ae.title, ae.description AS summary,"
            f" ae.release_date AS updated_at, ae.organisms,"
            f" {rank} AS rank, 'arrayexpress'::text AS source,"
            f" ae.accession AS dedup_key"
            f" FROM arrayexpress_experiments ae{where}"
        )

    # --- ENA ---
    if include_ena:
        w = []
        ena_tsv = (
            "to_tsvector('english',"
            " coalesce(es.study_title, '') || ' ' ||"
            " coalesce(es.study_description, ''))"
        )
        if q:
            w.append(f"{ena_tsv} @@ plainto_tsquery('english', %(q)s)")
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
        ena_date = "NULLIF(es.first_public, '')::date"
        if year_from is not None:
            w.append(f"{ena_date} >= make_date(%(year_from)s, 1, 1)")
        if year_to is not None:
            w.append(f"{ena_date} <= make_date(%(year_to)s, 12, 31)")

        rank = (
            f"ts_rank({ena_tsv}," f" plainto_tsquery('english', %(q)s)) * 0.7"
            if q
            else f"COALESCE(EXTRACT(EPOCH FROM {ena_date}), 0)"
        )
        where = (" WHERE " + " AND ".join(w)) if w else ""
        cte_parts.append(
            f"SELECT es.study_accession AS accession,"
            f" es.study_title AS title,"
            f" es.study_description AS summary,"
            f" {ena_date} AS updated_at,"
            f" CASE WHEN es.scientific_name IS NOT NULL"
            f" THEN ARRAY[es.scientific_name] END AS organisms,"
            f" {rank} AS rank, 'ena'::text AS source,"
            f" es.study_accession AS dedup_key"
            f" FROM ena_studies es{where}"
        )

    if not cte_parts:
        return (
            "SELECT NULL AS accession, NULL AS title, NULL AS summary,"
            " NULL AS updated_at, NULL::text[] AS organisms,"
            " NULL::float8 AS rank, NULL AS source,"
            " 0::bigint AS total_count WHERE FALSE",
            {},
        )

    all_matches = "\n    UNION ALL\n    ".join(cte_parts)

    if cursor_rank is not None and cursor_acc is not None:
        params["cursor_rank"] = cursor_rank
        params["cursor_acc"] = cursor_acc
        cursor_where = (
            "WHERE (rank, accession) < "
            "(%(cursor_rank)s::float8, %(cursor_acc)s::text)"
        )
    else:
        cursor_where = ""

    sql = f"""\
WITH all_matches AS (
    {all_matches}
),
deduped AS (
    SELECT DISTINCT ON (dedup_key)
        accession, title, summary, updated_at, organisms, rank, source
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
    accession, title, summary, updated_at, organisms, rank, source, total_count
FROM paged
ORDER BY rank DESC, accession DESC
LIMIT 20;"""

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
SELECT accession, link_type, source FROM gse_to_egeod;
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
