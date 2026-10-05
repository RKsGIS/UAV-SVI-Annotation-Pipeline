"""Label tab: view the UAV/SVI pairs from the submission folder and write labels_<name>.csv.

The plugin never edits GeoPackages; it only reads submission/ and writes the same CSV the scripts use.
The vocabulary must match scripts/utils/labels.py.
"""
from __future__ import annotations

import csv
import datetime
from pathlib import Path

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton,
                                 QVBoxLayout, QWidget)
from qgis.core import QgsProject, QgsSettings, QgsVectorLayer

VOCAB = {
    "structural_openness": ["closed_structure", "open_structure", "unknown"],
    "number_of_floors": ["one", "two", "three_or_more", "unknown"],
    "vegetation": ["yes", "no"],
    "material_rooftop": ["concrete", "metal", "tile", "asbestos", "thatch_wood", "other", "unknown"],
    "material_wall": ["concrete", "brick", "metal", "wood", "glass", "other", "unknown"],
}
COLUMNS = ["osm_id", "mapillary_id", "is_pano", *VOCAB]
PAIR_COLUMNS = ["osm_id", "mapillary_id", "is_pano", "distance_m", "compass_deg", "logged_at", "notes"]
SETTING = "uavsvi/submission_dir"


class LabelPanel(QWidget):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.rows: list[dict] = []
        self.pos = 0
        self.csv_path: Path | None = None

        root = QVBoxLayout(self)
        self.folder_btn = QPushButton("Choose submission folder ...")
        self.folder_btn.clicked.connect(self.choose_folder)
        self.files = QComboBox()
        self.files.currentIndexChanged.connect(self.load_csv)
        self.info = QLabel("No labels file loaded.")
        self.info.setWordWrap(True)
        self.uav = QLabel()
        self.svi = QLabel()
        images = QHBoxLayout()
        images.addWidget(self.uav)
        images.addWidget(self.svi)

        form = QFormLayout()
        self.boxes = {}
        for column, values in VOCAB.items():
            box = QComboBox()
            box.addItems(["", *values])
            self.boxes[column] = box
            form.addRow(column, box)

        prev_btn, save_btn, skip_btn = QPushButton("Previous"), QPushButton("Save and next"), QPushButton("Next")
        prev_btn.clicked.connect(lambda: self.go(-1))
        skip_btn.clicked.connect(lambda: self.go(1))
        save_btn.clicked.connect(self.save_and_next)
        nav = QHBoxLayout()
        for b in (prev_btn, save_btn, skip_btn):
            nav.addWidget(b)
        zoom_btn = QPushButton("Zoom to building in QGIS")
        zoom_btn.clicked.connect(self.zoom_to_building)

        for item in (self.folder_btn, self.files, self.info):
            root.addWidget(item)
        root.addLayout(images)
        root.addLayout(form)
        root.addLayout(nav)
        root.addWidget(zoom_btn)
        root.addStretch()

        saved = QgsSettings().value(SETTING, "", type=str)
        if saved and Path(saved).is_dir():
            self.set_folder(Path(saved))

    @property
    def folder(self) -> Path | None:
        saved = QgsSettings().value(SETTING, "", type=str)
        return Path(saved) if saved else None

    def choose_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, "Select the submission folder")
        if chosen:
            self.set_folder(Path(chosen))

    def set_folder(self, folder: Path):
        QgsSettings().setValue(SETTING, str(folder))
        self.files.blockSignals(True)
        self.files.clear()
        self.files.addItems(sorted(p.name for p in folder.glob("labels_*.csv")))
        self.files.blockSignals(False)
        self.load_csv()

    def load_csv(self):
        folder = self.folder
        if not folder or not self.files.currentText():
            self.info.setText("No labels_<name>.csv found in the submission folder (run script 05 first).")
            return
        self.csv_path = folder / self.files.currentText()
        with open(self.csv_path, newline="", encoding="utf-8") as fh:
            self.rows = list(csv.DictReader(fh))
        todo = [i for i, r in enumerate(self.rows) if any(not r.get(c) for c in VOCAB)]
        self.pos = todo[0] if todo else 0
        self.show_row()

    def show_row(self):
        if not self.rows:
            return
        row = self.rows[self.pos]
        osm_id = row["osm_id"]
        for label, view in ((self.uav, "uav"), (self.svi, "svi")):
            pix = QPixmap(str(self.folder / view / f"{osm_id}.png"))
            label.setPixmap(pix.scaled(190, 190, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        for column, box in self.boxes.items():
            box.setCurrentText(row.get(column, ""))
        done = sum(all(r.get(c) for c in VOCAB) for r in self.rows)
        self.info.setText(f"<b>{osm_id}</b>  Mapillary {row.get('mapillary_id', '')} (pano: {row.get('is_pano', '')})  ({self.pos + 1}/{len(self.rows)}, {done} labelled)")

    def go(self, step: int):
        if self.rows:
            self.pos = max(0, min(len(self.rows) - 1, self.pos + step))
            self.show_row()

    def save_and_next(self):
        if not self.rows or not self.csv_path:
            return
        for column, box in self.boxes.items():
            self.rows[self.pos][column] = box.currentText()
        with open(self.csv_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.rows)
        self.go(1)

    def zoom_to_building(self):
        if not self.rows:
            return
        osm_id = self.rows[self.pos]["osm_id"].replace("'", "''")
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.fields().lookupField("osm_id") >= 0:
                layer.selectByExpression(f"\"osm_id\" = '{osm_id}'")
                if layer.selectedFeatureCount():
                    self.iface.mapCanvas().zoomToSelected(layer)
                    return

    def log_pair(self, osm_id: str, mapillary_id: str, is_pano, distance_m, compass_deg, notes: str) -> str:
        """Append a human-confirmed building/image pair to selected_pairs.csv for script 02 (--manual-pairs)."""
        folder = self.folder
        if not folder:
            self.choose_folder()
            folder = self.folder
        if not folder:
            return "No submission folder chosen."
        path = folder / "selected_pairs.csv"
        new_file = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=PAIR_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerow({"osm_id": osm_id, "mapillary_id": mapillary_id, "is_pano": is_pano, "distance_m": distance_m,
                             "compass_deg": compass_deg, "logged_at": datetime.datetime.now().isoformat(timespec="seconds"),
                             "notes": notes})
        return f"Logged {osm_id} <-> {mapillary_id} in {path}"
