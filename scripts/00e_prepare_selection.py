#!/usr/bin/env python3
"""
00e_prepare_selection.py  (student step 0)
Pick scenes from your student package and download the OSM buildings inside them.
Writes data/selected_scenes.gpkg and data/input/osm_buildings.gpkg.

  python scripts/00e_prepare_selection.py --package student_01_scenes.gpkg --list
  python scripts/00e_prepare_selection.py --package student_01_scenes.gpkg --scene-ids ID1 ID2

--package is a file name inside data/input/packages/ or a full path.
"""

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from utils import pipeline_config as cfg
from utils.osm import fetch_buildings

MIN_AREA_M2, MAX_AREA_M2 = 15, 20000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--package", required=True, help="student_XX_scenes.gpkg")
    parser.add_argument("--scene-ids", nargs="*", default=None, help="Scenes to work on (default: all in the package).")
    parser.add_argument("--list", action="store_true", help="Print the scenes in the package and exit.")
    args = parser.parse_args()

    package = Path(args.package)
    if not package.exists():
        package = cfg.PACKAGES_DIR / args.package
    scenes = gpd.read_file(package)
    scenes["scene_id"] = scenes["scene_id"].astype(str)
    if args.list:
        cols = ["scene_id", "country", "title", "gsd_cm", "area_km2", "valid_los_matches"]
        print(scenes[cols].to_string(index=False))
        return

    if args.scene_ids:
        missing = set(args.scene_ids) - set(scenes["scene_id"])
        if missing:
            raise SystemExit(f"Scene IDs not in {package}: {sorted(missing)}")
        scenes = scenes[scenes["scene_id"].isin(args.scene_ids)]

    cfg.ensure_runtime_directories()
    scenes.to_file(cfg.SELECTED_SCENES_FILE, driver="GPKG")

    parts = []
    for row in scenes.itertuples():
        print(f"Downloading OSM buildings for scene {row.scene_id} ...")
        parts.append(fetch_buildings(row.geometry.bounds))
    buildings = pd.concat(parts, ignore_index=True).drop_duplicates("osm_id")
    gdf = gpd.GeoDataFrame(buildings, geometry="geometry", crs="EPSG:4326")
    area = gdf.to_crs(gdf.estimate_utm_crs()).area
    gdf = gdf[(area > MIN_AREA_M2) & (area < MAX_AREA_M2)]

    out = cfg.INPUT_DIR / "osm_buildings.gpkg"
    gdf.to_file(out, driver="GPKG")
    print(f"{len(scenes)} scene(s) -> {cfg.SELECTED_SCENES_FILE}\n{len(gdf)} buildings -> {out}")


if __name__ == "__main__":
    main()
