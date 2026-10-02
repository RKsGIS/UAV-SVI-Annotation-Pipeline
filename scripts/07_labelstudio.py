#!/usr/bin/env python3
"""
07_labelstudio.py
  tasks   -> submission/labelstudio_tasks.json to import into Label Studio
  export  -> merge a Label Studio JSON export into submission/labels_<name>.csv

Label Studio must serve submission/ as local files:
  set LOCAL_FILES_SERVING_ENABLED=true and LOCAL_FILES_DOCUMENT_ROOT=<repo>/submission
"""

import argparse
import json

import pandas as pd

from utils import pipeline_config as cfg
from utils.labels import CSV_COLUMNS, LABEL_COLUMNS, VOCAB

SUBMISSION_DIR = cfg.ROOT_DIR / "submission"


def cmd_tasks(args) -> None:
    ids = sorted(p.stem for p in (SUBMISSION_DIR / "uav").glob("*.png"))
    tasks = [{"data": {
        "osm_id": i,
        "uav_image": f"/data/local-files/?d=uav/{i}.png",
        "streetview_image": f"/data/local-files/?d=svi/{i}.png",
    }} for i in ids]
    out = SUBMISSION_DIR / "labelstudio_tasks.json"
    out.write_text(json.dumps(tasks, indent=2))
    print(f"{len(tasks)} tasks -> {out}")


def cmd_export(args) -> None:
    csv_path = SUBMISSION_DIR / f"labels_{args.name}.csv"
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False).set_index("osm_id")
    for task in json.loads(open(args.export, encoding="utf-8").read()):
        osm_id = str(task["data"]["osm_id"])
        annotations = [a for a in task.get("annotations", []) if not a.get("was_cancelled")]
        if osm_id not in df.index or not annotations:
            continue
        for item in annotations[-1]["result"]:
            col = item["from_name"]
            choices = item["value"].get("choices", [])
            if col in LABEL_COLUMNS and choices and choices[0] in VOCAB[col]:
                df.loc[osm_id, col] = choices[0]
    df.reset_index()[CSV_COLUMNS].to_csv(csv_path, index=False)
    print(f"Updated {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True)
    sub.add_parser("tasks").set_defaults(func=cmd_tasks)
    export = sub.add_parser("export")
    export.add_argument("--name", required=True)
    export.add_argument("--export", required=True, help="Label Studio JSON export file")
    export.set_defaults(func=cmd_export)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
