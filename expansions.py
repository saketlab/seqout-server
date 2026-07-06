"""Search query expansion helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable

_MAX_QUERY_VARIANTS = 48


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
OPTIONAL MATCH (start)-[:MAPS_TO*1..2]-(connected:Term)
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


# Split a glued "seq" suffix off a word: atacseq -> atac seq, chipseq -> chip seq.
_SEQ_RE = re.compile(r"\b([a-z0-9]+)(seq)\b", re.IGNORECASE)

# Glue a detached single-cell (sc) / single-nucleus (sn) prefix back onto its
# assay stem, so "sc rna seq" / "sc-rna" resolve to the graph's glued form
# "scrna". Allowlisted stems ONLY — "sc 1" (cell line) and "sc a" (gene) must stay
# untouched, so this can't just glue any "sc <word>". Add a stem here to cover it.
_SC_GLUE_RE = re.compile(
    r"\bsc[-_ ](rna|atac|dna|chip|bs|mt|rb|trio)\b|\bsn[-_ ](rna|mc|drop)\b",
    re.IGNORECASE,
)


def _glue_sc_prefix(text: str) -> str:
    return _SC_GLUE_RE.sub(
        lambda m: "sc" + m.group(1) if m.group(1) else "sn" + m.group(2), text
    )


def _graph_norm(phrase: str) -> str:
    """Normalise a phrase to how Term.name is stored in the graph.

    Term.name is lowercase with -/_/ turned into spaces, and ontologies write
    e.g. "ATAC-seq" as "atac seq". Users may type "atac-seq" or "atacseq" — both
    must resolve to "atac seq". So: split a glued "seq", turn -/_/ into spaces,
    lowercase, glue a detached sc/sn assay prefix, and collapse whitespace.
    """
    p = _SEQ_RE.sub(r"\1 \2", phrase)  # atacseq -> atac seq
    p = re.sub(r"[-_/]", " ", p).lower()  # atac-seq -> atac seq
    p = _glue_sc_prefix(p)  # sc rna seq -> scrna seq
    p = re.sub(r"\s+", " ", p).strip()
    return p


def extract_graph_terms(neo4j_driver, query: str) -> list[tuple[str, str]]:
    """Return (phrase, name) for query sub-phrases that exist as :Term nodes.

    `phrase` is the substring exactly as it appears in the query, so callers can
    swap it back into the raw query. `name` is its graph key (see _graph_norm:
    lowercase, -/_/ -> space, glued "seq" split — "atac-seq"/"atacseq" both ->
    "atac seq"). Longest non-overlapping matches, in query order.

    This is the term-detection step behind the search page's "Enhance search".
    """
    words = query.split()
    if not words:
        return []

    candidates = _generate_candidate_subphrases(
        words
    )  # (phrase, start, end), longest first
    norm_by_phrase = {phrase: _graph_norm(phrase) for phrase, _, _ in candidates}

    existing: set[str] = set()
    records, _, _ = neo4j_driver.execute_query(
        _CYPHER_BATCH_EXISTS,
        candidates=list(dict.fromkeys(norm_by_phrase.values())),
        database_="neo4j",
    )
    for record in records:
        existing.add(record["candidate"].lower())

    # Greedy longest-first, non-overlapping — matching on the normalised name.
    consumed: set[int] = set()
    matched: list[tuple[int, str, str]] = []  # (start, phrase, name)
    for phrase, start, end in candidates:
        positions = set(range(start, end))
        if positions & consumed:
            continue
        name = norm_by_phrase[phrase]
        if name in existing:
            consumed |= positions
            matched.append((start, phrase, name))

    matched.sort(key=lambda m: m[0])  # query order
    return [(phrase, name) for _, phrase, name in matched]


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

    # Glue a detached sc/sn assay prefix ("sc rna seq" -> "scrna seq") as an extra
    # form. Union only — the original stays, so a non-matching glue costs nothing.
    glued_query = _glue_sc_prefix(separated_query)
    if glued_query.lower() not in {q.lower() for q in queries_to_expand}:
        queries_to_expand.append(glued_query)

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


if __name__ == "__main__":
    # ponytail: normalization self-check (no DB needed) — the risky part is the
    # allowlist glue not firing on genes/cell lines.
    assert _glue_sc_prefix("sc rna seq") == "scrna seq"
    assert _glue_sc_prefix("sc-rna") == "scrna"
    assert _glue_sc_prefix("sn rna seq") == "snrna seq"
    assert _glue_sc_prefix("sc atac") == "scatac"
    assert _glue_sc_prefix("sc 1") == "sc 1"  # numeric cell line untouched
    assert _glue_sc_prefix("sc a") == "sc a"  # stem not allowlisted
    assert _glue_sc_prefix("disc rna") == "disc rna"  # no mid-word false match
    assert _graph_norm("SC-RNA-seq") == "scrna seq"
    assert _graph_norm("sc rna") == "scrna"
    assert _graph_norm("sc 1") == "sc 1"
    print("ok")
