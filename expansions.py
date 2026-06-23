"""Search query expansion helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable

COMMON_NAME_EXPANSION_SQL = """
SELECT scientific_name, common_name
FROM common_names
WHERE scientific_name IS NOT NULL
  AND common_name IS NOT NULL
  AND (
    POSITION(LOWER(scientific_name) IN LOWER(%s)) > 0
    OR POSITION(LOWER(common_name) IN LOWER(%s)) > 0
  )
ORDER BY GREATEST(LENGTH(scientific_name), LENGTH(common_name)) DESC
LIMIT %s;
"""

_MAX_COMMON_NAME_ROWS = 100
_MAX_QUERY_VARIANTS = 48


def _replace_term(query: str, source: str, target: str) -> str | None:
    pattern = re.compile(rf"(?<!\w){re.escape(source)}(?!\w)", re.IGNORECASE)
    replaced = pattern.sub(target, query)
    if replaced.lower() == query.lower():
        return None
    return " ".join(replaced.split())


def _dedupe(items: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _run_expansion_pass(conn, queries: list[str], sql: str, max_rows: int) -> list[str]:
    new_variants = []
    for q in queries:
        with conn.cursor() as cur:
            cur.execute(sql, (q, q, max_rows))
            rows = cur.fetchall()

        for row in rows:
            col1, col2 = row[0], row[1]
            if col1:
                variant = _replace_term(q, col1, col2)
                if variant:
                    new_variants.append(variant)
            if col2:
                variant = _replace_term(q, col2, col1)
                if variant:
                    new_variants.append(variant)
    return new_variants


def expand_common_names(conn, query: str) -> list[str]:
    """Return query variants with common/scientific organism names swapped."""
    normalized_query = " ".join(query.split())
    if not normalized_query:
        return []

    variants = [normalized_query]

    pass1_variants = _run_expansion_pass(
        conn, [normalized_query], COMMON_NAME_EXPANSION_SQL, _MAX_COMMON_NAME_ROWS
    )
    pass1_variants = _dedupe(pass1_variants)
    variants.extend(pass1_variants)

    if pass1_variants:
        pass2_variants = _run_expansion_pass(
            conn, pass1_variants, COMMON_NAME_EXPANSION_SQL, _MAX_COMMON_NAME_ROWS
        )
        variants.extend(pass2_variants)

    return _dedupe(variants)[:_MAX_QUERY_VARIANTS]


# ---------------------------------------------------------------------------
# Neo4j-based term expansions
# ---------------------------------------------------------------------------

# Cypher: batch-check which candidate sub-phrases exist as Term nodes
# Term.name is already all-lowercase, so toLower() only on the parameter side —
# never on t.name — keeps the term_name index usable instead of a full :Term scan.
_CYPHER_BATCH_EXISTS = """
UNWIND $candidates AS candidate
MATCH (t:Term)
WHERE t.name = toLower(candidate)
RETURN candidate
"""

# Cypher: for each matched term, traverse the entire connected component
_CYPHER_SYNONYM_CLUSTER = """
UNWIND $terms AS term
MATCH (start:Term)
WHERE start.name = toLower(term)
OPTIONAL MATCH (start)-[:MAPS_TO*]-(connected:Term)
RETURN term, collect(DISTINCT connected.name) AS synonyms
"""


def _generate_candidate_subphrases(words: list[str]) -> list[tuple[str, int, int]]:
    """Generate all contiguous sub-phrases ordered by length (longest first).

    Returns a list of (phrase, start_index, end_index) tuples where
    start_index is inclusive and end_index is exclusive.
    """
    n = len(words)
    candidates = []
    for length in range(n, 0, -1):
        for start in range(n - length + 1):
            phrase = " ".join(words[start : start + length])
            candidates.append((phrase, start, start + length))
    return candidates


def _greedy_match(
    candidates: list[tuple[str, int, int]], existing: set[str]
) -> list[tuple[str, int, int]]:
    """Greedily consume word positions with the longest matching candidates.

    Returns the list of matched (phrase, start, end) tuples.
    """
    consumed: set[int] = set()
    matched: list[tuple[str, int, int]] = []
    for phrase, start, end in candidates:
        positions = set(range(start, end))
        if positions & consumed:
            # Some word positions already consumed by a longer match
            continue
        if phrase.lower() in existing:
            consumed |= positions
            matched.append((phrase, start, end))
    return matched


def _cross_multiply_variants(
    words: list[str],
    matched_chunks: list[tuple[str, int, int]],
    synonym_map: dict[str, list[str]],
) -> list[str]:
    """Build query variants by cross-multiplying synonym sets.

    For each matched chunk, we have a set of alternatives (the original +
    all synonyms). Unmatched words stay fixed. We produce the Cartesian
    product across independent chunks, capped at _MAX_QUERY_VARIANTS.
    """
    from itertools import product

    # Build an ordered list of "slots": each slot is either a fixed word
    # or a list of alternatives for a matched chunk.
    consumed: dict[int, int] = {}  # word_index -> chunk_index
    chunk_alternatives: list[list[str]] = []

    for chunk_idx, (phrase, start, end) in enumerate(matched_chunks):
        for pos in range(start, end):
            consumed[pos] = chunk_idx
        alternatives = [phrase]
        for syn in synonym_map.get(phrase.lower(), []):
            if syn.lower() != phrase.lower():
                alternatives.append(syn)
        chunk_alternatives.append(alternatives)

    # Build ordered slots
    slots: list[list[str]] = []
    i = 0
    while i < len(words):
        if i in consumed:
            chunk_idx = consumed[i]
            phrase, start, end = matched_chunks[chunk_idx]
            slots.append(chunk_alternatives[chunk_idx])
            i = end  # skip past the entire chunk
        else:
            slots.append([words[i]])
            i += 1

    # Generate Cartesian product, capped
    variants: list[str] = []
    for combo in product(*slots):
        variants.append(" ".join(combo))
        if len(variants) >= _MAX_QUERY_VARIANTS:
            break

    return variants


def expand_terms(neo4j_driver, query: str) -> list[str]:
    """Return query variants using Neo4j graph-based synonym expansion."""
    normalized_query = " ".join(query.split())
    if not normalized_query:
        return []

    # Separate "seq" suffix if attached to another word
    separated_query = re.sub(
        r"\b([a-zA-Z0-9]+)(seq)\b",
        r"\1 \2",
        normalized_query,
        flags=re.IGNORECASE,
    )
    separated_query = " ".join(separated_query.split())

    queries_to_expand = [normalized_query]
    if separated_query.lower() != normalized_query.lower():
        queries_to_expand.append(separated_query)

    all_variants: list[str] = []

    for q in queries_to_expand:
        words = q.split()
        if not words:
            all_variants.append(q)
            continue

        # Step 1: Generate all contiguous sub-phrase candidates
        candidates = _generate_candidate_subphrases(words)
        candidate_phrases = list(
            dict.fromkeys(phrase.lower() for phrase, _, _ in candidates)
        )

        # Step 2: Batch-check which candidates exist as Term nodes in Neo4j
        existing: set[str] = set()
        records, _, _ = neo4j_driver.execute_query(
            _CYPHER_BATCH_EXISTS,
            candidates=candidate_phrases,
            database_="neo4j",
        )
        for record in records:
            existing.add(record["candidate"].lower())

        # Step 3: Greedy longest-first matching
        matched_chunks = _greedy_match(candidates, existing)

        if not matched_chunks:
            # No term found in the graph; keep the query as-is
            all_variants.append(q)
            continue

        # Step 4: Traverse synonym clusters for matched terms
        matched_terms = [phrase for phrase, _, _ in matched_chunks]
        synonym_map: dict[str, list[str]] = {}

        cypher = _CYPHER_SYNONYM_CLUSTER
        records, _, _ = neo4j_driver.execute_query(
            cypher,
            terms=matched_terms,
            database_="neo4j",
        )
        for record in records:
            term_key = record["term"].lower()
            synonyms = record["synonyms"] or []
            synonym_map[term_key] = synonyms

        # Step 5: Cross-multiply to produce all variant combinations
        chunk_variants = _cross_multiply_variants(words, matched_chunks, synonym_map)
        all_variants.extend(chunk_variants)

    return _dedupe(all_variants)[:_MAX_QUERY_VARIANTS]
