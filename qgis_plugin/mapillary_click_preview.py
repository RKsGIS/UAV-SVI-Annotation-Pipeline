# -*- coding: utf-8 -*-
# QGIS Plugin: Mapillary Click Preview (button-triggered coverage + OSM buildings)

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone

import requests
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox,
)
from qgis.core import (
    QgsSettings, QgsProject, QgsVectorLayer,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY,
    QgsMessageLog, Qgis, QgsFeature, QgsGeometry,
)

from . import mapillary_click_tool as tool


_SERVER_URLS = {
    'original': 'https://tiles.mapillary.com/maps/vtp/mly1_public/2/{z}/{x}/{y}?access_token={token}',
    'computed': 'https://tiles.mapillary.com/maps/vtp/mly1_computed_public/2/{z}/{x}/{y}?access_token={token}',
}
LAYER_LEVELS = ['image', 'sequence']
CACHE_EXPIRE_HOURS = 24
_CACHE_EXPIRE = timedelta(hours=CACHE_EXPIRE_HOURS)
MAX_WEB_MERCATOR_LAT = 85.05112878
MAPILLARY_LAUNCH_YEAR = 2012

MAPILLARY_LAYER_NAMES = {'Mapillary image', 'Mapillary sequence'}
BUILDINGS_LAYER_NAME = 'OSM Buildings'

# Public Overpass endpoints, tried in order until one responds. Large/global
# instances (overpass-api.de) are frequently rate-limited or overloaded and
# will 406/504/timeout under load; smaller regional mirrors are often faster
# for a small bbox. Users can override this list via QGIS Settings if needed.
_OVERPASS_ENDPOINTS = [
    'https://overpass.kumi.systems/api/interpreter',
    'https://overpass-api.de/api/interpreter',
    'https://overpass.private.coffee/api/interpreter',
    'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
]
_OVERPASS_HEADERS = {
    'User-Agent': 'MapillaryClickPreview-QGIS-Plugin/1.0 (+https://github.com/RKsGIS/MapillaryClickPreview)',
    'Accept': 'application/json',
    'Content-Type': 'application/x-www-form-urlencoded',
}
_OVERPASS_REQUEST_TIMEOUT = 25  # seconds per endpoint attempt
_OVERPASS_QUERY_TIMEOUT = 20    # seconds passed inside the Overpass QL query itself

# Overpass (and most bbox-based OSM services) get slow/likely to time out once
# the requested area gets large. Building queries in particular can return a
# huge number of ways, so we cap the AOI/extent size and ask the user to zoom
# in or draw a smaller selection instead of silently hammering the servers.
MAX_BUILDINGS_BBOX_DEG = 0.03  # ~roughly 3 km at the equator

BUILDINGS_CACHE_EXPIRE_HOURS = 24
_BUILDINGS_CACHE_EXPIRE = timedelta(hours=BUILDINGS_CACHE_EXPIRE_HOURS)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _is_finite_number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


def _deg2num(lat_deg, lon_deg, zoom):
    lat_deg = _clamp(float(lat_deg), -MAX_WEB_MERCATOR_LAT, MAX_WEB_MERCATOR_LAT)
    lon_deg = _clamp(float(lon_deg), -180.0, 179.999999999)
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    x = int((lon_deg + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + (1.0 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
    return x, y


def _zoom_for_pixel_size(pixel_size):
    for i in range(30):
        if pixel_size > (180 / 256.0 / 2 ** i):
            return i - 1 if i != 0 else 0
    return 29


def _get_tile_range(bounds, zoom):
    xm, ym, xmx, ymx = bounds
    start = _deg2num(ymx, xm, zoom)
    end = _deg2num(ym, xmx, zoom)
    return (min(start[0], end[0]), max(start[0], end[0])), (min(start[1], end[1]), max(start[1], end[1]))


def _build_tile_url(x, y, z, template):
    return template.replace('{x}', str(x)).replace('{y}', str(y)).replace('{z}', str(z))


def _get_proxies():
    from qgis.PyQt.QtCore import QSettings
    s = QSettings()
    if s.value('proxy/proxyEnabled', '') != 'true':
        return None
    host = s.value('proxy/proxyHost', '')
    port = s.value('proxy/proxyPort', '')
    user = s.value('proxy/proxyUser', '')
    pwd = s.value('proxy/proxyPassword', '')
    scheme = 'socks5' if s.value('proxy/proxyType', '') == 'Socks5Proxy' else 'http'
    addr = f'{scheme}://{user}:{pwd}@{host}:{port}'
    return {'http': addr, 'https': addr}


def _extend_layer(target, source, name):
    if not target:
        wkb_map = {0: 'UnknownType', 1: 'Point', 2: 'LineString', 3: 'Polygon',
                   4: 'MultiPoint', 5: 'MultiLineString', 6: 'MultiPolygon'}
        geom_type = wkb_map.get(int(source.wkbType()), 'UnknownType')
        crs = source.crs().toWkt()
        target = QgsVectorLayer(f'{geom_type}?crs={crs}', name, 'memory')
        target.dataProvider().addAttributes(source.fields())
        target.updateFields()
    target.dataProvider().addFeatures(list(source.getFeatures()))
    return target


def _build_year_filter_expr(from_year, to_year):
    """QGIS expression that filters coverage features by captured_at year range."""
    start_ms = int(datetime(from_year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(to_year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000) - 1
    return f'captured_at IS NULL OR (captured_at >= {start_ms} AND captured_at <= {end_ms})'


def _build_overpass_query(bounds):
    xmin, ymin, xmax, ymax = bounds
    # "out geom;" (no tags/ids) is enough for footprints and is noticeably
    # lighter/faster than "out body geom;" for building-dense areas.
    return (
        f'[out:json][timeout:{_OVERPASS_QUERY_TIMEOUT}];'
        f'(way["building"]({ymin},{xmin},{ymax},{xmax}););'
        f'out geom;'
    )


def _buildings_cache_path(bounds):
    cache_dir = os.path.join(tempfile.gettempdir(), 'go2mapillary', 'osm_buildings')
    os.makedirs(cache_dir, exist_ok=True)
    key = '_'.join(f'{v:.4f}' for v in bounds)
    digest = hashlib.md5(key.encode('utf-8')).hexdigest()
    return os.path.join(cache_dir, f'{digest}.json')


def _read_buildings_cache(bounds):
    path = _buildings_cache_path(bounds)
    if not os.path.exists(path):
        return None
    if datetime.fromtimestamp(os.path.getmtime(path)) < (datetime.now() - _BUILDINGS_CACHE_EXPIRE):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_buildings_cache(bounds, data):
    try:
        with open(_buildings_cache_path(bounds), 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception:
        pass


def _fetch_osm_buildings_layer(bounds):
    """Fetch building footprints (ways only) from Overpass API for the given WGS84 bounds.

    Tries several public Overpass mirrors in turn with a proper User-Agent
    (its absence is the usual cause of a 406 'Not Acceptable' from
    overpass-api.de) and a short per-endpoint timeout, since the public
    instances are frequently overloaded. Results are cached on disk for a
    while so repeated requests for the same area are instant.
    """
    cached = _read_buildings_cache(bounds)
    if cached is not None:
        return _osm_json_to_layer(cached), 'cache'

    query = _build_overpass_query(bounds)
    proxies = _get_proxies()

    last_error = None
    for endpoint in _OVERPASS_ENDPOINTS:
        try:
            resp = requests.post(
                endpoint,
                data={'data': query},
                headers=_OVERPASS_HEADERS,
                proxies=proxies,
                timeout=_OVERPASS_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            _write_buildings_cache(bounds, data)
            return _osm_json_to_layer(data), endpoint
        except Exception as e:
            last_error = e
            QgsMessageLog.logMessage(f'Overpass endpoint failed ({endpoint}): {e}', 'Mapillary', Qgis.Warning)
            continue

    raise RuntimeError(f'All Overpass endpoints failed: {last_error}')


def _osm_json_to_layer(data):
    layer = QgsVectorLayer('Polygon?crs=EPSG:4326', BUILDINGS_LAYER_NAME, 'memory')
    prov = layer.dataProvider()

    features = []
    for el in data.get('elements', []):
        if el.get('type') != 'way':
            continue
        geom = el.get('geometry')
        if not geom:
            continue
        coords = [QgsPointXY(pt['lon'], pt['lat']) for pt in geom]
        if len(coords) < 3:
            continue
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolygonXY([coords]))
        features.append(feat)

    prov.addFeatures(features)
    layer.updateExtents()
    return layer


# --------------------------------------------------------------------------
# Dialogs
# --------------------------------------------------------------------------

class TokenDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Mapillary Token')
        layout = QFormLayout(self)

        self.edit = QLineEdit(self)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)

        s = QgsSettings()
        self.edit.setText(s.value('mapillary/access_token', '', type=str))
        layout.addRow('Access token', self.edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self
        )
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def _on_accept(self):
        s = QgsSettings()
        s.setValue('mapillary/access_token', self.edit.text().strip())
        self.accept()


class YearFilterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Filter: Captured At (Year Range)')
        layout = QFormLayout(self)

        current_year = datetime.now().year
        s = QgsSettings()
        enabled = s.value('mapillary/year_filter_enabled', False, type=bool)
        from_year = s.value('mapillary/year_filter_from', MAPILLARY_LAUNCH_YEAR, type=int)
        to_year = s.value('mapillary/year_filter_to', current_year, type=int)

        self.enabled_check = QCheckBox('Enable year filter', self)
        self.enabled_check.setChecked(enabled)
        layout.addRow(self.enabled_check)

        self.from_spin = QSpinBox(self)
        self.from_spin.setRange(MAPILLARY_LAUNCH_YEAR, current_year)
        self.from_spin.setValue(max(MAPILLARY_LAUNCH_YEAR, min(from_year, current_year)))
        layout.addRow('From year:', self.from_spin)

        self.to_spin = QSpinBox(self)
        self.to_spin.setRange(MAPILLARY_LAUNCH_YEAR, current_year)
        self.to_spin.setValue(max(MAPILLARY_LAUNCH_YEAR, min(to_year, current_year)))
        layout.addRow('To year:', self.to_spin)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self
        )
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addRow(self.buttons)

    def _on_accept(self):
        from_year = self.from_spin.value()
        to_year = self.to_spin.value()
        if from_year > to_year:
            from_year, to_year = to_year, from_year
        s = QgsSettings()
        s.setValue('mapillary/year_filter_enabled', self.enabled_check.isChecked())
        s.setValue('mapillary/year_filter_from', from_year)
        s.setValue('mapillary/year_filter_to', to_year)
        self.accept()


# --------------------------------------------------------------------------
# Plugin
# --------------------------------------------------------------------------

class MapillaryClickPreviewPlugin:
    def __init__(self, iface):
        self.iface = iface

        # Actions
        self.action = None                     # toggle click-to-preview tool
        self.settings_action = None             # set access token
        self.add_tiles_action = None            # load Mapillary coverage (original)
        self.add_computed_tiles_action = None   # load Mapillary coverage (computed)
        self.filter_year_action = None          # open year-range filter dialog
        self.filter_pano_action = None          # toggle "panoramas only"
        self.add_buildings_action = None        # load OSM buildings for current view/AOI
        self.clear_action = None                # remove all loaded layers

        # State
        self.coverage_tile_set = None
        self.coverage_range_key = None
        self.coverage_layers = {level: None for level in LAYER_LEVELS}
        self.coverage_refreshing = False
        self._auto_preview_layer = None

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.svg')
        icon = QIcon(icon_path) if os.path.exists(icon_path) else self.iface.mainWindow().style().standardIcon(3)

        self.action = QAction(icon, 'Mapillary Click Preview', self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip('Toggle click-to-preview mode (left click = search, right click = stop)')
        self.action.toggled.connect(self._on_toggled)

        self.settings_action = QAction('Mapillary Token…', self.iface.mainWindow())
        self.settings_action.triggered.connect(self._open_settings)

        self.add_tiles_action = QAction('Load Mapillary Coverage (Original)', self.iface.mainWindow())
        self.add_tiles_action.triggered.connect(lambda: self._load_coverage('original', force=True))

        self.add_computed_tiles_action = QAction('Load Mapillary Coverage (Computed)', self.iface.mainWindow())
        self.add_computed_tiles_action.triggered.connect(lambda: self._load_coverage('computed', force=True))

        self.filter_year_action = QAction('Filter Coverage by Year…', self.iface.mainWindow())
        self.filter_year_action.triggered.connect(self._open_year_filter)

        self.filter_pano_action = QAction('Show Only Panoramas', self.iface.mainWindow())
        self.filter_pano_action.setCheckable(True)
        self.filter_pano_action.setChecked(QgsSettings().value('mapillary/pano_only', False, type=bool))
        self.filter_pano_action.toggled.connect(self._on_pano_filter_toggled)

        self.add_buildings_action = QAction('Load OSM Buildings (Overpass)', self.iface.mainWindow())
        self.add_buildings_action.triggered.connect(self._load_buildings)

        self.clear_action = QAction('Clear All Loaded Layers', self.iface.mainWindow())
        self.clear_action.triggered.connect(self._clear_all_layers)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('&Mapillary', self.action)
        self.iface.addPluginToMenu('&Mapillary', self.settings_action)
        self.iface.addPluginToMenu('&Mapillary', self.add_tiles_action)
        self.iface.addPluginToMenu('&Mapillary', self.add_computed_tiles_action)
        self.iface.addPluginToMenu('&Mapillary', self.filter_year_action)
        self.iface.addPluginToMenu('&Mapillary', self.filter_pano_action)
        self.iface.addPluginToMenu('&Mapillary', self.add_buildings_action)
        self.iface.addPluginToMenu('&Mapillary', self.clear_action)

        # NOTE: coverage loading is intentionally NOT tied to mapCanvasRefreshed.
        # It only runs when the user explicitly triggers one of the "Load…" actions,
        # to avoid re-downloading/re-rendering tiles on every pan/zoom.

        try:
            tool.enable_auto_identify_preview()
        except Exception as e:
            QgsMessageLog.logMessage(f'Could not enable auto identify preview: {e}', 'Mapillary', Qgis.Warning)

    def unload(self):
        try:
            if self.action:
                self.iface.removeToolBarIcon(self.action)
                self.iface.removePluginMenu('&Mapillary', self.action)
            if self.settings_action:
                self.iface.removePluginMenu('&Mapillary', self.settings_action)
            if self.add_tiles_action:
                self.iface.removePluginMenu('&Mapillary', self.add_tiles_action)
            if self.add_computed_tiles_action:
                self.iface.removePluginMenu('&Mapillary', self.add_computed_tiles_action)
            if self.filter_year_action:
                self.iface.removePluginMenu('&Mapillary', self.filter_year_action)
            if self.filter_pano_action:
                self.iface.removePluginMenu('&Mapillary', self.filter_pano_action)
            if self.add_buildings_action:
                self.iface.removePluginMenu('&Mapillary', self.add_buildings_action)
            if self.clear_action:
                self.iface.removePluginMenu('&Mapillary', self.clear_action)
        except Exception:
            pass

        try:
            tool.deactivate_click_tool(show_message=False)
        except Exception:
            pass

        try:
            tool.disable_auto_identify_preview()
        except Exception:
            pass

        self.coverage_tile_set = None
        self.coverage_range_key = None
        self._clear_all_layers()

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _on_toggled(self, checked):
        try:
            if checked:
                tool.activate_click_tool()
            else:
                tool.deactivate_click_tool(show_message=False)
        except Exception as e:
            self.action.setChecked(False)
            QgsMessageLog.logMessage(f'Error toggling Mapillary tool: {e}', 'Mapillary', Qgis.Critical)

    def _open_settings(self):
        dlg = TokenDialog(self.iface.mainWindow())
        dlg.setModal(True)
        dlg.exec()

    def _open_year_filter(self):
        dlg = YearFilterDialog(self.iface.mainWindow())
        dlg.setModal(True)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_filters_to_existing_layers()

    def _on_pano_filter_toggled(self, checked):
        QgsSettings().setValue('mapillary/pano_only', checked)
        self._apply_filters_to_existing_layers()

    def _load_buildings(self):
        bounds = self._get_active_aoi_bounds() or self._get_canvas_bounds()
        if not bounds or not all(_is_finite_number(v) for v in bounds):
            QgsMessageLog.logMessage('No valid extent/AOI to load buildings for.', 'Mapillary', Qgis.Warning)
            return

        width = abs(bounds[2] - bounds[0])
        height = abs(bounds[3] - bounds[1])
        if width > MAX_BUILDINGS_BBOX_DEG or height > MAX_BUILDINGS_BBOX_DEG:
            QgsMessageLog.logMessage(
                'Area too large for OSM buildings lookup. Zoom in further or select a smaller '
                f'area (max ~{MAX_BUILDINGS_BBOX_DEG}° per side) to avoid Overpass timeouts.',
                'Mapillary', Qgis.Warning)
            return

        try:
            buildings, source = _fetch_osm_buildings_layer(bounds)
            if buildings.isValid() and buildings.featureCount() > 0:
                self._remove_buildings_layers()
                QgsProject.instance().addMapLayer(buildings)
                QgsMessageLog.logMessage(
                    f'OSM buildings loaded via {source} ({buildings.featureCount()} features).',
                    'Mapillary', Qgis.Info)
            elif buildings.isValid():
                QgsMessageLog.logMessage('No buildings found for current extent/AOI.', 'Mapillary', Qgis.Info)
            else:
                QgsMessageLog.logMessage('OSM buildings layer is invalid.', 'Mapillary', Qgis.Warning)
        except Exception as e:
            QgsMessageLog.logMessage(
                f'Failed to load OSM buildings: {e}. Public Overpass servers may be busy — '
                'try again in a moment, zoom in to shrink the area, or install the QuickOSM '
                'plugin for more download options.',
                'Mapillary', Qgis.Warning)

    def _clear_all_layers(self):
        self._remove_coverage_layers()
        self._remove_buildings_layers()

    # ------------------------------------------------------------------
    # Auto-preview wiring (selecting a Mapillary image feature previews it)
    # ------------------------------------------------------------------

    def _on_image_layer_selection_changed(self, selected, deselected, clear_and_select):
        layer = self._auto_preview_layer
        if layer is None:
            return
        try:
            if layer.selectedFeatureCount() <= 0:
                return
            tool.preview_selected_feature()
        except Exception as e:
            QgsMessageLog.logMessage(f'Auto preview on selection failed: {e}', 'Mapillary', Qgis.Warning)

    def _connect_auto_preview_layer(self, layer):
        if layer is self._auto_preview_layer:
            return
        self._disconnect_auto_preview_layer()
        if layer is None or not layer.isValid():
            return
        try:
            layer.selectionChanged.connect(self._on_image_layer_selection_changed)
            self._auto_preview_layer = layer
        except Exception as e:
            QgsMessageLog.logMessage(f'Could not connect selection auto-preview: {e}', 'Mapillary', Qgis.Warning)
            self._auto_preview_layer = None

    def _disconnect_auto_preview_layer(self):
        if self._auto_preview_layer is None:
            return
        try:
            self._auto_preview_layer.selectionChanged.disconnect(self._on_image_layer_selection_changed)
        except Exception:
            pass
        self._auto_preview_layer = None

    # ------------------------------------------------------------------
    # Layer management
    # ------------------------------------------------------------------

    def _remove_coverage_layers(self):
        self._disconnect_auto_preview_layer()
        for level in LAYER_LEVELS:
            layer = self.coverage_layers.get(level)
            if not layer:
                continue
            try:
                QgsProject.instance().removeMapLayer(layer.id())
            except Exception:
                pass
            self.coverage_layers[level] = None

    def _remove_buildings_layers(self):
        for layer in list(QgsProject.instance().mapLayers().values()):
            if isinstance(layer, QgsVectorLayer) and layer.name() == BUILDINGS_LAYER_NAME:
                try:
                    QgsProject.instance().removeMapLayer(layer.id())
                except Exception:
                    pass

    def _apply_filters_to_existing_layers(self):
        s = QgsSettings()
        year_enabled = s.value('mapillary/year_filter_enabled', False, type=bool)
        pano_only = s.value('mapillary/pano_only', False, type=bool)

        year_expr = ''
        if year_enabled:
            from_year = s.value('mapillary/year_filter_from', MAPILLARY_LAUNCH_YEAR, type=int)
            to_year = s.value('mapillary/year_filter_to', datetime.now().year, type=int)
            year_expr = _build_year_filter_expr(from_year, to_year)

        for layer in QgsProject.instance().mapLayers().values():
            if not (isinstance(layer, QgsVectorLayer) and layer.name() in MAPILLARY_LAYER_NAMES):
                continue

            subset_parts = []
            if year_enabled and layer.fields().lookupField('captured_at') != -1:
                subset_parts.append(f'({year_expr})')
            if pano_only and layer.fields().lookupField('is_pano') != -1:
                subset_parts.append('("is_pano" = true)')

            layer.setSubsetString(' AND '.join(subset_parts))
            layer.triggerRepaint()

    # ------------------------------------------------------------------
    # AOI / extent helpers
    # ------------------------------------------------------------------

    def _get_active_aoi_bounds(self):
        """Use the extent of the selected feature(s) in the active layer as AOI, if any."""
        layer = self.iface.activeLayer()
        if isinstance(layer, QgsVectorLayer) and layer.selectedFeatureCount() > 0:
            box = layer.boundingBoxOfSelected()
            xform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem(4326), QgsProject.instance())
            box = xform.transformBoundingBox(box)
            return (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())
        return None

    def _get_canvas_bounds(self):
        canvas = self.iface.mapCanvas()
        crs_src = canvas.mapSettings().destinationCrs()
        crs_wgs84 = QgsCoordinateReferenceSystem(4326)
        xform = QgsCoordinateTransform(crs_src, crs_wgs84, QgsProject.instance())

        ex = canvas.extent()
        wgs84_min = xform.transform(QgsPointXY(ex.xMinimum(), ex.yMinimum()))
        wgs84_max = xform.transform(QgsPointXY(ex.xMaximum(), ex.yMaximum()))
        return (wgs84_min.x(), wgs84_min.y(), wgs84_max.x(), wgs84_max.y())

    # ------------------------------------------------------------------
    # Mapillary coverage loading (only runs when explicitly triggered)
    # ------------------------------------------------------------------

    def _load_coverage(self, tile_set='original', force=False):
        s = QgsSettings()
        token = s.value('mapillary/access_token', '', type=str).strip()
        if not token:
            self._open_settings()
            token = s.value('mapillary/access_token', '', type=str).strip()
            if not token:
                QgsMessageLog.logMessage('No Mapillary token set.', 'Mapillary', Qgis.Warning)
                return

        server_url = _SERVER_URLS[tile_set].replace('{token}', token)

        canvas = self.iface.mapCanvas()
        aoi = self._get_active_aoi_bounds()
        bounds = aoi if aoi else self._get_canvas_bounds()

        if not all(_is_finite_number(v) for v in bounds):
            QgsMessageLog.logMessage('Extent/AOI is not valid for tile loading.', 'Mapillary', Qgis.Warning)
            return

        canvas_width = canvas.width()
        if canvas_width <= 0:
            return

        map_units_per_pixel = abs(bounds[2] - bounds[0]) / canvas_width
        zoom_level = _zoom_for_pixel_size(map_units_per_pixel)
        zoom_level = max(0, min(int(zoom_level), 14))

        try:
            x_range, y_range = _get_tile_range(bounds, zoom_level)
        except (ValueError, OverflowError) as e:
            QgsMessageLog.logMessage(f'Could not compute tile range: {e}', 'Mapillary', Qgis.Warning)
            return

        range_key = (tile_set, zoom_level, x_range[0], x_range[1], y_range[0], y_range[1])
        if not force and range_key == self.coverage_range_key:
            return

        self.coverage_refreshing = True
        try:
            cache_dir = os.path.join(tempfile.gettempdir(), 'go2mapillary')
            layers = {level: None for level in LAYER_LEVELS}

            for x in range(x_range[0], x_range[1] + 1):
                for y in range(y_range[0], y_range[1] + 1):
                    folder = os.path.join(cache_dir, str(zoom_level), str(x))
                    os.makedirs(folder, exist_ok=True)
                    mvt_path = os.path.join(folder, f'{y}.mvt')

                    expired = (
                        not os.path.exists(mvt_path)
                        or datetime.fromtimestamp(os.path.getmtime(mvt_path)) < (datetime.now() - _CACHE_EXPIRE)
                    )
                    if expired:
                        url = _build_tile_url(x, y, zoom_level, server_url)
                        try:
                            resp = requests.get(url, proxies=_get_proxies(), timeout=15)
                            resp.raise_for_status()
                            with open(mvt_path, 'wb') as f:
                                f.write(resp.content)
                        except Exception as e:
                            QgsMessageLog.logMessage(
                                f'Tile download failed [{x},{y},{zoom_level}]: {e}', 'Mapillary', Qgis.Warning)
                            continue

                    if os.path.exists(mvt_path):
                        for level in LAYER_LEVELS:
                            tile = QgsVectorLayer(f'{mvt_path}|layername={level}', level, 'ogr')
                            if tile.isValid():
                                layers[level] = _extend_layer(layers[level], tile, f'Mapillary {level}')

            self._remove_coverage_layers()

            added = []
            for level in LAYER_LEVELS:
                lyr = layers[level]
                if lyr and lyr.isValid():
                    qml_path = os.path.join(os.path.dirname(__file__), 'res', f'mapillary_{level}.qml')
                    if os.path.exists(qml_path):
                        lyr.loadNamedStyle(qml_path)
                    QgsProject.instance().addMapLayer(lyr)
                    self.coverage_layers[level] = lyr
                    added.append(lyr)

            self.coverage_tile_set = tile_set
            self.coverage_range_key = range_key

            if added:
                self._apply_filters_to_existing_layers()
                self._connect_auto_preview_layer(self.coverage_layers.get('image'))
                QgsMessageLog.logMessage(
                    f'Mapillary coverage loaded: {len(added)} layer(s) at zoom {zoom_level}.',
                    'Mapillary', Qgis.Info)
            else:
                self._disconnect_auto_preview_layer()
                QgsMessageLog.logMessage(
                    'No Mapillary coverage tiles found for current extent/AOI.', 'Mapillary', Qgis.Warning)
        finally:
            self.coverage_refreshing = False
