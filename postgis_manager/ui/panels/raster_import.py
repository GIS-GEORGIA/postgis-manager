"""Raster Import panel — raster2pgsql wrapper (raster2postgis + pg_raster_upload pattern)."""

from __future__ import annotations
import subprocess
import shutil
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QLineEdit, QPushButton, QComboBox, QCheckBox, QSpinBox,
    QFileDialog, QMessageBox, QTextEdit, QFormLayout,
)
from PyQt5.QtCore import QThread, pyqtSignal

from ...db.connection import DBManager
from ...utils import i18n


class RasterImportWorker(QThread):
    log = pyqtSignal(str, str)
    done = pyqtSignal(bool)

    def __init__(self, cmd: str, psql_cmd: str):
        super().__init__()
        self.cmd = cmd
        self.psql_cmd = psql_cmd

    def run(self):
        try:
            self.log.emit(f"Running: {self.cmd}", "info")
            result = subprocess.run(
                self.cmd, shell=True, capture_output=True, text=True
            )
            if result.returncode != 0:
                self.log.emit(f"raster2pgsql error: {result.stderr}", "error")
                self.done.emit(False)
                return
            sql = result.stdout
            self.log.emit(f"SQL generated ({len(sql)} chars). Loading into DB...", "info")
            psql = subprocess.run(
                self.psql_cmd, input=sql, shell=True,
                capture_output=True, text=True
            )
            if psql.returncode != 0:
                self.log.emit(f"psql error: {psql.stderr}", "error")
                self.done.emit(False)
            else:
                self.log.emit("Raster import complete!", "success")
                self.done.emit(True)
        except Exception as e:
            self.log.emit(str(e), "error")
            self.done.emit(False)


class RasterImportPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._worker: RasterImportWorker | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel(f"🖼  {i18n.t('raster_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        # Form
        form = QFormLayout()

        self._file_edit = QLineEdit()
        file_row = QHBoxLayout()
        file_row.addWidget(self._file_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(browse_btn)
        form.addRow(i18n.t("raster_file"), file_row)

        self._schema_edit = QLineEdit("public")
        form.addRow(i18n.t("raster_schema"), self._schema_edit)

        self._table_edit = QLineEdit()
        form.addRow(i18n.t("raster_table"), self._table_edit)

        self._srid_edit = QLineEdit("4326")
        form.addRow(i18n.t("raster_srid"), self._srid_edit)

        self._tile_combo = QComboBox()
        for t in ["128x128", "256x256", "512x512", "auto"]:
            self._tile_combo.addItem(t)
        self._tile_combo.setCurrentIndex(1)
        form.addRow(i18n.t("raster_tile_size"), self._tile_combo)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem(i18n.t("raster_mode_insert"), "-")
        self._mode_combo.addItem(i18n.t("raster_mode_append"), "-a")
        self._mode_combo.addItem(i18n.t("raster_mode_delete"), "-d")
        form.addRow(i18n.t("raster_mode"), self._mode_combo)

        self._overview_check = QCheckBox(i18n.t("raster_overview"))
        form.addRow("", self._overview_check)

        self._constraints_check = QCheckBox(i18n.t("raster_constraints"))
        self._constraints_check.setChecked(True)
        form.addRow("", self._constraints_check)

        self._index_check = QCheckBox(i18n.t("raster_band_index"))
        self._index_check.setChecked(True)
        form.addRow("", self._index_check)

        layout.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        self._import_btn = QPushButton(f"🚀  {i18n.t('raster_import')}")
        self._import_btn.clicked.connect(self._run_import)
        btn_row.addWidget(self._import_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(200)
        layout.addWidget(self._log)

        layout.addStretch()

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Raster",
            filter="Raster files (*.tif *.tiff *.img *.ecw *.jp2 *.png *.jpg);;All files (*)"
        )
        if path:
            self._file_edit.setText(path)
            name = os.path.splitext(os.path.basename(path))[0]
            if not self._table_edit.text():
                self._table_edit.setText(name.lower().replace(" ", "_"))

    def _run_import(self):
        if not shutil.which("raster2pgsql"):
            QMessageBox.critical(self, "Error",
                "raster2pgsql not found in PATH.\n"
                "Install PostGIS client tools and ensure raster2pgsql is accessible.")
            return
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return

        rfile = self._file_edit.text().strip()
        schema = self._schema_edit.text().strip()
        table = self._table_edit.text().strip()
        srid = self._srid_edit.text().strip()
        tile = self._tile_combo.currentText()
        mode = self._mode_combo.currentData()

        if not rfile or not table:
            QMessageBox.warning(self, "Error", "File and table name are required.")
            return

        flags = [mode]
        flags.append(f"-s {srid}")
        if tile != "auto":
            flags.append(f"-t {tile}")
        if self._overview_check.isChecked():
            flags.append("-l 2,4,8")
        if self._constraints_check.isChecked():
            flags.append("-C")
        if self._index_check.isChecked():
            flags.append("-I")

        flags_str = " ".join(f for f in flags if f)
        r2p_cmd = f'raster2pgsql {flags_str} "{rfile}" {schema}.{table}'

        p = self.db.params
        psql_cmd = (f'psql -h {p["host"]} -p {p["port"]} '
                    f'-U {p["user"]} -d {p["dbname"]}')

        self._log.clear()
        self._import_btn.setEnabled(False)
        self._worker = RasterImportWorker(r2p_cmd, psql_cmd)
        self._worker.log.connect(
            lambda msg, lvl: self._log.append(f"[{lvl.upper()}] {msg}"))
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, success: bool):
        self._import_btn.setEnabled(True)
        if success and self.parent() and hasattr(self.parent(), "log"):
            self.parent().log("Raster import complete", "success")
            self.parent().browser.refresh()
