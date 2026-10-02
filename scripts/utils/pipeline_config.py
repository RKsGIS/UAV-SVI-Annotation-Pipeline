from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
INPUT_DIR = DATA_DIR / "input"
PACKAGES_DIR = INPUT_DIR / "packages"
INTERMEDIATE_DIR = DATA_DIR / "intermediate"
OUTPUT_PAIRS_DIR = DATA_DIR / "output_pairs"

CATALOG_DIR = DATA_DIR / "catalog"
ALL_UAV_JSON = CATALOG_DIR / "all_uav.json"
OAM_SCENES_FILE = CATALOG_DIR / "oam_scenes.gpkg"
SCENE_BBOX_CSV = CATALOG_DIR / "scene_bbox_pass_fail.csv"
CANDIDATES_CSV = CATALOG_DIR / "candidates_with_osm_status.csv"
SCORES_CSV = CATALOG_DIR / "scene_enrichment_scores.csv"
SCENES_MASTER_FILE = CATALOG_DIR / "scenes_master.gpkg"
SCENE_CLAIMS_CSV = CATALOG_DIR / "scene_claims.csv"
CHECK_CACHE_DIR = CATALOG_DIR / "cache"
OSM_DIR = CATALOG_DIR / "osm_geojson_results"
MAP_DIR = CATALOG_DIR / "mapillary_geojson_results"
LOS_DIR = CATALOG_DIR / "los_geojson_results"
NE_COUNTRIES_URL = "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip"

SELECTED_SCENES_FILE = DATA_DIR / "selected_scenes.gpkg"
BUILDING_ASSIGNMENTS_FILE = INTERMEDIATE_DIR / "building_uav_assignments.gpkg"
MAPILLARY_POINTS_FILE = INTERMEDIATE_DIR / "mapillary_points.gpkg"
BUILDINGS_WITH_SVI_FILE = INTERMEDIATE_DIR / "buildings_with_svi.gpkg"
SVI_VIEW_GEOMETRY_CSV = INTERMEDIATE_DIR / "svi_view_geometry.csv"
UAV_RASTERS_DIR = INTERMEDIATE_DIR / "uav_rasters"
SVI_PANORAMAS_DIR = INTERMEDIATE_DIR / "svi_panoramas"
UAV_CHIPS_DIR = INTERMEDIATE_DIR / "uav_chips"
SVI_CHIPS_DIR = INTERMEDIATE_DIR / "svi_chips"
MAPILLARY_DETECTIONS_DIR = INTERMEDIATE_DIR / "mapillary_detections"

load_dotenv(ROOT_DIR / ".env")

MAPILLARY_ACCESS_TOKEN = os.getenv("MAPILLARY_ACCESS_TOKEN")
MAPILLARY_CREATOR_USERNAME = os.getenv("MAPILLARY_CREATOR_USERNAME")
MAPILLARY_FIELDS = ",".join([
    "id",
    "geometry",
    "computed_geometry",
    "camera_type",
    "compass_angle",
    "computed_compass_angle",
    "altitude",
    "computed_altitude",
    "height",
    "width",
    "is_pano",
    "thumb_original_url",
    "captured_at",
    "sequence",
])

DEFAULT_SEARCH_RADIUS_METERS = 30
DEFAULT_TILE_SIZE_METERS = 100
DEFAULT_SCENE_ID_FIELDS = ["scene_id", "id", "uuid", "oam_id", "item_uuid", "item_id", "catalog_id"]
DEFAULT_SCENE_URL_FIELDS = ["download_url", "asset_url", "url", "tif_url", "raster_url", "cog_url", "ortho_url"]
DEFAULT_BUILDING_ID_FIELDS = ["osm_id", "id", "building_id"]

IMAGE_WIDTH = 4096
IMAGE_HEIGHT = 2048
CENTER_X = IMAGE_WIDTH / 2
PIXELS_PER_DEGREE = IMAGE_WIDTH / 360

TARGET_CLASSES = {
    "construction--barrier--wall": "orange",
    "construction--structure--building": "red",
}

def ensure_runtime_directories() -> None:
    for path in [
        INPUT_DIR,
        CATALOG_DIR,
        CHECK_CACHE_DIR,
        OSM_DIR,
        MAP_DIR,
        LOS_DIR,
        INTERMEDIATE_DIR,
        OUTPUT_PAIRS_DIR,
        UAV_RASTERS_DIR,
        SVI_PANORAMAS_DIR,
        UAV_CHIPS_DIR,
        SVI_CHIPS_DIR,
        MAPILLARY_DETECTIONS_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)
