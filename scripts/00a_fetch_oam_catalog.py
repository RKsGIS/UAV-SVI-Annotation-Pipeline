#!/usr/bin/env python3
"""
00a_fetch_oam_catalog.py
Downloads the OpenAerialMap UAV catalog and keeps scenes with GSD < --max-gsd-cm
and bbox area > --min-area-km2. Writes data/catalog/all_uav.json (raw) and
data/catalog/oam_scenes.gpkg (filtered footprints).
"""

import argparse
import json
import math
import time

import geopandas as gpd
import requests
from shapely.geometry import box, shape
from tqdm import tqdm

from utils import pipeline_config as cfg

OAM_META_URL = "https://api.openaerialmap.org/meta"
PAGE_SIZE = 100
HEADERS = {"User-Agent": "uav-svi-annotation-pipeline/1.0 (research)", "Accept": "application/json"}


def bbox_area_km2(bbox):
    min_lon, min_lat, max_lon, max_lat = bbox
    lat_c = math.radians((min_lat + max_lat) / 2)
    return abs(max_lat - min_lat) * 111.0 * abs(max_lon - min_lon) * 111.0 * math.cos(lat_c)


def fetch_all_uav(refresh: bool) -> list[dict]:
    if cfg.ALL_UAV_JSON.exists() and not refresh:
        return json.loads(cfg.ALL_UAV_JSON.read_text())

    session = requests.Session()
    session.headers.update(HEADERS)
    r = session.get(OAM_META_URL, params={"limit": 1, "page": 1, "platform": "uav"}, timeout=60)
    r.raise_for_status()
    total = r.json().get("meta", {}).get("found", 0)
    n_pages = max(1, math.ceil(total / PAGE_SIZE))

    rows, seen = [], set()
    for page in tqdm(range(1, n_pages + 1), desc="Fetching OAM pages"):
        for attempt in range(3):
            try:
                resp = session.get(OAM_META_URL, params={
                    "limit": PAGE_SIZE, "page": page, "platform": "uav",
                    "orderBy": "gsd", "order": "asc",
                }, timeout=90)
                resp.raise_for_status()
                for rec in resp.json().get("results", []):
                    sid = rec.get("_id")
                    if sid and sid not in seen:
                        seen.add(sid)
                        rows.append(rec)
                break
            except requests.RequestException:
                time.sleep(2 * (attempt + 1))
        time.sleep(0.2)

    cfg.ALL_UAV_JSON.write_text(json.dumps(rows))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-gsd-cm", type=float, default=6.0)
    parser.add_argument("--min-area-km2", type=float, default=1.0)
    parser.add_argument("--refresh", action="store_true", help="Re-download the catalog even if cached.")
    args = parser.parse_args()

    cfg.ensure_runtime_directories()
    records = fetch_all_uav(args.refresh)

    kept = []
    for rec in records:
        bbox, gsd_m = rec.get("bbox"), rec.get("gsd")
        if not bbox or len(bbox) != 4 or not gsd_m:
            continue
        gsd_cm, area = float(gsd_m) * 100, bbox_area_km2(bbox)
        if gsd_cm >= args.max_gsd_cm or area <= args.min_area_km2:
            continue
        geom = shape(rec["geojson"]) if rec.get("geojson") else box(*bbox)
        kept.append({
            "scene_id": str(rec["_id"]),
            "title": rec.get("title", ""),
            "gsd_cm": round(gsd_cm, 3),
            "area_km2": round(area, 3),
            "acquisition_start": (rec.get("acquisition_start") or "")[:10],
            "license": (rec.get("properties") or {}).get("license", ""),
            "download_url": rec.get("uuid", ""),
            "geometry": geom,
        })

    gdf = gpd.GeoDataFrame(kept, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(cfg.OAM_SCENES_FILE, driver="GPKG")
    print(f"{len(records)} UAV scenes in catalog -> {len(gdf)} pass GSD/area filter -> {cfg.OAM_SCENES_FILE}")


if __name__ == "__main__":
    main()
