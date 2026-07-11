"""SQL constants + filter/field resolution for the Beacon v2 API (see beacon.py).

Spec: docs/superpowers/specs/2026-07-10-beacon-v2-metadata-design.md (§5, §6).
"""

from __future__ import annotations

import json

ONTOLOGY_FIELDS: dict[str, str] = {
    "disease": "mondo",
    "tissue": "uberon",
    "cell_type": "cl",
    "assay": "efo",
    "development_stage": "uberon",
}

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


# Columns the biosample mapper reads (spec §5.2).
BIOSAMPLE_COLUMNS = (
    "sample, study_accession, organism, taxid, sex, ethnicity, cell_line, "
    "disease, disease_ontology_id, disease_ontology_name, "
    "tissue, tissue_ontology_id, tissue_ontology_name, "
    "cell_type, cell_type_ontology_id, cell_type_ontology_name, "
    "assay, assay_ontology_id, assay_ontology_name, "
    "development_stage, development_stage_ontology_id, development_stage_ontology_name"
)

_SQL_OPS = {"=": "=", "!": "<>", ">": ">", "<": "<", ">=": ">=", "<=": "<="}


def build_biosample_where(filters: list[dict]) -> tuple[str, list]:
    """Build the WHERE body + params for biosample/individual filtering"""
    clauses: list[str] = []
    params: list = []
    for f in filters:
        tid = f.get("id", "")
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
                clauses.append(f"{col} {op} %s")
                params.append(val)
        else:
            clauses.append("FALSE")  # unknown term -> empty result, not an error
    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


if __name__ == "__main__":
    # self-checks (spec §6.3/§6.5); run: .venv/bin/python beacon_queries.py
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

    w, p = build_biosample_where([{"id": "PMID:23125363"}])
    assert "study_publications" in w and p == ['[{"pmid": "23125363"}]'], (w, p)
    w, _ = build_biosample_where([{"id": "PMID:notanumber"}])
    assert w == "FALSE", w

    w, _ = build_biosample_where([{"id": "NCIT:C999999"}])  # unknown prefix
    assert w == "FALSE", w

    w, _ = build_biosample_where([])
    assert w == "TRUE", w
    print("OK: build_biosample_where self-checks pass")
