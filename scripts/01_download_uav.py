from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
from utils import pipeline_config as cfg
from utils.geospatial import (
    assign_building_ids,
    assign_scene_ids,
    choose_identifier_field,
    discover_default_buildings,
    load_vector_data,
    prepare_geometries,
    project_to_local_metric,
)
from utils.oam import download_uav_raster, raster_fully_covers_geometry, resolve_scene_asset_url


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Download UAV rasters and assign buildings to the best covering scene.')
    parser.add_argument('--selected-scenes', type=Path, default=cfg.SELECTED_SCENES_FILE)
    parser.add_argument('--scene-layer', default=None)
    parser.add_argument('--buildings', type=Path, default=None)
    parser.add_argument('--buildings-layer', default=None)
    parser.add_argument('--url-field', default=None, help='Optional explicit raster URL field name.')
    parser.add_argument('--output', type=Path, default=cfg.BUILDING_ASSIGNMENTS_FILE)
    return parser


def main() -> None:
    cfg.ensure_runtime_directories()
    args = build_parser().parse_args()
    buildings_path = args.buildings or discover_default_buildings()
    if buildings_path is None:
        raise FileNotFoundError('No buildings layer was supplied and no default building file was found in data/input/.')

    scenes = prepare_geometries(assign_scene_ids(load_vector_data(args.selected_scenes, args.scene_layer)))
    buildings = prepare_geometries(assign_building_ids(load_vector_data(buildings_path, args.buildings_layer)))
    scenes_metric, buildings_metric = project_to_local_metric(scenes, buildings)

    buildings_metric = buildings_metric.reset_index().rename(columns={'index': 'building_index'})
    buildings = buildings.reset_index().rename(columns={'index': 'building_index'})
    scene_lookup = scenes_metric[['scene_id', 'geometry']].copy()
    join = gpd.sjoin(
        buildings_metric[['building_index', 'osm_id', 'geometry']],
        scene_lookup,
        predicate='intersects',
        how='inner',
    )
    if join.empty:
        raise RuntimeError('No buildings intersect the selected scenes.')

    join['intersection_area_m2'] = join.apply(
        lambda row: buildings_metric.loc[
            buildings_metric['building_index'] == row['building_index'],
            'geometry',
        ].iloc[0].intersection(scene_lookup.loc[row['index_right'], 'geometry']).area,
        axis=1,
    )
    join = join.sort_values(['osm_id', 'intersection_area_m2'], ascending=[True, False])

    url_field = args.url_field or choose_identifier_field(scenes.columns, cfg.DEFAULT_SCENE_URL_FIELDS)
    assignments = []
    for osm_id, group in join.groupby('osm_id', sort=False):
        building_index = group.iloc[0]['building_index']
        building_geom_wgs84 = buildings.loc[buildings['building_index'] == building_index, 'geometry'].iloc[0]
        assigned = None
        for candidate_rank, candidate in enumerate(group.itertuples(), start=1):
            scene_id = candidate.scene_id
            scene_row = scenes.loc[scenes['scene_id'] == scene_id].iloc[0]
            asset_url = resolve_scene_asset_url(scene_row, url_field)
            if not asset_url:
                continue
            raster_path = cfg.UAV_RASTERS_DIR / f'{scene_id}.tif'
            download_uav_raster(asset_url, raster_path)
            if raster_fully_covers_geometry(raster_path, building_geom_wgs84, buildings.crs):
                assigned = {
                    'osm_id': osm_id,
                    'scene_id': scene_id,
                    'candidate_rank': candidate_rank,
                    'intersection_area_m2': candidate.intersection_area_m2,
                    'uav_raster_path': str(raster_path),
                    'download_url': asset_url,
                    'geometry': building_geom_wgs84,
                }
                break
        if assigned:
            assignments.append(assigned)

    if not assignments:
        raise RuntimeError('No building could be assigned to a fully covering UAV raster.')

    assignment_gdf = gpd.GeoDataFrame(assignments, geometry='geometry', crs=buildings.crs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    assignment_gdf.to_file(args.output, driver='GPKG')
    print(f'Saved {len(assignment_gdf)} building-to-UAV assignments to {args.output}')


if __name__ == '__main__':
    main()
