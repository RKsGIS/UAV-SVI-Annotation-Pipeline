# -*- coding: utf-8 -*-
"""Adapted Mapillary Click Preview image-preview backend.

The coverage loader is kept in mapillary_backend.py. This module keeps the
image-id -> Graph API -> thumbnail workflow used by MapillaryClickPreview.
"""
from datetime import datetime, timezone
import json
import urllib.error
import urllib.parse
import urllib.request

from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QDockWidget, QLabel, QVBoxLayout, QWidget
from qgis.core import QgsCoordinateTransform, QgsFeatureRequest, QgsGeometry, QgsProject, QgsRectangle, QgsSettings, QgsVectorLayer
from qgis.gui import QgsMapToolEmitPoint
from qgis.utils import iface

_qsettings = QgsSettings()
ACCESS_TOKEN = _qsettings.value('mapillary/access_token', '', type=str).strip()
MAPILLARY_IMAGE_LAYER_NAME = 'Mapillary image'
MAPILLARY_ID_FIELD = 'id'
FEATURE_PICK_TOLERANCE_PX = 8
THUMB_MAX_W, THUMB_MAX_H = 900, 600

canvas = None
project = None
preview = None


def _ensure_access_token():
    global ACCESS_TOKEN
    ACCESS_TOKEN = _qsettings.value('mapillary/access_token', '', type=str).strip()
    if not ACCESS_TOKEN:
        raise RuntimeError("No Mapillary access token found. Set it in QGIS settings under mapillary/access_token.")
    return ACCESS_TOKEN


def _validate_remote_url(url):
    p = urllib.parse.urlsplit(str(url).strip())
    if p.scheme.lower() != 'https' or not p.netloc:
        raise RuntimeError('Only absolute HTTPS URLs are accepted.')
    return p.geturl()


def _open_remote_url(url, timeout):
    opener = urllib.request.build_opener()
    req = urllib.request.Request(_validate_remote_url(url), headers={'User-Agent':'MapillaryClickPreview/1.3'})
    return opener.open(req, timeout=timeout)


def fetch_json(url):
    with _open_remote_url(url, 60) as resp:
        return json.loads(resp.read().decode('utf-8'))


def build_image_query_url(image_id):
    token = _ensure_access_token()
    image_id = str(image_id).strip()
    if not image_id:
        raise RuntimeError('No valid Mapillary image id.')
    base = f'https://graph.mapillary.com/{image_id}'
    return base + '?' + urllib.parse.urlencode({
        'access_token': token,
        'fields': 'id,computed_geometry,compass_angle,captured_at,is_pano,creator{id},thumb_1024_url',
    })


def timestamp_ms_to_year(value):
    try:
        ts = int(value)
        if ts > 0:
            return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).year
    except Exception:
        pass
    try:
        v = str(value).strip().replace('Z','+00:00')
        return datetime.fromisoformat(v).year if v else None
    except Exception:
        return None


def fetch_image_by_id(image_id):
    data = fetch_json(build_image_query_url(image_id))
    if not isinstance(data, dict):
        raise RuntimeError('Unexpected Mapillary API response.')
    pid = str(data.get('id') or image_id)
    return {
        'id': pid,
        'captured_at': timestamp_ms_to_year(data.get('captured_at')),
        'compass': float(data.get('compass_angle') or 0.0),
        'is_pano': bool(data.get('is_pano') or False),
        'creator_id': str((data.get('creator') or {}).get('id') or ''),
        'thumb_url': str(data.get('thumb_1024_url') or ''),
        'url': f'https://www.mapillary.com/app/?pKey={pid}&focus=photo',
    }


def fetch_pixmap_from_url(url):
    if not url:
        return None
    try:
        with _open_remote_url(url, 30) as resp:
            data = resp.read()
        pix = QPixmap()
        return pix if pix.loadFromData(data) else None
    except Exception:
        return None


def _ensure_canvas_project():
    global canvas, project
    if canvas is None:
        canvas = iface.mapCanvas()
    if project is None:
        project = QgsProject.instance()


def _find_mapillary_image_layer():
    _ensure_canvas_project()
    # Exact name first, matching the original plugin.
    for layer in project.mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.name() == MAPILLARY_IMAGE_LAYER_NAME:
            return layer
    # Fallback for temporary/existing layers with a Mapillary-like name.
    for layer in project.mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and layer.isValid() and layer.name().lower().startswith('mapillary'):
            if layer.fields().lookupField('id') >= 0:
                return layer
    return None


def _get_selected_image_id():
    layer = _find_mapillary_image_layer()
    if layer is None:
        raise RuntimeError("Layer 'Mapillary image' not found. Display SVI first.")
    idx = layer.fields().indexOf(MAPILLARY_ID_FIELD)
    if idx < 0:
        raise RuntimeError("Column 'id' is missing from the Mapillary image layer.")
    selected = layer.selectedFeatures()
    if not selected:
        raise RuntimeError("Select one feature in the 'Mapillary image' layer first.")
    image_id = str(selected[-1][idx]).strip()
    if not image_id:
        raise RuntimeError('The selected feature has no valid Mapillary image id.')
    return image_id


def create_preview_panel():
    global preview
    _ensure_canvas_project()
    main_window = iface.mainWindow()
    existing = main_window.findChild(QDockWidget, 'MapillaryPreviewDock')
    if existing:
        main_window.removeDockWidget(existing)
        existing.deleteLater()
    dock = QDockWidget('Mapillary Image Preview', main_window)
    dock.setObjectName('MapillaryPreviewDock')
    container = QWidget(dock)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(10,10,10,10)
    status = QLabel('Select a Mapillary image to preview it here.')
    status.setWordWrap(True)
    layout.addWidget(status)
    image = QLabel('No preview loaded.')
    image.setAlignment(Qt.AlignmentFlag.AlignCenter)
    image.setMinimumSize(420,300)
    image.setWordWrap(True)
    layout.addWidget(image,1)
    meta = QLabel('')
    meta.setWordWrap(True)
    layout.addWidget(meta)
    link = QLabel('')
    link.setOpenExternalLinks(True)
    layout.addWidget(link)
    dock.setWidget(container)
    main_window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
    dock.show()
    preview={'dock':dock,'status_label':status,'image_label':image,'meta_label':meta,'link_label':link}
    return preview


def _ensure_infrastructure():
    if preview is None:
        create_preview_panel()


def set_preview_empty(text):
    _ensure_infrastructure()
    preview['status_label'].setText(text)
    preview['image_label'].setPixmap(QPixmap())
    preview['image_label'].setText('No preview available.')
    preview['meta_label'].setText('')


def render_result(result):
    _ensure_infrastructure()
    preview['status_label'].setText('Mapillary image loaded from the selected feature.')
    year = result.get('captured_at')
    preview['meta_label'].setText(
        f"ID: {result['id']} | Year: {year if year is not None else 'unknown'} | "
        f"Compass: {result['compass']:.1f}° | Panorama: {result['is_pano']}"
    )
    pix = fetch_pixmap_from_url(result['thumb_url'])
    if pix is not None and not pix.isNull():
        preview['image_label'].setText('')
        preview['image_label'].setPixmap(pix.scaled(900,600,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
    else:
        preview['image_label'].setPixmap(QPixmap())
        preview['image_label'].setText('Thumbnail could not be loaded. Use the Mapillary link below.')
    preview['link_label'].setText(f'<a href="{result["url"]}">Open in Mapillary</a>')


def preview_selected_feature():
    _ensure_infrastructure()
    try:
        image_id = _get_selected_image_id()
        preview['status_label'].setText(f'Loading Mapillary image {image_id}…')
        result = fetch_image_by_id(image_id)
        render_result(result)
        return result
    except urllib.error.HTTPError as exc:
        try:
            msg = exc.read().decode('utf-8', errors='ignore')[:300]
        except Exception:
            msg = str(exc)
        set_preview_empty(f'Mapillary HTTP error {exc.code}: {msg}')
    except Exception as exc:
        set_preview_empty(f'Could not preview Mapillary image: {exc}')
    return None


def enable_auto_identify_preview():
    # Selection-driven preview is handled by the explorer plugin itself.
    return


def disable_auto_identify_preview():
    return


def activate_click_tool():
    return


def deactivate_click_tool(show_message=True):
    return
