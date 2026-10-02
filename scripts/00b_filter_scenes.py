#!/usr/bin/env python3
"""
00b_filter_scenes.py
For every scene from 00a: does its bbox contain Mapillary images, and how many OSM buildings
does Overpass report? Resumable (per-scene JSON cache). Writes scene_bbox_pass_fail.csv for every
scene and candidates_with_osm_status.csv for the Mapillary-positive scenes (input of 00c).
"""

import argparse
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import geopandas as gpd
import pandas as pd
import requests
from tqdm import tqdm

from utils import pipeline_config as cfg

MAPILLARY_URL = "https://graph.mapillary.com/images"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]
HEADERS = {"User-Agent": "uav-svi-annotation-pipeline/1.0 (research)", "Accept": "application/json"}
_OVERPASS_SEM = threading.Semaphore(2)


def _cache(kind: str, scene_id: str):
    d = cfg.CHECK_CACHE_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{scene_id}.json"


def has_mapillary(bbox, scene_id: str) -> bool | None:
    """None means the API call failed (not cached, retried on next run)."""
    cache = _cache("mapillary", scene_id)
    if cache.exists():
        return json.loads(cache.read_text())["has_images"]
    min_lon, min_lat, max_lon, max_lat = bbox
    try:
        r = requests.get(MAPILLARY_URL, params={
            "access_token": cfg.MAPILLARY_ACCESS_TOKEN, "fields": "id",
            "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}", "limit": 1,
        }, timeout=15)
        if r.status_code != 200:
            return None
        found = len(r.json().get("data", [])) > 0
    except requests.RequestException:
        return None
    cache.write_text(json.dumps({"has_images": found}))
    return found


def count_osm_buildings(bbox, scene_id: str, building_value: str, retries: int = 5) -> int | None:
    cache = _cache(f"osm_{building_value}", scene_id)
    if cache.exists():
        return json.loads(cache.read_text())["building_count"]
    xmin, ymin, xmax, ymax = bbox
    selector = '["building"]' if building_value == "any" else f'["building"="{building_value}"]'
    query = f"[out:json][timeout:25];way{selector}({ymin},{xmin},{ymax},{xmax});out count;"

    backoff = 1.0
    for attempt in range(retries):
        url = OVERPASS_ENDPOINTS[attempt % len(OVERPASS_ENDPOINTS)]
        try:
            with _OVERPASS_SEM:
                r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=30)
                time.sleep(random.uniform(0.5, 1.5))
            if r.status_code == 200:
                elements = r.json().get("elements", [])
                tags = elements[0].get("tags", {}) if elements else {}
                count = int(tags.get("ways", 0)) + int(tags.get("relations", 0))
                cache.write_text(json.dumps({"building_count": count}))
                return count
            if r.status_code == 429:
                time.sleep(backoff + random.uniform(0, 0.5))
                backoff = min(backoff * 2, 16)
        except requests.RequestException:
            time.sleep(backoff)
            backoff = min(backoff * 2, 16)
    return None


def check_scene(row, building_value: str) -> dict:
    out = {"scene_id": row.scene_id, "title": row.title, "gsd_cm": row.gsd_cm, "area_km2": row.area_km2,
           "mapillary_ok": False, "osm_ok": False, "building_count": 0}
    bbox = row.geometry.bounds

    mapillary = has_mapillary(bbox, row.scene_id)
    if mapillary is None:
        return {**out, "pass": False, "reason": "mapillary_api_error"}
    if not mapillary:
        return {**out, "pass": False, "reason": "no_mapillary"}
    out["mapillary_ok"] = True

    count = count_osm_buildings(bbox, row.scene_id, building_value)
    if count is None:
        return {**out, "pass": False, "reason": "osm_api_error"}
    out["building_count"] = count
    if count == 0:
        return {**out, "pass": False, "reason": "no_osm_buildings"}
    out["osm_ok"] = True
    return {**out, "pass": True, "reason": "pass"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building-value", default="residential",
                        help='OSM building=<value> to count; "any" counts every building=*.')
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=None, help="Only check the first N scenes (testing).")
    parser.add_argument("--recheck-errors", action="store_true", help="Retry scenes that previously hit an API error.")
    args = parser.parse_args()

    if not cfg.MAPILLARY_ACCESS_TOKEN:
        raise RuntimeError("MAPILLARY_ACCESS_TOKEN is required in the repository .env file.")
    cfg.ensure_runtime_directories()

    scenes = gpd.read_file(cfg.OAM_SCENES_FILE)
    if args.limit:
        scenes = scenes.head(args.limit)

    done = pd.DataFrame()
    if cfg.SCENE_BBOX_CSV.exists():
        done = pd.read_csv(cfg.SCENE_BBOX_CSV, dtype={"scene_id": str})
        if args.recheck_errors:
            done = done[~done["reason"].astype(str).str.endswith("api_error")]
    todo = scenes[~scenes["scene_id"].isin(done.get("scene_id", []))]
    print(f"{len(scenes)} scenes, {len(done)} already checked, {len(todo)} to check")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(check_scene, row, args.building_value) for row in todo.itertuples()]
        for fut in tqdm(as_completed(futures), total=len(futures), desc="Scenes"):
            results.append(fut.result())
            if len(results) % 50 == 0:
                pd.concat([done, pd.DataFrame(results)]).to_csv(cfg.SCENE_BBOX_CSV, index=False)

    all_rows = pd.concat([done, pd.DataFrame(results)], ignore_index=True)
    all_rows.to_csv(cfg.SCENE_BBOX_CSV, index=False)
    all_rows[all_rows["mapillary_ok"] == True].to_csv(cfg.CANDIDATES_CSV, index=False)  # noqa: E712

    print(all_rows["reason"].value_counts().to_string())
    print(f"Wrote {cfg.SCENE_BBOX_CSV} and {cfg.CANDIDATES_CSV}")


if __name__ == "__main__":
    main()
