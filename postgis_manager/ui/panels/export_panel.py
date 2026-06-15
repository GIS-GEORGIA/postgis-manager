"""Export panel — PostGIS → GeoPackage/Shapefile/GeoJSON (db2gpkg pattern)."""

from __future__ import annotations
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QListWidget, QFileDialog,
    QMessageBox, QAbstractItemView, QProgressBar,
)
from PyQt5.QtCore import QThread, pyqtSignal

from ...db.connection import DBManager
from ...utils import i18n


class ExportWorker(QThread):
    progress = pyqtSignal(str)
    done = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, db, layers, output_path, fmt, srid, schema):
        super().__init__()
        self.db = db
        self.layers = layers   # list of (schema, table, geom_col, srid)
        self.output_path = output_path
        self.fmt = fmt
        self.target_srid = srid
        self.schema = schema

    def run(self):
        try:
            import geopandas as gpd
            exported = 0
            for schema, table, geom_col, srid in self.layers:
                self.progress.emit(f"Exporting {schema}.{table}...")
                gdf = self.db.export_to_geodataframe(schema, table, geom_col, srid)
                if self.target_srid and self.target_srid != srid:
                    gdf = gdf.to_crs(epsg=self.target_srid)
                if self.fmt == "gpkg":
                    gdf.to_file(self.output_path, layer=f"{schema}_{table}",
                                driver="GPKG")
                elif self.fmt == "shp":
                    out = os.path.join(self.output_path, f"{schema}_{table}.shp")
                    gdf.to_file(out)
                elif self.fmt == "geojson":
                    out = os.path.join(self.output_path, f"{schema}_{table}.geojson")
                    gdf.to_file(out, driver="GeoJSON")
                elif self.fmt == "csv":
                    out = os.path.join(self.output_path, f"{schema}_{table}.csv")
                    gdf.drop(columns="geometry", errors="ignore").to_csv(out, index=False)
                exported += 1
            self.done.emit(exported)
        except Exception as e:
            self.error.emit(str(e))


class ExportPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._schema_data: dict = {}
        self._worker: ExportWorker | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel(f"📦  {i18n.t('export_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()

        self._schema_combo = QComboBox()
        self._schema_combo.currentTextChanged.connect(self._load_layers)
        form.addRow(i18n.t("export_schema"), self._schema_combo)
        layout.addLayout(form)

        layout.addWidget(QLabel(i18n.t("export_tables")))
        self._layer_list = QListWidget()
        self._layer_list.setSelectionMode(QAbstractItemView.MultiSelection)
        layout.addWidget(self._layer_list)

        sel_row = QHBoxLayout()
        sel_all = QPushButton("Select All")
        sel_all.setProperty("class", "secondary")
        sel_all.clicked.connect(self._layer_list.selectAll)
        sel_row.addWidget(sel_all)
        sel_none = QPushButton("Clear")
        sel_none.setProperty("class", "secondary")
        sel_none.clicked.connect(self._layer_list.clearSelection)
        sel_row.addWidget(sel_none)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        form2 = QFormLayout()
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItem("GeoPackage (.gpkg)", "gpkg")
        self._fmt_combo.addItem("Shapefile (.shp)", "shp")
        self._fmt_combo.addItem("GeoJSON (.geojson)", "geojson")
        self._fmt_combo.addItem("CSV (no geometry)", "csv")
        self._fmt_combo.currentIndexChanged.connect(self._on_format_change)
        form2.addRow(i18n.t("export_format"), self._fmt_combo)

        self._srid_edit = QLineEdit()
        self._srid_edit.setPlaceholderText("(keep original)")
        form2.addRow(i18n.t("export_srid"), self._srid_edit)

        out_row = QHBoxLayout()
        self._output_edit = QLineEdit()
        out_row.addWidget(self._output_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(browse_btn)
        form2.addRow(i18n.t("export_output"), out_row)
        layout.addLayout(form2)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        export_btn = QPushButton(f"📤  {i18n.t('export_start')}")
        export_btn.clicked.connect(self._run_export)
        layout.addWidget(export_btn)
        layout.addStretch()

    def set_active_schema(self, schema: str):
        idx = self._schema_combo.findText(schema)
        if idx >= 0:
            self._schema_combo.setCurrentIndex(idx)

    def _load_schemas(self):
        if not self.db.is_connected():
            return
        try:
            schemas = self.db.get_schemas()
            self._schema_combo.clear()
            self._schema_combo.addItems(schemas)
        except Exception:
            pass

    def _load_layers(self, schema: str):
        self._layer_list.clear()
        if not schema or not self.db.is_connected():
            return
        try:
            layers = self.db.get_geometry_layers(schema)
            self._schema_data[schema] = layers
            for name, geom_col, geom_type, srid in layers:
                self._layer_list.addItem(f"{name}  [{geom_type}]  EPSG:{srid}")
        except Exception:
            pass

    def _on_format_change(self, _):
        fmt = self._fmt_combo.currentData()
        self._output_edit.clear()

    def _browse_output(self):
        fmt = self._fmt_combo.currentData()
        if fmt == "gpkg":
            path, _ = QFileDialog.getSaveFileName(
                self, "Save GeoPackage", "export.gpkg", "GeoPackage (*.gpkg)")
        else:
            path = QFileDialog.getExistingDirectory(self, "Select output directory")
        if path:
            self._output_edit.setText(path)

    def _run_export(self):
        schema = self._schema_combo.currentText()
        selected = self._layer_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Error", "Select at least one layer.")
            return
        out = self._output_edit.text().strip()
        if not out:
            QMessageBox.warning(self, "Error", "Select output path.")
            return

        layers_data = self._schema_data.get(schema, [])
        selected_names = {item.text().split("  [")[0] for item in selected}
        to_export = [l for l in layers_data if l[0] in selected_names]

        srid_str = self._srid_edit.text().strip()
        target_srid = int(srid_str) if srid_str.isdigit() else None

        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._worker = ExportWorker(
            self.db, to_export, out,
            self._fmt_combo.currentData(), target_srid, schema
        )
        self._worker.progress.connect(
            lambda msg: self._status_label.setText(msg))
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(
            lambda e: QMessageBox.critical(self, "Error", e))
        self._worker.start()

    def _on_done(self, count: int):
        self._progress.setVisible(False)
        out = self._output_edit.text()
        self._status_label.setText(i18n.t("export_done", path=out))
        if self.parent() and hasattr(self.parent(), "log"):
            self.parent().log(f"Exported {count} layer(s) to {out}", "success")
