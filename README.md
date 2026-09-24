# UAV-SVI Annotation Pipeline

This repository contains a lightweight UAV/SVI pairing pipeline for building-level annotation tasks. It reuses and adapts core logic from the sibling repository [`RKsGIS/Assessing-Building-Heat-Resilience-Using-UAV-and-Street-Imagery-with-Coupled-Vision-Transformers`](https://github.com/RKsGIS/Assessing-Building-Heat-Resilience-Using-UAV-and-Street-Imagery-with-Coupled-Vision-Transformers), especially the original `preprocessing/` steps for Mapillary fetching, geospatial filtering, UAV masking, and panorama-facing crop logic.

The supported workflow in this repository is the new numbered `scripts/` layout:

1. `python scripts/00_select_scenes.py`
2. `python scripts/01_download_uav.py`
3. `python scripts/02_download_svi.py`
4. `python scripts/03_mask_uav.py`
5. `python scripts/04_mask_svi.py`

## Repository Layout

```
UAV-SVI-Annotation-Pipeline/
├── qgis_plugin/                 # QGIS plugin skeleton + existing plugin work
├── scripts/                     # Standalone backend pipeline
├── labeling_schemas/            # Annotation tool templates
├── data/
│   ├── input/                   # User-provided .gpkg / .qgs / .qgz and optional OSM buildings
│   └── output_pairs/            # Final {osm_id}_uav.png / {osm_id}_svi.png chips
├── preprocessing/               # Legacy/source preprocessing retained for reference
├── requirements.txt
└── README.md
```

## Inputs

Place the user-supplied source files in `data/input/`:

- a GeoPackage containing OAM scene polygons, **or**
- a QGIS project file (`.qgs` / `.qgz`) that references the scene layer
- optionally an OSM buildings layer used for filtering and downstream chip creation

The scene layer should contain one polygon per UAV scene. If possible, keep these fields in the layer (or rename them with CLI arguments):

- a scene identifier (`scene_id`, `id`, `uuid`, `oam_id`, ...)
- a download URL for the raster (`download_url`, `asset_url`, `url`, `tif_url`, ...)

## Sequential Workflow

### 0. Select scenes

`scripts/00_select_scenes.py` reads the input GeoPackage or QGIS project, optionally intersects it with an OSM buildings layer, and writes `data/selected_scenes.gpkg`.

Example:

```bash
python scripts/00_select_scenes.py \
  --input data/input/oam_scenes.gpkg \
  --buildings data/input/osm_buildings.gpkg \
  --require-buildings
```

Outputs:

- `data/selected_scenes.gpkg`

### 1. Download UAV scenes

`scripts/01_download_uav.py` downloads the raster for each selected scene and assigns each building to the **largest intersecting raster that fully covers it**. If the first candidate fails, the script falls back to the next intersecting scene.

Example:

```bash
python scripts/01_download_uav.py \
  --selected-scenes data/selected_scenes.gpkg \
  --buildings data/input/osm_buildings.gpkg
```

Outputs:

- `data/intermediate/uav_rasters/*.tif`
- `data/intermediate/building_uav_assignments.gpkg`

### 2. Download SVI panoramas

`scripts/02_download_svi.py` fetches Mapillary image metadata near each building centroid, applies the nearby-point + visibility + compass-angle logic adapted from the sibling repo, and downloads the selected panorama(s).

Example:

```bash
python scripts/02_download_svi.py \
  --selected-scenes data/selected_scenes.gpkg \
  --buildings data/intermediate/building_uav_assignments.gpkg
```

Outputs:

- `data/intermediate/mapillary_points.gpkg`
- `data/intermediate/buildings_with_svi.gpkg`
- `data/intermediate/svi_panoramas/*.jpg`

### 3. Mask UAV chips

`scripts/03_mask_uav.py` crops each assigned UAV raster around its building footprint and applies a building mask. It writes final annotation chips to `data/output_pairs/`.

```bash
python scripts/03_mask_uav.py
```

Outputs:

- `data/intermediate/uav_chips/*.tif`
- `data/output_pairs/{osm_id}_uav.png`

### 4. Mask SVI chips

`scripts/04_mask_svi.py` reads the stored building-to-panorama geometry, crops the building-facing section of the panorama, and saves paired chips to `data/output_pairs/`.

```bash
python scripts/04_mask_svi.py
```

Outputs:

- `data/intermediate/svi_chips/*.png`
- `data/output_pairs/{osm_id}_svi.png`

## Labeling

The repository includes `labeling_schemas/label_studio_schema.xml` for side-by-side UAV/SVI annotation. The schema expects fields such as:

- `uav_image` (URL or local path exposed to Label Studio for the UAV chip)
- `svi_image` (URL or local path exposed to Label Studio for the SVI chip)
- `osm_id`
- `structural_openness`
- `number_of_floors`
- `vegetation`
- `material_rooftop`
- `material_wall`

Example row:

```text
338449341, closed_structure, one, yes, metal, concrete
```

## QGIS Plugin Note

The `qgis_plugin/` folder is intentionally kept lightweight here. It provides a starting skeleton for interactive scene selection and preview (download/display button flow, OSM building overlays, compass arrows, red linking lines), but the CLI scripts are the supported pipeline entry point and do **not** require QGIS.

## Environment

Create a `.env` file in the repository root for API-backed steps:

```env
MAPILLARY_ACCESS_TOKEN=your_token_here
MAPILLARY_CREATOR_USERNAME=
```

Install dependencies with:

```bash
pip install -r requirements.txt
```
