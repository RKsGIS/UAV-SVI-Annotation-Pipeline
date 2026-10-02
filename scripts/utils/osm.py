from __future__ import annotations

import random
import time

import geopandas as gpd
import requests
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]
HEADERS = {"User-Agent": "uav-svi-annotation-pipeline/1.0 (research)", "Accept": "application/json"}


def _relation_geometry(element):
    rings = {"outer": [], "inner": []}
    for member in element.get("members", []):
        role = member.get("role") or "outer"
        coords = [(p["lon"], p["lat"]) for p in member.get("geometry", [])]
        if member.get("type") == "way" and role in rings and len(coords) >= 2:
            rings[role].append(LineString(coords))
    outer = unary_union(list(polygonize(unary_union(rings["outer"])))) if rings["outer"] else None
    if outer is None or outer.is_empty:
        return None
    if rings["inner"]:
        holes = unary_union(list(polygonize(unary_union(rings["inner"]))))
        outer = outer.difference(holes)
    return outer


def fetch_buildings(bbox, retries: int = 5) -> gpd.GeoDataFrame:
    """OSM building ways and multipolygon relations in bbox=(min_lon, min_lat, max_lon, max_lat).

    osm_id is the plain OSM id for ways and 'r<id>' for relations, so the two id spaces never collide.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    area = f"({min_lat},{min_lon},{max_lat},{max_lon})"
    query = f'[out:json][timeout:120];(way["building"]{area};relation["building"]{area};);out geom;'
    backoff = 2.0
    for attempt in range(retries):
        url = OVERPASS_ENDPOINTS[attempt % len(OVERPASS_ENDPOINTS)]
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=180)
        except requests.RequestException:
            time.sleep(backoff)
            backoff *= 2
            continue
        if r.status_code == 200:
            rows = []
            for el in r.json().get("elements", []):
                tags = el.get("tags", {})
                if el["type"] == "way":
                    coords = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
                    if len(coords) < 4 or coords[0] != coords[-1]:
                        continue
                    geom, osm_id = Polygon(coords), str(el["id"])
                else:
                    geom, osm_id = _relation_geometry(el), f"r{el['id']}"
                if geom is None or geom.is_empty:
                    continue
                rows.append({"osm_id": osm_id, "osm_type": el["type"], "building": tags.get("building", ""),
                             "geometry": geom})
            return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
        time.sleep(backoff + random.uniform(0, 1))
        backoff *= 2
    raise RuntimeError("Overpass request failed after several retries; try again later.")
