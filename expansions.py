"""Search query expansion helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable

_MAX_QUERY_VARIANTS = 100

# Postgres' `english` snowball stop-word list, verbatim. Many of these are also
# HGNC gene aliases ("OF" -> BRIP1, "AN"/"AS"/"AT"/"CAN"/"NOT"/"WAS" -> others), so
# without this guard a query like "single cell atlas of liver" matches "of" as a gene
# and cross-multiplies its 22 synonyms into the variant list ("...atlas brip1 liver").
# plainto_tsquery discards these lexemes anyway, so expanding them is pure cost.
# Must stay in sync with the FTS config used in queries.py ('english').
_STOPWORDS = frozenset(
    """i me my myself we our ours ourselves you your yours yourself yourselves he him his
    himself she her hers herself it its itself they them their theirs themselves what which
    who whom this that these those am is are was were be been being have has had having do
    does did doing a an the and but if or because as until while of at by for with about
    against between into through during before after above below to from up down in out on
    off over under again further then once here there when where why how all any both each
    few more most other some such no nor not only own same so than too very s t can will
    just don should now""".split()
)


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

    Bare stop-words are never candidates (see _STOPWORDS). Multi-word phrases that
    merely contain one still are — "disease or disorder" is a real term.
    """
    n = len(words)
    candidates = []
    for length in range(n, 0, -1):
        for start in range(n - length + 1):
            phrase = " ".join(words[start : start + length])
            if length == 1 and phrase.lower() in _STOPWORDS:
                continue
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
) -> tuple[list[tuple[str, tuple[str, ...]]], list[list[str]]]:
    """Build query variants by cross-multiplying synonym sets.

    For each matched chunk, we have a set of alternatives (the original +
    all synonyms). Unmatched words stay fixed. We produce the Cartesian
    product across independent chunks, capped at _MAX_QUERY_VARIANTS.

    Returns (variants, chunk_alternatives), where each variant is
    (text, chosen alternative per chunk). The per-chunk choice is what lets
    expand_terms_explained() report which synonyms survived the cap.
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

    # Build ordered slots. chunk_slots[chunk_idx] = index of that chunk's slot, so
    # a product combo can be read back as "which alternative did chunk i take".
    slots: list[list[str]] = []
    chunk_slots: list[int] = [0] * len(matched_chunks)
    i = 0
    while i < len(words):
        if i in consumed:
            chunk_idx = consumed[i]
            phrase, start, end = matched_chunks[chunk_idx]
            chunk_slots[chunk_idx] = len(slots)
            slots.append(chunk_alternatives[chunk_idx])
            i = end  # skip past the entire chunk
        else:
            slots.append([words[i]])
            i += 1

    # Generate Cartesian product, capped
    variants: list[tuple[str, tuple[str, ...]]] = []
    for combo in product(*slots):
        variants.append((" ".join(combo), tuple(combo[s] for s in chunk_slots)))
        if len(variants) >= _MAX_QUERY_VARIANTS:
            break

    return variants, chunk_alternatives


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


def _expand_passes(
    neo4j_driver, query: str
) -> tuple[list[tuple[str, int, tuple[str, ...]]], list[list[tuple[str, list[str]]]]]:
    """Run the expansion and keep the bookkeeping the search itself throws away.

    Returns (records, passes):
      records = [(variant_text, pass_index, alternative chosen per chunk)]
      passes  = per pass, [(chunk phrase, its alternatives — original first)]
    A "pass" is one of the (at most two) rewrites of the query we expand: the
    query as normalised, and the seq-separated form when it differs.
    """
    normalized_query = " ".join(query.split())
    if not normalized_query:
        return [], []

    # Glue a detached sc/sn assay prefix up front ("sc rna"/"sc-rna" -> "scrna") so
    # the pieces are never matched as separate terms. Otherwise "sc rna" expands
    # "sc" and "rna" independently and their cross-product buries the real scrna
    # cluster past the variant cap. Allowlisted stems only (see _SC_GLUE_RE).
    normalized_query = _glue_sc_prefix(normalized_query)

    # Separate "seq" suffix if attached to another word
    separated_query = re.sub(
        r"\b([a-zA-Z0-9]+)(seq)\b",
        r"\1 \2",
        normalized_query,
        flags=re.IGNORECASE,
    )
    separated_query = " ".join(separated_query.split())

    # Punctuation at a token boundary is not part of any graph key: "NSCLC," is
    # not the term "nsclc". Candidate sub-phrases inherit the whole token, so a
    # single comma misses at every length and switches expansion off for the
    # entire query. Expanded as an EXTRA pass rather than in place, so a label
    # that owns its comma ("carcinoma, non small cell lung" — 12% of :Term) still
    # anchors as typed; whichever form matches wins, and the two merge on dedupe.
    depunctuated = " ".join(s for w in normalized_query.split() if (s := w.strip(",;")))

    # The query as typed always leads, so variants[0] stays the user's own wording.
    queries_to_expand = [normalized_query]
    for extra in (separated_query, depunctuated):
        if extra and extra.lower() not in {p.lower() for p in queries_to_expand}:
            queries_to_expand.append(extra)

    variant_records: list[tuple[str, int, tuple[str, ...]]] = []
    passes: list[list[tuple[str, list[str]]]] = []

    for pass_idx, q in enumerate(queries_to_expand):
        words = q.split()
        if not words:
            passes.append([])
            variant_records.append((q, pass_idx, ()))
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
            passes.append([])
            variant_records.append((q, pass_idx, ()))
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
        chunk_variants, chunk_alternatives = _cross_multiply_variants(
            words, matched_chunks, synonym_map
        )
        passes.append(
            list(zip([p for p, _, _ in matched_chunks], chunk_alternatives))
        )
        for variant, choices in chunk_variants:
            variant_records.append((variant, pass_idx, choices))

    return variant_records, passes


def expand_terms(neo4j_driver, query: str) -> list[str]:
    """Return query variants using Neo4j graph-based synonym expansion."""
    records, _ = _expand_passes(neo4j_driver, query)
    return _dedupe(v for v, _, _ in records)[:_MAX_QUERY_VARIANTS]


def expand_terms_explained(neo4j_driver, query: str) -> dict:
    """Explain the expansion: which synonyms actually reached the search.

    Same code path as expand_terms(), so what this reports is what ran. Only the
    synonyms that appear in a surviving variant are listed — a chunk whose
    alternatives fell past the variant cap reports fewer `synonyms` than `total`.
    Returns {query, variants, variant_cap, chunks: [{term, synonyms, total}]}.
    """
    records, passes = _expand_passes(neo4j_driver, query)

    # Replay expand_terms' dedupe+cap, but keep each survivor's chunk choices.
    seen: set[str] = set()
    kept: list[tuple[str, int, tuple[str, ...]]] = []
    for variant, pass_idx, choices in records:
        key = variant.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append((variant, pass_idx, choices))
        if len(kept) >= _MAX_QUERY_VARIANTS:
            break

    used: dict[tuple[int, int], set[str]] = {}
    for _, pass_idx, choices in kept:
        for chunk_idx, choice in enumerate(choices):
            used.setdefault((pass_idx, chunk_idx), set()).add(choice.casefold())

    # Merge the two passes on the graph key, so "atacseq" and "atac seq" are one
    # chunk in the output instead of two near-identical ones.
    chunks: dict[str, dict] = {}
    for pass_idx, chunk_list in enumerate(passes):
        for chunk_idx, (phrase, alternatives) in enumerate(chunk_list):
            entry = chunks.setdefault(
                _graph_norm(phrase), {"term": phrase, "synonyms": [], "total": 0}
            )
            survived = used.get((pass_idx, chunk_idx), set())
            have = {s.casefold() for s in entry["synonyms"]}
            for alt in alternatives[1:]:  # [0] is the user's own wording
                if alt.casefold() in survived and alt.casefold() not in have:
                    have.add(alt.casefold())
                    entry["synonyms"].append(alt)
            entry["total"] = max(entry["total"], len(alternatives) - 1)

    return {
        "query": query,
        "variants": [v for v, _, _ in kept],
        "variant_cap": _MAX_QUERY_VARIANTS,
        # total=0 means the term is in the graph but has no synonyms — nothing to show.
        "chunks": [c for c in chunks.values() if c["total"]],
    }


if __name__ == "__main__":
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

    # stop-words must never be candidates on their own ("of" is an HGNC alias for
    # BRIP1), but must still be usable inside a longer phrase.
    phrases = {
        p for p, _, _ in _generate_candidate_subphrases("atlas of liver".split())
    }
    assert "of" not in phrases
    assert "atlas of liver" in phrases and "atlas of" in phrases
    assert "liver" in phrases and "atlas" in phrases

    # Fake graph: nafld has 2 synonyms, liver has 1. Enough to check that
    # expand_terms and expand_terms_explained agree on what reached the search.
    class _FakeDriver:
        vocab = {"nafld": ["masld", "fatty liver"], "liver": ["hepatic"]}

        def execute_query(self, cypher, **kw):
            if "RETURN candidate" in cypher:
                found = [c for c in kw["candidates"] if c.lower() in self.vocab]
                return [{"candidate": c} for c in found], None, None
            return (
                [{"term": t, "synonyms": self.vocab.get(t.lower(), [])} for t in kw["terms"]],
                None,
                None,
            )

    d = _FakeDriver()
    variants = expand_terms(d, "nafld liver")
    assert variants[0] == "nafld liver"  # user's own wording always runs first
    assert len(variants) == 6  # 3 alternatives x 2 alternatives
    exp = expand_terms_explained(d, "nafld liver")
    assert exp["variants"] == variants
    assert [c["term"] for c in exp["chunks"]] == ["nafld", "liver"]
    assert exp["chunks"][0]["synonyms"] == ["masld", "fatty liver"]
    assert exp["chunks"][0]["total"] == 2

    # A comma between two concepts must not switch expansion off: "nafld," is not
    # a graph key, so nothing matched and the query used to run unexpanded.
    commaed = expand_terms(d, "nafld, liver")
    assert commaed[0] == "nafld, liver"  # the query as typed still leads
    assert set(variants) <= set(commaed)  # the depunctuated pass adds the rest
    assert set(variants) <= set(expand_terms(d, "nafld; liver"))
    # Only the token boundary is stripped — punctuation inside a token stays, and
    # a query that is nothing but punctuation adds no pass (and must not crash).
    assert expand_terms(d, "cd4/cd8") == ["cd4/cd8"]
    assert expand_terms(d, " , ; ") == [", ;"]

    # Past the cap, only the synonyms that actually made the cut are reported.
    _MAX_QUERY_VARIANTS = 2
    exp = expand_terms_explained(d, "nafld liver")
    assert len(exp["variants"]) == 2  # nafld liver, nafld hepatic
    assert exp["chunks"][0]["synonyms"] == []  # no nafld synonym reached the search
    assert exp["chunks"][0]["total"] == 2  # ...but there were 2 to be had
    assert exp["chunks"][1]["synonyms"] == ["hepatic"]
    print("ok")
