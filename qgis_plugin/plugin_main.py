# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from qgis.PyQt.QtCore import Qt, QMetaType
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDockWidget, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsRectangle, QgsSingleSymbolRenderer,
    QgsField, QgsFillSymbol, QgsGeometry, QgsLineSymbol, QgsMessageLog, QgsProject,
    QgsRasterLayer, QgsSettings, QgsSpatialIndex, QgsVectorLayer, QgsWkbTypes, Qgis, QgsPointXY, QgsMarkerSymbol,
)

from . import mapillary_backend
from . import mapillary_click_tool as tool

PLUGIN_NAME = 'UAV/SVI Annotation Explorer'
TEMP_PREFIX = 'TEMP • UAV/SVI • '

OAM_API = 'https://api.openaerialmap.org'
OAM_ID_FIELDS = ('oam_id','scene_id','oam','id','_id')
OAM_URL_FIELDS = ('download','download_url','asset_url','tif_url','uuid','url')
COMPASS_FIELDS = ('compass_angle','compass','heading','bearing','ca')


def clean(v):
    if v is None:
        return ''
    try:
        if hasattr(v,'isNull') and v.isNull(): return ''
    except Exception: pass
    return str(v).strip()


def field_value(f, names):
    for n in names:
        idx=f.fields().lookupField(n)
        if idx>=0:
            v=clean(f[idx])
            if v: return v
    return ''


def selected_feature(layer):
    if not isinstance(layer,QgsVectorLayer): return None
    fs=layer.selectedFeatures()
    return fs[-1] if fs else None


def metric_crs_from_layer(layer, feature):
    geom=feature.geometry()
    p=geom.centroid().asPoint() if not geom.isEmpty() else None
    if p is None: return QgsCoordinateReferenceSystem('EPSG:3857')
    wgs=QgsCoordinateReferenceSystem('EPSG:4326')
    tr=QgsCoordinateTransform(layer.crs(),wgs,QgsProject.instance())
    p=tr.transform(p)
    zone=int((p.x()+180)/6)+1
    return QgsCoordinateReferenceSystem(f"EPSG:{32600+zone if p.y()>=0 else 32700+zone}")


def transform_geom(geom, src, dst):
    g=QgsGeometry(geom)
    if src!=dst:
        g.transform(QgsCoordinateTransform(src,dst,QgsProject.instance()))
    return g


def bearing(p1,p2,crs):
    wgs=QgsCoordinateReferenceSystem('EPSG:4326')
    tr=QgsCoordinateTransform(crs,wgs,QgsProject.instance())
    a=tr.transform(p1); b=tr.transform(p2)
    lon1,lat1=math.radians(a.x()),math.radians(a.y())
    lon2,lat2=math.radians(b.x()),math.radians(b.y())
    dlon=lon2-lon1
    y=math.sin(dlon)*math.cos(lat2)
    x=math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
    return (math.degrees(math.atan2(y,x))+360)%360


class Panel(QWidget):
    def __init__(self, plugin):
        super().__init__()
        self.plugin=plugin
        root=QVBoxLayout(self); root.setContentsMargins(14,14,14,14); root.setSpacing(10)
        title=QLabel('UAV / SVI Annotation Explorer'); title.setStyleSheet('font-size:18px;font-weight:700;'); root.addWidget(title)
        sub=QLabel('Temporary exploration of the selected scene before permanent annotation.'); sub.setWordWrap(True); sub.setStyleSheet('color:#64748b;'); root.addWidget(sub)
        self.scene=QLabel('Selected scene: —'); self.scene.setStyleSheet('padding:8px;background:#f1f5f9;border-radius:6px;'); root.addWidget(self.scene)
        g=QGroupBox('Exploration'); gl=QVBoxLayout(g)
        self.uav=QPushButton('▣  Display UAV'); self.svi=QPushButton('◉  Display SVI'); self.osm=QPushButton('⌂  Display OSM Buildings'); self.directions=QPushButton('➜  Calculate Building Directions'); self.preview=QPushButton('▣  Preview Selected Mapillary Image')
        for b in (self.uav,self.svi,self.osm,self.directions,self.preview): b.setMinimumHeight(36); gl.addWidget(b)
        self.uav.clicked.connect(plugin.display_uav); self.svi.clicked.connect(plugin.display_svi); self.osm.clicked.connect(plugin.display_osm); self.directions.clicked.connect(plugin.calculate_directions); self.preview.clicked.connect(plugin.preview_selected_mapillary)
        root.addWidget(g)
        settings=QGroupBox('Building search'); form=QFormLayout(settings)
        self.buffer=QDoubleSpinBox(); self.buffer.setRange(5,1000); self.buffer.setSingleStep(10); self.buffer.setValue(QgsSettings().value('uavsvi/building_buffer_m',100.0,type=float)); self.buffer.setSuffix(' m'); self.buffer.valueChanged.connect(lambda v: QgsSettings().setValue('uavsvi/building_buffer_m',v)); form.addRow('Search buffer:',self.buffer); root.addWidget(settings)
        self.status=QLabel('Ready'); self.status.setWordWrap(True); self.status.setStyleSheet('padding:8px;background:#f8fafc;border-radius:6px;'); root.addWidget(self.status)
        self.clear=QPushButton('Clear temporary exploration layers'); self.clear.clicked.connect(plugin.clear_temporary); root.addWidget(self.clear); root.addStretch()

    def msg(self,text,kind='normal'):
        self.status.setText(text)
        bg='#f8fafc'; fg='#475569'
        if kind=='warning': bg='#fffbeb'; fg='#92400e'
        if kind=='error': bg='#fef2f2'; fg='#991b1b'
        self.status.setStyleSheet(f'padding:8px;background:{bg};color:{fg};border-radius:6px;')


class UAVSVIAnnotationPlugin:
    def __init__(self,iface):
        self.iface=iface; self.dock=None; self.action=None; self.panel=None
        self.backend=mapillary_backend.MapillaryClickPreviewPlugin(iface)
        self.uav=None; self.uavs=[]; self.direction=None; self.direction_arrow_layer=None; self.direction_arrow=None; self.highlight=None; self.connection=None; self.relevant_buildings=None; self.qualified_svi=None
        self._layers=[]

    def initGui(self):
        icon=QIcon(os.path.join(os.path.dirname(__file__),'icon.svg'))
        self.action=QAction(icon,PLUGIN_NAME,self.iface.mainWindow()); self.action.triggered.connect(self.toggle)
        self.iface.addToolBarIcon(self.action); self.iface.addPluginToMenu('&UAV/SVI',self.action)
        self.dock=QDockWidget(PLUGIN_NAME,self.iface.mainWindow()); self.dock.setObjectName('UAVSVIAnnotationDock'); self.dock.setMinimumWidth(390)
        self.panel=Panel(self); scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.panel); self.dock.setWidget(scroll)
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,self.dock)
        self.iface.mapCanvas().mapCanvasRefreshed.connect(self._scene_label)
        self._scene_label()

    def unload(self):
        try: self.iface.mapCanvas().mapCanvasRefreshed.disconnect(self._scene_label)
        except Exception: pass
        try: self.iface.removeToolBarIcon(self.action); self.iface.removePluginMenu('&UAV/SVI',self.action)
        except Exception: pass
        try: self.backend._remove_coverage_layers(); self.backend._remove_buildings_layers()
        except Exception: pass
        for l in getattr(self, 'uavs', []):
            try: QgsProject.instance().removeMapLayer(l.id())
            except Exception: pass
        if self.dock: self.iface.removeDockWidget(self.dock); self.dock.deleteLater()

    def toggle(self): self.dock.setVisible(not self.dock.isVisible())

    def _scene_label(self):
        if not self.panel: return
        l=self.iface.activeLayer(); f=selected_feature(l)
        self.panel.scene.setText(f"Selected scene: <b>{field_value(f,OAM_ID_FIELDS) if f else '—'}</b>")

    def selected_scene(self):
        l=self.iface.activeLayer(); f=selected_feature(l)
        return l,f,field_value(f,OAM_ID_FIELDS) if f else ''

    def _oam_url(self,f):
        u=field_value(f,OAM_URL_FIELDS)
        if u and u.startswith('http'): return u
        sid=field_value(f,OAM_ID_FIELDS)
        if not sid: return ''
        s=requests.Session(); s.headers.update({'User-Agent':'UAV-SVI-Annotation-Explorer/0.3.1','Accept':'application/json'})
        for endpoint in (f'{OAM_API}/meta/{sid}',f'{OAM_API}/meta?search={sid}',f'{OAM_API}/meta?limit=10&_id={sid}'):
            try:
                r=s.get(endpoint,timeout=20); r.raise_for_status(); p=r.json(); records=p.get('data',[]) if isinstance(p,dict) else p
                if isinstance(records,dict): records=[records]
                if not isinstance(records,list): records=[p]
                for rec in records:
                    if not isinstance(rec,dict): continue
                    rid=clean(rec.get('_id') or rec.get('id'))
                    if rid and rid!=sid: continue
                    for k in ('uuid','download','download_path','url'):
                        v=clean(rec.get(k))
                        if v.startswith('http'): return v
            except Exception: continue
        return ''

    def display_uav(self):
        l,f,sid=self.selected_scene()
        if f is None: self.panel.msg('Select one OAM scene feature first.','warning'); return
        try:
            url=self._oam_url(f)
            if not url: raise RuntimeError('No OAM download URL was found for the selected scene.')
            cache=Path(tempfile.gettempdir())/'UAVSVIAnnotation'/'uav'; cache.mkdir(parents=True,exist_ok=True)
            safe=re.sub(r'[^A-Za-z0-9_.-]+','_',sid or 'scene'); ext=Path(urlparse(url).path).suffix or '.tif'; dest=cache/f'{safe}{ext}'
            if not dest.exists():
                with requests.get(url,stream=True,timeout=60,headers={'User-Agent':'UAV-SVI-Annotation-Explorer/0.3.1'}) as r:
                    r.raise_for_status()
                    with open(dest,'wb') as out:
                        for chunk in r.iter_content(1024*1024):
                            if chunk: out.write(chunk)
            uav=QgsRasterLayer(str(dest),f'{TEMP_PREFIX}UAV • {sid}')
            if not uav.isValid(): raise RuntimeError('Downloaded OAM file is not a valid raster.')
            QgsProject.instance().addMapLayer(uav)
            self.uavs.append(uav)
            self.uav=uav
            self.iface.mapCanvas().setExtent(uav.extent()); self.iface.mapCanvas().refresh()
            self.panel.msg(f'UAV displayed temporarily for <b>{sid}</b>.')
        except Exception as e:
            self.panel.msg(f'OAM could not be displayed: {e}<br>Other SVI/OSM/direction tools remain available.','warning')

    def _apply_annadeckmyn_style(self, layer, level):
        """Apply the original MapillaryClickPreview QML styling when available.

        The reference plugin uses mapillary_image.qml and mapillary_sequence.qml
        from its res directory. We fetch the same public styles into the QGIS
        temporary directory so the explorer gets the same green image/sequence
        direction rendering without hard-coding a second styling system.
        """
        if layer is None or not layer.isValid():
            return
        url = f'https://raw.githubusercontent.com/annadeckmyn/MapillaryClickPreview/main/res/mapillary_{level}.qml'
        try:
            cache = Path(tempfile.gettempdir()) / 'UAVSVIAnnotation' / 'mapillary_style'
            cache.mkdir(parents=True, exist_ok=True)
            path = cache / f'mapillary_{level}.qml'
            if not path.exists():
                r = requests.get(url, timeout=15, headers={'User-Agent': 'UAV-SVI-Annotation-Explorer/0.3.2'})
                r.raise_for_status()
                path.write_text(r.text, encoding='utf-8')
            layer.loadNamedStyle(str(path))
            layer.triggerRepaint()
        except Exception as e:
            QgsMessageLog.logMessage(
                f'Could not apply Mapillary reference style for {level}: {e}',
                'UAV/SVI Annotation Explorer', Qgis.Warning
            )

    def _selected_scene_bounds(self):
        layer, feature, sid = self.selected_scene()
        if feature is None or feature.geometry().isEmpty():
            return None, sid
        box = feature.geometry().boundingBox()
        xform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance())
        box = xform.transformBoundingBox(box)
        return (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()), sid

    def display_svi(self):
        # Request only the selected OAM scene bbox. The current map canvas
        # extent is deliberately not used. Older ROI layers remain visible.
        bounds, sid = self._selected_scene_bounds()
        if not bounds:
            self.panel.msg('Select one OAM scene feature first so Mapillary can be requested for that scene bbox.', 'warning')
            return
        try:
            before = len(getattr(self.backend, '_explorer_coverage_layers', []))
            self.backend._load_coverage('original', force=True, bounds=bounds, append=True)
            after = len(getattr(self.backend, '_explorer_coverage_layers', []))
            if after <= before:
                self.panel.msg(f'No Mapillary coverage was returned for scene <b>{sid}</b>.', 'warning')
                return
            self.panel.msg(f'Temporary Mapillary SVI coverage loaded for scene <b>{sid}</b> bbox. Older ROI coverage remains visible.')
        except Exception as e:
            self.panel.msg(f'Mapillary coverage could not be displayed: {e}','warning')

    def display_osm(self):
        bounds, sid = self._selected_scene_bounds()
        if not bounds:
            self.panel.msg('Select one OAM scene feature first.', 'warning')
            return
        try:
            layer=self.backend._load_buildings(bounds=bounds, append=True)
            if layer:
                sym=QgsFillSymbol.createSimple({'color':'255,80,80,25','outline_color':'255,60,60,220','outline_width':'0.8'})
                layer.setRenderer(QgsSingleSymbolRenderer(sym)); layer.triggerRepaint()
                self.panel.msg(f'Temporary OSM buildings displayed for scene <b>{sid}</b>: <b>{layer.featureCount()}</b>.')
            else: self.panel.msg('No OSM buildings were returned for the selected scene.','warning')
        except Exception as e:
            self.panel.msg(f'OSM could not be downloaded: {e}','warning')

    def _find_svi_layers(self):
        return [l for l in QgsProject.instance().mapLayers().values()
                if isinstance(l,QgsVectorLayer) and l.name().startswith('Mapillary image') and l.fields().lookupField('id')>=0]

    def _find_svi(self):
        layers=self._find_svi_layers()
        return layers[-1] if layers else None

    def _find_building_layers(self):
        return [l for l in QgsProject.instance().mapLayers().values()
                if isinstance(l,QgsVectorLayer) and l.name().startswith('OSM Buildings')]

    def _find_buildings(self):
        layers=self._find_building_layers()
        return layers[-1] if layers else None

    def _nearest(self,svi_layer,svi_feature,bld_layer):
        metric=metric_crs_from_layer(svi_layer,svi_feature)
        p=transform_geom(svi_feature.geometry(),svi_layer.crs(),metric).asPoint()
        idx=QgsSpatialIndex(bld_layer.getFeatures()); best=None; bestd=float('inf'); maxd=self.panel.buffer.value()
        for fid in idx.nearestNeighbor(p,30):
            b=bld_layer.getFeature(fid)
            c=transform_geom(b.geometry(),bld_layer.crs(),metric).centroid().asPoint(); d=math.hypot(c.x()-p.x(),c.y()-p.y())
            if d<=maxd and d<bestd: best=(b,c,d,metric,p); bestd=d
        return best

    def _field(self, name, kind):
        """QGIS 4 field helper using Qt6 QMetaType values."""
        return QgsField(name, kind)

    @staticmethod
    def _arrow_geometry(start, compass_deg, length):
        """Return a 2-part MultiLineString arrow following Mapillary compass angle.

        Mapillary compass_angle is degrees clockwise from north. The main arrow
        therefore uses dx = sin(theta), dy = cos(theta) in a metric CRS.
        """
        theta = math.radians(float(compass_deg))
        dx, dy = math.sin(theta), math.cos(theta)
        tip = QgsPointXY(start.x() + dx * length, start.y() + dy * length)
        head = min(max(length * 0.28, 2.0), 6.0)
        wing = head * 0.55
        base = QgsPointXY(tip.x() - dx * head, tip.y() - dy * head)
        # perpendicular to the heading vector
        px, py = -dy, dx
        left = QgsPointXY(base.x() + px * wing, base.y() + py * wing)
        right = QgsPointXY(base.x() - px * wing, base.y() - py * wing)
        return QgsGeometry.fromMultiPolylineXY([
            [start, tip],
            [left, tip, right],
        ])

    def calculate_directions(self):
        """Replicate the heat-resilience preprocessing logic for exploration.

        For every building:
          1. transform building centroid and Mapillary points to a metric CRS;
          2. find the nearest Mapillary point within the search radius;
          3. keep it only when it is within the configured nearby threshold;
          4. test line-of-sight against the full building layer;
          5. qualify the building only when the nearest point is visible.

        Only qualifying buildings and their selected Mapillary points are
        highlighted. The original OSM and Mapillary layers remain untouched.
        """
        svi_layers=self._find_svi_layers()
        bld_layers=self._find_building_layers()
        if not svi_layers:
            self.panel.msg('No Mapillary image layer found. Click Display SVI first.', 'warning'); return
        if not bld_layers:
            self.panel.msg('No OSM Buildings layer found. Click Display OSM Buildings first.', 'warning'); return

        # Remove previous calculation outputs, but never remove the source
        # OSM/Mapillary/UAV exploration layers.
        for attr in ('direction','direction_arrow_layer','direction_arrow','highlight','connection','relevant_buildings','qualified_svi'):
            layer=getattr(self,attr,None)
            if layer:
                try: QgsProject.instance().removeMapLayer(layer.id())
                except Exception: pass
                setattr(self,attr,None)

        search_radius=float(self.panel.buffer.value())
        nearby_radius=search_radius

        # One metric CRS centred on the current SVI/building area.
        seed_layer=svi_layers[-1]
        seed_feature=next(seed_layer.getFeatures(),None)
        if seed_feature is None:
            self.panel.msg('The Mapillary layer contains no image points.', 'warning'); return
        out_crs=metric_crs_from_layer(seed_layer,seed_feature)

        # Spatial indexes for fast nearest-point candidate lookup.
        svi_indexes=[(sl,QgsSpatialIndex(sl.getFeatures())) for sl in svi_layers]

        # Build a combined building list. This is also the obstruction layer,
        # matching the source preprocessing code's use of buildings_gdf.sindex.
        building_records=[]
        for bl in bld_layers:
            for bf in bl.getFeatures():
                if not bf.isValid() or bf.geometry().isEmpty():
                    continue
                geom_metric=transform_geom(bf.geometry(),bl.crs(),out_crs)
                centroid=geom_metric.centroid().asPoint()
                building_records.append((bl,bf,geom_metric,centroid))

        if not building_records:
            self.panel.msg('No OSM building features are available for analysis.', 'warning'); return

        # Build one spatial index over all building geometries in the metric CRS.
        obstruction_layer=QgsVectorLayer(f'Polygon?crs={out_crs.authid()}','TEMP • Building obstruction index', 'memory')
        op=obstruction_layer.dataProvider()
        op.addAttributes([self._field('source_layer',QMetaType.Type.QString), self._field('source_fid',QMetaType.Type.QString), self._field('osm_id',QMetaType.Type.QString)])
        obstruction_layer.updateFields()
        obstruction_map={}
        for idx,(bl,bf,g,c) in enumerate(building_records):
            of=QgsFeature(obstruction_layer.fields()); of.setGeometry(g)
            of['source_layer']=bl.id(); of['source_fid']=str(bf.id()); of['osm_id']=field_value(bf,('osm_id','id'))
            op.addFeature(of); obstruction_map[idx]=(bl,bf)
        obstruction_layer.updateExtents()
        obstruction_index=QgsSpatialIndex(obstruction_layer.getFeatures())

        matches=[]
        # Equivalent to cKDTree(...).query(..., distance_upper_bound=...):
        # candidate filtering uses an index, then exact metric distance chooses
        # exactly ONE nearest Mapillary point for each building.
        for bl,bf,bgeom,bc in building_records:
            best=None
            best_distance=float('inf')
            best_svi=None
            best_svi_metric=None
            best_svi_layer=None

            for sl,sidx in svi_indexes:
                bc_sl=transform_geom(QgsGeometry.fromPointXY(bc),out_crs,sl.crs()).asPoint()
                if sl.crs().isGeographic():
                    lat=max(-89.0,min(89.0,bc_sl.y()))
                    dy=search_radius/111320.0
                    dx=search_radius/(111320.0*max(0.1,math.cos(math.radians(lat))))
                else:
                    dx=dy=search_radius
                rect=QgsRectangle(bc_sl.x()-dx,bc_sl.y()-dy,bc_sl.x()+dx,bc_sl.y()+dy)

                for fid in sidx.intersects(rect):
                    sf=sl.getFeature(fid)
                    if not sf.isValid() or sf.geometry().isEmpty():
                        continue
                    sp=transform_geom(sf.geometry(),sl.crs(),out_crs).asPoint()
                    d=math.hypot(sp.x()-bc.x(),sp.y()-bc.y())
                    if d<=search_radius and d<best_distance:
                        best_distance=d
                        best_svi=sf
                        best_svi_metric=sp
                        best_svi_layer=sl

            if best_svi is None or best_distance>nearby_radius:
                continue

            # Line-of-sight test, following the source code. Ignore the target
            # building itself by source layer + feature id rather than relying
            # only on osm_id, since some temporary OSM layers may lack osm_id.
            line_geom=QgsGeometry.fromPolylineXY([best_svi_metric,bc])
            possible=obstruction_index.intersects(line_geom.boundingBox())
            obstructed=False
            for oid in possible:
                ob=obstruction_layer.getFeature(oid)
                src_layer,src_feature=obstruction_map.get(oid,(None,None))
                if src_layer is bl and src_feature is not None and src_feature.id()==bf.id():
                    continue
                if ob.geometry().intersects(line_geom):
                    obstructed=True
                    break
            if obstructed:
                continue

            matches.append((bf,bl,best_svi,best_svi_layer,best_svi_metric,bc,best_distance))

        if not matches:
            self.panel.msg(f'No buildings passed nearest-point, {nearby_radius:.0f} m distance, and line-of-sight checks.', 'warning'); return

        # ---- Qualified building overlay ----
        rel=QgsVectorLayer(f'Polygon?crs={out_crs.authid()}','TEMP • Qualified Buildings','memory')
        rp=rel.dataProvider()
        rp.addAttributes([
            self._field('relevant',QMetaType.Type.QString),
            self._field('mapillary_id',QMetaType.Type.QString),
            self._field('distance_m',QMetaType.Type.Double),
            self._field('visible',QMetaType.Type.QString)])
        rel.updateFields()
        for bf,bl,sf,sl,sp,bc,d in matches:
            rf=QgsFeature(rel.fields())
            rf.setGeometry(transform_geom(bf.geometry(),bl.crs(),out_crs))
            rf['relevant']='yes'
            rf['mapillary_id']=field_value(sf,('id','image_id','mapillary_id')) or str(sf.id())
            rf['distance_m']=float(d)
            rf['visible']='yes'
            rp.addFeature(rf)
        rel.updateExtents()
        rel.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
            'color':'255,235,59,80','outline_color':'255,120,0,255','outline_width':'1.8'})))
        QgsProject.instance().addMapLayer(rel)
        self.relevant_buildings=rel

        # ---- Qualified Mapillary point overlay ----
        point=QgsVectorLayer(f'Point?crs={out_crs.authid()}','TEMP • Qualified Mapillary Images','memory')
        pp=point.dataProvider()
        pp.addAttributes([
            self._field('mapillary_id',QMetaType.Type.QString),
            self._field('distance_m',QMetaType.Type.Double),
            self._field('visible',QMetaType.Type.QString)])
        point.updateFields()
        seen_svi=set()
        for bf,bl,sf,sl,sp,bc,d in matches:
            sid=field_value(sf,('id','image_id','mapillary_id')) or str(sf.id())
            # A Mapillary point is displayed once even if it is the nearest
            # visible point for more than one building.
            key=(sl.id(),sf.id())
            if key in seen_svi:
                continue
            seen_svi.add(key)
            pf=QgsFeature(point.fields()); pf.setGeometry(QgsGeometry.fromPointXY(sp)); pf['mapillary_id']=sid; pf['distance_m']=float(d); pf['visible']='yes'; pp.addFeature(pf)
        point.updateExtents()
        point.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({'color':'0,220,120,255','outline_color':'0,80,50,255','size':'5.0'})))
        QgsProject.instance().addMapLayer(point)
        self.qualified_svi=point

        # ---- Building-centroid to selected Mapillary relation + compass ----
        line=QgsVectorLayer(f'MultiLineString?crs={out_crs.authid()}','TEMP • Building-SVI Directions','memory')
        pr=line.dataProvider()
        pr.addAttributes([
            self._field('building_id',QMetaType.Type.QString),
            self._field('svi_id',QMetaType.Type.QString),
            self._field('distance_m',QMetaType.Type.Double),
            self._field('bearing_deg',QMetaType.Type.Double),
            self._field('compass_deg',QMetaType.Type.Double),
            self._field('relative_deg',QMetaType.Type.Double)])
        line.updateFields()
        arrows=[]
        for bf,bl,sf,sl,sp,bc,distance in matches:
            building_bearing=bearing(bc,sp,out_crs)
            raw=field_value(sf,COMPASS_FIELDS)
            try: compass=float(raw)%360.0
            except Exception: compass=None
            relative=(((building_bearing-compass+180)%360)-180) if compass is not None else None
            parts=[[bc,sp]]
            if compass is not None:
                arrow_len=min(max(distance*0.35,8.0),25.0)
                geom=self._arrow_geometry(sp,compass,arrow_len)
                parts.extend(geom.asMultiPolyline()); arrows.append(geom)
            f=QgsFeature(line.fields()); f.setGeometry(QgsGeometry.fromMultiPolylineXY(parts))
            f['building_id']=field_value(bf,('osm_id','id')) or str(bf.id())
            f['svi_id']=field_value(sf,('id','image_id','mapillary_id')) or str(sf.id())
            f['distance_m']=float(distance); f['bearing_deg']=float(building_bearing); f['compass_deg']=compass; f['relative_deg']=relative
            pr.addFeature(f)

        line.updateExtents()
        line.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({'color':'220,30,30,220','width':'1.5'})))
        QgsProject.instance().addMapLayer(line); self.direction=line

        arr=QgsVectorLayer(f'MultiLineString?crs={out_crs.authid()}','TEMP • Camera Direction Arrows','memory')
        ap=arr.dataProvider(); ap.addAttributes([self._field('compass_deg',QMetaType.Type.Double)]); arr.updateFields()
        for geom in arrows:
            af=QgsFeature(arr.fields()); af.setGeometry(geom); ap.addFeature(af)
        arr.updateExtents(); arr.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({'color':'30,90,220,240','width':'2.0'})))
        QgsProject.instance().addMapLayer(arr); self.direction_arrow_layer=arr

        unique_svi=len(seen_svi)
        self.panel.msg(
            f'Qualified <b>{len(matches)}</b> buildings and <b>{unique_svi}</b> Mapillary images. '
            f'Each building uses its nearest Mapillary point within <b>{search_radius:.0f} m</b>, '
            f'followed by line-of-sight filtering. Yellow = buildings; green = selected Mapillary points; '
            f'red = relation; blue = compass direction.'
        )

    def _nearest_with_index(self, svi_layer, svi_feature, bld_layer, index, maxd):
        metric=metric_crs_from_layer(svi_layer,svi_feature)
        p=transform_geom(svi_feature.geometry(),svi_layer.crs(),metric).asPoint()
        p_bld=transform_geom(svi_feature.geometry(),svi_layer.crs(),bld_layer.crs()).asPoint()
        if bld_layer.crs().isGeographic():
            lat=max(-89.0,min(89.0,p_bld.y())); dy=maxd/111320.0; dx=maxd/(111320.0*max(0.1,math.cos(math.radians(lat))))
        else:
            dx=dy=maxd
        rect=QgsRectangle(p_bld.x()-dx,p_bld.y()-dy,p_bld.x()+dx,p_bld.y()+dy)
        best=None; bestd=float('inf')
        for fid in index.intersects(rect):
            b=bld_layer.getFeature(fid)
            if not b.isValid() or b.geometry().isEmpty(): continue
            c=transform_geom(b.geometry(),bld_layer.crs(),metric).centroid().asPoint()
            d=math.hypot(c.x()-p.x(),c.y()-p.y())
            if d<=maxd and d<bestd:
                best=(b,c,d,metric,p); bestd=d
        return best

    def preview_selected_mapillary(self):
        layer=self.iface.activeLayer() if isinstance(self.iface.activeLayer(),QgsVectorLayer) else None
        if not (layer and layer.name().startswith('Mapillary image') and layer.fields().lookupField('id')>=0):
            selected_layers=[l for l in self._find_svi_layers() if l.selectedFeatureCount()>0]
            layer=selected_layers[-1] if selected_layers else self._find_svi()
        if not layer:
            self.panel.msg('No Mapillary image layer found. Click Display SVI first.', 'warning'); return
        sf=selected_feature(layer)
        if not sf:
            self.panel.msg('Select one feature in a Mapillary image layer first.', 'warning'); return
        self.iface.setActiveLayer(layer)
        result=tool.preview_selected_feature()
        if not result:
            self.panel.msg('Mapillary preview could not be loaded.', 'warning'); return

        best=None
        for bld in self._find_building_layers():
            m=self._nearest_with_index(layer,sf,bld,QgsSpatialIndex(bld.getFeatures()),float(self.panel.buffer.value()))
            if m and (best is None or m[2]<best[2]): best=m
        if best:
            self._highlight(sf,layer,best)
            self.panel.msg(f'Previewing <b>{result["id"]}</b>. Relevant building highlighted; red = SVI to centroid; blue = camera compass direction.')
        else:
            self.panel.msg(f'Previewing Mapillary image <b>{result["id"]}</b>. No building was found within the search buffer.')

    def _highlight(self, sf, svi, bmatch):
        """Highlight selected building and show a clean SVI -> centroid relation."""
        b, centroid, distance, metric, p = bmatch
        for attr in ('highlight', 'connection', 'direction_arrow'):
            layer = getattr(self, attr, None)
            if layer:
                try:
                    QgsProject.instance().removeMapLayer(layer.id())
                except Exception:
                    pass
                setattr(self, attr, None)

        bld_crs = self._find_buildings().crs()
        h = QgsVectorLayer(f'Polygon?crs={bld_crs.authid()}', 'TEMP • Selected Building', 'memory')
        hp = h.dataProvider()
        hp.addAttributes([self._field('osm_id', QMetaType.Type.QString)])
        h.updateFields()
        hf = QgsFeature(h.fields())
        hf.setGeometry(b.geometry())
        hf['osm_id'] = field_value(b, ('osm_id', 'id'))
        hp.addFeature(hf)
        h.updateExtents()
        h.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({
            'color': '255,0,0,25',
            'outline_color': '255,0,0,255',
            'outline_width': '2.0'
        })))
        QgsProject.instance().addMapLayer(h)
        self.highlight = h

        line = QgsVectorLayer(f'LineString?crs={metric.authid()}', 'TEMP • SVI to Building Centroid', 'memory')
        lp = line.dataProvider()
        lp.addAttributes([self._field('distance_m', QMetaType.Type.Double)])
        line.updateFields()
        lf = QgsFeature(line.fields())
        lf.setGeometry(QgsGeometry.fromPolylineXY([p, centroid]))
        lf['distance_m'] = float(distance)
        lp.addFeature(lf)
        line.updateExtents()
        line.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({
            'color': '220,30,30,255', 'width': '2.5'
        })))
        QgsProject.instance().addMapLayer(line)
        self.connection = line

        # Compass arrow for the selected image.
        raw = field_value(sf, COMPASS_FIELDS)
        try:
            compass = float(raw) % 360.0
        except Exception:
            compass = None
        if compass is not None:
            arrow = QgsVectorLayer(f'MultiLineString?crs={metric.authid()}', 'TEMP • Selected Camera Direction', 'memory')
            ap = arrow.dataProvider()
            ap.addAttributes([self._field('compass_deg', QMetaType.Type.Double)])
            arrow.updateFields()
            af = QgsFeature(arrow.fields())
            length = min(max(distance * 0.35, 8.0), 25.0)
            af.setGeometry(self._arrow_geometry(p, compass, length))
            af['compass_deg'] = compass
            ap.addFeature(af)
            arrow.updateExtents()
            arrow.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({
                'color': '30,90,220,255', 'width': '2.2'
            })))
            QgsProject.instance().addMapLayer(arrow)
            self.direction_arrow = arrow

    def clear_temporary(self):
        try: self.backend._remove_coverage_layers(); self.backend._remove_buildings_layers()
        except Exception: pass
        for l in getattr(self, 'uavs', []):
            try: QgsProject.instance().removeMapLayer(l.id())
            except Exception: pass
        for l in list(self.uavs) + [self.direction,getattr(self,'direction_arrow_layer',None),self.highlight,self.connection,getattr(self,'direction_arrow',None),getattr(self,'relevant_buildings',None)]:
            if l:
                try: QgsProject.instance().removeMapLayer(l.id())
                except Exception: pass
        self.uavs=[]; self.direction=self.highlight=self.connection=self.uav=self.relevant_buildings=None; self.direction_arrow_layer=None; self.direction_arrow=None; self.panel.msg('Temporary exploration layers cleared.')
