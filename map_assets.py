"""Build deepscatter map assets — quadfeather tiles, geojson.json (cluster
labels), countries.json — from the rows the API loads out of Postgres.

Each point is colored by its cluster at one representative layer (``color_level``);
the geojson carries one label per cluster per layer, its font size scaled by
member count so deepscatter's label-collision reveals coarse clusters when zoomed
out and finer ones as you zoom in (no per-zoom backend work needed). Heavy deps
(pyarrow / i18n-iso-countries) import with this module, which the server does
lazily on the first map request.
"""

from __future__ import annotations

import json
import math
import pathlib
import shutil
from functools import lru_cache

import pyarrow as pa
from i18n_iso_countries import get_country_name

from vendor.qf_tiler import main as qf_main

TILE_SIZE = 10_000

# Font point range for cluster labels. Member counts are log-scaled into this
# range: big (coarse) clusters get large fonts → wider collision boxes → they win
# when zoomed out; small (fine) clusters get small fonts → revealed on zoom-in.
MIN_LABEL_PT = 11.0
MAX_LABEL_PT = 34.0

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


def build_labels_geojson(labels: list[dict], color_level: str | None) -> dict:
    """All cluster labels (every layer) as a GeoJSON FeatureCollection.

    ``size`` is a font point size (log-scaled member count) that drives
    deepscatter's zoom-aware label collision. ``level``/``cluster_id`` let the
    frontend tie a clicked point back to its label at the color layer.
    """
    placed = [
        lbl
        for lbl in labels
        if lbl.get("centroid_x") is not None and lbl.get("centroid_y") is not None
    ]
    sizes = [lbl["size"] for lbl in placed if lbl.get("size")]
    lo = math.log(min(sizes) + 1) if sizes else 0.0
    span = (math.log(max(sizes) + 1) - lo) if sizes else 0.0

    def font_pt(size) -> float:
        if span <= 0:
            return MIN_LABEL_PT
        t = (math.log((size or 1) + 1) - lo) / span
        return round(MIN_LABEL_PT + t * (MAX_LABEL_PT - MIN_LABEL_PT), 2)

    features = []
    max_color_id = 0
    for lbl in placed:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lbl["centroid_x"], lbl["centroid_y"]],
                },
                "properties": {
                    "label": lbl["label"],
                    "size": font_pt(lbl["size"]),
                    "level": lbl["level"],
                    "cluster_id": lbl["cluster_id"],
                },
            }
        )
        if lbl["level"] == color_level:
            max_color_id = max(max_color_id, int(lbl["cluster_id"]))

    return {
        "type": "FeatureCollection",
        "features": features,
        "color_level": color_level,
        "max_cluster_id": max_color_id,
    }


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
    labels: list[dict],
    color_level: str | None,
    out_dir: pathlib.Path | str,
    tile_size: int = TILE_SIZE,
) -> None:
    """Generate countries.json, geojson.json and the tiles/ quadtree into out_dir.

    ``rows`` are the map points ({accession, x, y, countries, clusters}); ``labels``
    are the cluster_labels rows. out_dir is expected to be a fresh directory (the
    caller builds into a temp dir and atomically renames it into place).
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    countries = sorted({c for r in rows for c in country_names(r.get("countries"))})
    (out_dir / "countries.json").write_text(json.dumps(countries))

    geojson = build_labels_geojson(labels, color_level)
    (out_dir / "geojson.json").write_text(json.dumps(geojson))

    csv_path = out_dir / "points.csv"
    _write_points_csv(rows, color_level, csv_path)
    _run_quadfeather(csv_path, out_dir / "tiles", tile_size)
    csv_path.unlink(missing_ok=True)


if __name__ == "__main__":
    # ponytail: self-check the pure logic (translation, geojson scaling, noise
    # handling, csv shape) without running the heavy quadfeather tiler.
    import tempfile

    assert cluster_at({"cluster_l4": 2}, "cluster_l4") == 2
    assert cluster_at({"cluster_l4": 2}, "cluster_l0") == -1  # absent layer -> noise
    assert cluster_at({}, "cluster_l4") == -1
    assert cluster_at(None, "cluster_l4") == -1

    names = country_names(["US", "", "----------", None])
    assert len(names) == 1 and "United" in names[0], names

    labels = [
        {"level": "cluster_l0", "cluster_id": 5, "label": "fine",
         "size": 10, "centroid_x": 1.0, "centroid_y": 2.0},
        {"level": "cluster_l4", "cluster_id": 2, "label": "coarse",
         "size": 100000, "centroid_x": 1.5, "centroid_y": 2.5},
        {"level": "cluster_l4", "cluster_id": 9, "label": "nowhere",
         "size": 5, "centroid_x": None, "centroid_y": None},  # dropped, no centroid
    ]
    gj = build_labels_geojson(labels, "cluster_l4")
    assert gj["color_level"] == "cluster_l4"
    assert gj["max_cluster_id"] == 2  # id 9 dropped (no centroid)
    assert len(gj["features"]) == 2
    by_label = {f["properties"]["label"]: f["properties"]["size"] for f in gj["features"]}
    assert by_label["coarse"] == MAX_LABEL_PT and by_label["fine"] == MIN_LABEL_PT
    assert all(MIN_LABEL_PT <= s <= MAX_LABEL_PT for s in by_label.values())

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
