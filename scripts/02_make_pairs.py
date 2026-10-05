#!/usr/bin/env python3
"""
02_make_pairs.py
Builds the UAV/SVI image pairs for the scenes downloaded by 01_download.py.

For every selected building:
  * UAV chip: raster cropped around the building and masked to its footprint
  * SVI chip: nearest Mapillary image with a clear line of sight to the building; panoramas are cut to the
    half that faces the building (from the camera compass angle), other images are used as they are

Which buildings: by default every building that has a valid line of sight to a camera. Narrow it with
--osm-ids, --osm-ids-csv (column osm_id, e.g. exported from QGIS) or --limit.

  python scripts/02_make_pairs.py
  python scripts/02_make_pairs.py --scene-ids 5ae3ac2d0b093000130aff91 --limit 40
  python scripts/02_make_pairs.py --osm-ids-csv my_buildings.csv
"""

import argparse

import cv2
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform_bounds
from shapely.geometry import LineString, box
from tqdm import tqdm

from utils import pipeline_config as cfg
from utils.image_processing import mask_uav_building_chip, raster_to_png_array
from utils.mapillary import calculate_bearing, download_image


def load_cached(folder, key):
    parts = [gpd.read_file(p) for p in sorted(folder.glob("*.gpkg"))]
    parts = [p for p in parts if len(p)]
    if not parts:
        return None
    gdf = pd.concat(parts, ignore_index=True)
    return gpd.GeoDataFrame(gdf.drop_duplicates(key), geometry="geometry", crs="EPSG:4326")


def angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def find_match(row, points, point_index, all_buildings, building_index, radius, fov, pano_only=False):
    """First camera within `radius` m that faces the building (non-pano) and is not blocked by another building."""
    centroid = row.geometry.centroid
    near = points.iloc[list(point_index.query(centroid.buffer(radius), predicate="intersects"))]
    if near.empty:
        return None
    near = near.assign(distance_m=near.geometry.distance(centroid)).sort_values("distance_m")
    cam_wgs = near.to_crs(4326).geometry
    cen_wgs = gpd.GeoSeries([centroid], crs=points.crs).to_crs(4326).iloc[0]
    for (idx, cam), cam_ll in zip(near.iterrows(), cam_wgs):
        if cam.distance_m > radius or (pano_only and not cam["is_pano"]):
            continue
        bearing = calculate_bearing(cam_ll.y, cam_ll.x, cen_wgs.y, cen_wgs.x)
        heading = cam["heading"]
        relative = (bearing - (0 if pd.isna(heading) else heading)) % 360
        if not cam["is_pano"] and (pd.isna(heading) or angle_diff(bearing, heading) > fov):
            continue
        line = LineString([cam.geometry, centroid])
        blockers = all_buildings.iloc[list(building_index.intersection(line.bounds))]
        blockers = blockers[blockers["osm_id"] != row.osm_id]
        if blockers.intersects(line).any():
            continue
        return {"mapillary_id": cam["id"], "distance_m": round(float(cam.distance_m), 1), "bearing": round(bearing, 1),
                "relative_angle": round(relative, 1), "is_pano": bool(cam["is_pano"])}
    return None


def crop_svi(image: np.ndarray, match: dict) -> np.ndarray:
    if not match["is_pano"]:
        return image
    width = image.shape[1]
    target_x = (width / 2 + match["relative_angle"] * width / 360) % width
    return image[:, : width // 2] if target_x < width / 2 else image[:, width // 2:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene-ids", nargs="*", default=None, help="Default: all scenes in data/selected_scenes.gpkg.")
    parser.add_argument("--osm-ids", nargs="*", default=None)
    parser.add_argument("--osm-ids-csv", default=None)
    parser.add_argument("--manual-pairs", default=None, help="CSV osm_id,mapillary_id (QGIS plugin selected_pairs.csv); overrides the automatic match.")
    parser.add_argument("--only-manual", action="store_true", help="Only build pairs for buildings in --manual-pairs.")
    parser.add_argument("--limit", type=int, default=None, help="Random sample of N valid pairs per run (seed 0).")
    parser.add_argument("--search-radius", type=float, default=cfg.DEFAULT_SEARCH_RADIUS_METERS)
    parser.add_argument("--fov", type=float, default=50.0, help="Max angle between camera heading and building (non-panorama images).")
    parser.add_argument("--pano-only", action="store_true", help="Only use 360 panoramas (is_pano true); run without it later to add ordinary photos.")
    parser.add_argument("--padding-factor", type=float, default=1.25)
    args = parser.parse_args()

    cfg.ensure_runtime_directories()
    scenes = gpd.read_file(cfg.SELECTED_SCENES_FILE)
    scenes["scene_id"] = scenes["scene_id"].astype(str)
    if args.scene_ids:
        scenes = scenes[scenes["scene_id"].isin(args.scene_ids)]
    buildings = load_cached(cfg.BUILDINGS_DIR, "osm_id")
    points = load_cached(cfg.MAPILLARY_POINTS_DIR, "id")
    if buildings is None or points is None:
        raise SystemExit("No buildings or Mapillary points found; run 01_download.py first.")
    buildings["osm_id"] = buildings["osm_id"].astype(str)

    wanted = set(args.osm_ids or [])
    if args.osm_ids_csv:
        wanted |= set(pd.read_csv(args.osm_ids_csv, dtype=str)["osm_id"])
    manual = {}
    if args.manual_pairs:
        pairs = pd.read_csv(args.manual_pairs, dtype=str, keep_default_na=False)
        manual = dict(zip(pairs["osm_id"], pairs["mapillary_id"]))
        if args.only_manual:
            wanted |= set(manual)

    utm = buildings.estimate_utm_crs()
    buildings_m, points_m = buildings.to_crs(utm), points.to_crs(utm)
    building_index, point_index = buildings_m.sindex, points_m.sindex

    done, records, failed, empty = set(), [], 0, 0
    for scene in scenes.itertuples():
        raster = cfg.UAV_RASTERS_DIR / f"{scene.scene_id}.tif"
        if not raster.exists():
            print(f"[{scene.scene_id}] raster missing, skipping (run 01_download.py)")
            continue
        with rasterio.open(raster) as src:
            coverage = box(*transform_bounds(src.crs, "EPSG:4326", *src.bounds))
        in_scene = buildings_m[buildings.geometry.within(coverage).to_numpy() & ~buildings["osm_id"].isin(done).to_numpy()]
        if wanted:
            in_scene = in_scene[in_scene["osm_id"].isin(wanted)]

        matches = {}
        for row in tqdm(in_scene.itertuples(), total=len(in_scene), desc=f"[{scene.scene_id}] line of sight", unit="bld"):
            if row.osm_id in manual:
                hit = points_m[points_m["id"] == manual[row.osm_id]]
                if hit.empty:
                    continue
                cam = hit.iloc[0]
                cam_ll = gpd.GeoSeries([cam.geometry], crs=utm).to_crs(4326).iloc[0]
                cen_ll = gpd.GeoSeries([row.geometry.centroid], crs=utm).to_crs(4326).iloc[0]
                bearing = calculate_bearing(cam_ll.y, cam_ll.x, cen_ll.y, cen_ll.x)
                heading = 0 if pd.isna(cam["heading"]) else cam["heading"]
                m = {"mapillary_id": cam["id"], "distance_m": round(float(cam.geometry.distance(row.geometry.centroid)), 1),
                     "bearing": round(bearing, 1), "relative_angle": round((bearing - heading) % 360, 1), "is_pano": bool(cam["is_pano"])}
            else:
                m = find_match(row, points_m, point_index, buildings_m, building_index, args.search_radius, args.fov, args.pano_only)
            if m:
                matches[row.osm_id] = m
        ids = sorted(matches)
        print(f"[{scene.scene_id}] {len(ids)} of {len(in_scene)} buildings have a valid line of sight")
        if args.limit and len(ids) > args.limit:
            ids = sorted(np.random.default_rng(0).choice(ids, args.limit, replace=False).tolist())

        geoms = in_scene.set_index("osm_id").geometry.to_crs(4326)
        for osm_id in tqdm(ids, desc=f"[{scene.scene_id}] chips", unit="pair"):
            m = matches[osm_id]
            uav_png, svi_png = cfg.OUTPUT_PAIRS_DIR / f"{osm_id}_uav.png", cfg.OUTPUT_PAIRS_DIR / f"{osm_id}_svi.png"
            try:
                if not (uav_png.exists() and svi_png.exists()):
                    chip, _ = mask_uav_building_chip(raster, geoms[osm_id], "EPSG:4326", args.padding_factor)
                    if not chip.any():
                        empty += 1
                        continue
                    pano = cv2.imread(str(download_image(m["mapillary_id"], cfg.SVI_PANORAMAS_DIR / f"{m['mapillary_id']}.jpg", cfg.MAPILLARY_ACCESS_TOKEN)))
                    if pano is None:
                        raise RuntimeError("unreadable image")
                    cv2.imwrite(str(uav_png), cv2.cvtColor(raster_to_png_array(chip), cv2.COLOR_RGB2BGR))
                    cv2.imwrite(str(svi_png), crop_svi(pano, m))
            except Exception as exc:
                failed += 1
                tqdm.write(f"  {osm_id}: {exc}")
                continue
            done.add(osm_id)
            records.append({"osm_id": osm_id, "scene_id": scene.scene_id, **m})

    new = pd.DataFrame(records)
    if cfg.PAIRS_CSV.exists() and len(new):
        old = pd.read_csv(cfg.PAIRS_CSV, dtype={"osm_id": str, "mapillary_id": str})
        new = pd.concat([old[~old["osm_id"].isin(new["osm_id"])], new], ignore_index=True)
    new.to_csv(cfg.PAIRS_CSV, index=False)
    print(f"{len(records)} pairs in {cfg.OUTPUT_PAIRS_DIR} ({failed} failed, {empty} UAV chips empty/outside the image). Provenance: {cfg.PAIRS_CSV}")


if __name__ == "__main__":
    main()
