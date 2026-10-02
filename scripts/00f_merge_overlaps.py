#!/usr/bin/env python3
"""
00f_merge_overlaps.py  (optional helper)
Overlapping scenes can split a building across two rasters, so no single raster fully covers it.
This script can

  --buildings A.gpkg B.gpkg ...   merge building files into one, without duplicate osm_id
                                  (on a conflict the larger footprint is kept)
  --scene-ids ID1 ID2 ...         mosaic these scenes into one raster (downloads them first) and replace
                                  them in data/selected_scenes.gpkg by one merged scene

Examples:
  python scripts/00f_merge_overlaps.py --buildings data/input/osm_buildings.gpkg other.gpkg
  python scripts/00f_merge_overlaps.py --scene-ids 611cce7438f25a0006989180 663426676049ef00013b827c
"""

import argparse
import hashlib

import geopandas as gpd
import pandas as pd
import rasterio
from rasterio.merge import merge
from shapely.ops import unary_union

from utils import pipeline_config as cfg
from utils.oam import download_uav_raster


def merge_buildings(paths, output):
    parts = [gpd.read_file(p).to_crs(epsg=4326) for p in paths]
    gdf = pd.concat(parts, ignore_index=True)
    gdf["osm_id"] = gdf["osm_id"].astype(str)
    area = gdf.to_crs(gdf.estimate_utm_crs()).area
    gdf = gdf.assign(_area=area).sort_values("_area", ascending=False).drop_duplicates("osm_id").drop(columns="_area")
    gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(output, driver="GPKG")
    print(f"{sum(len(p) for p in parts)} features -> {len(gdf)} unique osm_id -> {output}")


def merge_scenes(scene_ids):
    scenes = gpd.read_file(cfg.SELECTED_SCENES_FILE)
    scenes["scene_id"] = scenes["scene_id"].astype(str)
    group = scenes[scenes["scene_id"].isin(scene_ids)]
    if len(group) != len(set(scene_ids)):
        raise SystemExit("Some scene IDs are not in data/selected_scenes.gpkg.")

    merged_id = "merged_" + hashlib.md5("".join(sorted(scene_ids)).encode()).hexdigest()[:10]
    out = cfg.UAV_RASTERS_DIR / f"{merged_id}.tif"
    cfg.ensure_runtime_directories()
    if not out.exists():
        paths = [download_uav_raster(r.download_url, cfg.UAV_RASTERS_DIR / f"{r.scene_id}.tif") for r in group.itertuples()]
        sources = [rasterio.open(p) for p in paths]
        try:
            merge(sources, dst_path=out, nodata=0, dst_kwds={"compress": "deflate", "tiled": True, "BIGTIFF": "IF_SAFER"})
        finally:
            for s in sources:
                s.close()

    row = group.iloc[0].copy()
    row["scene_id"] = merged_id
    row["title"] = "merged: " + ", ".join(group["scene_id"])
    row["download_url"] = f"local:{out.name}"  # the file already exists, so 01 never downloads it
    row["geometry"] = unary_union(list(group.geometry))
    rest = scenes[~scenes["scene_id"].isin(scene_ids)]
    result = gpd.GeoDataFrame(pd.concat([rest, gpd.GeoDataFrame([row], crs=scenes.crs)], ignore_index=True), crs=scenes.crs)
    result.to_file(cfg.SELECTED_SCENES_FILE, driver="GPKG")
    print(f"Merged {len(group)} scenes into {out} and updated {cfg.SELECTED_SCENES_FILE}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--buildings", nargs="+", default=None)
    parser.add_argument("--output", default=str(cfg.INPUT_DIR / "osm_buildings.gpkg"))
    parser.add_argument("--scene-ids", nargs="+", default=None)
    args = parser.parse_args()
    if not args.buildings and not args.scene_ids:
        parser.error("give --buildings and/or --scene-ids")
    if args.buildings:
        merge_buildings(args.buildings, args.output)
    if args.scene_ids:
        merge_scenes(args.scene_ids)


if __name__ == "__main__":
    main()
