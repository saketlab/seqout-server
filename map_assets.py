"""Build deepscatter map assets — quadfeather tiles and countries.json — from the
rows the API loads out of Postgres.

The cluster layers used for coloring are baked as per-point *dictionary*
(categorical) columns so the frontend can color points distinctly by their
cluster at whatever layer matches the current zoom — deepscatter only assigns
distinct colors to dictionary columns, and a shared dictionary keeps a cluster's
color consistent across tiles. Labels are NOT baked here; the frontend fetches
them on demand per viewport/zoom from /map/labels. Heavy deps (pyarrow /
i18n-iso-countries) import with this module, which the server does lazily on the
first map request.
"""

from __future__ import annotations

import csv
import json
import pathlib
import shutil
from functools import lru_cache

import pyarrow as pa
from i18n_iso_countries import get_country_name

from vendor.qf_tiler import main as qf_main

TILE_SIZE = 10_000

# Country codes the i18n-iso-countries library can't decode on its own. Kept
# verbatim from the original pipeline so the generated names match what the
# frontend was validated against.
_SELF_NAMED = {"India", "Turkey", "Russia", "Macau", "Cote d'Ivoire", "Swaziland"}


def _normalize_country_name(country_name: str) -> str:
    # Some countries (e.g. Taiwan -> "Taiwan, Province of China") decode with a
    # comma that breaks CSV encoding; keep only the part before the comma.
    return country_name.strip().split(",")[0]


@lru_cache(maxsize=4096)
def _translate_one(code: str) -> str | None:
    """ISO country code -> display name, or None for junk/undecodable codes."""
    code = (code or "").strip()
    if not code or code == "----------":
        return None
    try:
        name = get_country_name(code=code, language="en")
    except ValueError as e:
        if "country 3 letter code parameters error" in str(e) and code == "EUR":
            name = "Europe"
        elif "parameters error" in str(e) and code in _SELF_NAMED:
            name = code
        else:
            return None
    return _normalize_country_name(name)


def country_names(codes) -> list[str]:
    """Translate a row's ISO country codes to display names, dropping unknowns."""
    out = []
    for code in codes or []:
        name = _translate_one(code)
        if name:
            out.append(name)
    return out


def clean_values(values) -> list[str]:
    """Sanitize a row's facet value(s) into distinct display strings safe for the
    ;-joined tile column: drop empties, strip, and replace the ; list-delimiter
    (and newlines) inside a value so the frontend's split stays correct. Commas
    are fine — the CSV writer quotes them. Accepts a scalar (the dominant value),
    a list, or None."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if v is None:
            continue
        s = str(v).strip().replace(";", ",").replace("\n", " ").replace("\r", " ")
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def cluster_at(clusters: dict | None, level: str | None) -> int:
    """Point's cluster id at ``level``, or -1 for noise (layer key absent/null)."""
    if not clusters or level is None:
        return -1
    val = clusters.get(level)
    return int(val) if val is not None else -1


def _write_points_csv(
    rows: list[dict],
    levels: list[str],
    csv_path: pathlib.Path,
    filter_columns: list[str],
) -> None:
    cols = ["x", "y", "accession", "source", "countries", *filter_columns, *levels]
    # csv.writer (not hand-rolled) because enriched facet values can contain commas
    # / quotes; pyarrow's CSV reader (quadfeather) parses the RFC4180 quoting back.
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            countries = ";".join(country_names(r.get("countries")))
            facets = [";".join(clean_values(r.get(c))) for c in filter_columns]
            clusters = r.get("clusters")
            cids = [str(cluster_at(clusters, lvl)) for lvl in levels]
            source = r.get("source") or ""
            w.writerow(
                [r["x"], r["y"], r["accession"], source, countries, *facets, *cids]
            )


def _layer_dictionaries(rows: list[dict], levels: list[str]) -> dict:
    """Shared (cross-tile) dictionary of cluster-id strings per layer. deepscatter
    only colors distinctly from dictionary (categorical) columns, and a unified
    dictionary keeps a cluster's color consistent across tiles."""
    seen = {lvl: set() for lvl in levels}
    for r in rows:
        clusters = r.get("clusters")
        for lvl in levels:
            seen[lvl].add(cluster_at(clusters, lvl))
    return {
        lvl: pa.array([str(v) for v in sorted(seen[lvl])], type=pa.utf8())
        for lvl in levels
    }


def _run_quadfeather(
    csv_path: pathlib.Path,
    tiles_dir: pathlib.Path,
    levels: list[str],
    filter_columns: list[str],
    dictionaries: dict,
    tile_size: int,
) -> None:
    # Cluster layers are dictionary-encoded so deepscatter treats them as
    # categorical (distinct colors). They live in the main tile (small index ints)
    # so they're available the moment a tile loads. Facet columns are plain utf8
    # (;-joined values) like `countries`, scanned by the frontend's filter.
    schema = pa.schema(
        [
            pa.field("x", pa.float32()),
            pa.field("y", pa.float32()),
            pa.field("accession", pa.utf8()),
            # source (archive) is dictionary-encoded so deepscatter can color by
            # it categorically, exactly like the cluster layers.
            pa.field("source", pa.dictionary(pa.int32(), pa.utf8())),
            pa.field("countries", pa.utf8()),
            *[pa.field(c, pa.utf8()) for c in filter_columns],
            *[pa.field(lvl, pa.dictionary(pa.int32(), pa.utf8())) for lvl in levels],
        ]
    )
    qf_main(
        files=[csv_path],
        destination=tiles_dir,
        tile_size=tile_size,
        schema=schema,
        dictionaries=dictionaries,
    )
    # quadfeather leaves a temp dir next to the csv that it doesn't clean up.
    shutil.rmtree(csv_path.parent / "_deepscatter_tmp", ignore_errors=True)


def build_assets(
    rows: list[dict],
    levels: list[str],
    out_dir: pathlib.Path | str,
    tile_size: int = TILE_SIZE,
    filter_columns: list[str] | None = None,
) -> None:
    """Generate countries.json, the facet filter/<col>.json lists, and the tiles/
    quadtree into out_dir.

    ``rows`` are the map points ({accession, x, y, countries, clusters, <facets>});
    ``levels`` are the cluster layers to bake as categorical (dictionary) columns
    for point coloring; ``filter_columns`` are the enriched facet columns to bake as
    ;-joined utf8 columns (each also gets a distinct-values sidecar). out_dir is
    expected to be a fresh directory (the caller builds into a temp dir and
    atomically renames it into place).
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filter_columns = filter_columns or []

    countries = sorted({c for r in rows for c in country_names(r.get("countries"))})
    (out_dir / "countries.json").write_text(json.dumps(countries))

    if filter_columns:
        filter_dir = out_dir / "filter"
        filter_dir.mkdir(exist_ok=True)
        for col in filter_columns:
            vals = sorted({v for r in rows for v in clean_values(r.get(col))})
            (filter_dir / f"{col}.json").write_text(json.dumps(vals))

    dictionaries = _layer_dictionaries(rows, levels)
    # shared dictionary for the categorical source column (7 archive strings).
    source_vals = sorted({r.get("source") for r in rows if r.get("source")})
    dictionaries["source"] = pa.array(source_vals, type=pa.utf8())
    csv_path = out_dir / "points.csv"
    _write_points_csv(rows, levels, csv_path, filter_columns)
    _run_quadfeather(
        csv_path, out_dir / "tiles", levels, filter_columns, dictionaries, tile_size
    )
    csv_path.unlink(missing_ok=True)


if __name__ == "__main__":
    import tempfile

    assert cluster_at({"cluster_l4": 2}, "cluster_l4") == 2
    assert cluster_at({"cluster_l4": 2}, "cluster_l0") == -1  # absent layer -> noise
    assert cluster_at({}, "cluster_l4") == -1
    assert cluster_at(None, "cluster_l4") == -1

    names = country_names(["US", "", "----------", None])
    assert len(names) == 1 and "United" in names[0], names

    # Facet sanitizing: distinct, empties dropped, ; replaced (commas kept).
    # Accepts a scalar (the per-project dominant value), a list, or None.
    assert clean_values(["RNA-seq", "RNA-seq", "", None, "a;b"]) == ["RNA-seq", "a,b"]
    assert clean_values("brain") == ["brain"]
    assert clean_values(None) == []

    rows = [
        {
            "accession": "GSE1",
            "x": 1.0,
            "y": 2.0,
            "countries": ["US", "----------"],
            "clusters": {"cluster_l0": 5, "cluster_l4": 2},
            "tissue": "brain",
            "assay": "scRNA-seq, 10x",
        },
        {
            "accession": "SRP2",
            "x": 3.0,
            "y": 4.0,
            "countries": None,
            "clusters": {},
            "tissue": None,
            "assay": None,
        },
    ]
    dicts = _layer_dictionaries(rows, ["cluster_l0", "cluster_l4"])
    assert dicts["cluster_l0"].to_pylist() == ["-1", "5"]  # SRP2 noise(-1) + GSE1's 5
    assert dicts["cluster_l4"].to_pylist() == ["-1", "2"]

    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "p.csv"
        _write_points_csv(rows, ["cluster_l0", "cluster_l4"], p, ["tissue", "assay"])
        # Re-parse with a real CSV reader: the assay value's comma must round-trip
        # as one field (it's quoted), not split the row.
        with open(p, newline="") as f:
            parsed = list(csv.reader(f))
        assert parsed[0] == [
            "x",
            "y",
            "accession",
            "countries",
            "tissue",
            "assay",
            "cluster_l0",
            "cluster_l4",
        ], parsed[0]
        assert parsed[1][2] == "GSE1" and parsed[1][4] == "brain"
        assert parsed[1][5] == "scRNA-seq, 10x"  # comma preserved inside the field
        assert parsed[1][6:] == ["5", "2"]
        assert parsed[2][2] == "SRP2" and parsed[2][3] == "" and parsed[2][4] == ""
        assert parsed[2][6:] == ["-1", "-1"]

    print("map_assets self-check OK")
