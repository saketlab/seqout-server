"""Build deepscatter map assets — quadfeather tiles, geojson.json, countries.json
— from the in-memory binary payloads the API already produces.

Ported from saketlab-experiments/pipeline.py, adapted to operate on the bytes
and cluster rows the server already has in memory (no HTTP downloads, no tqdm
progress output). The quadfeather tiler itself is vendored under ``vendor/``.

Heavy deps (pyarrow / numpy / i18n-iso-countries) are only imported when this
module is imported, which the server does lazily on the first map request.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import struct

import numpy as np
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


def parse_accessions(accessions_bytes: bytes) -> dict[str, list[str]]:
    """Decode accessions.bin into {accession: [country_name, ...]}.

    Record format: accession_len(uint32 LE), accession_utf8,
    country_count(uint32 LE), then repeated country_len(uint32 LE), country_utf8.
    """
    accessions: dict[str, list[str]] = {}
    buf = accessions_bytes
    buf_len = len(buf)
    offset = 0

    while offset + 4 <= buf_len:
        length = struct.unpack_from("<I", buf, offset)[0]
        offset += 4
        if offset + length > buf_len:
            break

        accession = buf[offset : offset + length].decode("utf-8")
        offset += length

        countries: list[str] = []

        if offset + 4 <= buf_len:
            num_countries = struct.unpack_from("<I", buf, offset)[0]
            offset += 4
            complete = True

            for _ in range(num_countries):
                if offset + 4 > buf_len:
                    complete = False
                    break
                country_len = struct.unpack_from("<I", buf, offset)[0]
                offset += 4
                if offset + country_len > buf_len:
                    complete = False
                    break

                country_code = (
                    buf[offset : offset + country_len].decode("utf-8").strip()
                )
                offset += country_len
                if len(country_code) == 0:
                    continue

                try:
                    country_name = get_country_name(code=country_code, language="en")
                except ValueError as e:
                    if (
                        "country 3 letter code parameters error" in str(e)
                        and country_code == "EUR"
                    ):
                        country_name = "Europe"
                    elif "parameters error" in str(e) and country_code in _SELF_NAMED:
                        country_name = country_code
                    elif country_code == "----------":
                        continue
                    else:
                        raise

                countries.append(_normalize_country_name(country_name))

            if not complete:
                countries = []
                offset = buf_len

        accessions[accession] = countries

    return accessions


def parse_points(points_bytes: bytes) -> list[tuple[float, float]]:
    """Decode points.bin (packed little-endian float32 x,y pairs)."""
    points: list[tuple[float, float]] = []
    offset = 0
    n = len(points_bytes)
    while offset < n:
        x, y = struct.unpack_from("<ff", points_bytes, offset)
        points.append((x, y))
        offset += 8
    return points


def build_geojson(clusters_rows: list[dict]) -> dict:
    """Cluster rows -> GeoJSON FeatureCollection (centroid + title)."""
    features = []
    for cluster in clusters_rows:
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": list(cluster["centroid"]),
                },
                "properties": {"title": cluster["title"]},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _assign_clusters(
    points: list[tuple[float, float]],
    centroids: list[tuple[float, float]],
    batch_size: int = 10_000,
) -> np.ndarray:
    centroid_arr = np.array(centroids, dtype=np.float32)
    points_arr = np.array(points, dtype=np.float32)
    cluster_ids = np.empty(len(points_arr), dtype=np.int32)

    for start in range(0, len(points_arr), batch_size):
        end = min(start + batch_size, len(points_arr))
        batch = points_arr[start:end]
        diffs = batch[:, None, :] - centroid_arr[None, :, :]
        dists = (diffs**2).sum(axis=2)
        cluster_ids[start:end] = dists.argmin(axis=1)
        del batch, diffs, dists

    return cluster_ids


def _write_points_csv(
    points: list[tuple[float, float]],
    accessions_items: list[tuple[str, list[str]]],
    clusters_rows: list[dict],
    csv_path: pathlib.Path,
) -> None:
    centroids = [(c["centroid"][0], c["centroid"][1]) for c in clusters_rows]
    cluster_ids = _assign_clusters(points, centroids)

    with open(csv_path, "w") as f:
        f.write("x,y,accession,cluster_id,countries\n")
        for i, (x, y) in enumerate(points):
            acc, countries = accessions_items[i]
            country_str = ";".join(countries) if countries else ""
            f.write(f"{x},{y},{acc},{cluster_ids[i]},{country_str}\n")


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
    points_bytes: bytes,
    accessions_bytes: bytes,
    clusters_rows: list[dict],
    out_dir: pathlib.Path | str,
    tile_size: int = TILE_SIZE,
) -> None:
    """Generate countries.json, geojson.json and the tiles/ quadtree into out_dir.

    out_dir is expected to be a fresh directory (the caller builds into a temp
    dir and atomically renames it into place).
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    accessions = parse_accessions(accessions_bytes)

    countries = sorted({c for names in accessions.values() for c in names})
    (out_dir / "countries.json").write_text(json.dumps(countries))

    geojson = build_geojson(clusters_rows)
    (out_dir / "geojson.json").write_text(json.dumps(geojson))

    points = parse_points(points_bytes)
    accessions_items = list(accessions.items())
    csv_path = out_dir / "points.csv"
    _write_points_csv(points, accessions_items, clusters_rows, csv_path)

    _run_quadfeather(csv_path, out_dir / "tiles", tile_size)
    csv_path.unlink(missing_ok=True)
