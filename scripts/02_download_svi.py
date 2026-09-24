from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from utils import pipeline_config as cfg
from utils.geospatial import assign_building_ids, assign_scene_ids, load_vector_data, prepare_geometries, project_to_local_metric
from utils.mapillary import compute_target_x, download_panorama, fetch_mapillary_points_for_scenes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Download Mapillary panoramas near selected buildings.')
    parser.add_argument('--selected-scenes', type=Path, default=cfg.SELECTED_SCENES_FILE)
    parser.add_argument('--scene-layer', default=None)
    parser.add_argument('--buildings', type=Path, default=cfg.BUILDING_ASSIGNMENTS_FILE)
    parser.add_argument('--buildings-layer', default=None)
    parser.add_argument('--search-radius', type=float, default=cfg.DEFAULT_SEARCH_RADIUS_METERS)
    parser.add_argument('--points-output', type=Path, default=cfg.MAPILLARY_POINTS_FILE)
    parser.add_argument('--assignments-output', type=Path, default=cfg.BUILDINGS_WITH_SVI_FILE)
    return parser


def choose_visible_point(building_row, candidate_points_metric, obstruction_buildings_metric):
    centroid = building_row.geometry.centroid
    obstruction_sindex = obstruction_buildings_metric.sindex
    for point_row in candidate_points_metric.itertuples():
        line = LineString([point_row.geometry, centroid])
        possible_idx = list(obstruction_sindex.intersection(line.bounds))
        obstructors = obstruction_buildings_metric.iloc[possible_idx]
        obstructed = obstructors[obstructors['osm_id'] != building_row.osm_id].intersects(line).any()
        if not obstructed:
            return point_row
    return None


def main() -> None:
    cfg.ensure_runtime_directories()
    args = build_parser().parse_args()
    if not cfg.MAPILLARY_ACCESS_TOKEN:
        raise RuntimeError('MAPILLARY_ACCESS_TOKEN is required in the repository .env file.')

    scenes = prepare_geometries(assign_scene_ids(load_vector_data(args.selected_scenes, args.scene_layer)))
    buildings = prepare_geometries(assign_building_ids(load_vector_data(args.buildings, args.buildings_layer)))
    points = fetch_mapillary_points_for_scenes(
        scenes,
        access_token=cfg.MAPILLARY_ACCESS_TOKEN,
        creator_username=cfg.MAPILLARY_CREATOR_USERNAME,
    )
    if points.empty:
        raise RuntimeError('No Mapillary points were returned for the selected scenes.')
    args.points_output.parent.mkdir(parents=True, exist_ok=True)
    points.to_file(args.points_output, driver='GPKG')

    buildings_wgs84 = buildings.to_crs(epsg=4326)
    points_wgs84 = points.to_crs(epsg=4326)
    buildings_metric, points_metric = project_to_local_metric(buildings_wgs84, points_wgs84)

    building_records = []
    for row in buildings_metric.itertuples():
        centroid = row.geometry.centroid
        buffer = centroid.buffer(args.search_radius)
        candidate_idx = list(points_metric.sindex.query(buffer, predicate='intersects'))
        if not candidate_idx:
            continue
        candidates = points_metric.iloc[candidate_idx].copy()
        candidates['distance_m'] = candidates.geometry.distance(centroid)
        candidates = candidates.sort_values('distance_m')
        chosen = choose_visible_point(row, candidates, buildings_metric)
        if chosen is None:
            continue
        point_row = points_wgs84.loc[chosen.Index]
        building_row_wgs84 = buildings_wgs84.loc[row.Index]
        bearing, relative_angle, target_x, half = compute_target_x(point_row, building_row_wgs84.geometry.centroid)
        if point_row.get('thumb_original_url'):
            panorama_path = cfg.SVI_PANORAMAS_DIR / f"{point_row['id']}.jpg"
            download_panorama(point_row['thumb_original_url'], panorama_path)
        else:
            panorama_path = None
        building_records.append({
            **building_row_wgs84.drop(labels='geometry').to_dict(),
            'geometry': building_row_wgs84.geometry,
            'mapillary_id': str(point_row['id']),
            'distance_m': float(chosen.distance_m),
            'bearing_to_building': bearing,
            'relative_angle': relative_angle,
            'line_x_pixel': target_x,
            'half_cropped_image': half,
            'panorama_path': str(panorama_path) if panorama_path else None,
        })

    if not building_records:
        raise RuntimeError('No visible building / panorama pairs were identified.')
    assignments = gpd.GeoDataFrame(building_records, geometry='geometry', crs=buildings_wgs84.crs)
    args.assignments_output.parent.mkdir(parents=True, exist_ok=True)
    assignments.to_file(args.assignments_output, driver='GPKG')
    cfg.SVI_VIEW_GEOMETRY_CSV.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(assignments.drop(columns='geometry')).to_csv(cfg.SVI_VIEW_GEOMETRY_CSV, index=False)
    print(f'Saved {len(assignments)} building-to-SVI assignments to {args.assignments_output}')


if __name__ == '__main__':
    main()
