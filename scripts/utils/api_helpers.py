from __future__ import annotations

import os

import pandas as pd
import requests
from tqdm import tqdm

from .pipeline_config import MAPILLARY_ACCESS_TOKEN, MAPILLARY_DETECTIONS_DIR


def fetch_mapillary_detections(image_ids):
    """Ported from the sibling repo's preprocessing utils."""
    print("--> Fetching Mapillary object detections...")
    os.makedirs(MAPILLARY_DETECTIONS_DIR, exist_ok=True)
    unique_ids = list(dict.fromkeys(str(image_id) for image_id in image_ids if pd.notna(image_id)))
    for image_id in tqdm(unique_ids, desc="  Fetching detections"):
        csv_path = os.path.join(MAPILLARY_DETECTIONS_DIR, f"{image_id}.csv")
        if os.path.exists(csv_path):
            continue
        url = (
            f"https://graph.mapillary.com/{image_id}/detections"
            f"?access_token={MAPILLARY_ACCESS_TOKEN}&fields=image,value,geometry"
        )
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json().get('data', [])
                if data:
                    pd.DataFrame(data).to_csv(csv_path, index=False)
            else:
                print(f"    Warning: Failed for ID {image_id}: {response.status_code}")
        except requests.RequestException as exc:
            print(f"    Error for ID {image_id}: {exc}")
