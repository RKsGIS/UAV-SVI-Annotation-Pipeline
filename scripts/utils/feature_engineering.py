from __future__ import annotations

import base64
import os

import mapbox_vector_tile
import numpy as np
import pandas as pd
import rasterio
from PIL import Image, ImageDraw
from tqdm import tqdm

from . import pipeline_config as cfg


def process_svi_for_wall_brightness(line_info_df: pd.DataFrame) -> pd.DataFrame:
    print("--> Calculating Wall Brightness from detections...")
    for column in ['mean_R', 'mean_G', 'mean_B']:
        if column not in line_info_df.columns:
            line_info_df[column] = np.nan

    for idx, row in tqdm(line_info_df.iterrows(), total=len(line_info_df), desc="  Processing SVI"):
        osm_id = str(int(row['osm_id']))
        mapillary_id = str(int(row['mapillary_id']))
        line_x_full = row['line_x_pixel']
        half_side = row['half_cropped_image']
        if pd.isna(half_side) or pd.isna(line_x_full):
            continue
        is_right = half_side.lower() == 'right'
        offset = cfg.IMAGE_WIDTH / 2 if is_right else 0
        image_path = cfg.SVI_CHIPS_DIR / f"{osm_id}_svi.png"
        csv_path = cfg.MAPILLARY_DETECTIONS_DIR / f"{mapillary_id}.csv"
        if not (image_path.exists() and csv_path.exists()):
            continue
        image = Image.open(image_path).convert('RGB')
        detections_df = pd.read_csv(csv_path)
        target_detections = detections_df[detections_df['value'].isin(cfg.TARGET_CLASSES.keys())]
        if target_detections.empty:
            continue
        mask_image = Image.new('1', image.size, 0)
        draw = ImageDraw.Draw(mask_image)
        for _, det_row in target_detections.iterrows():
            try:
                decoded = base64.b64decode(det_row['geometry'])
                tile = mapbox_vector_tile.decode(decoded)
                for layer in tile.values():
                    extent = layer.get('extent', 4096)
                    for feature in layer['features']:
                        if feature['geometry']['type'] != 'Polygon':
                            continue
                        for polygon_coords in feature['geometry']['coordinates']:
                            scaled_coords = [
                                (
                                    int((x / extent) * cfg.IMAGE_WIDTH - offset),
                                    int(cfg.IMAGE_HEIGHT - (y / extent) * cfg.IMAGE_HEIGHT),
                                )
                                for x, y in polygon_coords
                            ]
                            draw.polygon(scaled_coords, outline=1, fill=1)
            except Exception:
                continue
        np_img = np.array(image).astype(float)
        np_mask = np.array(mask_image, dtype=bool)
        np_img[~np_mask] = np.nan
        mean_rgb = np.nanmean(np_img, axis=(0, 1))
        line_info_df.loc[idx, ['mean_R', 'mean_G', 'mean_B']] = mean_rgb
    line_info_df['wall_brightness'] = line_info_df[['mean_R', 'mean_G', 'mean_B']].mean(axis=1)
    return line_info_df


def calculate_roof_brightness(df: pd.DataFrame) -> pd.DataFrame:
    print("--> Calculating Roof Brightness...")
    results = []
    for _, row in tqdm(df.iterrows(), total=len(df), desc="  Processing UAV"):
        osm_id = str(int(row['osm_id']))
        tif_path = cfg.UAV_CHIPS_DIR / f"{osm_id}_uav.tif"
        if not tif_path.exists():
            results.append({'osm_id': osm_id})
            continue
        try:
            with rasterio.open(tif_path) as src:
                img = src.read().astype(float)
                nodata = src.nodata if src.nodata is not None else 0
                valid = np.all(img != nodata, axis=0)
                if not np.any(valid):
                    results.append({'osm_id': osm_id})
                    continue
                mean_rgb = [np.mean(band[valid]) for band in img[:3]]
                results.append({
                    'osm_id': osm_id,
                    'uav_mean_r': mean_rgb[0],
                    'uav_mean_g': mean_rgb[1],
                    'uav_mean_b': mean_rgb[2],
                })
        except Exception as exc:
            print(f"    Warning: Could not process {tif_path}: {exc}")
            results.append({'osm_id': osm_id})
    uav_df = pd.DataFrame(results)
    if not uav_df.empty and {'uav_mean_r', 'uav_mean_g', 'uav_mean_b'} <= set(uav_df.columns):
        uav_df['roof_brightness'] = uav_df[['uav_mean_r', 'uav_mean_g', 'uav_mean_b']].mean(axis=1)
    df['osm_id'] = df['osm_id'].astype(str)
    return pd.merge(df, uav_df, on='osm_id', how='left')


def calculate_vegetation_presence(df: pd.DataFrame) -> pd.DataFrame:
    print("--> Calculating vegetation presence...")
    results = []
    for _, row in tqdm(df.iterrows(), total=len(df), desc="  Checking for vegetation"):
        osm_id = str(int(row['osm_id']))
        mapillary_id = str(int(row['mapillary_id']))
        csv_path = cfg.MAPILLARY_DETECTIONS_DIR / f"{mapillary_id}.csv"
        if not csv_path.exists():
            results.append({'osm_id': osm_id, 'contains_vegetation': False})
            continue
        detections_df = pd.read_csv(csv_path)
        found_veg = not detections_df[detections_df['value'] == 'nature--vegetation'].empty
        results.append({'osm_id': osm_id, 'contains_vegetation': found_veg})
    veg_df = pd.DataFrame(results)
    df['osm_id'] = df['osm_id'].astype(str)
    return pd.merge(df, veg_df, on='osm_id', how='left')
