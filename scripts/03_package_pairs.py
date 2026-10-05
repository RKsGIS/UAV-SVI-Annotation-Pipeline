#!/usr/bin/env python3
"""
03_package_pairs.py
Turns data/output_pairs/{osm_id}_uav.png + {osm_id}_svi.png into the submission format:
  submission/uav/{osm_id}.png   256x256, aspect ratio kept (letterboxed)
  submission/svi/{osm_id}.png   256x256
  submission/labels_<name>.csv  osm_id + empty label columns (existing labels are kept)
  submission/pairs_index_<name>.csv  osm_id -> scene_id, mapillary_id, distance and viewing angle
"""

import argparse
import re

import cv2
import numpy as np
import pandas as pd

from utils import pipeline_config as cfg
from utils.labels import CSV_COLUMNS, IMAGE_SIZE, META_COLUMNS

SUBMISSION_DIR = cfg.ROOT_DIR / "submission"
INDEX_COLUMNS = ["osm_id", "scene_id", "mapillary_id", "distance_m", "bearing", "relative_angle", "is_pano"]


def letterbox(img: np.ndarray, size: int = IMAGE_SIZE) -> np.ndarray:
    """Crop the black margin, pad to a square, then resize (never stretches)."""
    valid = np.argwhere(img.any(axis=-1))
    if valid.size:
        (y0, x0), (y1, x1) = valid.min(0), valid.max(0) + 1
        img = img[y0:y1, x0:x1]
    h, w = img.shape[:2]
    side = max(h, w)
    canvas = np.zeros((side, side, 3), dtype=img.dtype)
    canvas[(side - h) // 2:(side - h) // 2 + h, (side - w) // 2:(side - w) // 2 + w] = img
    return cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Your student name/ID, used in the labels file name.")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        raise SystemExit("--name may only contain letters, digits, '_' and '-'.")

    uav_out, svi_out = SUBMISSION_DIR / "uav", SUBMISSION_DIR / "svi"
    uav_out.mkdir(parents=True, exist_ok=True)
    svi_out.mkdir(parents=True, exist_ok=True)

    ids = []
    for uav_path in sorted(cfg.OUTPUT_PAIRS_DIR.glob("*_uav.png")):
        osm_id = uav_path.name[: -len("_uav.png")]
        svi_path = cfg.OUTPUT_PAIRS_DIR / f"{osm_id}_svi.png"
        if not svi_path.exists():
            continue
        uav, svi = cv2.imread(str(uav_path)), cv2.imread(str(svi_path))
        if uav is None or svi is None:
            continue
        cv2.imwrite(str(uav_out / f"{osm_id}.png"), letterbox(uav))
        cv2.imwrite(str(svi_out / f"{osm_id}.png"), letterbox(svi))
        ids.append(osm_id)

    csv_path = SUBMISSION_DIR / f"labels_{args.name}.csv"
    new = pd.DataFrame({"osm_id": ids})
    if csv_path.exists():
        old = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
        new = new.merge(old, on="osm_id", how="left")
    if cfg.PAIRS_CSV.exists():
        meta = pd.read_csv(cfg.PAIRS_CSV, dtype=str, keep_default_na=False)[["osm_id", *META_COLUMNS]]
        new = new.drop(columns=META_COLUMNS, errors="ignore").merge(meta, on="osm_id", how="left")
    for col in CSV_COLUMNS:
        if col not in new:
            new[col] = ""
    new[CSV_COLUMNS].fillna("").to_csv(csv_path, index=False)
    print(f"{len(ids)} complete pairs -> {SUBMISSION_DIR}\nLabel template: {csv_path}")

    if cfg.PAIRS_CSV.exists():
        info = pd.read_csv(cfg.PAIRS_CSV, dtype=str, keep_default_na=False)
        keep = [c for c in INDEX_COLUMNS if c in info.columns]
        index_path = SUBMISSION_DIR / f"pairs_index_{args.name}.csv"
        info[info["osm_id"].isin(ids)][keep].to_csv(index_path, index=False)
        print(f"Pair provenance (osm_id -> Mapillary id, scene): {index_path}")


if __name__ == "__main__":
    main()
