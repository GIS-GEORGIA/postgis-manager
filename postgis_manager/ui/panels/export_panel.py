"""Export panel — PyQt6."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QListWidget, QFileDialog,
    QMessageBox, QAbstractItemView, QProgressBar,
)
from PyQt6.QtCore import QThread, pyqtSignal
from ...db.connection import DBManager
from ...utils import i18n


class ExportWorker(QThread):
    progress = pyqtSignal(str)
    done     = pyqtSignal(int)
    error    = pyqtSignal(str)

    def __init__(self, db, layers, out_path, fmt, srid):
        super().__init__()
        self.db = db; self.layers = layers
        self.out_path = out_path; self.fmt = fmt; self.srid = srid

    def run(self):
        import os
        try:
            import geopandas as gpd
            exported = 0
            for schema, table, geom_col, srid in self.layers:
                self.progress.emit(f"Exporting {schema}.{table}...")
                gdf = self.db.export_to_geodataframe(schema, table, geom_col, srid)
                if self.srid and self.srid != srid:
                    gdf = gdf.to_crs(epsg=self.srid)
                if self.fmt == "gpkg":
                    gdf.to_file(self.out_path, layer=f"{schema}_{table}", driver="GPKG")
                elif self.fmt == "shp":
                    gdf.to_file(os.path.join(self.out_path, f"{schema}_{table}.shp"))
                elif self.fmt == "geojson":
                    gdf.to_file(os.path.join(self.out_path, f"{schema}_{table}.geojson"),
                                driver="GeoJSON")
                elif self.fmt == "csv":
                    gdf.drop(columns="geometry", errors="ignore").to_csv(
                        os.path.join(self.out_path, f"{schema}_{table}.csv"), index=False)
                exported += 1
            self.done.emit(exported)
        except Exception as e:
            self.error.emit(str(e))


class ExportPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db; self._schema_data: dict = {}; self._build_ui()

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
        self._layer_list.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection)
        layout.addWidget(self._layer_list)

        sel_row = QHBoxLayout()
        sa = QPushButton("Select All"); sa.clicked.connect(self._layer_list.selectAll)
        sel_row.addWidget(sa)
        sc = QPushButton("Clear"); sc.clicked.connect(self._layer_list.clearSelection)
        sel_row.addWidget(sc)
        sel_row.addStretch()
        layout.addLayout(sel_row)

        form2 = QFormLayout()
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItem("GeoPackage (.gpkg)", "gpkg")
        self._fmt_combo.addItem("Shapefile (.shp)", "shp")
        self._fmt_combo.addItem("GeoJSON (.geojson)", "geojson")
        self._fmt_combo.addItem("CSV (no geometry)", "csv")
        form2.addRow(i18n.t("export_format"), self._fmt_combo)
        self._srid_edit = QLineEdit(); self._srid_edit.setPlaceholderText("(keep original)")
        form2.addRow(i18n.t("export_srid"), self._srid_edit)
        out_row = QHBoxLayout()
        self._output = QLineEdit()
        out_row.addWidget(self._output)
        b = QPushButton("Browse..."); b.setFixedWidth(80); b.clicked.connect(self._browse)
        out_row.addWidget(b)
        form2.addRow(i18n.t("export_output"), out_row)
        layout.addLayout(form2)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)
        self._status = QLabel("")
        layout.addWidget(self._status)

        export_btn = QPushButton(f"📤  {i18n.t('export_start')}")
        export_btn.clicked.connect(self._run)
        layout.addWidget(export_btn)
        layout.addStretch()

    def set_active_schema(self, schema: str):
        if not self.db.is_connected(): return
        try:
            schemas = self.db.get_schemas()
            self._schema_combo.clear()
            self._schema_combo.addItems(schemas)
            idx = self._schema_combo.findText(schema)
            if idx >= 0: self._schema_combo.setCurrentIndex(idx)
        except Exception: pass

    def _load_layers(self, schema: str):
        self._layer_list.clear()
        if not schema or not self.db.is_connected(): return
        try:
            layers = self.db.get_geometry_layers(schema)
            self._schema_data[schema] = layers
            for name, _, gt, srid in layers:
                self._layer_list.addItem(f"{name}  [{gt}]  EPSG:{srid}")
        except Exception: pass

    def _browse(self):
        fmt = self._fmt_combo.currentData()
        if fmt == "gpkg":
            path, _ = QFileDialog.getSaveFileName(
                self, "Save GeoPackage", "export.gpkg", "GeoPackage (*.gpkg)")
        else:
            path = QFileDialog.getExistingDirectory(self, "Output directory")
        if path: self._output.setText(path)

    def _run(self):
        schema = self._schema_combo.currentText()
        selected = self._layer_list.selectedItems()
        if not selected:
            QMessageBox.warning(self, "Error", "Select at least one layer."); return
        out = self._output.text().strip()
        if not out:
            QMessageBox.warning(self, "Error", "Select output path."); return

        all_layers = self._schema_data.get(schema, [])
        names = {item.text().split("  [")[0] for item in selected}
        to_export = [l for l in all_layers if l[0] in names]
        srid_s = self._srid_edit.text().strip()
        srid = int(srid_s) if srid_s.isdigit() else None

        self._progress.setVisible(True); self._progress.setRange(0, 0)
        worker = ExportWorker(self.db, to_export, out,
                              self._fmt_combo.currentData(), srid)
        worker.progress.connect(lambda m: self._status.setText(m))
        worker.done.connect(self._on_done)
        worker.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        worker.start()

    def _on_done(self, n: int):
        self._progress.setVisible(False)
        out = self._output.text()
        self._status.setText(i18n.t("export_done", path=out))
        if self.parent() and hasattr(self.parent(), "log"):
            self.parent().log(f"Exported {n} layer(s) to {out}", "success")
