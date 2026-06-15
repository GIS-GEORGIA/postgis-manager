"""Raster Import panel — raster2pgsql wrapper."""

from __future__ import annotations
import subprocess, shutil, os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QCheckBox,
    QFileDialog, QMessageBox, QTextEdit,
)
from PyQt6.QtCore import QThread, pyqtSignal
from ...db.connection import DBManager
from ...utils import i18n


class RasterWorker(QThread):
    log  = pyqtSignal(str, str)
    done = pyqtSignal(bool)

    def __init__(self, r2p_cmd, psql_cmd):
        super().__init__()
        self.r2p_cmd = r2p_cmd
        self.psql_cmd = psql_cmd

    def run(self):
        try:
            self.log.emit(f"Running: {self.r2p_cmd}", "info")
            res = subprocess.run(self.r2p_cmd, shell=True,
                                 capture_output=True, text=True)
            if res.returncode != 0:
                self.log.emit(f"Error: {res.stderr}", "error")
                self.done.emit(False); return
            psql = subprocess.run(self.psql_cmd, input=res.stdout,
                                  shell=True, capture_output=True, text=True)
            if psql.returncode != 0:
                self.log.emit(f"psql error: {psql.stderr}", "error")
                self.done.emit(False)
            else:
                self.log.emit("Raster import complete!", "success")
                self.done.emit(True)
        except Exception as e:
            self.log.emit(str(e), "error"); self.done.emit(False)


class RasterImportPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db; self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel(f"🖼  {i18n.t('raster_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()
        file_row = QHBoxLayout()
        self._file = QLineEdit()
        file_row.addWidget(self._file)
        b = QPushButton("Browse...")
        b.setFixedWidth(80)
        b.clicked.connect(self._browse)
        file_row.addWidget(b)
        form.addRow(i18n.t("raster_file"), file_row)

        self._schema = QLineEdit("public")
        form.addRow(i18n.t("raster_schema"), self._schema)
        self._table = QLineEdit()
        form.addRow(i18n.t("raster_table"), self._table)
        self._srid = QLineEdit("4326")
        form.addRow(i18n.t("raster_srid"), self._srid)

        self._tile = QComboBox()
        for t in ["128x128", "256x256", "512x512", "auto"]:
            self._tile.addItem(t)
        self._tile.setCurrentIndex(1)
        form.addRow(i18n.t("raster_tile_size"), self._tile)

        self._mode = QComboBox()
        self._mode.addItem(i18n.t("raster_mode_insert"), "-")
        self._mode.addItem(i18n.t("raster_mode_append"), "-a")
        self._mode.addItem(i18n.t("raster_mode_delete"), "-d")
        form.addRow(i18n.t("raster_mode"), self._mode)

        self._overview = QCheckBox(i18n.t("raster_overview"))
        form.addRow("", self._overview)
        self._constraints = QCheckBox(i18n.t("raster_constraints"))
        self._constraints.setChecked(True)
        form.addRow("", self._constraints)
        self._index = QCheckBox(i18n.t("raster_band_index"))
        self._index.setChecked(True)
        form.addRow("", self._index)
        layout.addLayout(form)

        self._import_btn = QPushButton(f"🚀  {i18n.t('raster_import')}")
        self._import_btn.clicked.connect(self._run)
        layout.addWidget(self._import_btn)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(200)
        layout.addWidget(self._log)
        layout.addStretch()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Raster",
            filter="Raster (*.tif *.tiff *.img *.ecw *.jp2 *.png *.jpg);;All (*)")
        if path:
            self._file.setText(path)
            name = os.path.splitext(os.path.basename(path))[0]
            if not self._table.text():
                self._table.setText(name.lower().replace(" ", "_"))

    def _run(self):
        if not shutil.which("raster2pgsql"):
            QMessageBox.critical(self, "Error",
                "raster2pgsql not found in PATH.\n"
                "Install PostGIS client tools.")
            return
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return
        rfile = self._file.text().strip()
        table = self._table.text().strip()
        if not rfile or not table:
            QMessageBox.warning(self, "Error", "File and table are required.")
            return

        flags = [self._mode.currentData(), f"-s {self._srid.text().strip()}"]
        if self._tile.currentText() != "auto":
            flags.append(f"-t {self._tile.currentText()}")
        if self._overview.isChecked(): flags.append("-l 2,4,8")
        if self._constraints.isChecked(): flags.append("-C")
        if self._index.isChecked(): flags.append("-I")

        r2p = f'raster2pgsql {" ".join(f for f in flags if f)} "{rfile}" {self._schema.text()}.{table}'
        p = self.db.params
        psql = (f'psql -h {p["host"]} -p {p["port"]} '
                f'-U {p["user"]} -d {p["dbname"]}')

        self._log.clear()
        self._import_btn.setEnabled(False)
        worker = RasterWorker(r2p, psql)
        worker.log.connect(lambda m, l: self._log.append(f"[{l.upper()}] {m}"))
        worker.done.connect(self._on_done)
        worker.start()

    def _on_done(self, ok: bool):
        self._import_btn.setEnabled(True)
        if ok and self.parent() and hasattr(self.parent(), "log"):
            self.parent().log("Raster import complete", "success")
            self.parent().browser.refresh()
