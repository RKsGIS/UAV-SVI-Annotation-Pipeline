#!/usr/bin/env python3
"""
00d_build_scene_table.py
Joins 00c scores with scene footprints, adds country / continent / global_south and overlap
clusters, and writes the claim sheet (upload to Google Sheet) plus scenes_master.gpkg.
With --scene-ids or --claimed-by it also writes data/selected_scenes.gpkg for steps 01-04.
"""

import argparse

import geopandas as gpd
import pandas as pd

from utils import pipeline_config as cfg

# Everything outside these Natural Earth regions counts as Global South, except the listed countries.
NORTH_REGIONS = {"Europe", "North America"}
NORTH_COUNTRIES = {"Japan", "South Korea", "Israel", "Australia", "New Zealand", "Singapore", "Taiwan"}


def attach_country(scenes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    world = gpd.read_file(cfg.NE_COUNTRIES_URL)[["ADMIN", "CONTINENT", "geometry"]]
    pts = scenes.copy()
    pts["geometry"] = scenes.representative_point()
    joined = gpd.sjoin(pts, world, how="left", predicate="within").drop(columns="index_right")
    joined = joined[~joined.index.duplicated(keep="first")]

    missing = joined["ADMIN"].isna()
    if missing.any():  # coastal scenes that fall just outside the country polygons
        metric = joined.loc[missing].to_crs(epsg=3857)
        nearest = gpd.sjoin_nearest(metric, world.to_crs(epsg=3857), how="left")
        nearest = nearest[~nearest.index.duplicated(keep="first")]
        joined.loc[missing, "ADMIN"] = nearest["ADMIN_right"] if "ADMIN_right" in nearest else nearest["ADMIN"]
        joined.loc[missing, "CONTINENT"] = nearest["CONTINENT_right"] if "CONTINENT_right" in nearest else nearest["CONTINENT"]

    scenes = scenes.copy()
    scenes["country"] = joined["ADMIN"]
    scenes["continent"] = joined["CONTINENT"]
    scenes["global_south"] = ~(scenes["continent"].isin(NORTH_REGIONS) | scenes["country"].isin(NORTH_COUNTRIES))
    return scenes


def attach_overlap_clusters(scenes: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    parent = {sid: sid for sid in scenes["scene_id"]}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = gpd.sjoin(scenes[["scene_id", "geometry"]], scenes[["scene_id", "geometry"]], predicate="intersects")
    for a, b in zip(pairs["scene_id_left"], pairs["scene_id_right"]):
        if a != b:
            parent[find(a)] = find(b)

    scenes = scenes.copy()
    scenes["cluster_id"] = scenes["scene_id"].map(find)
    scenes["cluster_size"] = scenes.groupby("cluster_id")["scene_id"].transform("count")
    return scenes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-matches", type=int, default=1, help="Drop scenes with fewer valid LOS matches.")
    parser.add_argument("--scene-ids", nargs="*", default=None, help="Scene IDs to export as selected_scenes.gpkg.")
    parser.add_argument("--claimed-csv", default=None, help="Claim sheet exported from Google Sheets (needs scene_id, claimed_by).")
    parser.add_argument("--claimed-by", default=None, help="Name in the claimed_by column to export.")
    parser.add_argument("--include-overlaps", action="store_true",
                        help="Also export scenes overlapping the chosen ones (for merging / intersection cropping).")
    args = parser.parse_args()

    cfg.ensure_runtime_directories()
    scores = pd.read_csv(cfg.SCORES_CSV, dtype={"scene_id": str})
    footprints = gpd.read_file(cfg.OAM_SCENES_FILE)
    score_cols = ["scene_id", "total_buildings", "total_svi_points", "buildings_on_path", "valid_los_matches"]
    scenes = footprints.merge(scores[score_cols], on="scene_id", how="inner")
    scenes["valid_los_matches"] = scenes["valid_los_matches"].fillna(0).astype(int)
    scenes = scenes[scenes["valid_los_matches"] >= args.min_matches]

    scenes = attach_overlap_clusters(attach_country(scenes))
    scenes["cluster_matches"] = scenes.groupby("cluster_id")["valid_los_matches"].transform("sum")
    scenes = scenes.sort_values(["global_south", "valid_los_matches"], ascending=[False, False])
    scenes.to_file(cfg.SCENES_MASTER_FILE, driver="GPKG")

    sheet = scenes.drop(columns=["geometry", "download_url"]).assign(claimed_by="")
    sheet.to_csv(cfg.SCENE_CLAIMS_CSV, index=False)
    print(f"{len(scenes)} scenes, {int(scenes['valid_los_matches'].sum())} potential pairs "
          f"({int(scenes.loc[scenes['global_south'], 'valid_los_matches'].sum())} in the Global South)")
    print(f"Claim sheet: {cfg.SCENE_CLAIMS_CSV}")

    chosen = set(args.scene_ids or [])
    if args.claimed_csv and args.claimed_by:
        claims = pd.read_csv(args.claimed_csv, dtype={"scene_id": str})
        chosen |= set(claims.loc[claims["claimed_by"] == args.claimed_by, "scene_id"])
    if not chosen:
        return

    selected = scenes[scenes["scene_id"].isin(chosen)]
    if args.include_overlaps:
        selected = scenes[scenes["cluster_id"].isin(selected["cluster_id"])]
    if selected.empty:
        raise RuntimeError("None of the requested scene IDs are in the scene table.")
    selected.to_file(cfg.SELECTED_SCENES_FILE, driver="GPKG")
    print(f"Wrote {len(selected)} scenes to {cfg.SELECTED_SCENES_FILE}")


if __name__ == "__main__":
    main()
