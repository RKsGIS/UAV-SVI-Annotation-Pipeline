from __future__ import annotations

import re
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from . import pipeline_config as cfg


def discover_default_input(patterns=("*.gpkg", "*.qgs", "*.qgz")) -> Path | None:
    for pattern in patterns:
        matches = sorted(cfg.INPUT_DIR.glob(pattern))
        if matches:
            return matches[0]
    return None


def discover_default_buildings(patterns=("*building*.gpkg", "*building*.geojson", "*buildings*.shp")) -> Path | None:
    for pattern in patterns:
        matches = sorted(cfg.INPUT_DIR.glob(pattern))
        if matches:
            return matches[0]
    return None


def _read_qgis_project_text(project_path: Path) -> str:
    if project_path.suffix.lower() == ".qgz":
        with zipfile.ZipFile(project_path) as archive:
            qgs_names = [name for name in archive.namelist() if name.endswith('.qgs')]
            if not qgs_names:
                raise FileNotFoundError(f"No embedded .qgs file found in {project_path}")
            return archive.read(qgs_names[0]).decode('utf-8')
    return project_path.read_text(encoding='utf-8')


def _candidate_project_sources(project_path: Path) -> list[tuple[str | None, Path, str | None]]:
    xml_text = _read_qgis_project_text(project_path)
    root = ET.fromstring(xml_text)
    candidates = []
    for maplayer in root.findall('.//maplayer'):
        layer_name = maplayer.findtext('layername')
        datasource = maplayer.findtext('datasource') or ''
        resolved = parse_qgis_datasource(datasource, project_path.parent)
        if resolved is not None and resolved.exists():
            candidates.append((layer_name, resolved, datasource))
    return candidates


def parse_qgis_datasource(datasource: str, base_dir: Path) -> Path | None:
    if not datasource:
        return None
    dbname_match = re.search(r"dbname='([^']+)'", datasource)
    if dbname_match:
        candidate = Path(dbname_match.group(1))
        return candidate if candidate.is_absolute() else (base_dir / candidate).resolve()
    path_part = datasource.split('|', 1)[0].strip()
    if not path_part:
        return None
    candidate = Path(path_part)
    return candidate if candidate.is_absolute() else (base_dir / candidate).resolve()


def parse_qgis_layer_name(datasource: str) -> str | None:
    layer_match = re.search(r"layername=([^|]+)", datasource)
    if layer_match:
        return layer_match.group(1)
    return None


def load_vector_data(path: str | Path, layer: str | None = None) -> gpd.GeoDataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {'.qgs', '.qgz'}:
        sources = _candidate_project_sources(path)
        if not sources:
            raise FileNotFoundError(f"No readable vector sources were found in {path}")
        if layer:
            for layer_name, source_path, datasource in sources:
                if layer_name == layer or f"layername={layer}" in (datasource or ''):
                    gpkg_layer = parse_qgis_layer_name(datasource or '')
                    return gpd.read_file(source_path, layer=gpkg_layer if source_path.suffix.lower() == '.gpkg' else None)
            raise ValueError(f"Layer '{layer}' was not found in {path}")
        preferred = next((item for item in sources if 'scene' in (item[0] or '').lower() or 'oam' in (item[0] or '').lower()), sources[0])
        source_path = preferred[1]
        gpkg_layer = parse_qgis_layer_name(preferred[2] or '')
        return gpd.read_file(source_path, layer=gpkg_layer if source_path.suffix.lower() == '.gpkg' else None)
    return gpd.read_file(path, layer=layer)


def choose_identifier_field(columns, candidates):
    lowered = {str(column).lower(): column for column in columns}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def assign_scene_ids(scenes: gpd.GeoDataFrame, preferred_field: str | None = None) -> gpd.GeoDataFrame:
    scenes = scenes.copy()
    field = preferred_field or choose_identifier_field(scenes.columns, cfg.DEFAULT_SCENE_ID_FIELDS)
    if field:
        scenes['scene_id'] = scenes[field].astype(str)
    else:
        scenes['scene_id'] = [f"scene_{idx:04d}" for idx in range(1, len(scenes) + 1)]
    return scenes


def assign_building_ids(buildings: gpd.GeoDataFrame, preferred_field: str | None = None) -> gpd.GeoDataFrame:
    buildings = buildings.copy()
    field = preferred_field or choose_identifier_field(buildings.columns, cfg.DEFAULT_BUILDING_ID_FIELDS)
    if field:
        buildings['osm_id'] = buildings[field].astype(str)
    else:
        buildings['osm_id'] = [f"building_{idx:05d}" for idx in range(1, len(buildings) + 1)]
    return buildings


def prepare_geometries(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    prepared = gdf.loc[~gdf.geometry.isna()].copy()
    prepared = prepared.loc[~prepared.geometry.is_empty]
    prepared = prepared.loc[prepared.geometry.is_valid]
    return prepared


def project_to_local_metric(*gdfs: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, ...]:
    anchor = next((gdf for gdf in gdfs if gdf is not None and len(gdf) > 0), None)
    if anchor is None:
        return gdfs
    metric_crs = anchor.estimate_utm_crs() or 'EPSG:3857'
    return tuple(gdf.to_crs(metric_crs) if gdf is not None else None for gdf in gdfs)


def count_buildings_per_scene(scenes: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    scenes_metric, buildings_metric = project_to_local_metric(scenes, buildings)
    joined = gpd.sjoin(
        buildings_metric[['geometry']],
        scenes_metric[['scene_id', 'geometry']],
        predicate='intersects',
        how='inner',
    )
    counts = joined.groupby('scene_id').size().rename('building_count')
    result = scenes.copy()
    result['building_count'] = result['scene_id'].map(counts).fillna(0).astype(int)
    result['has_osm_building'] = result['building_count'] > 0
    return result


def find_scene_bbox(geom):
    minx, miny, maxx, maxy = geom.bounds
    return box(minx, miny, maxx, maxy)


def optional_query_filter(gdf: gpd.GeoDataFrame, expression: str | None) -> gpd.GeoDataFrame:
    if not expression:
        return gdf
    frame = pd.DataFrame(gdf.drop(columns='geometry'))
    mask = frame.eval(expression)
    return gdf.loc[mask].copy()
