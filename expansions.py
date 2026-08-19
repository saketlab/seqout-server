from __future__ import annotations

import re
from collections.abc import Iterable

_MAX_QUERY_VARIANTS = 200

# Keep aligned with queries.py english FTS because Postgres stop words overlap HGNC aliases.
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


# Term.name is already lowercase, so lower only the parameter side to keep the term_name index usable.
_CYPHER_BATCH_EXISTS = """
UNWIND $candidates AS candidate
MATCH (t:Term)
WHERE t.name = toLower(candidate)
RETURN candidate
"""

# The eight ontologies actually ingested into the graph (ontology-scripts/GRAPH.md
# "What's ingested"). Everything else a node's xrefs may name -- GO, PATO,
# NCBITaxon, HP, PR -- rides in as an imported class of one of these, so it is
# not a source anyone can switch off.
#
# Provenance lives in Term.xrefs as `PREFIX:id`; Cellosaurus is the exception,
# its ids are bare `CVCL_0030` with no prefix at all.
ONTOLOGIES = (
    ("MONDO", "Diseases (MONDO)"),
    ("MeSH", "MeSH"),
    ("HGNC", "Genes (HGNC)"),
    ("CHEBI", "Chemicals (ChEBI)"),
    ("UBERON", "Anatomy (Uberon)"),
    ("CL", "Cell types (CL)"),
    ("EFO", "Experimental factors (EFO)"),
    ("CVCL", "Cell lines (Cellosaurus)"),
)
ONTOLOGY_IDS = frozenset(o for o, _ in ONTOLOGIES)

# A synonym survives if ANY of its xrefs is from an ontology still switched on --
# nodes are merged by name, so "spinal muscular atrophy" carries HP, MONDO and
# MeSH ids at once and turning MeSH off must not delete the MONDO term. Nodes
# with no xrefs at all (~130k of the synonym-bearing ones) are never dropped:
# nothing attributes them, so no toggle owns them.
#
# Every node the path passes through is tested, not just the one it ends on: a
# disabled hub that still bridges hands back everything behind it. "sma" carries
# MONDO, EFO and HGNC ids, and with those off it used to disappear from the
# results while its 2-hop neighbours -- "superior mesenteric artery", GO's
# "s methyltransferase activity" -- stayed.
_XREF_PREFIX = (
    "CASE WHEN x STARTS WITH 'CVCL_' THEN 'CVCL' ELSE split(x, ':')[0] END"
)
_ENABLED = (
    "size($disabled) = 0"
    " OR size(coalesce(n.xrefs, [])) = 0"
    f" OR size([x IN n.xrefs WHERE NOT ({_XREF_PREFIX}) IN $disabled]) > 0"
)
_CYPHER_SYNONYM_CLUSTER = f"""
UNWIND $terms AS term
MATCH (start:Term)
WHERE start.name = toLower(term)
OPTIONAL MATCH path = (start)-[:MAPS_TO*1..2]-(connected:Term)
WHERE all(n IN tail(nodes(path)) WHERE {_ENABLED})
RETURN term, collect(DISTINCT connected.name) AS synonyms
"""


def _generate_candidate_subphrases(words: list[str]) -> list[tuple[str, int, int]]:
    """Bare stop words are excluded because HGNC aliases make them expensive false terms."""
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
    candidates: list[tuple[str, int, int]],
    existing: set[str],
    norm_by_phrase: dict[str, str],
) -> list[tuple[str, int, int]]:
    """Match by graph key so scRNA-seq can hit stored scrna seq."""
    consumed: set[int] = set()
    matched: list[tuple[str, int, int]] = []
    for phrase, start, end in candidates:
        positions = set(range(start, end))
        if positions & consumed:
            continue
        if norm_by_phrase[phrase] in existing:
            consumed |= positions
            matched.append((phrase, start, end))
    return matched


def _iter_combos(slots: list[list[str]]):
    """Emit single substitutions before combinations so variant caps do not starve chunks."""
    from itertools import combinations, product

    base = tuple(slot[0] for slot in slots)
    swappable = [i for i, slot in enumerate(slots) if len(slot) > 1]
    for k in range(len(swappable) + 1):
        for positions in combinations(swappable, k):
            for choice in product(*(slots[p][1:] for p in positions)):
                swap = dict(zip(positions, choice))
                yield tuple(swap.get(i, word) for i, word in enumerate(base))


def _cross_multiply_variants(
    words: list[str],
    matched_chunks: list[tuple[str, int, int]],
    synonym_map: dict[str, list[str]],
) -> tuple[list[tuple[str, tuple[str, ...]]], list[list[str]]]:
    """Keep per-chunk choices so explanations can report which synonyms survived the cap."""

    consumed: dict[int, int] = {}
    chunk_alternatives: list[list[str]] = []

    for chunk_idx, (phrase, start, end) in enumerate(matched_chunks):
        for pos in range(start, end):
            consumed[pos] = chunk_idx
        # Key by graph key so hyphenated surface forms share the same synonym map.
        alternatives = [phrase]
        for syn in synonym_map.get(_graph_norm(phrase), []):
            if syn.lower() != phrase.lower():
                alternatives.append(syn)
        chunk_alternatives.append(alternatives)

    # chunk_slots maps each matched chunk back to its slot so choices can be reported.
    slots: list[list[str]] = []
    chunk_slots: list[int] = [0] * len(matched_chunks)
    i = 0
    while i < len(words):
        if i in consumed:
            chunk_idx = consumed[i]
            phrase, start, end = matched_chunks[chunk_idx]
            chunk_slots[chunk_idx] = len(slots)
            slots.append(chunk_alternatives[chunk_idx])
            i = end
        else:
            slots.append([words[i]])
            i += 1

    variants: list[tuple[str, tuple[str, ...]]] = []
    for combo in _iter_combos(slots):
        variants.append((" ".join(combo), tuple(combo[s] for s in chunk_slots)))
        if len(variants) >= _MAX_QUERY_VARIANTS:
            break

    return variants, chunk_alternatives


_SEQ_RE = re.compile(r"\b([a-z0-9]+)(seq)\b", re.IGNORECASE)

# Only allowlisted sc/sn assay stems are glued, otherwise cell lines such as "sc 1" and gene-like phrases such as "sc a" mis-normalize.
_SC_GLUE_RE = re.compile(
    r"\bsc[-_ ](rna|atac|dna|chip|bs|mt|rb|trio)\b|\bsn[-_ ](rna|mc|drop)\b",
    re.IGNORECASE,
)


def _glue_sc_prefix(text: str) -> str:
    return _SC_GLUE_RE.sub(
        lambda m: "sc" + m.group(1) if m.group(1) else "sn" + m.group(2), text
    )


def _graph_norm(phrase: str) -> str:
    """Normalize to Term.name storage: lowercase, punctuation to spaces, split seq, and glue allowlisted sc/sn prefixes."""
    p = _SEQ_RE.sub(r"\1 \2", phrase)
    p = re.sub(r"[-_/]", " ", p).lower()
    p = _glue_sc_prefix(p)
    p = re.sub(r"\s+", " ", p).strip()
    return p


def extract_graph_terms(neo4j_driver, query: str) -> list[tuple[str, str]]:
    """Detect terms by graph key while preserving typed phrases for replacement."""
    words = query.split()
    if not words:
        return []

    candidates = _generate_candidate_subphrases(words)
    norm_by_phrase = {phrase: _graph_norm(phrase) for phrase, _, _ in candidates}

    existing: set[str] = set()
    records, _, _ = neo4j_driver.execute_query(
        _CYPHER_BATCH_EXISTS,
        candidates=list(dict.fromkeys(norm_by_phrase.values())),
        database_="neo4j",
    )
    for record in records:
        existing.add(record["candidate"].lower())

    consumed: set[int] = set()
    matched: list[tuple[int, str, str]] = []
    for phrase, start, end in candidates:
        positions = set(range(start, end))
        if positions & consumed:
            continue
        name = norm_by_phrase[phrase]
        if name in existing:
            consumed |= positions
            matched.append((start, phrase, name))

    matched.sort(key=lambda m: m[0])
    return [(phrase, name) for _, phrase, name in matched]


def _normalize_disabled(disabled) -> list[str]:
    """Split the comma-joined param and keep only ids we know; an unknown one
    would silently filter nothing."""
    ids = (part for value in (disabled or ()) for part in str(value).split(","))
    return [o for o in dict.fromkeys(i.strip() for i in ids) if o in ONTOLOGY_IDS]


def _expand_passes(
    neo4j_driver, query: str, disabled=()
) -> tuple[list[tuple[str, int, tuple[str, ...]]], list[list[tuple[str, list[str]]]]]:
    """Run every expansion pass and keep per-chunk choices for explain mode."""
    normalized_query = " ".join(query.split())
    if not normalized_query:
        return [], []

    # Every ontology off means nothing attributable survives. The graph would
    # still hand back the unattributed nodes (nothing names them, so no toggle
    # drops them), which reads as the switches doing nothing -- so skip it and
    # run the query as typed.
    if set(disabled) >= ONTOLOGY_IDS:
        return [(normalized_query, 0, ())], [[]]

    # Detached sc/sn assay prefixes are glued before matching so separate terms cannot bury the real cluster past the variant cap.
    # The stem allowlist prevents cell line or gene-like phrases from being glued.
    normalized_query = _glue_sc_prefix(normalized_query)

    separated_query = re.sub(
        r"\b([a-zA-Z0-9]+)(seq)\b",
        r"\1 \2",
        normalized_query,
        flags=re.IGNORECASE,
    )
    separated_query = " ".join(separated_query.split())

    # Token-boundary punctuation can make every candidate miss; an extra pass keeps comma-owning labels anchored as typed.
    depunctuated = " ".join(s for w in normalized_query.split() if (s := w.strip(",;")))

    # A hyphen inside a token hides the words either side of it: candidates come
    # from whitespace splitting, so "sepsis-associated" is one candidate and only
    # ever matches a graph term spelled exactly that way — "sepsis" is never
    # tried, and expansion silently switches off. An extra pass, not in place, so
    # hyphenated terms the graph does store ("scrna-seq") still anchor as typed.
    dehyphenated = " ".join(normalized_query.replace("-", " ").split())

    # The query as typed always leads, so variants[0] stays the user's own wording.
    queries_to_expand = [normalized_query]
    for extra in (separated_query, depunctuated, dehyphenated):
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

        # Look up candidates by graph key; raw phrase matching misses scrna-seq versus stored scrna seq.
        candidates = _generate_candidate_subphrases(words)
        norm_by_phrase = {phrase: _graph_norm(phrase) for phrase, _, _ in candidates}
        candidate_phrases = list(dict.fromkeys(norm_by_phrase.values()))

        existing: set[str] = set()
        records, _, _ = neo4j_driver.execute_query(
            _CYPHER_BATCH_EXISTS,
            candidates=candidate_phrases,
            database_="neo4j",
        )
        for record in records:
            existing.add(record["candidate"].lower())

        matched_chunks = _greedy_match(candidates, existing, norm_by_phrase)

        if not matched_chunks:
            passes.append([])
            variant_records.append((q, pass_idx, ()))
            continue

        # Synonym clusters are keyed by graph key for the same reason candidates are.
        matched_terms = [norm_by_phrase[phrase] for phrase, _, _ in matched_chunks]
        synonym_map: dict[str, list[str]] = {}

        cypher = _CYPHER_SYNONYM_CLUSTER
        records, _, _ = neo4j_driver.execute_query(
            cypher,
            terms=matched_terms,
            disabled=disabled,
            database_="neo4j",
        )
        for record in records:
            term_key = record["term"].lower()
            synonyms = record["synonyms"] or []
            synonym_map[term_key] = synonyms

        chunk_variants, chunk_alternatives = _cross_multiply_variants(
            words, matched_chunks, synonym_map
        )
        passes.append(list(zip([p for p, _, _ in matched_chunks], chunk_alternatives)))
        for variant, choices in chunk_variants:
            variant_records.append((variant, pass_idx, choices))

    return variant_records, passes


def expand_terms(neo4j_driver, query: str, disabled=()) -> list[str]:
    records, _ = _expand_passes(neo4j_driver, query, _normalize_disabled(disabled))
    return _dedupe(v for v, _, _ in records)[:_MAX_QUERY_VARIANTS]


def expand_terms_explained(neo4j_driver, query: str, disabled=()) -> dict:
    """Report only synonyms that reached surviving variants, using the same path as expand_terms."""
    records, passes = _expand_passes(
        neo4j_driver, query, _normalize_disabled(disabled)
    )

    # Replay expand_terms dedupe and cap while preserving choices for explanation.
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

    # Merge passes by graph key so atacseq and atac seq do not appear as duplicate chunks.
    chunks: dict[str, dict] = {}
    for pass_idx, chunk_list in enumerate(passes):
        for chunk_idx, (phrase, alternatives) in enumerate(chunk_list):
            entry = chunks.setdefault(
                _graph_norm(phrase), {"term": phrase, "synonyms": [], "total": 0}
            )
            survived = used.get((pass_idx, chunk_idx), set())
            have = {s.casefold() for s in entry["synonyms"]}
            for alt in alternatives[1:]:
                if alt.casefold() in survived and alt.casefold() not in have:
                    have.add(alt.casefold())
                    entry["synonyms"].append(alt)
            entry["total"] = max(entry["total"], len(alternatives) - 1)

    return {
        "query": query,
        "variants": [v for v, _, _ in kept],
        "variant_cap": _MAX_QUERY_VARIANTS,
        "chunks": [c for c in chunks.values() if c["total"]],
    }


if __name__ == "__main__":
    assert _glue_sc_prefix("sc rna seq") == "scrna seq"
    assert _glue_sc_prefix("sc-rna") == "scrna"
    assert _glue_sc_prefix("sn rna seq") == "snrna seq"
    assert _glue_sc_prefix("sc atac") == "scatac"
    assert _glue_sc_prefix("sc 1") == "sc 1"
    assert _glue_sc_prefix("sc a") == "sc a"
    assert _glue_sc_prefix("disc rna") == "disc rna"
    assert _graph_norm("SC-RNA-seq") == "scrna seq"
    assert _graph_norm("sc rna") == "scrna"
    assert _graph_norm("sc 1") == "sc 1"

    # Stop words must never stand alone because terms such as OF are HGNC aliases, but phrases may contain them.
    phrases = {
        p for p, _, _ in _generate_candidate_subphrases("atlas of liver".split())
    }
    assert "of" not in phrases
    assert "atlas of liver" in phrases and "atlas of" in phrases
    assert "liver" in phrases and "atlas" in phrases

    class _FakeDriver:
        vocab = {"nafld": ["masld", "fatty liver"], "liver": ["hepatic"]}

        def execute_query(self, cypher, **kw):
            if "RETURN candidate" in cypher:
                found = [c for c in kw["candidates"] if c.lower() in self.vocab]
                return [{"candidate": c} for c in found], None, None
            return (
                [
                    {"term": t, "synonyms": self.vocab.get(t.lower(), [])}
                    for t in kw["terms"]
                ],
                None,
                None,
            )

    d = _FakeDriver()
    variants = expand_terms(d, "nafld liver")
    assert variants[0] == "nafld liver"
    assert len(variants) == 6
    exp = expand_terms_explained(d, "nafld liver")
    assert exp["variants"] == variants
    assert [c["term"] for c in exp["chunks"]] == ["nafld", "liver"]
    assert exp["chunks"][0]["synonyms"] == ["masld", "fatty liver"]
    assert exp["chunks"][0]["total"] == 2

    # Boundary punctuation must not make every candidate miss and disable expansion.
    commaed = expand_terms(d, "nafld, liver")
    assert commaed[0] == "nafld, liver"
    assert set(variants) <= set(commaed)
    assert set(variants) <= set(expand_terms(d, "nafld; liver"))
    # Only token-boundary punctuation is stripped; punctuation-only queries must not add an empty pass.
    assert expand_terms(d, "cd4/cd8") == ["cd4/cd8"]
    assert expand_terms(d, " , ; ") == [", ;"]

    # A hyphen joining a graph term to a plain word must not switch expansion off:
    # "nafld-associated" is one whitespace token, so "nafld" was never a candidate.
    hyphenated = expand_terms(d, "nafld-associated")
    assert hyphenated[0] == "nafld-associated"  # the query as typed still leads
    assert "masld associated" in hyphenated

    # Candidates are looked up by graph key, so every surface form of an assay
    # reaches the same node. Regression: the search path used to match on the raw
    # phrase, so "scrna-seq" missed the stored "scrna seq" and that chunk was
    # silently left unexpanded while extract_graph_terms still reported it found.
    d.vocab["scrna seq"] = ["single cell rna sequencing"]
    for form in ("scrna seq", "scrna-seq", "scrnaseq", "scRNA-seq"):
        got = expand_terms(d, f"nafld {form}")
        assert got[0] == f"nafld {form}", form
        assert any("single cell rna sequencing" in v for v in got), form
        assert any(v.startswith("masld ") for v in got), form

    # Fewest substitutions first prevents variant caps from starving any chunk.
    assert variants[:4] == [
        "nafld liver",
        "masld liver",
        "fatty liver liver",
        "nafld hepatic",
    ]
    assert variants[4:] == ["masld hepatic", "fatty liver hepatic"]

    # The param arrives as one comma-joined value.
    assert _normalize_disabled(["MeSH,MONDO"]) == ["MeSH", "MONDO"]
    assert _normalize_disabled(["MeSH, MONDO, nope"]) == ["MeSH", "MONDO"]

    # Switching every ontology off is no expansion at all, not expansion through
    # whatever the toggles cannot reach.
    assert expand_terms(d, "nafld liver", [o for o, _ in ONTOLOGIES]) == ["nafld liver"]
    assert expand_terms_explained(d, "nafld liver", ONTOLOGY_IDS)["chunks"] == []

    # Explanation must report capped survivors without starving earlier chunks.
    _MAX_QUERY_VARIANTS = 4
    exp = expand_terms_explained(d, "nafld liver")
    assert len(exp["variants"]) == 4
    assert exp["chunks"][0]["synonyms"] == ["masld", "fatty liver"]
    assert exp["chunks"][0]["total"] == 2
    assert exp["chunks"][1]["synonyms"] == ["hepatic"]
    assert exp["chunks"][1]["total"] == 1
    print("ok")
