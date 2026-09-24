from __future__ import annotations

import argparse

import cv2
import geopandas as gpd

from utils import pipeline_config as cfg
from utils.image_processing import apply_focus_mask, extract_wrapped_panorama_window


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Crop/mask building-facing SVI chips from downloaded panoramas.')
    parser.add_argument('--assignments', default=cfg.BUILDINGS_WITH_SVI_FILE)
    parser.add_argument('--window-width', type=int, default=1536)
    parser.add_argument('--focus-ratio', type=float, default=0.6)
    return parser


def main() -> None:
    cfg.ensure_runtime_directories()
    args = build_parser().parse_args()
    assignments = gpd.read_file(args.assignments)
    if assignments.empty:
        raise RuntimeError('The SVI assignment file is empty.')
    for row in assignments.itertuples():
        panorama_path = row.panorama_path
        if not panorama_path:
            continue
        image = cv2.imread(panorama_path, cv2.IMREAD_COLOR)
        if image is None:
            continue
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        crop = extract_wrapped_panorama_window(image, row.line_x_pixel, args.window_width)
        masked = apply_focus_mask(crop, args.focus_ratio)
        intermediate_path = cfg.SVI_CHIPS_DIR / f'{row.osm_id}_svi.png'
        output_path = cfg.OUTPUT_PAIRS_DIR / f'{row.osm_id}_svi.png'
        cv2.imwrite(str(intermediate_path), cv2.cvtColor(masked, cv2.COLOR_RGB2BGR))
        cv2.imwrite(str(output_path), cv2.cvtColor(masked, cv2.COLOR_RGB2BGR))
    print(f'Wrote SVI chips to {cfg.OUTPUT_PAIRS_DIR}')


if __name__ == '__main__':
    main()
