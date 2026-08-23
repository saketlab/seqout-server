"""Deep-dive ontology-hierarchy queries for the search page.

Pure Neo4j helpers stay importable before main.py opens Postgres.
Lower only the query parameter so the term_name index stays usable.
"""

from __future__ import annotations

from expansions import extract_graph_terms

# For each query term, count its children (synonym-transparent). Batched over
# all terms in one round-trip. Two WITH steps because Cypher won't let a grouping
# key (start) sit inside the same projection as an aggregation (collect).
_CYPHER_CHILDCOUNTS = """
UNWIND $terms AS term
MATCH (start:Term) WHERE start.name = toLower(term)
OPTIONAL MATCH (start)-[:MAPS_TO*1..2]-(syn:Term)
WITH term, start, collect(DISTINCT syn) AS syns
WITH term, [start] + syns AS cluster
UNWIND cluster AS s
OPTIONAL MATCH (child:Term)-[:SUBCLASS_OF|PART_OF]->(s)
WHERE NOT child IN cluster
RETURN term, count(DISTINCT child) AS child_count
"""

# Direct children of one term (synonym-transparent), capped at 300, each flagged
# with whether it can expand further (has_children, also synonym-transparent via
# the child's own MAPS_TO cluster).
_CYPHER_CHILDREN = """
MATCH (start:Term) WHERE start.name = toLower($term)
OPTIONAL MATCH (start)-[:MAPS_TO*1..2]-(syn:Term)
WITH start, collect(DISTINCT syn) AS syns
WITH [start] + syns AS cluster
UNWIND cluster AS s
MATCH (child:Term)-[:SUBCLASS_OF|PART_OF]->(s)
WHERE NOT child IN cluster
WITH DISTINCT child
ORDER BY child.name
LIMIT 300
// has_children is precomputed once by ingest_has_children.py (synonym-transparent,
// truthful: true iff expanding the node returns >=1 child), so we just read it
// instead of walking each child's synonym cluster at query time. coalesce guards
// nodes ingested after the last precompute run — re-run the script to refresh.
RETURN child.name AS name, coalesce(child.has_children, false) AS has_children
ORDER BY name
"""


# Everything known about one term: its own xrefs, its MAPS_TO synonym cluster
# (each with xrefs), within `hops` hops. Cypher requires a literal
# variable-length bound, so callers MUST pass a validated int (see _HOPS_MAX).
_CYPHER_TERM = """
MATCH (start:Term) WHERE start.name = toLower($term)
OPTIONAL MATCH (start)-[:MAPS_TO*1..%d]-(syn:Term)
WITH start, [s IN collect(DISTINCT syn) WHERE s <> start] AS syns
RETURN start.name AS name,
       coalesce(start.xrefs, []) AS xrefs,
       coalesce(start.has_children, false) AS has_children,
       [s IN syns | {name: s.name, xrefs: coalesce(s.xrefs, [])}][0..%d] AS synonyms,
       size(syns) AS synonym_total
"""

# Direct hierarchy children of the term's synonym cluster. Same shape as
# _CYPHER_CHILDREN but hop-configurable and carrying each child's xrefs.
_CYPHER_TERM_CHILDREN = """
MATCH (start:Term) WHERE start.name = toLower($term)
OPTIONAL MATCH (start)-[:MAPS_TO*1..%d]-(syn:Term)
WITH start, collect(DISTINCT syn) AS syns
WITH [start] + syns AS cluster
UNWIND cluster AS s
MATCH (child:Term)-[:SUBCLASS_OF|PART_OF]->(s)
WHERE NOT child IN cluster
WITH DISTINCT child
ORDER BY child.name
LIMIT %d
RETURN child.name AS name,
       coalesce(child.has_children, false) AS has_children,
       coalesce(child.xrefs, []) AS xrefs
"""

_HOPS_MAX = 4
_SYNONYM_LIMIT = 500
_CHILDREN_LIMIT = 300


def fetch_ontology_term(
    neo4j_driver, term: str, max_hops: int = 2, children: bool = True
) -> dict | None:
    """Everything the graph knows about one term.

    `max_hops` bounds the MAPS_TO synonym walk only (matching what "hops" means
    everywhere else here); hierarchy children are always DIRECT children of the
    resulting synonym cluster, exactly as the deep-dive explorer defines them.

    Returns None if the term is not in the graph, else
    {name, xrefs, has_children, synonyms: [{name, xrefs}], synonym_total,
     children: [{name, has_children, xrefs}] | None, children_truncated}.
    """
    hops = max(1, min(int(max_hops), _HOPS_MAX))
    records, _, _ = neo4j_driver.execute_query(
        _CYPHER_TERM % (hops, _SYNONYM_LIMIT), term=term, database_="neo4j"
    )
    if not records:
        return None
    r = records[0]
    result = {
        "name": r["name"],
        "xrefs": r["xrefs"],
        "has_children": r["has_children"],
        "synonyms": r["synonyms"],
        "synonym_total": r["synonym_total"],
        "children": None,
        "children_truncated": False,
    }
    if children:
        rows, _, _ = neo4j_driver.execute_query(
            _CYPHER_TERM_CHILDREN % (hops, _CHILDREN_LIMIT),
            term=term,
            database_="neo4j",
        )
        result["children"] = [
            {"name": c["name"], "has_children": c["has_children"], "xrefs": c["xrefs"]}
            for c in rows
        ]
        result["children_truncated"] = len(rows) == _CHILDREN_LIMIT
    return result


def fetch_deep_dive_terms(neo4j_driver, query: str) -> list[dict]:
    """Query sub-phrases that exist in the graph AND have hierarchy children.

    Returns [{term, name, child_count}] in query order. `term` is the phrase as
    it appeared in the query (so the frontend can swap it back into the raw
    query); `name` is its normalised graph key. Empty if none qualify.
    """
    pairs = extract_graph_terms(neo4j_driver, query)  # [(phrase, name)]
    if not pairs:
        return []
    names = list(dict.fromkeys(name for _, name in pairs))
    records, _, _ = neo4j_driver.execute_query(
        _CYPHER_CHILDCOUNTS, terms=names, database_="neo4j"
    )
    counts = {r["term"].lower(): r["child_count"] for r in records}
    result: list[dict] = []
    seen: set[str] = set()
    for phrase, name in pairs:  # keep query order, first phrase wins per name
        if name in seen:
            continue
        seen.add(name)
        count = counts.get(name, 0)
        if count > 0:
            result.append({"term": phrase, "name": name, "child_count": count})
    return result


def fetch_deep_dive_children(neo4j_driver, term: str) -> list[dict]:
    """Return the direct children of `term` as [{name, has_children}]."""
    records, _, _ = neo4j_driver.execute_query(
        _CYPHER_CHILDREN, term=term, database_="neo4j"
    )
    return [{"name": r["name"], "has_children": r["has_children"]} for r in records]
