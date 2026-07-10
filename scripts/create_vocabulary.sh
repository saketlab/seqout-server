#!/bin/bash
set -e

DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)

echo "Ensuring pg_trgm extension..."
psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"

echo "Creating search_vocabulary table..."
psql "$DATABASE_URL" <<'EOF'
CREATE TABLE IF NOT EXISTS search_vocabulary (
    term        TEXT PRIMARY KEY,
    term_type   TEXT NOT NULL,
    frequency   INT DEFAULT 1,
    normalized  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_vocab_normalized ON search_vocabulary (normalized);
CREATE INDEX IF NOT EXISTS idx_vocab_trgm ON search_vocabulary USING GiST (normalized gist_trgm_ops);

-- Wrap population in transaction for atomicity
BEGIN;
TRUNCATE search_vocabulary;

-- 1. FTS corpus lexemes from geo_series (terms in 50+ documents)
INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT word, 'corpus', ndoc::int, LOWER(word)
FROM ts_stat('SELECT tsv FROM geo_series')
WHERE ndoc > 50 AND LENGTH(word) >= 3
ON CONFLICT (term) DO UPDATE SET frequency = GREATEST(search_vocabulary.frequency, EXCLUDED.frequency);

-- 2. FTS corpus lexemes from sra_studies
INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT word, 'corpus', ndoc::int, LOWER(word)
FROM ts_stat('SELECT tsv FROM sra_studies')
WHERE ndoc > 50 AND LENGTH(word) >= 3
ON CONFLICT (term) DO UPDATE SET frequency = GREATEST(search_vocabulary.frequency, EXCLUDED.frequency);

-- 3. FTS corpus lexemes from arrayexpress_experiments
INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT word, 'corpus', ndoc::int, LOWER(word)
FROM ts_stat('SELECT tsv FROM arrayexpress_experiments')
WHERE ndoc > 50 AND LENGTH(word) >= 3
ON CONFLICT (term) DO UPDATE SET frequency = GREATEST(search_vocabulary.frequency, EXCLUDED.frequency);

-- 3b. FTS corpus lexemes from gsa_studies_search
INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT word, 'corpus', ndoc::int, LOWER(word)
FROM ts_stat('SELECT tsv FROM gsa_studies_search')
WHERE ndoc > 50 AND LENGTH(word) >= 3
ON CONFLICT (term) DO UPDATE SET frequency = GREATEST(search_vocabulary.frequency, EXCLUDED.frequency);

-- 4. Organisms (always include, high value)
INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT organism, 'organism', SUM(count)::int, LOWER(organism)
FROM organism_growth_monthly
WHERE organism IS NOT NULL AND organism != ''
GROUP BY organism
ON CONFLICT (term) DO UPDATE SET
    frequency = GREATEST(search_vocabulary.frequency, EXCLUDED.frequency),
    term_type = CASE WHEN search_vocabulary.term_type = 'corpus' THEN 'organism' ELSE search_vocabulary.term_type END;

-- 5. Common names
INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT common_name, 'common_name', 1, LOWER(common_name)
FROM common_names
WHERE common_name IS NOT NULL AND common_name != ''
ON CONFLICT (term) DO NOTHING;

-- 6. Library strategies (RNA-Seq, ATAC-seq, etc.)
INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT DISTINCT library_strategy, 'assay', COUNT(*)::int, LOWER(library_strategy)
FROM sra_experiments
WHERE library_strategy IS NOT NULL AND library_strategy != ''
GROUP BY library_strategy
ON CONFLICT (term) DO NOTHING;

-- 7. Instrument models
INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT DISTINCT instrument_model, 'instrument_model', COUNT(*)::int, LOWER(instrument_model)
FROM sra_experiments
WHERE instrument_model IS NOT NULL AND instrument_model != ''
GROUP BY instrument_model
ON CONFLICT (term) DO NOTHING;

-- 8. Enriched sample metadata (cell_type, cell_line, tissue, disease, strain)
INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT DISTINCT cell_type, 'cell_type', COUNT(*)::int, LOWER(cell_type)
FROM enriched_samples
WHERE cell_type IS NOT NULL AND cell_type != ''
GROUP BY cell_type
ON CONFLICT (term) DO NOTHING;

INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT DISTINCT cell_line, 'cell_line', COUNT(*)::int, LOWER(cell_line)
FROM enriched_samples
WHERE cell_line IS NOT NULL AND cell_line != '' AND LENGTH(cell_line) <= 200
GROUP BY cell_line
ON CONFLICT (term) DO NOTHING;

INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT DISTINCT tissue, 'tissue', COUNT(*)::int, LOWER(tissue)
FROM enriched_samples
WHERE tissue IS NOT NULL AND tissue != ''
GROUP BY tissue
ON CONFLICT (term) DO NOTHING;

INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT DISTINCT disease, 'disease', COUNT(*)::int, LOWER(disease)
FROM enriched_samples
WHERE disease IS NOT NULL AND disease != ''
GROUP BY disease
ON CONFLICT (term) DO NOTHING;

INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT DISTINCT strain, 'strain', COUNT(*)::int, LOWER(strain)
FROM enriched_samples
WHERE strain IS NOT NULL AND strain != ''
GROUP BY strain
ON CONFLICT (term) DO NOTHING;

INSERT INTO search_vocabulary (term, term_type, frequency, normalized)
SELECT DISTINCT genetic_modification, 'genotype', COUNT(*)::int, LOWER(genetic_modification)
FROM enriched_samples
WHERE genetic_modification IS NOT NULL AND genetic_modification != '' AND LENGTH(genetic_modification) <= 200
GROUP BY genetic_modification
ON CONFLICT (term) DO NOTHING;

COMMIT;

-- Report stats (outside transaction)
SELECT term_type, COUNT(*) AS cnt FROM search_vocabulary GROUP BY term_type ORDER BY cnt DESC;
SELECT COUNT(*) AS total_vocabulary FROM search_vocabulary;
EOF

echo "Done. Vocabulary table populated."
