from __future__ import annotations

import json
from pathlib import Path

import requests
from tqdm import tqdm

from . import pipeline_config as cfg

OAM_META_URL = "https://api.openaerialmap.org/meta"


def fetch_scene_meta(scene_id: str) -> dict:
    """OAM metadata record of one scene (cached). 'uuid' is the GeoTIFF URL, 'bbox' is lon/lat."""
    cache = cfg.OAM_META_DIR / f"{scene_id}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    r = requests.get(OAM_META_URL, params={"_id": scene_id}, timeout=60)
    r.raise_for_status()
    results = r.json().get("results", [])
    if not results:
        raise ValueError(f"No OAM scene with _id={scene_id}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(results[0]))
    return results[0]


def download_uav_raster(url: str, destination: str | Path) -> Path:
    """Download once; a partially downloaded file is never mistaken for a finished one."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return destination
    part = destination.with_suffix(destination.suffix + ".part")
    with requests.get(url, stream=True, timeout=(15, 120)) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0)) or None
        with part.open("wb") as handle, tqdm(total=total, unit="B", unit_scale=True, desc=destination.name) as bar:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                handle.write(chunk)
                bar.update(len(chunk))
    part.rename(destination)
    return destination
