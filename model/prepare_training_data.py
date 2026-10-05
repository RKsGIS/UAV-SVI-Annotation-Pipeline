#!/usr/bin/env python3
"""
Turns the uploaded pairs into the layout expected by training/train_classifier_single_input.py
of the Assessing-Building-Heat-Resilience repository:

  <out-dir>/labels_data/{uav,svi}/<task>/<class>/<osm_id>.png
  <out-dir>/CV_classdata.csv      columns: osm_id, mapillary_id, is_pano, task, label, svi_path, uav_path

Input (--data-dir, e.g. /content/drive/MyDrive/CV_dataset):
  uav/<osm_id>.png, svi/<osm_id>.png, labels/labels_<name>.csv (one file per student)

Labels 'unknown' and empty labels are skipped. --pano-only keeps only 360 panoramas (is_pano true).

  python model/prepare_training_data.py --data-dir /content/drive/MyDrive/CV_dataset --out-dir output --pano-only
"""

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from utils.labels import LABEL_COLUMNS  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--pano-only", action="store_true")
    args = parser.parse_args()

    data, out = Path(args.data_dir), Path(args.out_dir).resolve()
    files = sorted((data / "labels").glob("labels_*.csv")) or sorted(data.glob("labels_*.csv"))
    if not files:
        raise SystemExit(f"No labels_*.csv found in {data}/labels or {data}")
    labels = pd.concat([pd.read_csv(f, dtype=str, keep_default_na=False).assign(labeller=f.stem[7:]) for f in files], ignore_index=True)

    duplicated = labels["osm_id"].duplicated()
    if duplicated.any():
        print(f"{int(duplicated.sum())} duplicate osm_id rows (labelled by several students), keeping the first.")
        labels = labels[~duplicated]
    if args.pano_only:
        labels = labels[labels["is_pano"].str.lower().isin(["true", "1"])]

    rows = []
    for rec in labels.itertuples():
        src = {v: data / v / f"{rec.osm_id}.png" for v in ("uav", "svi")}
        if not all(p.exists() for p in src.values()):
            continue
        for task in LABEL_COLUMNS:
            label = getattr(rec, task)
            if label in ("", "unknown"):
                continue
            dst = {}
            for view, path in src.items():
                target = out / "labels_data" / view / task / label / f"{rec.osm_id}.png"
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copy2(path, target)
                dst[view] = target.as_posix()
            rows.append({"osm_id": rec.osm_id, "mapillary_id": rec.mapillary_id, "is_pano": rec.is_pano, "task": task,
                         "label": label, "svi_path": dst["svi"], "uav_path": dst["uav"]})

    table = pd.DataFrame(rows)
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "CV_classdata.csv", index=False)
    print(f"{labels['osm_id'].nunique()} labelled buildings -> {out / 'CV_classdata.csv'}")
    print(table.groupby(["task", "label"]).size().to_string())


if __name__ == "__main__":
    main()
