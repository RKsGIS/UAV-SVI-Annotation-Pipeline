from __future__ import annotations

import math
import time
from io import BytesIO
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from PIL import Image
from shapely.geometry import Point

from . import pipeline_config as cfg


def calculate_bearing(lat1, lon1, lat2, lon2):
    lat1, lat2, delta_lon = map(math.radians, [lat1, lat2, lon2 - lon1])
    y = math.sin(delta_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def fetch_mapillary_points_for_scenes(
    scenes_gdf: gpd.GeoDataFrame,
    access_token: str,
    creator_username: str | None = None,
    fields: str = cfg.MAPILLARY_FIELDS,
    pause_seconds: float = 0.6,
) -> gpd.GeoDataFrame:
    records = []
    for _, row in scenes_gdf.to_crs(epsg=4326).iterrows():
        minx, miny, maxx, maxy = row.geometry.bounds
        bbox_str = f"{minx},{miny},{maxx},{maxy}"
        next_url = (
            f"https://graph.mapillary.com/images?access_token={access_token}"
            f"&bbox={bbox_str}&fields={fields}&limit=2000"
        )
        if creator_username:
            next_url += f"&creator_username={creator_username}"
        while next_url:
            response = requests.get(next_url, timeout=30)
            response.raise_for_status()
            payload = response.json()
            for item in payload.get('data', []):
                coords = item.get('computed_geometry', {}).get('coordinates')
                if coords:
                    records.append({**item, 'geometry': Point(coords), 'scene_id': row['scene_id']})
            next_url = payload.get('paging', {}).get('next')
        time.sleep(pause_seconds)
    if not records:
        return gpd.GeoDataFrame(columns=['id', 'scene_id', 'geometry'], geometry='geometry', crs='EPSG:4326')
    points = gpd.GeoDataFrame(records, geometry='geometry', crs='EPSG:4326')
    points = points.drop_duplicates(subset=['id'])
    return points


def compute_target_x(mapillary_row, building_centroid):
    bearing = calculate_bearing(
        mapillary_row.geometry.y,
        mapillary_row.geometry.x,
        building_centroid.y,
        building_centroid.x,
    )
    heading = mapillary_row.get('computed_compass_angle')
    if pd.isna(heading):
        heading = mapillary_row.get('compass_angle', 0)
    relative_angle = (bearing - float(heading) + 360) % 360
    target_x = (cfg.CENTER_X + (relative_angle * cfg.PIXELS_PER_DEGREE)) % cfg.IMAGE_WIDTH
    return bearing, relative_angle, target_x, ('left' if target_x < cfg.CENTER_X else 'right')


def download_panorama(image_url: str, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        return output_path
    response = requests.get(image_url, timeout=30)
    response.raise_for_status()
    Image.open(BytesIO(response.content)).save(output_path)
    return output_path
