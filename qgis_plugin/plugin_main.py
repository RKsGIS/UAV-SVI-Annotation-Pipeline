"""Minimal QGIS plugin scaffold for the UAV/SVI annotation workflow.

This file intentionally does not replace the existing Mapillary preview plugin
implementation already present in this repository. Instead, it provides a small
starting point for future button-driven scene selection / download / display work.
"""

from __future__ import annotations

try:
    from qgis.PyQt.QtWidgets import QAction
except Exception:  # pragma: no cover - QGIS is not available in CLI environments.
    QAction = object


class UAVSVIPluginMain:
    """Tiny controller skeleton that can be extended inside QGIS later."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):  # noqa: N802 - QGIS naming convention
        if QAction is object:
            return
        self.action = QAction("Download / display selected UAV-SVI scenes", self.iface.mainWindow())
        self.action.triggered.connect(self.handle_download_display)
        self.iface.addPluginToMenu("UAV-SVI Annotation Pipeline", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if not self.action or QAction is object:
            return
        self.iface.removePluginMenu("UAV-SVI Annotation Pipeline", self.action)
        self.iface.removeToolBarIcon(self.action)
        self.action = None

    def handle_download_display(self):
        message = (
            "This is a starting skeleton only. Extend plugin_main.py with the "
            "scene selection, OSM overlay, download, and compass-arrow display logic "
            "that best fits your local QGIS workflow."
        )
        try:
            self.iface.messageBar().pushInfo("UAV-SVI Annotation Pipeline", message)
        except Exception:
            print(message)
