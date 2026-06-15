"""Geoprocessing panel — PostGIS server-side operations (NaturalGIS pattern)."""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QDoubleSpinBox, QSpinBox,
    QGroupBox, QMessageBox, QTabWidget,
)
from PyQt5.QtCore import QThread, pyqtSignal

from ...db.connection import DBManager
from ...utils import i18n


class GeoWorker(QThread):
    done = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, geom_col, function, params, out_schema, out_table, srid):
        super().__init__()
        self.db = db
        self.schema = schema
        self.table = table
        self.geom_col = geom_col
        self.function = function
        self.params = params
        self.out_schema = out_schema
        self.out_table = out_table
        self.srid = srid

    def run(self):
        try:
            count = self.db.geoprocess(
                self.schema, self.table, self.geom_col,
                self.function, self.params,
                self.out_schema, self.out_table, self.srid
            )
            self.done.emit(count)
        except Exception as e:
            self.error.emit(str(e))


FUNCTIONS = [
    ("Buffer", "buffer"),
    ("Simplify", "simplify"),
    ("Convex Hull", "convex_hull"),
    ("Centroid", "centroid"),
    ("Dissolve (Union)", "union"),
]


class GeoprocessingPanel(QWidget):
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

        title = QLabel(f"⚙  {i18n.t('geo_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        self._layer_label = QLabel("(select a layer in browser)")
        layout.addWidget(self._layer_label)

        form = QFormLayout()

        self._func_combo = QComboBox()
        for label, _ in FUNCTIONS:
            self._func_combo.addItem(label)
        self._func_combo.currentIndexChanged.connect(self._update_params)
        form.addRow(i18n.t("geo_function"), self._func_combo)

        # Dynamic param
        self._param_label = QLabel(i18n.t("geo_buffer_dist"))
        self._param_spin = QDoubleSpinBox()
        self._param_spin.setRange(0.001, 9999999)
        self._param_spin.setValue(100)
        form.addRow(self._param_label, self._param_spin)

        self._out_schema = QLineEdit("public")
        form.addRow("Output Schema:", self._out_schema)

        self._out_table = QLineEdit()
        self._out_table.setPlaceholderText("result_layer")
        form.addRow(i18n.t("geo_output_table"), self._out_table)

        self._out_srid = QLineEdit()
        self._out_srid.setPlaceholderText("(same as input)")
        form.addRow(i18n.t("geo_result_srid"), self._out_srid)

        layout.addLayout(form)

        run_btn = QPushButton(f"▶  {i18n.t('geo_run')}")
        run_btn.clicked.connect(self._run)
        layout.addWidget(run_btn)

        self._result_label = QLabel("")
        layout.addWidget(self._result_label)

        # ── Validation group ──
        val_group = QGroupBox("Geometry Validation (PostGISQueries)")
        val_layout = QVBoxLayout(val_group)

        self._val_label = QLabel("Validate the active layer's geometry:")
        val_layout.addWidget(self._val_label)

        val_btn = QPushButton("🔍 Run Validation")
        val_btn.clicked.connect(self._run_validation)
        val_layout.addWidget(val_btn)

        self._val_result = QLabel("")
        self._val_result.setWordWrap(True)
        val_layout.addWidget(self._val_result)

        layout.addWidget(val_group)
        layout.addStretch()

    def _update_params(self, idx: int):
        _, fn = FUNCTIONS[idx]
        if fn == "buffer":
            self._param_label.setText(i18n.t("geo_buffer_dist"))
            self._param_spin.setVisible(True)
        elif fn == "simplify":
            self._param_label.setText(i18n.t("geo_simplify_tol"))
            self._param_spin.setVisible(True)
        else:
            self._param_spin.setVisible(False)

    def set_active_layer(self, schema: str, table: str, geom_col: str, srid: int):
        self._schema = schema
        self._table = table
        self._geom_col = geom_col
        self._srid = srid
        self._layer_label.setText(f"Input: {schema}.{table}  [EPSG:{srid}]")
        if not self._out_table.text():
            _, fn = FUNCTIONS[self._func_combo.currentIndex()]
            self._out_table.setText(f"{table}_{fn}")

    def _run(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return
        if not self._schema:
            QMessageBox.warning(self, "Error", "Select a layer first.")
            return
        _, fn = FUNCTIONS[self._func_combo.currentIndex()]
        out_table = self._out_table.text().strip() or f"{self._table}_{fn}"
        srid_str = self._out_srid.text().strip()
        srid = int(srid_str) if srid_str.isdigit() else self._srid
        params = {"distance": self._param_spin.value(),
                  "tolerance": self._param_spin.value()}
        worker = GeoWorker(
            self.db, self._schema, self._table, self._geom_col,
            fn, params,
            self._out_schema.text().strip(), out_table, srid
        )
        worker.done.connect(
            lambda n: (self._result_label.setText(f"✔ Done: {n} features → {out_table}"),
                       self.parent().browser.refresh() if self.parent() and hasattr(self.parent(), "browser") else None))
        worker.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        worker.start()

    def _run_validation(self):
        if not self.db.is_connected() or not self._schema:
            return
        try:
            results = self.db.run_validation(
                self._schema, self._table, self._geom_col, self._srid)
            lines = []
            for check, rows in results.items():
                for row in rows:
                    if "error" in row:
                        lines.append(f"⚠ {check}: {row['error']}")
                    else:
                        vals = "  |  ".join(f"{k}: {v}" for k, v in row.items())
                        lines.append(f"• {check}: {vals}")
            self._val_result.setText("\n".join(lines) or "✔ All checks passed!")
        except Exception as e:
            self._val_result.setText(f"Error: {e}")
