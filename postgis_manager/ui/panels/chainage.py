"""Linear referencing / Chainage panel (pgChainage pattern)."""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QDoubleSpinBox, QMessageBox, QSpinBox,
)
from ...db.connection import DBManager
from ...utils import i18n


class ChainagePanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._schema = ""
        self._table = ""
        self._geom_col = "geom"
        self._srid = 4326
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel(f"📏  {i18n.t('chainage_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()

        self._line_layer_label = QLabel("(select a line layer in browser)")
        form.addRow(i18n.t("chainage_line_layer"), self._line_layer_label)

        self._id_col = QLineEdit("id")
        form.addRow("Line ID Column:", self._id_col)

        self._interval = QDoubleSpinBox()
        self._interval.setRange(0.1, 999999)
        self._interval.setValue(100)
        self._interval.setSuffix(" m")
        form.addRow(i18n.t("chainage_interval"), self._interval)

        self._start_offset = QDoubleSpinBox()
        self._start_offset.setRange(0, 999999)
        self._start_offset.setValue(0)
        form.addRow(i18n.t("chainage_start_offset"), self._start_offset)

        self._out_schema = QLineEdit("public")
        form.addRow("Output Schema:", self._out_schema)

        self._out_table = QLineEdit()
        self._out_table.setPlaceholderText("chainage_points")
        form.addRow(i18n.t("chainage_point_result"), self._out_table)

        layout.addLayout(form)

        run_btn = QPushButton(f"⚡  {i18n.t('chainage_run')}")
        run_btn.clicked.connect(self._run)
        layout.addWidget(run_btn)

        self._result_label = QLabel("")
        layout.addWidget(self._result_label)
        layout.addStretch()

    def set_active_layer(self, schema: str, table: str, geom_col: str, srid: int):
        self._schema = schema
        self._table = table
        self._geom_col = geom_col
        self._srid = srid
        self._line_layer_label.setText(f"{schema}.{table}")
        if not self._out_table.text():
            self._out_table.setText(f"{table}_chainage")

    def _run(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return
        if not self._schema:
            QMessageBox.warning(self, "Error", "Select a line layer in the browser first.")
            return
        out_table = self._out_table.text().strip() or f"{self._table}_chainage"
        try:
            count = self.db.generate_chainage_points(
                schema=self._schema,
                line_table=self._table,
                geom_col=self._geom_col,
                id_col=self._id_col.text().strip(),
                interval=self._interval.value(),
                start_offset=self._start_offset.value(),
                out_schema=self._out_schema.text().strip(),
                out_table=out_table,
                srid=self._srid,
            )
            self._result_label.setText(f"✔  Generated {count:,} chainage points → {out_table}")
            if self.parent() and hasattr(self.parent(), "log"):
                self.parent().log(f"Chainage: {count} points in {out_table}", "success")
                self.parent().browser.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
