"""Translation of a data grid's column filters into SQL.

Kept out of main.py so it can be exercised without a database connection —
this is the code that decides which user input reaches a query.
"""

import json

from fastapi import HTTPException

# The grids page their rows in, so a column filter only sees what has loaded.
# The /find endpoints resolve the grid's filter model against the whole study
# instead.
#
# The predicate built here must never be STRICTER than the grid's own filter:
# the grid re-applies the full filter model to whatever comes back, so
# returning a superset is always safe while returning a subset silently drops
# rows. That is why every comparison is case-insensitive (matching AG Grid's
# default) and why unknown columns are skipped rather than rejected — a column
# that only exists client-side just contributes no server-side narrowing.
_FILTER_OPS = ("equals", "contains")
_MAX_FILTERS = 8
_FILTER_VALUE_MAX = 200


def _like_value(op: str, value: str) -> str:
    """Escape LIKE metacharacters so a user's % or _ stays literal."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%" if op == "contains" else escaped


def _indexed_accession(expr: str):
    """Equality against an indexed accession column.

    Deliberately `=` and not ILIKE: run_accession (PK) and experiment_accession
    are plain btrees that only accelerate equality, and a wildcard ILIKE
    seq-scans the whole study — measured at 68s on a 2.7M-run study. Accessions
    are stored uppercase, so the needle is uppercased to keep the comparison
    case-insensitive in practice, matching the grid's own `equals`.

    `contains` is dropped rather than served, since honouring it here is the
    68-second path; the grid only offers `equals` on these columns anyway.
    """

    def build(op: str, value: str):
        if op != "equals":
            return None
        return (f"{expr} = %s", [value.upper()])

    return build


def _jsonb_any_element(container: str, path: str):
    """Match any element of a JSONB array — e.g. any channel of a sample.

    One DB row can explode into several grid rows (GEO samples become one row
    per channel), so "this sample has a channel that matches" is the right
    server-side question; the grid then narrows to the matching rows.
    """

    def build(op: str, value: str) -> tuple[str, list]:
        return (
            f"EXISTS (SELECT 1 FROM jsonb_array_elements("
            f"COALESCE({container}::jsonb, '[]'::jsonb)) e "
            f"WHERE ({path}) ILIKE %s)",
            [_like_value(op, value)],
        )

    return build


def _parse_grid_filters(raw: str, columns: dict) -> tuple[str, list]:
    """Turn a grid filter model into an SQL WHERE fragment plus its params.

    `raw` is the JSON the grid sends: [{"c": field, "o": op, "v": value}, ...].
    `columns` maps a grid field name to either an SQL expression or a callable
    (op, value) -> (clause, params) for the JSONB cases.
    """
    if not raw:
        return "", []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "filters must be JSON")
    if not isinstance(parsed, list):
        raise HTTPException(400, "filters must be a JSON array")

    clauses: list[str] = []
    params: list = []
    for item in parsed[:_MAX_FILTERS]:
        if not isinstance(item, dict):
            continue
        op = item.get("o")
        value = item.get("v")
        if op not in _FILTER_OPS or not isinstance(value, str):
            continue
        value = value.strip()[:_FILTER_VALUE_MAX]
        if not value:
            continue
        expr = columns.get(item.get("c"))
        if expr is None:
            continue
        if callable(expr):
            built = expr(op, value)
            if built is None:  # this column does not serve that operator
                continue
            clause, extra = built
            clauses.append(clause)
            params.extend(extra)
        else:
            clauses.append(f"({expr}) ILIKE %s")
            params.append(_like_value(op, value))
    if not clauses:
        return "", []
    return "\n  AND ".join(clauses), params
