# Mapillary Click Preview (adapted in this repository)

This folder preserves and adapts the original [`annadeckmyn/MapillaryClickPreview`](https://github.com/annadeckmyn/MapillaryClickPreview/) QGIS plugin work already present in this repository.

In `RKsGIS/UAV-SVI-Annotation-Pipeline`, it serves two purposes:

- keep the original Mapillary coverage / click-preview workflow available
- provide a starting point for future UAV/SVI-specific extensions such as scene selection, download/display buttons, OSM building overlays, compass arrows, and line symbology

## Attribution

- Original plugin author: Anna Deckmyn
- Original upstream repository: <https://github.com/annadeckmyn/MapillaryClickPreview/>
- This repository keeps that code visible and adds lightweight scaffolding (`plugin_main.py`, `symbology/`) without claiming the upstream plugin as original work.

## What It Does Today

- Adds a toggle tool for click-only preview on the map canvas.
- Loads Mapillary coverage as vector layers (`image`, `sequence`) from:
  - `mly1_public` (original)
  - `mly1_computed_public` (computed)
- Opens a docked preview panel with:
  - thumbnail
  - image metadata (ID, year, compass, pano flag)
  - link to open the image in Mapillary
- Supports auto-preview when:
  - selecting a feature in the `Mapillary image` layer
  - clicking with QGIS Identify mode
- Includes an optional year filter (`captured_at`) for coverage layers.

## Requirements

- QGIS `>= 3.44` and `< 5.0`
- Internet access for Mapillary API and tiles
- A Mapillary access token

## Installation (From Source)

1. Close QGIS.
2. Copy this folder into your QGIS plugin directory.
3. Rename the copied folder to `MapillaryClickPreview`.
4. Restart QGIS.
5. Enable the plugin in `Plugins > Manage and Install Plugins`.

Typical plugin directories:

- Windows (QGIS 3.x): `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins`
- Windows (QGIS 4.x): `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins`
- Linux (QGIS 3.x): `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins`
- Linux (QGIS 4.x): `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins`
- macOS (QGIS 3.x): `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins`
- macOS (QGIS 4.x): `~/Library/Application Support/QGIS/QGIS4/profiles/default/python/plugins`

## First-Time Setup

1. Open `Plugins > Mapillary > Mapillary Token...`
2. Paste your Mapillary access token and save.

The token is stored in QGIS settings under `mapillary/access_token`.

## Usage

1. Load coverage:
   - `Plugins > Mapillary > Load Mapillary Coverage (Original)`
   - or `Plugins > Mapillary > Load Mapillary Coverage (Computed)`
2. Optional: set year filtering via `Plugins > Mapillary > Filter Mapillary Coverage by Year...`
3. Start click-only preview using the toolbar button `Mapillary Click Preview`.
4. Left-click near an image feature to fetch and preview the nearest image.
5. Right-click to stop click-only mode and restore the previous map tool.

## Development Notes

- Active upstream-style entry point: `mapillary_click_preview.py`
- Click and preview logic: `mapillary_click_tool.py`
- Local scaffold for future UAV/SVI actions: `plugin_main.py`
- Placeholder styles for future annotation overlays: `symbology/`

## License

This plugin remains under GNU GPL v2 or later. See `LICENSE` for the full text.

## Disclaimer

This plugin is an independent tool and is not officially affiliated with Mapillary or Meta.
