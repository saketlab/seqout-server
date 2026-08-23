"""Translate a data grid's column filters into SQL."""

import json

from fastapi import HTTPException

_FILTER_OPS = ("equals", "contains")
_MAX_FILTERS = 8
_FILTER_VALUE_MAX = 200


def _like_value(op: str, value: str) -> str:
    """Escape LIKE metacharacters so a user's % or _ stays literal."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%" if op == "contains" else escaped


def _indexed_accession(expr: str):
    """Equality against an indexed accession column."""

    def build(op: str, value: str):
        if op != "equals":
            return None
        return (f"{expr} = %s", [value.upper()])

    return build


def _jsonb_any_element(container: str, path: str):
    """Match any element of a `jsonb[]` column, such as any channel of a sample."""

    def build(op: str, value: str) -> tuple[str, list]:
        return (
            f"EXISTS (SELECT 1 FROM unnest("
            f"COALESCE({container}, '{{}}'::jsonb[])) e "
            f"WHERE ({path}) ILIKE %s)",
            [_like_value(op, value)],
        )

    return build


def _parse_grid_filters(raw: str, columns: dict) -> tuple[str, list]:
    """Turn a grid filter model into an SQL WHERE fragment plus its params."""
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
