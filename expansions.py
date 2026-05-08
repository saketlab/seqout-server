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

TERM_EXPANSION_SQL = """
SELECT short, long
FROM term_expansions
WHERE short IS NOT NULL
  AND long IS NOT NULL
  AND (
    POSITION(LOWER(short) IN LOWER(%s)) > 0
    OR POSITION(LOWER(long) IN LOWER(%s)) > 0
  )
ORDER BY GREATEST(LENGTH(short), LENGTH(long)) DESC
LIMIT %s;
"""

_MAX_COMMON_NAME_ROWS = 100
_MAX_TERM_EXPANSION_ROWS = 100
_MAX_QUERY_VARIANTS = 12


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


def expand_common_names(conn, query: str) -> list[str]:
    """Return query variants with common/scientific organism names swapped."""
    normalized_query = " ".join(query.split())
    if not normalized_query:
        return []

    variants = [normalized_query]
    with conn.cursor() as cur:
        cur.execute(
            COMMON_NAME_EXPANSION_SQL,
            (normalized_query, normalized_query, _MAX_COMMON_NAME_ROWS),
        )
        rows = cur.fetchall()

    for scientific_name, common_name in rows:
        if scientific_name:
            variant = _replace_term(normalized_query, scientific_name, common_name)
            if variant:
                variants.append(variant)
        if common_name:
            variant = _replace_term(normalized_query, common_name, scientific_name)
            if variant:
                variants.append(variant)

    return _dedupe(variants)[:_MAX_QUERY_VARIANTS]


# Term expansions


def expand_terms(conn, query: str) -> list[str]:
    """Return query variants with short/long forms of sequencing terms swapped."""
    normalized_query = " ".join(query.split())
    if not normalized_query:
        return []

    variants = [normalized_query]

    # Separate "seq" suffix if attached to another word
    separated_query = re.sub(r'\b([a-zA-Z0-9]+)(seq)\b', r'\1 \2', normalized_query, flags=re.IGNORECASE)
    separated_query = " ".join(separated_query.split())

    queries_to_expand = [normalized_query]

    if separated_query.lower() != normalized_query.lower():
        # Add the version with just the separated 'seq' without replacement
        variants.append(separated_query)
        queries_to_expand.append(separated_query)

    for q in queries_to_expand:
        with conn.cursor() as cur:
            cur.execute(
                TERM_EXPANSION_SQL,
                (q, q, _MAX_TERM_EXPANSION_ROWS),
            )
            rows = cur.fetchall()

        for short_form, long_form in rows:
            if short_form:
                variant = _replace_term(q, short_form, long_form)
                if variant:
                    variants.append(variant)
            if long_form:
                variant = _replace_term(q, long_form, short_form)
                if variant:
                    variants.append(variant)

    return _dedupe(variants)[:_MAX_QUERY_VARIANTS]
