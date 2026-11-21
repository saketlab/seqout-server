"""Accession patterns shared by the routes and the MCP layer."""

import re

_STUDY_PREFIX = r"[SED]RP"
_EXPERIMENT_PREFIX = r"[SED]RX"
_SAMPLE_PREFIX = r"[SED]RS"
_RUN_PREFIX = r"[SED]RR"

STUDY_PATTERN = re.compile(rf"^({_STUDY_PREFIX})\d+$", re.IGNORECASE)
SAMPLE_PATTERN = re.compile(rf"^({_SAMPLE_PREFIX})\d+$", re.IGNORECASE)
EXPERIMENT_PATTERN = re.compile(rf"^({_EXPERIMENT_PREFIX})\d+$", re.IGNORECASE)
RUN_PATTERN = re.compile(rf"^({_RUN_PREFIX})\d+$", re.IGNORECASE)
GEO_SERIES_PATTERN = re.compile(r"^GSE\d+$", re.IGNORECASE)
GEO_SAMPLE_PATTERN = re.compile(r"^GSM\d+$", re.IGNORECASE)
BIOSAMPLE_PATTERN = re.compile(r"^SAM[A-Z]+\d+$", re.IGNORECASE)
AE_PATTERN = re.compile(r"^E-[A-Z]{4}-\d+$", re.IGNORECASE)
# "GEAD" is four letters, so E-GEAD-N also matches AE_PATTERN: always test GEA first.
_GEA_PREFIX = r"[Ee]-[Gg][Ee][Aa][Dd]-"
GEA_PATTERN = re.compile(rf"^{_GEA_PREFIX}\d+$")
PRJ_PATTERN = re.compile(r"^PRJ[A-Z]+\d+$", re.IGNORECASE)
# GSA (CNCB-NGDC) accessions; PRJCA/SAMC already match PRJ/BIOSAMPLE above.
GSA_STUDY_PATTERN = re.compile(r"^(CRA|HRA)\d+$", re.IGNORECASE)
GSA_RUN_PATTERN = re.compile(r"^(CRR|HRR)\d+$", re.IGNORECASE)
GSA_EXPERIMENT_PATTERN = re.compile(r"^(CRX|HRX)\d+$", re.IGNORECASE)
GSA_SAMPLE_PATTERN = re.compile(r"^HRS\d+$", re.IGNORECASE)
# CRA/HRA are GSA study accessions; they turn up as similarity neighbours, so a
# project lookup has to accept them like any other archive's project accession.
PROJECT_PATTERN = re.compile(
    rf"^(GSE|{_STUDY_PREFIX}|CRA|HRA|[Ee]-[A-Za-z]{{4}}-)\d+$", re.IGNORECASE
)
BULK_PATTERN = re.compile(
    rf"^(GSE|{_STUDY_PREFIX}|PRJNA|[Ee]-[A-Za-z]{{4}}-)\d+$", re.IGNORECASE
)

STUDY_REGEX = rf"^{_STUDY_PREFIX}\d+$"
RUN_REGEX = rf"^{_RUN_PREFIX}\d+$"
SAMPLE_REGEX = rf"^{_SAMPLE_PREFIX}\d+$"
EXPERIMENT_REGEX = rf"^({_EXPERIMENT_PREFIX}|CRX|HRX)\d+$"
GEO_SAMPLE_REGEX = r"^GSM\d+$"
PRJ_REGEX = r"^PRJ[A-Z]+\d+$"
# SRA/ENA/DDBJ submission accession (SRA######, ERA######, DRA######).
SUBMISSION_REGEX = r"^[SED]RA\d+$"
PROJECT_REGEX = rf"^(GSE|{_STUDY_PREFIX}|PRJ[A-Z]+|CRA|HRA|[Ee]-[A-Za-z]{{4}}-)\d+$"
STUDY_EXPERIMENTS_REGEX = rf"^({_STUDY_PREFIX}|PRJ[A-Z]+|CRA|HRA|{_GEA_PREFIX})\d+$"
SAMPLE_OR_ENA_REGEX = rf"^({_SAMPLE_PREFIX}|SAM[A-Z]+|HRS)\d+$"
GEO_SERIES_REGEX = r"^GSE\d+$"
AE_REGEX = r"^[Ee]-[A-Za-z]{4}-\d+$"
METADATA_ENTITY_REGEX = rf"^(GSE|{_STUDY_PREFIX}|{_EXPERIMENT_PREFIX}|{_RUN_PREFIX}|PRJ[A-Z]+|CRA|HRA|CRX|HRX|[Ee]-[A-Za-z]{{4}}-)\d+$"
