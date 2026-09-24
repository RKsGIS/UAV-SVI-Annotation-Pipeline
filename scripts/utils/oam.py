from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import requests
import rasterio
from rasterio.mask import mask
from shapely.geometry import box, mapping

from . import pipeline_config as cfg


def resolve_scene_asset_url(row, explicit_field: str | None = None):
    fields = [explicit_field] if explicit_field else []
    fields.extend(cfg.DEFAULT_SCENE_URL_FIELDS)
    for field in fields:
        if field and field in row and row[field]:
            return row[field]
    return None


def download_uav_raster(url: str, destination: str | Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return destination
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with destination.open('wb') as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    return destination


def raster_fully_covers_geometry(raster_path: str | Path, geometry, geometry_crs) -> bool:
    with rasterio.open(raster_path) as src:
        geometry = gpd.GeoSeries([geometry], crs=geometry_crs).to_crs(src.crs).iloc[0]
        raster_bounds = box(*src.bounds)
        return raster_bounds.contains(geometry)


def try_crop(raster_path: str | Path, geometry):
    with rasterio.open(raster_path) as src:
        return mask(src, [mapping(geometry)], crop=True, all_touched=False)
