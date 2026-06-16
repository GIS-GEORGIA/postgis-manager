"""Vector import dialog — PyQt6."""

from __future__ import annotations
import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton, QComboBox, QProgressBar,
    QDialogButtonBox, QFileDialog, QMessageBox, QTextEdit,
)
from PyQt6.QtCore import QThread, pyqtSignal

from ...db.connection import DBManager
from ...utils import i18n


class ImportWorker(QThread):
    log = pyqtSignal(str, str)
    done = pyqtSignal(int)

    def __init__(self, db, path, schema, table, mode, srid, encoding):
        super().__init__()
        self.db = db; self.path = path; self.schema = schema
        self.table = table; self.mode = mode; self.srid = srid
        self.encoding = encoding

    def run(self):
        try:
            import geopandas as gpd
            self.log.emit(f"Reading {os.path.basename(self.path)}...", "info")
            gdf = gpd.read_file(self.path, encoding=self.encoding)
            self.log.emit(f"Loaded {len(gdf)} features", "info")
            count = self.db.import_geodataframe(
                gdf, self.schema, self.table,
                "geom", self.srid,
                lambda m, l="info": self.log.emit(m, l),
                mode=self.mode,
            )
            self.done.emit(count)
        except Exception as e:
            self.log.emit(str(e), "error")
            self.done.emit(-1)


class ImportDialog(QDialog):
    def __init__(self, db: DBManager, schema: str, table: str, parent=None):
        super().__init__(parent)
        self.db = db
        self._worker: ImportWorker | None = None
        self.setWindowTitle(i18n.t("import_title"))
        self.setMinimumWidth(500)
        self._build_ui(schema, table)

    def _build_ui(self, schema, table):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        file_row = QHBoxLayout()
        self._file_edit = QLineEdit()
        file_row.addWidget(self._file_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(browse_btn)
        form.addRow(i18n.t("import_file"), file_row)

        self._schema_edit = QLineEdit(schema)
        form.addRow(i18n.t("import_target_schema"), self._schema_edit)

        self._table_edit = QLineEdit(table)
        form.addRow(i18n.t("import_target_table"), self._table_edit)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem(i18n.t("import_append"), "append")
        self._mode_combo.addItem(i18n.t("import_replace"), "replace")
        self._mode_combo.addItem(i18n.t("import_create_new"), "create")
        form.addRow("Mode:", self._mode_combo)

        self._srid_edit = QLineEdit("4326")
        form.addRow(i18n.t("import_srid"), self._srid_edit)

        self._enc_edit = QLineEdit("utf-8")
        form.addRow(i18n.t("import_encoding"), self._enc_edit)

        layout.addLayout(form)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(150)
        layout.addWidget(self._log)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        btns = QDialogButtonBox()
        self._import_btn = btns.addButton(
            i18n.t("import_start"), QDialogButtonBox.ButtonRole.AcceptRole)
        self._import_btn.clicked.connect(self._run_import)
        cancel_btn = btns.addButton(
            i18n.t("import_cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(btns)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("import_file"),
            filter="Vector files (*.shp *.geojson *.gpkg *.json *.kml *.gml);;All (*)")
        if path:
            self._file_edit.setText(path)
            name = os.path.splitext(os.path.basename(path))[0]
            if not self._table_edit.text():
                self._table_edit.setText(name.lower().replace(" ", "_"))

    def _run_import(self):
        path = self._file_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Error", "Select a file first.")
            return
        try:
            srid = int(self._srid_edit.text().strip())
        except ValueError:
            srid = 4326
        self._progress.setVisible(True)
        self._import_btn.setEnabled(False)
        self._worker = ImportWorker(
            self.db, path,
            self._schema_edit.text().strip(),
            self._table_edit.text().strip(),
            self._mode_combo.currentData(),
            srid,
            self._enc_edit.text().strip() or "utf-8",
        )
        self._worker.log.connect(
            lambda msg, lvl: self._log.append(f"[{lvl.upper()}] {msg}"))
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, count: int):
        self._progress.setVisible(False)
        self._import_btn.setEnabled(True)
        if count >= 0:
            self._log.append(f"✔ Import done: {count} features")
            if self.parent() and hasattr(self.parent(), "log"):
                self.parent().log(i18n.t("import_done", n=count), "success")
                self.parent().browser.refresh()
