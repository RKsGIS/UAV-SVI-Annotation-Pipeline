from __future__ import annotations

import numpy as np
import cv2
import geopandas as gpd
import rasterio
from rasterio.features import geometry_mask
from rasterio.mask import mask
from shapely.geometry import box, mapping

from . import pipeline_config as cfg


def square_crop_geometry(geometry, padding_factor: float = 1.25, min_size: float = 4.0):
    minx, miny, maxx, maxy = geometry.bounds
    width = max(maxx - minx, min_size)
    height = max(maxy - miny, min_size)
    side = max(width, height) * padding_factor
    centroid = geometry.centroid
    half = side / 2
    return box(centroid.x - half, centroid.y - half, centroid.x + half, centroid.y + half)


def mask_uav_building_chip(raster_path, geometry, geometry_crs, padding_factor: float = 1.25):
    with rasterio.open(raster_path) as src:
        geometry = gpd.GeoSeries([geometry], crs=geometry_crs).to_crs(src.crs).iloc[0]
        crop_geom = square_crop_geometry(geometry, padding_factor=padding_factor)
        out_image, out_transform = mask(src, [mapping(crop_geom)], crop=True, all_touched=False)
        building_mask = geometry_mask(
            [mapping(geometry)],
            out_shape=out_image.shape[1:],
            transform=out_transform,
            invert=True,
        )
        masked_image = out_image[:3].copy() if out_image.shape[0] > 3 else out_image.copy()
        masked_image[:, ~building_mask] = 0
        metadata = src.meta.copy()
        metadata.update({
            'count': masked_image.shape[0],
            'height': masked_image.shape[1],
            'width': masked_image.shape[2],
            'transform': out_transform,
            'nodata': 0,
        })
        return masked_image, metadata


def raster_to_png_array(masked_image: np.ndarray) -> np.ndarray:
    if masked_image.shape[0] == 1:
        rgb = np.repeat(masked_image, 3, axis=0)
    else:
        rgb = masked_image[:3]
    rgb = np.moveaxis(rgb, 0, -1).astype(np.float32)
    valid = np.any(rgb > 0, axis=-1)
    if not np.any(valid):
        return np.zeros_like(rgb, dtype=np.uint8)
    for band in range(rgb.shape[-1]):
        band_values = rgb[..., band][valid]
        low = np.percentile(band_values, 2)
        high = np.percentile(band_values, 98)
        if high <= low:
            high = low + 1
        rgb[..., band] = np.clip((rgb[..., band] - low) * 255.0 / (high - low), 0, 255)
    return rgb.astype(np.uint8)


def extract_wrapped_panorama_window(image: np.ndarray, target_x: float, window_width: int = 1536) -> np.ndarray:
    height, width = image.shape[:2]
    half = window_width // 2
    center = int(round(target_x)) % width
    strip = np.concatenate([image, image, image], axis=1)
    start = center + width - half
    end = start + window_width
    return strip[:, start:end].copy()


def apply_focus_mask(image: np.ndarray, focus_ratio: float = 0.6) -> np.ndarray:
    masked = image.copy()
    height, width = image.shape[:2]
    focus_width = int(width * focus_ratio)
    margin = max((width - focus_width) // 2, 0)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.rectangle(mask, (margin, 0), (width - margin, height), color=255, thickness=-1)
    masked[mask == 0] = 0
    return masked
