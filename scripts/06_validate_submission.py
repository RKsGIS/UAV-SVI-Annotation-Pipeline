#!/usr/bin/env python3
"""
06_validate_submission.py
Checks submission/labels_<name>.csv against the label vocabulary and the image folders.
Exit code is non-zero if anything is wrong.
"""

import argparse
import sys

import cv2
import pandas as pd

from utils.labels import CSV_COLUMNS, IMAGE_SIZE, LABEL_COLUMNS, VOCAB
from utils import pipeline_config as cfg

SUBMISSION_DIR = cfg.ROOT_DIR / "submission"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    csv_path = SUBMISSION_DIR / f"labels_{args.name}.csv"
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    errors = []
    if list(df.columns) != CSV_COLUMNS:
        errors.append(f"columns must be exactly {CSV_COLUMNS}, got {list(df.columns)}")
    else:
        if df["osm_id"].duplicated().any():
            errors.append("duplicate osm_id values")
        for col in LABEL_COLUMNS:
            bad = df.loc[~df[col].isin(VOCAB[col]), ["osm_id", col]]
            errors += [f"{r.osm_id}: {col}='{getattr(r, col)}' not in {VOCAB[col]}" for r in bad.itertuples()]
        for osm_id in df["osm_id"]:
            for view in ("uav", "svi"):
                img = cv2.imread(str(SUBMISSION_DIR / view / f"{osm_id}.png"))
                if img is None or img.shape[:2] != (IMAGE_SIZE, IMAGE_SIZE):
                    errors.append(f"{osm_id}: {view}/{osm_id}.png missing or not {IMAGE_SIZE}x{IMAGE_SIZE}")

    for e in errors[:50]:
        print("ERROR", e)
    print(f"{len(df)} rows, {len(errors)} problems" + (" (first 50 shown)" if len(errors) > 50 else ""))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
