"""SQL constants + filter/field resolution for the Beacon v2 API (see beacon.py)."""

from __future__ import annotations

import json
import re

from queries import DAYS_PER_YEAR, ONTOLOGY_FIELDS

_PREFIX_TO_FIELDS: dict[str, list[tuple[str, str]]] = {
    "MONDO": [("disease", "mondo")],
    "CL": [("cell_type", "cl")],
    "EFO": [("assay", "efo")],
    "UBERON": [("tissue", "uberon"), ("development_stage", "uberon")],
}

ALPHANUMERIC_FIELDS = {"organism": "organism", "sex": "sex"}


def resolve_filter_id(term_id: str) -> list[tuple[str, str]]:
    """CURIE filter id -> [(field, ontology), ...] (spec §6.5).

    Unknown/unmapped prefix -> [] (the filter then matches nothing, per Beacon).
    Alphanumeric ids (organism/sex) are handled separately by the endpoint.
    """
    if ":" in term_id:
        return _PREFIX_TO_FIELDS.get(term_id.split(":", 1)[0].upper(), [])
    return []


BIOSAMPLE_COLUMNS = (
    "sample, study_accession, organism, taxid, sex, ethnicity, cell_line, "
    "disease, disease_ontology_id, disease_ontology_name, "
    "tissue, tissue_ontology_id, tissue_ontology_name, "
    "cell_type, cell_type_ontology_id, cell_type_ontology_name, "
    "assay, assay_ontology_id, assay_ontology_name, "
    "development_stage, development_stage_ontology_id, development_stage_ontology_name"
)

_SQL_OPS = {
    "=": "=",
    "!": "<>",
    "!=": "<>",
    "<>": "<>",
    ">": ">",
    "<": "<",
    ">=": ">=",
    "<=": "<=",
}

# Age is requested as an "alphanumeric" filter carrying an ISO8601 duration value
# (e.g. EFO:0004847 / PATO:0000011 / "age" with operator ">" value "P70Y"). It
# resolves against the precomputed ontology_samples_v5.age_days (days)
_AGE_IDS = {"EFO:0004847", "PATO:0000011", "AGE"}
_ISO_DUR_RE = re.compile(
    r"^P(?=\d)(?:(\d+(?:\.\d+)?)Y)?(?:(\d+(?:\.\d+)?)M)?"
    r"(?:(\d+(?:\.\d+)?)W)?(?:(\d+(?:\.\d+)?)D)?$"
)
_DAYS_PER_YEAR = DAYS_PER_YEAR
_DAYS_PER_MONTH = DAYS_PER_YEAR / 12
_EMBEDDED_OP_RE = re.compile(r"^(<=|>=|!=|!|=|<|>)\s*(.+)$")


def _iso8601_duration_to_days(value: str) -> float | None:
    """ISO8601 duration (P70Y, P6M, P2W, P10D, P70Y6M) -> days; None if unparseable."""
    m = _ISO_DUR_RE.match(value.strip())
    if not m or not any(m.groups()):
        return None
    y, mo, w, d = (float(g) if g else 0.0 for g in m.groups())
    return round(y * _DAYS_PER_YEAR + mo * _DAYS_PER_MONTH + w * 7 + d, 3)


def _age_clause(f: dict, params: list) -> str:
    """Build an `age_days <op> <days>` clause; FALSE on a bad operator/value."""
    raw = str(f.get("value", "")).strip()
    op_token = f.get("operator")
    m = _EMBEDDED_OP_RE.match(raw)  # allow ">P70Y" as well as a separate operator
    if m and not op_token:
        op_token, raw = m.group(1), m.group(2).strip()
    op = _SQL_OPS.get("!" if op_token == "!=" else (op_token or "="))
    days = _iso8601_duration_to_days(raw)
    if op is None or days is None:
        return "FALSE"
    params.append(days)
    return f"age_days {op} %s"


def build_biosample_where(filters: list[dict]) -> tuple[str, list]:
    """Build the WHERE body + params for biosample/individual filtering"""
    clauses: list[str] = []
    params: list = []
    for f in filters:
        tid = f.get("id", "")
        if tid.upper() in _AGE_IDS:  # numeric age range; before EFO->assay resolution
            clauses.append(_age_clause(f, params))
            continue
        include_desc = f.get("includeDescendantTerms", True)
        fields = resolve_filter_id(tid)
        if fields:  # ontology filter
            ors = []
            for field, onto in fields:
                if include_desc:
                    ors.append(
                        f"{field}_ontology_id = ANY(ARRAY("
                        f"SELECT descendant_id FROM ontology_closure "
                        f"WHERE ontology = %s AND ancestor_id = %s "
                        f"UNION SELECT %s))"
                    )
                    params += [onto, tid, tid]
                else:
                    ors.append(f"{field}_ontology_id = %s")
                    params.append(tid)
            clauses.append("(" + " OR ".join(ors) + ")")
        elif tid.upper().startswith("PMID:"):
            pmid = tid.split(":", 1)[1].strip()
            if pmid.isdigit():
                clauses.append(
                    "study_accession IN (SELECT accession FROM study_publications "
                    "WHERE publications @> %s::jsonb)"
                )
                params.append(json.dumps([{"pmid": pmid}]))
            else:
                clauses.append("FALSE")
        elif tid in ALPHANUMERIC_FIELDS:
            col = ALPHANUMERIC_FIELDS[tid]
            val = f.get("value")
            if val is None:
                clauses.append("FALSE")
            else:
                op = _SQL_OPS.get(f.get("operator", "="), "=")
                if "%" in str(val) and op in ("=", "<>"):
                    like = "LIKE" if op == "=" else "NOT LIKE"
                    clauses.append(f"{col} {like} %s")
                else:
                    clauses.append(f"{col} {op} %s")
                params.append(val)
        else:
            clauses.append("FALSE")  # unknown term -> empty result
    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


if __name__ == "__main__":
    w, p = build_biosample_where([{"id": "MONDO:0004992"}, {"id": "CL:0000624"}])
    assert " AND " in w and w.count("ontology_closure") == 2, w
    assert "= ANY(ARRAY(" in w, w
    assert p == [
        "mondo",
        "MONDO:0004992",
        "MONDO:0004992",
        "cl",
        "CL:0000624",
        "CL:0000624",
    ], p

    w, _ = build_biosample_where(
        [{"id": "UBERON:0002048"}]
    )  # lung -> tissue OR dev_stage
    assert (
        "tissue_ontology_id" in w
        and "development_stage_ontology_id" in w
        and " OR " in w
    ), w

    w, p = build_biosample_where(
        [{"id": "MONDO:0004992", "includeDescendantTerms": False}]
    )
    assert "ontology_closure" not in w and p == ["MONDO:0004992"], (w, p)

    w, p = build_biosample_where([{"id": "sex", "operator": "=", "value": "female"}])
    assert w == "sex = %s" and p == ["female"], (w, p)

    # "!=" has to negate; absent from _SQL_OPS it falls back to "=" and
    # returns the set the caller asked to exclude
    for token in ("!", "!=", "<>"):
        w, p = build_biosample_where(
            [{"id": "sex", "operator": token, "value": "Male"}]
        )
        assert w == "sex <> %s" and p == ["Male"], (token, w, p)

    # % is an alphanumeric wildcard in the spec, so it needs LIKE
    w, p = build_biosample_where(
        [{"id": "organism", "operator": "=", "value": "%sapiens%"}]
    )
    assert w == "organism LIKE %s" and p == ["%sapiens%"], (w, p)
    w, _ = build_biosample_where(
        [{"id": "organism", "operator": "!", "value": "%sapiens%"}]
    )
    assert w == "organism NOT LIKE %s", w

    w, p = build_biosample_where([{"id": "PMID:23125363"}])
    assert "study_publications" in w and p == ['[{"pmid": "23125363"}]'], (w, p)
    w, _ = build_biosample_where([{"id": "PMID:notanumber"}])
    assert w == "FALSE", w

    w, _ = build_biosample_where([{"id": "NCIT:C999999"}])  # unknown prefix
    assert w == "FALSE", w

    w, _ = build_biosample_where([])
    assert w == "TRUE", w

    # age: EFO:0004847 must NOT resolve as an EFO/assay ontology filter
    w, p = build_biosample_where(
        [{"id": "EFO:0004847", "operator": ">", "value": "P70Y"}]
    )
    assert w == "age_days > %s" and abs(p[0] - 70 * 365.25) < 0.01, (w, p)
    w, p = build_biosample_where(
        [{"id": "PATO:0000011", "value": ">P70Y"}]
    )  # embedded op
    assert w == "age_days > %s" and abs(p[0] - 25567.5) < 0.01, (w, p)
    w, p = build_biosample_where([{"id": "age", "operator": "<=", "value": "P6M"}])
    assert w == "age_days <= %s" and abs(p[0] - 6 * (365.25 / 12)) < 0.01, (w, p)
    w, p = build_biosample_where(
        [{"id": "EFO:0004847", "operator": "=", "value": "P70Y6M"}]
    )
    assert (
        w == "age_days = %s" and abs(p[0] - (70 * 365.25 + 6 * 365.25 / 12)) < 0.01
    ), (w, p)
    w, _ = build_biosample_where(
        [{"id": "EFO:0004847", "value": "seventy"}]
    )  # unparseable
    assert w == "FALSE", w
    print("OK: build_biosample_where self-checks pass")
