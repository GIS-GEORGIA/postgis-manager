"""Geoprocessing panel — PyQt6."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QDoubleSpinBox, QMessageBox,
)
from PyQt6.QtCore import QThread, pyqtSignal
from ...db.connection import DBManager
from ...utils import i18n


class GeoWorker(QThread):
    done  = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, geom_col, operation, out_schema, out_table, **kw):
        super().__init__()
        self.db = db; self.schema = schema; self.table = table
        self.geom_col = geom_col; self.operation = operation
        self.out_schema = out_schema; self.out_table = out_table; self.kw = kw

    def run(self):
        try:
            result = self.db.geoprocess(
                self.schema, self.table, self.geom_col,
                self.operation, self.out_schema, self.out_table, **self.kw)
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class GeoprocessingPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._schema = self._table = self._geom_col = ""
        self._srid = 4326
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel(f"⚙  {i18n.t('geo_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()
        self._layer_lbl = QLabel("(select a layer in browser)")
        form.addRow(i18n.t("geo_layer"), self._layer_lbl)

        self._op_combo = QComboBox()
        self._op_combo.addItem(i18n.t("geo_op_buffer"), "buffer")
        self._op_combo.addItem(i18n.t("geo_op_simplify"), "simplify")
        self._op_combo.addItem(i18n.t("geo_op_convex_hull"), "convex_hull")
        self._op_combo.addItem(i18n.t("geo_op_centroid"), "centroid")
        self._op_combo.currentIndexChanged.connect(self._toggle_params)
        form.addRow(i18n.t("geo_operation"), self._op_combo)

        self._distance = QDoubleSpinBox()
        self._distance.setRange(0.0001, 999999); self._distance.setValue(100)
        self._dist_lbl = QLabel(i18n.t("geo_distance"))
        form.addRow(self._dist_lbl, self._distance)

        self._tolerance = QDoubleSpinBox()
        self._tolerance.setRange(0.0001, 999999); self._tolerance.setValue(1)
        self._tol_lbl = QLabel(i18n.t("geo_tolerance"))
        form.addRow(self._tol_lbl, self._tolerance)
        self._tolerance.setVisible(False); self._tol_lbl.setVisible(False)

        self._out_schema = QLineEdit("public")
        form.addRow(i18n.t("geo_output_schema"), self._out_schema)
        self._out_table = QLineEdit()
        self._out_table.setPlaceholderText("output_table")
        form.addRow(i18n.t("geo_output_table"), self._out_table)
        layout.addLayout(form)

        run_btn = QPushButton(f"⚡  {i18n.t('geo_run')}")
        run_btn.clicked.connect(self._run)
        layout.addWidget(run_btn)
        self._result = QLabel("")
        layout.addWidget(self._result)
        layout.addStretch()

    def _toggle_params(self):
        op = self._op_combo.currentData()
        is_buf = op == "buffer"
        is_simp = op == "simplify"
        self._distance.setVisible(is_buf)
        self._dist_lbl.setVisible(is_buf)
        self._tolerance.setVisible(is_simp)
        self._tol_lbl.setVisible(is_simp)

    def set_active_layer(self, schema, table, geom_col, srid):
        self._schema = schema; self._table = table
        self._geom_col = geom_col; self._srid = srid
        self._layer_lbl.setText(f"{schema}.{table}")
        if not self._out_table.text():
            self._out_table.setText(f"{table}_geo")

    def _run(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected")); return
        if not self._schema:
            QMessageBox.warning(self, "Error", "Select a layer first."); return
        op = self._op_combo.currentData()
        out = self._out_table.text().strip() or f"{self._table}_geo"
        kw = {}
        if op == "buffer": kw["distance"] = self._distance.value()
        if op == "simplify": kw["tolerance"] = self._tolerance.value()

        w = GeoWorker(self.db, self._schema, self._table, self._geom_col,
                      op, self._out_schema.text().strip(), out, **kw)
        w.done.connect(self._on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        w.start()

    def _on_done(self, result: str):
        self._result.setText(f"✔  {result}")
        if self.parent() and hasattr(self.parent(), "log"):
            self.parent().log(result, "success")
            if hasattr(self.parent(), "browser"):
                self.parent().browser.refresh()
