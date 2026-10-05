from __future__ import annotations

import math
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import Point

GRAPH = "https://graph.mapillary.com"
POINT_FIELDS = "id,geometry,computed_geometry,compass_angle,computed_compass_angle,is_pano,camera_type,captured_at,sequence"


def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1, lat2, delta_lon = map(math.radians, [lat1, lat2, lon2 - lon1])
    y = math.sin(delta_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def fetch_points(bbox, token: str) -> gpd.GeoDataFrame:
    """All Mapillary image points in bbox=(min_lon, min_lat, max_lon, max_lat) as plain columns."""
    url = f"{GRAPH}/images"
    params = {"access_token": token, "fields": POINT_FIELDS, "limit": 2000, "bbox": ",".join(map(str, bbox))}
    rows = []
    while url:
        r = requests.get(url, params=params, timeout=60)
        r.raise_for_status()
        payload = r.json()
        for item in payload.get("data", []):
            geom = item.get("computed_geometry") or item.get("geometry")
            if not geom:
                continue
            heading = item.get("computed_compass_angle")
            if heading is None:
                heading = item.get("compass_angle")
            rows.append({
                "id": str(item["id"]),
                "heading": heading,
                "is_pano": bool(item.get("is_pano")),
                "captured_at": item.get("captured_at"),
                "sequence": item.get("sequence"),
                "geometry": Point(geom["coordinates"]),
            })
        url, params = payload.get("paging", {}).get("next"), None
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def download_image(image_id: str, output_path: str | Path, token: str) -> Path:
    """Mapillary image URLs expire, so ask for a fresh one right before downloading."""
    output_path = Path(output_path)
    if output_path.exists():
        return output_path
    r = requests.get(f"{GRAPH}/{image_id}", params={"access_token": token, "fields": "thumb_original_url,thumb_2048_url"}, timeout=30)
    r.raise_for_status()
    meta = r.json()
    url = meta.get("thumb_original_url") or meta.get("thumb_2048_url")
    if not url:
        raise RuntimeError(f"No image URL for Mapillary image {image_id}")
    img = requests.get(url, timeout=60)
    img.raise_for_status()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(img.content)
    return output_path
