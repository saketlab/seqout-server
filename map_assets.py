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


def cluster_at(clusters: dict | None, level: str | None) -> int:
    """Point's cluster id at ``level``, or -1 for noise (layer key absent/null)."""
    if not clusters or level is None:
        return -1
    val = clusters.get(level)
    return int(val) if val is not None else -1


def _write_points_csv(
    rows: list[dict], levels: list[str], csv_path: pathlib.Path
) -> None:
    cols = ["x", "y", "accession", "countries", *levels]
    with open(csv_path, "w") as f:
        f.write(",".join(cols) + "\n")
        for r in rows:
            countries = ";".join(country_names(r.get("countries")))
            clusters = r.get("clusters")
            cids = ",".join(str(cluster_at(clusters, lvl)) for lvl in levels)
            f.write(f"{r['x']},{r['y']},{r['accession']},{countries},{cids}\n")


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
    dictionaries: dict,
    tile_size: int,
) -> None:
    # Cluster layers are dictionary-encoded so deepscatter treats them as
    # categorical (distinct colors). They live in the main tile (small index ints)
    # so they're available the moment a tile loads.
    schema = pa.schema(
        [
            pa.field("x", pa.float32()),
            pa.field("y", pa.float32()),
            pa.field("accession", pa.utf8()),
            pa.field("countries", pa.utf8()),
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
) -> None:
    """Generate countries.json and the tiles/ quadtree into out_dir.

    ``rows`` are the map points ({accession, x, y, countries, clusters}); ``levels``
    are the cluster layers to bake as categorical (dictionary) columns for point
    coloring. out_dir is expected to be a fresh directory (the caller builds into a
    temp dir and atomically renames it into place).
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    countries = sorted({c for r in rows for c in country_names(r.get("countries"))})
    (out_dir / "countries.json").write_text(json.dumps(countries))

    dictionaries = _layer_dictionaries(rows, levels)
    csv_path = out_dir / "points.csv"
    _write_points_csv(rows, levels, csv_path)
    _run_quadfeather(csv_path, out_dir / "tiles", levels, dictionaries, tile_size)
    csv_path.unlink(missing_ok=True)


if __name__ == "__main__":
    # ponytail: self-check the pure logic (translation, noise handling, csv shape)
    # without running the heavy quadfeather tiler.
    import tempfile

    assert cluster_at({"cluster_l4": 2}, "cluster_l4") == 2
    assert cluster_at({"cluster_l4": 2}, "cluster_l0") == -1  # absent layer -> noise
    assert cluster_at({}, "cluster_l4") == -1
    assert cluster_at(None, "cluster_l4") == -1

    names = country_names(["US", "", "----------", None])
    assert len(names) == 1 and "United" in names[0], names

    rows = [
        {"accession": "GSE1", "x": 1.0, "y": 2.0,
         "countries": ["US", "----------"], "clusters": {"cluster_l0": 5, "cluster_l4": 2}},
        {"accession": "SRP2", "x": 3.0, "y": 4.0, "countries": None, "clusters": {}},
    ]
    dicts = _layer_dictionaries(rows, ["cluster_l0", "cluster_l4"])
    assert dicts["cluster_l0"].to_pylist() == ["-1", "5"]  # SRP2 noise(-1) + GSE1's 5
    assert dicts["cluster_l4"].to_pylist() == ["-1", "2"]

    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "p.csv"
        _write_points_csv(rows, ["cluster_l0", "cluster_l4"], p)
        lines = p.read_text().strip().splitlines()
        assert lines[0] == "x,y,accession,countries,cluster_l0,cluster_l4"
        # GSE1: l0=5, l4=2; countries US -> name
        assert lines[1].startswith("1.0,2.0,GSE1,United") and lines[1].endswith(",5,2"), lines[1]
        # SRP2: noise at both layers -> -1, no countries
        assert lines[2] == "3.0,4.0,SRP2,,-1,-1", lines[2]

    print("map_assets self-check OK")
