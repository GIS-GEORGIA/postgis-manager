"""Import / Export panel — GeoJSON, Shapefile, GeoPackage, CSV, WFS."""

from __future__ import annotations
import os

import psycopg2
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QColor, QFont, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QLineEdit,
    QComboBox, QTextEdit, QGroupBox, QFormLayout, QSpinBox,
    QMessageBox, QFileDialog, QProgressBar, QSplitter,
    QHeaderView, QAbstractItemView, QCheckBox, QSizePolicy,
    QRadioButton, QButtonGroup,
)

from ...utils.geodata_io import (
    import_geojson, import_shapefile, import_geopackage,
    import_csv, export_geojson, export_ogr,
    wfs_capabilities, wfs_to_postgis,
    list_gpkg_layers, find_ogr2ogr,
)


# ── Drop zone ─────────────────────────────────────────────────────────────

class DropZone(QLabel):
    """Drag-and-drop file target."""
    file_dropped = pyqtSignal(str)

    EXTS = {".geojson", ".json", ".shp", ".gpkg", ".csv"}

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(80)
        self.setStyleSheet(
            "QLabel { border: 2px dashed #444; border-radius: 8px; "
            "color: #888; font-size: 13px; padding: 8px; }"
            "QLabel:hover { border-color: #58a6ff; color: #58a6ff; }")

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            urls = e.mimeData().urls()
            if any(os.path.splitext(u.toLocalFile())[1].lower()
                   in self.EXTS for u in urls):
                e.acceptProposedAction()
                self.setStyleSheet(
                    "QLabel { border: 2px dashed #58a6ff; border-radius: 8px; "
                    "color: #58a6ff; font-size: 13px; padding: 8px; "
                    "background: rgba(88,166,255,0.06); }")

    def dragLeaveEvent(self, e):
        self._reset_style()

    def dropEvent(self, e: QDropEvent):
        self._reset_style()
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if os.path.splitext(path)[1].lower() in self.EXTS:
                self.file_dropped.emit(path)
                break

    def _reset_style(self):
        self.setStyleSheet(
            "QLabel { border: 2px dashed #444; border-radius: 8px; "
            "color: #888; font-size: 13px; padding: 8px; }"
            "QLabel:hover { border-color: #58a6ff; color: #58a6ff; }")


# ── Workers ───────────────────────────────────────────────────────────────

class ImportWorker(QThread):
    log      = pyqtSignal(str)
    progress = pyqtSignal(int)        # percent 0-100
    finished = pyqtSignal(bool, str)  # ok, message

    def __init__(self, task: dict):
        super().__init__()
        self.task = task

    def _log(self, msg: str):
        self.log.emit(msg)

    def run(self):
        t = self.task
        kind = t["kind"]
        try:
            if kind == "geojson":
                conn = psycopg2.connect(**t["conn"])
                n = import_geojson(
                    conn, t["path"], t["schema"], t["table"],
                    srid=t.get("srid", 4326),
                    mode=t.get("mode", "create"),
                    log_fn=self._log)
                conn.close()
                self.finished.emit(True, f"✓ {n} features imported")

            elif kind == "shapefile":
                ok = import_shapefile(
                    t["conn"], t["path"], t["schema"], t["table"],
                    srid_override=t.get("srid") or None,
                    mode=t.get("mode", "create"),
                    log_fn=self._log)
                self.finished.emit(ok, "✓ Shapefile imported" if ok
                                   else "✗ Import failed")

            elif kind == "geopackage":
                ok = import_geopackage(
                    t["conn"], t["path"], t.get("layer", ""),
                    t["schema"], t["table"],
                    mode=t.get("mode", "create"),
                    log_fn=self._log)
                self.finished.emit(ok, "✓ GeoPackage imported" if ok
                                   else "✗ Import failed")

            elif kind == "csv":
                conn = psycopg2.connect(**t["conn"])
                n = import_csv(
                    conn, t["path"], t["schema"], t["table"],
                    lon_col=t["lon_col"], lat_col=t["lat_col"],
                    srid=t.get("srid", 4326),
                    delimiter=t.get("delimiter", ","),
                    mode=t.get("mode", "create"),
                    log_fn=self._log)
                conn.close()
                self.finished.emit(True, f"✓ {n} rows imported")

            elif kind == "wfs":
                conn = psycopg2.connect(**t["conn"])
                n = wfs_to_postgis(
                    conn, t["url"], t["layer"],
                    t["schema"], t["table"],
                    max_features=t.get("max_features", 5000),
                    srid=t.get("srid", 4326),
                    mode=t.get("mode", "create"),
                    log_fn=self._log)
                conn.close()
                self.finished.emit(True, f"✓ {n} WFS features imported")

        except Exception as e:
            self.finished.emit(False, f"✗ {e}")


class ExportWorker(QThread):
    log      = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, task: dict):
        super().__init__()
        self.task = task

    def run(self):
        t = self.task
        try:
            fmt = t["format"]
            if fmt == "GeoJSON (pure SQL)":
                conn = psycopg2.connect(**t["conn"])
                n = export_geojson(
                    conn, t["schema"], t["table"], t["geom_col"],
                    t["path"], where=t.get("where", ""),
                    log_fn=self.log.emit)
                conn.close()
                self.finished.emit(True, f"✓ {n} features → {t['path']}")
            else:
                fmt_map = {
                    "GeoPackage (.gpkg)": "GPKG",
                    "Shapefile (.shp)":   "ESRI Shapefile",
                    "GeoJSON (ogr2ogr)":  "GeoJSON",
                    "FlatGeobuf (.fgb)":  "FlatGeobuf",
                    "CSV":                "CSV",
                }
                ogr_fmt = fmt_map.get(fmt, "GPKG")
                ok = export_ogr(
                    t["conn"], t["schema"], t["table"],
                    t["path"], fmt=ogr_fmt,
                    where=t.get("where", ""),
                    log_fn=self.log.emit)
                self.finished.emit(ok, f"✓ Exported → {t['path']}" if ok
                                   else "✗ Export failed")
        except Exception as e:
            self.finished.emit(False, f"✗ {e}")


class WFSCapWorker(QThread):
    ready    = pyqtSignal(list)
    error    = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            layers = wfs_capabilities(self.url)
            self.ready.emit(layers)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


# ── Main panel ────────────────────────────────────────────────────────────

class ImportExportPanel(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._conn_params: dict = {}
        self._import_worker: ImportWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._wfs_layers: list[dict] = []
        self._setup_ui()

    def set_connection(self, params: dict):
        self._conn_params = params

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # ogr2ogr status bar
        ogr = find_ogr2ogr()
        ogr_bar = QHBoxLayout()
        ogr_bar.setContentsMargins(8, 4, 8, 0)
        ogr_lbl = QLabel(
            f"ogr2ogr: <b style='color:#3fb950;'>{ogr}</b>" if ogr
            else "ogr2ogr: <b style='color:#ff7b72;'>NOT FOUND</b> "
                 "— Shapefile/GeoPackage import requires GDAL")
        ogr_lbl.setTextFormat(Qt.TextFormat.RichText)
        ogr_lbl.setWordWrap(True)
        ogr_bar.addWidget(ogr_lbl)
        root.addLayout(ogr_bar)

        tabs = QTabWidget()
        tabs.addTab(self._build_import_tab(), "📥  Import")
        tabs.addTab(self._build_export_tab(), "📤  Export")
        tabs.addTab(self._build_wfs_tab(),    "🌐  WFS / WMS")
        root.addWidget(tabs, 1)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 10))
        self._log.setMaximumHeight(120)
        root.addWidget(self._log)

    # ── Tab 1: Import ─────────────────────────────────────────────────────

    def _build_import_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        # Drop zone
        drop = DropZone(
            "⬇  Drag & Drop  GeoJSON · Shapefile · GeoPackage · CSV\n"
            "or use the buttons below to browse")
        drop.file_dropped.connect(self._auto_detect_file)
        lay.addWidget(drop)

        # Source selector tabs
        src_tabs = QTabWidget()
        src_tabs.addTab(self._build_geojson_src(), "GeoJSON")
        src_tabs.addTab(self._build_shp_src(),     "Shapefile")
        src_tabs.addTab(self._build_gpkg_src(),    "GeoPackage")
        src_tabs.addTab(self._build_csv_src(),     "CSV")
        self._src_tabs = src_tabs
        lay.addWidget(src_tabs)

        # Target
        tgt_box = QGroupBox("Target — PostGIS")
        tfl = QFormLayout(tgt_box)
        self._imp_schema = QLineEdit("public")
        self._imp_table  = QLineEdit()
        self._imp_table.setPlaceholderText("new or existing table name")
        self._imp_srid   = QSpinBox()
        self._imp_srid.setRange(0, 999999)
        self._imp_srid.setValue(4326)
        self._imp_srid.setFixedWidth(90)
        self._imp_mode   = QComboBox()
        self._imp_mode.addItems(["create", "append", "replace"])
        tfl.addRow("Schema:", self._imp_schema)
        tfl.addRow("Table:",  self._imp_table)
        tfl.addRow("SRID:",   self._imp_srid)
        tfl.addRow("Mode:",   self._imp_mode)
        lay.addWidget(tgt_box)

        # Action
        act = QHBoxLayout()
        self._imp_btn = QPushButton("▶  Import")
        self._imp_btn.setStyleSheet("font-weight:bold;")
        self._imp_btn.clicked.connect(self._do_import)
        self._imp_progress = QProgressBar()
        self._imp_progress.setRange(0, 0)
        self._imp_progress.setVisible(False)
        act.addWidget(self._imp_btn)
        act.addWidget(self._imp_progress, 1)
        lay.addLayout(act)
        return w

    def _build_geojson_src(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._gj_path = QLineEdit()
        self._gj_path.setPlaceholderText("path to .geojson / .json")
        btn = QPushButton("Browse…")
        btn.clicked.connect(lambda: self._browse(
            self._gj_path,
            "GeoJSON files (*.geojson *.json);;All files (*)"))
        row = self._hrow(self._gj_path, btn)
        fl.addRow("File:", row)
        return w

    def _build_shp_src(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._shp_path = QLineEdit()
        self._shp_path.setPlaceholderText("path to .shp")
        btn = QPushButton("Browse…")
        btn.clicked.connect(lambda: self._browse(
            self._shp_path,
            "Shapefile (*.shp);;All files (*)"))
        self._shp_srid_override = QSpinBox()
        self._shp_srid_override.setRange(0, 999999)
        self._shp_srid_override.setValue(0)
        self._shp_srid_override.setSpecialValueText("auto-detect")
        fl.addRow("File:", self._hrow(self._shp_path, btn))
        fl.addRow("SRID override (0=auto):", self._shp_srid_override)
        return w

    def _build_gpkg_src(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._gpkg_path = QLineEdit()
        self._gpkg_path.setPlaceholderText("path to .gpkg")
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(lambda: self._browse(
            self._gpkg_path,
            "GeoPackage (*.gpkg);;All files (*)"))
        self._gpkg_path.textChanged.connect(self._refresh_gpkg_layers)
        self._gpkg_layer = QComboBox()
        btn_refresh = QPushButton("↺")
        btn_refresh.setFixedWidth(28)
        btn_refresh.clicked.connect(
            lambda: self._refresh_gpkg_layers(self._gpkg_path.text()))
        fl.addRow("File:",  self._hrow(self._gpkg_path, btn_browse))
        fl.addRow("Layer:", self._hrow(self._gpkg_layer, btn_refresh))
        return w

    def _build_csv_src(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        self._csv_path = QLineEdit()
        btn = QPushButton("Browse…")
        btn.clicked.connect(lambda: self._browse(
            self._csv_path,
            "CSV files (*.csv *.tsv *.txt);;All files (*)"))
        self._csv_lon   = QLineEdit("longitude")
        self._csv_lat   = QLineEdit("latitude")
        self._csv_delim = QComboBox()
        self._csv_delim.addItems([",", ";", "\\t", "|"])
        fl.addRow("File:",      self._hrow(self._csv_path, btn))
        fl.addRow("Longitude column:", self._csv_lon)
        fl.addRow("Latitude column:",  self._csv_lat)
        fl.addRow("Delimiter:", self._csv_delim)
        return w

    # ── Tab 2: Export ─────────────────────────────────────────────────────

    def _build_export_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        src_box = QGroupBox("Source — PostGIS")
        sfl = QFormLayout(src_box)
        self._exp_schema   = QLineEdit("public")
        self._exp_table    = QLineEdit()
        self._exp_table.setPlaceholderText("table name")
        self._exp_geom_col = QLineEdit("geom")
        self._exp_where    = QLineEdit()
        self._exp_where.setPlaceholderText("optional WHERE clause (no WHERE keyword)")
        sfl.addRow("Schema:",      self._exp_schema)
        sfl.addRow("Table:",       self._exp_table)
        sfl.addRow("Geom column:", self._exp_geom_col)
        sfl.addRow("Filter:",      self._exp_where)
        lay.addWidget(src_box)

        fmt_box = QGroupBox("Output Format & Path")
        ffl = QFormLayout(fmt_box)
        self._exp_format = QComboBox()
        self._exp_format.addItems([
            "GeoJSON (pure SQL)",
            "GeoPackage (.gpkg)",
            "Shapefile (.shp)",
            "GeoJSON (ogr2ogr)",
            "FlatGeobuf (.fgb)",
        ])
        self._exp_path = QLineEdit()
        self._exp_path.setPlaceholderText("output file path")
        btn_out = QPushButton("Browse…")
        btn_out.clicked.connect(self._browse_export_path)
        ffl.addRow("Format:", self._exp_format)
        ffl.addRow("Output:", self._hrow(self._exp_path, btn_out))
        lay.addWidget(fmt_box)

        act = QHBoxLayout()
        self._exp_btn = QPushButton("▶  Export")
        self._exp_btn.setStyleSheet("font-weight:bold;")
        self._exp_btn.clicked.connect(self._do_export)
        self._exp_progress = QProgressBar()
        self._exp_progress.setRange(0, 0)
        self._exp_progress.setVisible(False)
        act.addWidget(self._exp_btn)
        act.addWidget(self._exp_progress, 1)
        lay.addLayout(act)
        lay.addStretch()
        return w

    # ── Tab 3: WFS ────────────────────────────────────────────────────────

    def _build_wfs_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        url_box = QGroupBox("WFS Endpoint")
        ufl = QFormLayout(url_box)
        self._wfs_url = QLineEdit()
        self._wfs_url.setPlaceholderText(
            "https://example.com/geoserver/wfs")
        btn_cap = QPushButton("Get Capabilities")
        btn_cap.clicked.connect(self._get_wfs_caps)
        ufl.addRow("URL:", self._wfs_url)
        ufl.addRow("",    btn_cap)
        lay.addWidget(url_box)

        # Layers table
        self._wfs_table = QTableWidget(0, 3)
        self._wfs_table.setHorizontalHeaderLabels(
            ["Layer name", "Title", "CRS"])
        self._wfs_table.horizontalHeader().setStretchLastSection(True)
        self._wfs_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._wfs_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._wfs_table.itemSelectionChanged.connect(
            self._on_wfs_layer_selected)
        lay.addWidget(self._wfs_table, 1)

        imp_box = QGroupBox("Import Selected Layer → PostGIS")
        ifl = QFormLayout(imp_box)
        self._wfs_schema     = QLineEdit("public")
        self._wfs_tbl        = QLineEdit()
        self._wfs_max        = QSpinBox()
        self._wfs_max.setRange(1, 100000)
        self._wfs_max.setValue(5000)
        self._wfs_srid       = QSpinBox()
        self._wfs_srid.setRange(0, 999999)
        self._wfs_srid.setValue(4326)
        self._wfs_mode       = QComboBox()
        self._wfs_mode.addItems(["create", "append", "replace"])
        ifl.addRow("Schema:",      self._wfs_schema)
        ifl.addRow("Table:",       self._wfs_tbl)
        ifl.addRow("Max features:", self._wfs_max)
        ifl.addRow("SRID:",        self._wfs_srid)
        ifl.addRow("Mode:",        self._wfs_mode)
        btn_imp_wfs = QPushButton("▶  Import Layer")
        btn_imp_wfs.clicked.connect(self._import_wfs_layer)
        ifl.addRow("", btn_imp_wfs)
        lay.addWidget(imp_box)
        return w

    # ── Auto-detect dropped file ──────────────────────────────────────────

    def _auto_detect_file(self, path: str):
        ext = os.path.splitext(path)[1].lower()
        if ext in (".geojson", ".json"):
            self._src_tabs.setCurrentIndex(0)
            self._gj_path.setText(path)
        elif ext == ".shp":
            self._src_tabs.setCurrentIndex(1)
            self._shp_path.setText(path)
        elif ext == ".gpkg":
            self._src_tabs.setCurrentIndex(2)
            self._gpkg_path.setText(path)
            self._refresh_gpkg_layers(path)
        elif ext == ".csv":
            self._src_tabs.setCurrentIndex(3)
            self._csv_path.setText(path)
        # auto-fill table name from filename
        base = os.path.splitext(os.path.basename(path))[0]
        self._imp_table.setText(base.lower().replace(" ", "_").replace("-", "_"))
        self._log_msg(f"File loaded: {path}")

    def _refresh_gpkg_layers(self, path: str):
        if not path or not os.path.isfile(path):
            return
        layers = list_gpkg_layers(path)
        self._gpkg_layer.clear()
        self._gpkg_layer.addItems(layers)

    # ── Import slots ──────────────────────────────────────────────────────

    def _do_import(self):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection",
                                "Open a DB connection first.")
            return
        idx   = self._src_tabs.currentIndex()
        kinds = ["geojson", "shapefile", "geopackage", "csv"]
        kind  = kinds[idx]
        schema = self._imp_schema.text().strip()
        table  = self._imp_table.text().strip()
        if not table:
            QMessageBox.warning(self, "Missing", "Enter a table name.")
            return

        task: dict = {
            "kind":   kind,
            "conn":   self._conn_params,
            "schema": schema,
            "table":  table,
            "srid":   self._imp_srid.value(),
            "mode":   self._imp_mode.currentText(),
        }

        if kind == "geojson":
            p = self._gj_path.text().strip()
            if not p:
                return self._warn_no_file()
            task["path"] = p
        elif kind == "shapefile":
            p = self._shp_path.text().strip()
            if not p:
                return self._warn_no_file()
            task["path"] = p
            ov = self._shp_srid_override.value()
            if ov:
                task["srid"] = ov
        elif kind == "geopackage":
            p = self._gpkg_path.text().strip()
            if not p:
                return self._warn_no_file()
            task["path"]  = p
            task["layer"] = self._gpkg_layer.currentText()
        elif kind == "csv":
            p = self._csv_path.text().strip()
            if not p:
                return self._warn_no_file()
            task["path"]      = p
            task["lon_col"]   = self._csv_lon.text().strip()
            task["lat_col"]   = self._csv_lat.text().strip()
            delim = self._csv_delim.currentText()
            task["delimiter"] = "\t" if delim == "\\t" else delim

        self._start_import(task)

    def _start_import(self, task: dict):
        if self._import_worker and self._import_worker.isRunning():
            return
        self._imp_btn.setEnabled(False)
        self._imp_progress.setVisible(True)
        w = ImportWorker(task)
        w.log.connect(self._log_msg)
        w.finished.connect(self._on_import_done)
        w.finished.connect(w.deleteLater)
        self._import_worker = w
        w.start()

    def _on_import_done(self, ok: bool, msg: str):
        self._imp_btn.setEnabled(True)
        self._imp_progress.setVisible(False)
        self._log_msg(msg, "success" if ok else "error")
        if ok:
            QMessageBox.information(self, "Import complete", msg)

    # ── Export slots ──────────────────────────────────────────────────────

    def _browse_export_path(self):
        fmt = self._exp_format.currentText()
        filters = {
            "GeoJSON (pure SQL)":  "GeoJSON (*.geojson)",
            "GeoPackage (.gpkg)":  "GeoPackage (*.gpkg)",
            "Shapefile (.shp)":    "Shapefile (*.shp)",
            "GeoJSON (ogr2ogr)":   "GeoJSON (*.geojson)",
            "FlatGeobuf (.fgb)":   "FlatGeobuf (*.fgb)",
        }
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to",
            self._exp_table.text() or "export",
            filters.get(fmt, "All files (*)"))
        if path:
            self._exp_path.setText(path)

    def _do_export(self):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection",
                                "Open a DB connection first.")
            return
        path = self._exp_path.text().strip()
        if not path:
            QMessageBox.warning(self, "No path", "Choose an output file.")
            return
        task = {
            "conn":     self._conn_params,
            "schema":   self._exp_schema.text().strip(),
            "table":    self._exp_table.text().strip(),
            "geom_col": self._exp_geom_col.text().strip() or "geom",
            "where":    self._exp_where.text().strip(),
            "format":   self._exp_format.currentText(),
            "path":     path,
        }
        if self._export_worker and self._export_worker.isRunning():
            return
        self._exp_btn.setEnabled(False)
        self._exp_progress.setVisible(True)
        w = ExportWorker(task)
        w.log.connect(self._log_msg)
        w.finished.connect(self._on_export_done)
        w.finished.connect(w.deleteLater)
        self._export_worker = w
        w.start()

    def _on_export_done(self, ok: bool, msg: str):
        self._exp_btn.setEnabled(True)
        self._exp_progress.setVisible(False)
        self._log_msg(msg, "success" if ok else "error")

    # ── WFS slots ─────────────────────────────────────────────────────────

    def _get_wfs_caps(self):
        url = self._wfs_url.text().strip()
        if not url:
            return
        self._log_msg(f"Fetching capabilities from {url}…")
        w = WFSCapWorker(url)
        w.ready.connect(self._on_wfs_caps)
        w.error.connect(lambda e: self._log_msg(f"✗ WFS error: {e}", "error"))
        w.finished.connect(w.deleteLater)
        w.start()

    def _on_wfs_caps(self, layers: list):
        self._wfs_layers = layers
        t = self._wfs_table
        t.setRowCount(0)
        for lyr in layers:
            r = t.rowCount()
            t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(lyr["name"]))
            t.setItem(r, 1, QTableWidgetItem(lyr["title"]))
            t.setItem(r, 2, QTableWidgetItem(lyr["crs"]))
        self._log_msg(f"✓ {len(layers)} WFS layers found", "success")

    def _on_wfs_layer_selected(self):
        row = self._wfs_table.currentRow()
        if row < 0 or row >= len(self._wfs_layers):
            return
        lyr = self._wfs_layers[row]
        # auto-fill table name
        name = lyr["name"].split(":")[-1]
        self._wfs_tbl.setText(name.lower().replace("-", "_"))

    def _import_wfs_layer(self):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection",
                                "Open a DB connection first.")
            return
        row = self._wfs_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No selection",
                                "Select a layer first.")
            return
        lyr = self._wfs_layers[row]
        task = {
            "kind":         "wfs",
            "conn":         self._conn_params,
            "url":          self._wfs_url.text().strip(),
            "layer":        lyr["name"],
            "schema":       self._wfs_schema.text().strip(),
            "table":        self._wfs_tbl.text().strip() or lyr["name"].split(":")[-1],
            "max_features": self._wfs_max.value(),
            "srid":         self._wfs_srid.value(),
            "mode":         self._wfs_mode.currentText(),
        }
        self._start_import(task)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _browse(self, field: QLineEdit, filt: str):
        path, _ = QFileDialog.getOpenFileName(self, "Select file", "", filt)
        if path:
            field.setText(path)

    def _warn_no_file(self):
        QMessageBox.warning(self, "No file", "Select a file first.")

    def _hrow(self, *widgets) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        for ww in widgets:
            lay.addWidget(ww)
        return w

    def _log_msg(self, msg: str, level: str = "info"):
        colors = {
            "success": "#3fb950", "error": "#ff7b72",
            "warn": "#d29922",    "info":  "#aaa",
        }
        color = colors.get(level, "#aaa")
        self._log.append(f'<span style="color:{color};">{msg}</span>')
