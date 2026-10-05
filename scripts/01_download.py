#!/usr/bin/env python3
"""
01_download.py
For each OAM scene id: download the UAV GeoTIFF (URL = 'uuid' from the OAM API), the OSM buildings
(ways + relations) and the Mapillary image points inside the scene. Everything is cached under
data/intermediate/ and re-runs skip finished work.

  python scripts/01_download.py --scene-ids 5ae3ac2d0b093000130aff91
  python scripts/01_download.py --scene-ids ID1 ID2 --skip-raster
  python scripts/01_download.py --package student_02_scenes.gpkg        # every scene in a package
"""

import argparse
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box
from tqdm import tqdm

from utils import pipeline_config as cfg
from utils.mapillary import fetch_points
from utils.oam import download_uav_raster, fetch_scene_meta
from utils.osm import fetch_buildings

MIN_AREA_M2, MAX_AREA_M2 = 15, 20000


def scene_ids_from_package(package: str) -> list[str]:
    path = Path(package)
    if not path.exists():
        path = cfg.PACKAGES_DIR / package
    return gpd.read_file(path)["scene_id"].astype(str).tolist()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene-ids", nargs="*", default=[])
    parser.add_argument("--package", default=None, help="Use every scene of this package (file name in data/input/packages/).")
    parser.add_argument("--skip-raster", action="store_true")
    parser.add_argument("--skip-buildings", action="store_true")
    parser.add_argument("--skip-svi", action="store_true", help="Skip the Mapillary point download.")
    args = parser.parse_args()

    ids = list(dict.fromkeys(args.scene_ids + (scene_ids_from_package(args.package) if args.package else [])))
    if not ids:
        parser.error("give --scene-ids and/or --package")
    if not args.skip_svi and not cfg.MAPILLARY_ACCESS_TOKEN:
        raise SystemExit("MAPILLARY_ACCESS_TOKEN is missing in .env (or use --skip-svi).")
    cfg.ensure_runtime_directories()

    rows = []
    for sid in tqdm(ids, desc="Scenes", unit="scene"):
        meta = fetch_scene_meta(sid)
        bbox = meta["bbox"]
        tqdm.write(f"[{sid}] {meta.get('title', '')}  gsd {float(meta['gsd']) * 100:.1f} cm")

        if not args.skip_raster:
            download_uav_raster(meta["uuid"], cfg.UAV_RASTERS_DIR / f"{sid}.tif")

        buildings_file = cfg.BUILDINGS_DIR / f"{sid}.gpkg"
        if not args.skip_buildings and not buildings_file.exists():
            gdf = fetch_buildings(bbox)
            if len(gdf):
                area = gdf.to_crs(gdf.estimate_utm_crs()).area
                gdf = gdf[(area > MIN_AREA_M2) & (area < MAX_AREA_M2)]
                gdf.to_file(buildings_file, driver="GPKG")
            tqdm.write(f"[{sid}] {len(gdf)} OSM buildings")

        points_file = cfg.MAPILLARY_POINTS_DIR / f"{sid}.gpkg"
        if not args.skip_svi and not points_file.exists():
            points = fetch_points(bbox, cfg.MAPILLARY_ACCESS_TOKEN)
            if len(points):
                points.to_file(points_file, driver="GPKG")
            tqdm.write(f"[{sid}] {len(points)} Mapillary points")

        rows.append({
            "scene_id": sid, "title": meta.get("title", ""), "gsd_cm": round(float(meta["gsd"]) * 100, 3),
            "acquisition_start": (meta.get("acquisition_start") or "")[:10],
            "license": (meta.get("properties") or {}).get("license", ""),
            "download_url": meta["uuid"], "geometry": box(*bbox),
        })

    gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326").to_file(cfg.SELECTED_SCENES_FILE, driver="GPKG")
    print(f"Done. Scene list: {cfg.SELECTED_SCENES_FILE}")


if __name__ == "__main__":
    main()
