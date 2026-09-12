"""Helper for resolving which SRA-family study resolves a project's
library_strategy data.

"""

from __future__ import annotations

import re

STUDY_ACCESSION_RE = re.compile(r"^(SRP|ERP|DRP)\d+$")


def is_gsa_study(study: str) -> bool:
    return study.upper().startswith(("CRA", "HRA"))


def is_ena_study(study: str) -> bool:
    return study.upper().startswith("PRJ")


def resolve_study_for_library_strategy(study_acc: str | None, row: dict) -> str | None:
    """Which SRA-family study's experiments hold this project's library_strategy.
    """
    if not study_acc:
        return None
    if (
        study_acc.startswith(("SRP", "ERP", "DRP"))
        or is_gsa_study(study_acc)
        or is_ena_study(study_acc)
    ):
        return study_acc
    if study_acc.startswith("G") or study_acc.startswith("E-GEAD-"):
        alias = row.get("alias")
        candidates = alias if isinstance(alias, list) else [alias]
        for candidate in candidates:
            if isinstance(candidate, str) and STUDY_ACCESSION_RE.match(
                candidate.upper()
            ):
                return candidate.upper()
    return None
