"""Chainage panel — PyQt6."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QDoubleSpinBox, QMessageBox,
)
from ...db.connection import DBManager
from ...utils import i18n


class ChainagePanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._schema = self._table = self._geom_col = ""
        self._srid = 4326
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel(f"📏  {i18n.t('chainage_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)
        form = QFormLayout()
        self._layer_lbl = QLabel("(select a line layer in browser)")
        form.addRow(i18n.t("chainage_line_layer"), self._layer_lbl)
        self._id_col = QLineEdit("id")
        form.addRow("Line ID Column:", self._id_col)
        self._interval = QDoubleSpinBox()
        self._interval.setRange(0.1, 999999); self._interval.setValue(100)
        self._interval.setSuffix(" m")
        form.addRow(i18n.t("chainage_interval"), self._interval)
        self._start_off = QDoubleSpinBox()
        self._start_off.setRange(0, 999999)
        form.addRow(i18n.t("chainage_start_offset"), self._start_off)
        self._out_schema = QLineEdit("public")
        form.addRow("Output Schema:", self._out_schema)
        self._out_table = QLineEdit()
        self._out_table.setPlaceholderText("chainage_points")
        form.addRow(i18n.t("chainage_point_result"), self._out_table)
        layout.addLayout(form)
        run_btn = QPushButton(f"⚡  {i18n.t('chainage_run')}")
        run_btn.clicked.connect(self._run)
        layout.addWidget(run_btn)
        self._result = QLabel("")
        layout.addWidget(self._result)
        layout.addStretch()

    def set_active_layer(self, schema, table, geom_col, srid):
        self._schema = schema; self._table = table
        self._geom_col = geom_col; self._srid = srid
        self._layer_lbl.setText(f"{schema}.{table}")
        if not self._out_table.text():
            self._out_table.setText(f"{table}_chainage")

    def _run(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected")); return
        if not self._schema:
            QMessageBox.warning(self, "Error", "Select a line layer first."); return
        out = self._out_table.text().strip() or f"{self._table}_chainage"
        try:
            n = self.db.generate_chainage_points(
                self._schema, self._table, self._geom_col,
                self._id_col.text().strip(), self._interval.value(),
                self._start_off.value(),
                self._out_schema.text().strip(), out, self._srid)
            self._result.setText(f"✔  {n:,} points → {out}")
            if self.parent() and hasattr(self.parent(), "log"):
                self.parent().log(f"Chainage: {n} points in {out}", "success")
                self.parent().browser.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
