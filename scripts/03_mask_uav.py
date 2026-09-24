from __future__ import annotations

import argparse

import cv2
import geopandas as gpd
import rasterio

from utils import pipeline_config as cfg
from utils.image_processing import mask_uav_building_chip, raster_to_png_array


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Create masked UAV chips for each assigned building.')
    parser.add_argument('--assignments', default=cfg.BUILDING_ASSIGNMENTS_FILE)
    parser.add_argument('--padding-factor', type=float, default=1.25)
    return parser


def main() -> None:
    cfg.ensure_runtime_directories()
    args = build_parser().parse_args()
    assignments = gpd.read_file(args.assignments)
    if assignments.empty:
        raise RuntimeError('The UAV assignment file is empty.')
    for row in assignments.itertuples():
        raster_path = row.uav_raster_path
        masked_image, metadata = mask_uav_building_chip(raster_path, row.geometry, assignments.crs, args.padding_factor)
        tif_path = cfg.UAV_CHIPS_DIR / f'{row.osm_id}_uav.tif'
        png_path = cfg.OUTPUT_PAIRS_DIR / f'{row.osm_id}_uav.png'
        with rasterio.open(tif_path, 'w', **metadata) as dst:
            dst.write(masked_image)
        png = raster_to_png_array(masked_image)
        cv2.imwrite(str(png_path), cv2.cvtColor(png, cv2.COLOR_RGB2BGR))
    print(f'Wrote {len(assignments)} UAV chips to {cfg.OUTPUT_PAIRS_DIR}')


if __name__ == '__main__':
    main()
