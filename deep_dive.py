"""Deep-dive ontology-hierarchy queries for the search page.

Pure Neo4j helpers with no web framework and no Postgres dependency, so they can
be tested in isolation (the rest of main.py needs a live Postgres to import).
The FastAPI routes in main.py are thin wrappers over these two functions.

Graph shape: (:Term {name}) nodes joined by MAPS_TO (synonym short->long),
SUBCLASS_OF and PART_OF (child->parent). "Children" of a term = everything
pointing at it OR at any of its MAPS_TO synonyms via SUBCLASS_OF/PART_OF, so
synonyms are transparent to the caller. SUBCLASS_OF and PART_OF are merged.
Term.name is stored lowercase, so we only ever toLower() the parameter side
(never t.name) to keep the term_name index usable.
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
