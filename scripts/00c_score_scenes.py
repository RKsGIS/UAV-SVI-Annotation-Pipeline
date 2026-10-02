#!/usr/bin/env python3
"""
00c_score_scenes.py
Downloads OSM buildings + Mapillary points per candidate scene and scores them by Line-of-Sight matches
(camera heading, edge distance, building occlusion).
"""

import json
import math
import time
import random
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, LineString, mapping
from tqdm import tqdm

from utils import pipeline_config as cfg

# ─── CONFIGURATION ───
MAPILLARY_TOKEN = cfg.MAPILLARY_ACCESS_TOKEN
if not MAPILLARY_TOKEN:
    raise RuntimeError("MAPILLARY_ACCESS_TOKEN is required in the repository .env file.")

BASE_DIR = cfg.ROOT_DIR
CANDIDATES_CSV = cfg.CANDIDATES_CSV
OAM_CACHE_FILE = cfg.ALL_UAV_JSON
SCORES_CSV = cfg.SCORES_CSV

OSM_DIR = cfg.OSM_DIR
MAP_DIR = cfg.MAP_DIR
LOS_DIR = cfg.LOS_DIR

for d in [OSM_DIR, MAP_DIR, LOS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Spatial Parameters
ROAD_BUFFER_METERS = 20.0
NEARBY_DISTANCE_METERS = 20.0  # Max distance from camera to building EDGE
FOV_TOLERANCE_DEG = 50.0

MAX_WORKERS = 4
_OVERPASS_SEM = threading.Semaphore(2)  

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HEADERS = {"User-Agent": "oam-enrichment-pipeline/1.0", "Accept": "application/json"}

def get_bbox_lookup():
    if OAM_CACHE_FILE.exists():
        data = json.loads(OAM_CACHE_FILE.read_text())
        return {str(rec["_id"]): rec["bbox"] for rec in data if "_id" in rec and "bbox" in rec}
    return {}

def calculate_bearing(p1, p2):
    angle = math.degrees(math.atan2(p2.x - p1.x, p2.y - p1.y))
    return (angle + 360) % 360

def fetch_mapillary(bbox, scene_id):
    """Fetches non-panoramic Mapillary points. Skips API if local file exists."""
    out_file = MAP_DIR / f"{scene_id}_mapillary_points.geojson"
    
    # ⚡ Cache Check: If file exists, load it directly to save API calls
    if out_file.exists():
        return gpd.read_file(out_file)

    min_lon, min_lat, max_lon, max_lat = bbox
    r = requests.get("https://graph.mapillary.com/images", params={
        "access_token": MAPILLARY_TOKEN,
        "fields": "id,geometry,compass_angle,sequence_id,is_pano",
        "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "limit": 2000
    }, timeout=30)
    
    data = r.json().get("data", [])
    features = []
    
    for d in data:
        if d.get("is_pano") == True:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "mapillary_id": d["id"], 
                "compass_angle": d.get("compass_angle", 0.0), 
                "sequence_id": d.get("sequence_id", "")
            },
            "geometry": d["geometry"]
        })
        
    with open(out_file, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)
        
    if not features:
        return gpd.GeoDataFrame()
    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")

def fetch_osm_buildings(bbox, scene_id, endpoint_idx=0):
    """Fetches OSM buildings. Skips API if local file exists."""
    out_file = OSM_DIR / f"{scene_id}_osm_buildings.geojson"
    
    # ⚡ Cache Check: If file exists, load it directly
    if out_file.exists():
        gdf = gpd.read_file(out_file)
        return gdf[gdf.get("source", "") != "search_bounding_box"]

    min_lon, min_lat, max_lon, max_lat = bbox
    overpass_bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    query = f'[out:json][timeout:60];(way["building"]({overpass_bbox});relation["building"]({overpass_bbox}););out geom;'

    with _OVERPASS_SEM:
        backoff = 1
        for attempt in range(4):
            endpoint = OVERPASS_ENDPOINTS[(endpoint_idx + attempt) % len(OVERPASS_ENDPOINTS)]
            try:
                r = requests.post(endpoint, data={"data": query}, headers=HEADERS, timeout=75)
                if r.status_code == 200:
                    elements = r.json().get("elements", [])
                    features = []
                    
                    for el in elements:
                        tags = el.get("tags", {})
                        geom_type = el.get("type")
                        coordinates = []

                        if geom_type == "way" and "geometry" in el:
                            coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
                            if len(coords) >= 4 and coords[0] == coords[-1]:
                                coordinates = [coords]
                        elif geom_type == "relation" and "members" in el:
                            for member in el["members"]:
                                if member.get("type") == "way" and "geometry" in member:
                                    coords = [[p["lon"], p["lat"]] for p in member["geometry"]]
                                    if len(coords) >= 4 and coords[0] == coords[-1]:
                                        coordinates.append(coords)

                        # if coordinates:
                        #     geometry = {"type": "Polygon" if len(coordinates) == 1 else "MultiPolygon", 
                        #                 "coordinates": coordinates if len(coordinates) == 1 else [coordinates]}
                        #     features.append({
                        #         "type": "Feature",
                        #         "properties": {"osm_id": str(el.get("id")), "building_type": tags.get("building", "unknown")},
                        #         "geometry": geometry
                        #     })
                        if coordinates:
                            geometry = {"type": "Polygon" if len(coordinates) == 1 else "MultiPolygon", 
                                        "coordinates": coordinates if len(coordinates) == 1 else [coordinates]}
                            features.append({
                                "type": "Feature",
                                "properties": {"osm_id": str(el.get("id")), "building_type": tags.get("building", "unknown")},
                                "geometry": geometry
                            })

                    # features.append({
                    #     "type": "Feature",
                    #     "properties": {"source": "search_bounding_box"},
                    #     "geometry": {"type": "Polygon", "coordinates": [[[min_lon, min_lat], [max_lon, min_lat], [max_lon, max_lat], [min_lon, max_lat], [min_lon, min_lat]]]}
                    # })


                
                    with open(out_file, "w", encoding="utf-8") as f:
                        json.dump({"type": "FeatureCollection", "features": features}, f, indent=2)
                    
                    if not features: return gpd.GeoDataFrame()
                    # No longer need to filter out the bounding box here!
                    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")

                elif r.status_code == 429:
                    time.sleep(backoff + random.uniform(1, 3))
                    backoff *= 2
                else:
                    time.sleep(1)

            except Exception:
                time.sleep(backoff)
                backoff *= 2

    return gpd.GeoDataFrame()

def process_scene(scene_id, bbox, idx):
    try:
        bldgs_wgs84 = fetch_osm_buildings(bbox, scene_id, idx)
        maps_wgs84 = fetch_mapillary(bbox, scene_id)

        total_buildings = len(bldgs_wgs84)
        total_svi = len(maps_wgs84)

        if total_buildings == 0 or total_svi == 0:
            return {"scene_id": scene_id, "total_buildings": total_buildings, "total_svi_points": total_svi, "buildings_on_path": 0, "valid_los_matches": 0}

        bldgs_wgs84 = bldgs_wgs84.reset_index(drop=True)
        if "osm_id" not in bldgs_wgs84.columns:
            bldgs_wgs84["osm_id"] = bldgs_wgs84.index.astype(str)

        utm_crs = bldgs_wgs84.estimate_utm_crs()
        bldgs_utm = bldgs_wgs84.to_crs(utm_crs)
        maps_utm = maps_wgs84.to_crs(utm_crs)

        seq_lines = []
        for _, group in maps_utm.groupby("sequence_id"):
            if len(group) > 1:
                seq_lines.append(LineString(group.geometry.tolist()))
            else:
                seq_lines.append(group.geometry.iloc[0])
                
        paths_buffered = gpd.GeoDataFrame(geometry=seq_lines, crs=utm_crs).buffer(ROAD_BUFFER_METERS)
        bldgs_on_path = gpd.sjoin(bldgs_utm, gpd.GeoDataFrame(geometry=paths_buffered), how="inner", predicate="intersects")
        bldgs_on_path = bldgs_on_path[~bldgs_on_path.index.duplicated(keep='first')]
        buildings_on_path_count = len(bldgs_on_path)

        valid_pairs = 0
        all_buildings_sindex = bldgs_utm.sindex
        los_features = []

        for bldg_idx, bldg in bldgs_on_path.iterrows():
            target_pos_idx = bldgs_utm.index.get_loc(bldg_idx)
            
            distances = maps_utm.geometry.distance(bldg.geometry)
            min_dist = distances.min()
            
            if min_dist > NEARBY_DISTANCE_METERS:
                continue
                
            cam_idx = distances.idxmin()
            cam = maps_utm.loc[cam_idx]
            centroid = bldg.geometry.centroid
            
            bearing = calculate_bearing(cam.geometry, centroid)
            angle_diff = abs(cam["compass_angle"] - bearing) % 360
            angle_diff = angle_diff if angle_diff <= 180 else 360 - angle_diff
            
            cam_wgs84_pt = maps_wgs84.loc[cam_idx].geometry
            bldg_wgs84_cent = bldgs_wgs84.iloc[target_pos_idx].geometry.centroid
            
            status = "bad_angle"
            if angle_diff <= FOV_TOLERANCE_DEG:
                los_line = LineString([cam.geometry, centroid])
                possible_blockers_pos_idx = list(all_buildings_sindex.intersection(los_line.bounds))
                
                is_occluded = False
                for blocker_pos in possible_blockers_pos_idx:
                    if blocker_pos != target_pos_idx: 
                        if los_line.intersects(bldgs_utm.iloc[blocker_pos].geometry):
                            is_occluded = True
                            break
                
                if not is_occluded:
                    valid_pairs += 1
                    status = "valid_los"
                else:
                    status = "occluded"

            los_features.append({
                "type": "Feature",
                "properties": {
                    "osm_id": bldg.get("osm_id", "unknown"),
                    "mapillary_id": cam.get("mapillary_id", "unknown"),
                    "status": status,
                    "edge_dist_m": round(min_dist, 1),
                    "angle_diff_deg": round(angle_diff, 1)
                },
                "geometry": mapping(LineString([cam_wgs84_pt, bldg_wgs84_cent]))
            })

        # ⚡ Automatically overwrites the debug line geojson every run
        los_out_file = LOS_DIR / f"{scene_id}_los_lines.geojson"
        with open(los_out_file, "w") as f:
            json.dump({"type": "FeatureCollection", "features": los_features}, f, indent=2)

        return {
            "scene_id": scene_id, 
            "total_buildings": total_buildings, 
            "total_svi_points": total_svi, 
            "buildings_on_path": buildings_on_path_count, 
            "valid_los_matches": valid_pairs
        }

    except Exception as e:
        print(f"Error on {scene_id}: {e}")
        return {"scene_id": scene_id, "total_buildings": 0, "total_svi_points": 0, "buildings_on_path": 0, "valid_los_matches": 0}


def main():
    print("=" * 70)
    print("OAM SCENE ENRICHMENT & FILTERING PIPELINE")
    print("=" * 70)

    if not CANDIDATES_CSV.exists():
        print(f"❌ Could not find {CANDIDATES_CSV}")
        return

    df_orig = pd.read_csv(CANDIDATES_CSV)
    df_targets = df_orig[df_orig["mapillary_ok"] == True].copy()
    scenes_to_check = df_targets["scene_id"].astype(str).tolist()
    print(f"📡 Found {len(scenes_to_check)} Mapillary-positive target scenes.")

    bbox_lookup = get_bbox_lookup()
    if not bbox_lookup:
        print("❌ Could not load OAM bounding boxes.")
        return

    completed_ids = set()
    results = []
    
    # ⚡ By deleting SCORES_CSV before you run, you force this to calculate 100% of scenes.
    if SCORES_CSV.exists():
        df_existing = pd.read_csv(SCORES_CSV)
        completed_ids = set(df_existing["scene_id"].astype(str).tolist())
        results = df_existing.to_dict("records")
        print(f"🔄 Resuming from {SCORES_CSV.name}: {len(completed_ids)} scenes already scored.")

    queue = [sid for sid in scenes_to_check if sid not in completed_ids and sid in bbox_lookup]
    print(f"🚀 Processing remaining {len(queue)} scenes...\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_sid = {executor.submit(process_scene, sid, bbox_lookup[sid], i): sid for i, sid in enumerate(queue)}

        for future in tqdm(as_completed(future_to_sid), total=len(future_to_sid), desc="Enriching Scenes"):
            res = future.result()
            results.append(res)
            
            df_scores = pd.DataFrame(results)
            df_scores.to_csv(SCORES_CSV, index=False)

    print("\n" + "=" * 70)
    df_final = pd.DataFrame(results)
    
    df_merged = df_targets.merge(df_final, on="scene_id", how="left")
    df_merged = df_merged.sort_values(by="valid_los_matches", ascending=False)
    df_merged.to_csv(SCORES_CSV, index=False)
    
    print(f"✅ Pipeline complete! Results saved to {SCORES_CSV.resolve()}")

if __name__ == "__main__":
    main()