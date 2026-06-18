"""Build deepscatter map assets — quadfeather tiles and countries.json — from the
rows the API loads out of Postgres.

Each point is colored by its cluster at one representative layer (``color_level``),
baked into the tiles as ``cluster_id``. Labels are NOT baked here; the frontend
fetches them on demand per viewport/zoom from the /map/labels endpoint, so the
heavy all-layers label set never hits the browser at once. Heavy deps (pyarrow /
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
    rows: list[dict], color_level: str | None, csv_path: pathlib.Path
) -> None:
    with open(csv_path, "w") as f:
        f.write("x,y,accession,cluster_id,countries\n")
        for r in rows:
            countries = ";".join(country_names(r.get("countries")))
            cid = cluster_at(r.get("clusters"), color_level)
            f.write(f"{r['x']},{r['y']},{r['accession']},{cid},{countries}\n")


def _run_quadfeather(
    csv_path: pathlib.Path, tiles_dir: pathlib.Path, tile_size: int
) -> None:
    qf_main(
        files=[csv_path],
        destination=tiles_dir,
        tile_size=tile_size,
        schema=pa.schema(
            [
                pa.field("x", pa.float32()),
                pa.field("y", pa.float32()),
                pa.field("accession", pa.utf8()),
                pa.field("cluster_id", pa.int32()),
                pa.field("countries", pa.utf8()),
            ]
        ),
    )
    # quadfeather leaves a temp dir next to the csv that it doesn't clean up.
    shutil.rmtree(csv_path.parent / "_deepscatter_tmp", ignore_errors=True)


def build_assets(
    rows: list[dict],
    color_level: str | None,
    out_dir: pathlib.Path | str,
    tile_size: int = TILE_SIZE,
) -> None:
    """Generate countries.json and the tiles/ quadtree into out_dir.

    ``rows`` are the map points ({accession, x, y, countries, clusters}). out_dir
    is expected to be a fresh directory (the caller builds into a temp dir and
    atomically renames it into place).
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    countries = sorted({c for r in rows for c in country_names(r.get("countries"))})
    (out_dir / "countries.json").write_text(json.dumps(countries))

    csv_path = out_dir / "points.csv"
    _write_points_csv(rows, color_level, csv_path)
    _run_quadfeather(csv_path, out_dir / "tiles", tile_size)
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
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "p.csv"
        _write_points_csv(rows, "cluster_l4", p)
        lines = p.read_text().strip().splitlines()
        assert lines[0] == "x,y,accession,cluster_id,countries"
        assert lines[1].startswith("1.0,2.0,GSE1,2,"), lines[1]
        assert lines[2] == "3.0,4.0,SRP2,-1,", lines[2]  # noise -> -1, no countries

    print("map_assets self-check OK")
