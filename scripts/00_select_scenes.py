from __future__ import annotations

import argparse
from pathlib import Path

from utils import pipeline_config as cfg
from utils.geospatial import (
    assign_scene_ids,
    count_buildings_per_scene,
    discover_default_buildings,
    discover_default_input,
    load_vector_data,
    optional_query_filter,
    prepare_geometries,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Select/filter OAM scene polygons for the pipeline.')
    parser.add_argument('--input', type=Path, default=None, help='Scene GeoPackage or QGIS project path.')
    parser.add_argument('--layer', default=None, help='Optional layer name for GeoPackage / QGIS project input.')
    parser.add_argument('--buildings', type=Path, default=None, help='Optional OSM buildings layer used for filtering.')
    parser.add_argument('--buildings-layer', default=None, help='Optional layer name for the buildings input.')
    parser.add_argument('--output', type=Path, default=cfg.SELECTED_SCENES_FILE, help='Output GeoPackage path.')
    parser.add_argument('--require-buildings', action='store_true', help='Keep only scenes intersecting at least one building.')
    parser.add_argument('--scene-query', default=None, help='Optional pandas eval expression, e.g. "quality_score > 0.8".')
    parser.add_argument('--max-scenes', type=int, default=None, help='Limit output to the first N filtered scenes.')
    return parser


def main() -> None:
    cfg.ensure_runtime_directories()
    args = build_parser().parse_args()
    scene_input = args.input or discover_default_input()
    if scene_input is None:
        raise FileNotFoundError('No scene input was supplied and no default file was found in data/input/.')

    scenes = prepare_geometries(assign_scene_ids(load_vector_data(scene_input, args.layer)))
    if args.scene_query:
        scenes = optional_query_filter(scenes, args.scene_query)

    buildings_input = args.buildings or discover_default_buildings()
    if buildings_input is not None:
        buildings = prepare_geometries(load_vector_data(buildings_input, args.buildings_layer))
        scenes = count_buildings_per_scene(scenes, buildings)
    elif 'has_osm_building' not in scenes.columns:
        scenes['building_count'] = 0
        scenes['has_osm_building'] = False

    if args.require_buildings:
        scenes = scenes.loc[scenes['has_osm_building']].copy()
    if args.max_scenes is not None:
        scenes = scenes.head(args.max_scenes).copy()
    if scenes.empty:
        raise RuntimeError('Scene selection removed every feature; widen the filters and run again.')

    args.output.parent.mkdir(parents=True, exist_ok=True)
    scenes.to_file(args.output, driver='GPKG')
    print(f'Saved {len(scenes)} selected scenes to {args.output}')
    if 'building_count' in scenes.columns:
        print(f"Scenes with buildings: {(scenes['building_count'] > 0).sum()} / {len(scenes)}")


if __name__ == '__main__':
    main()
