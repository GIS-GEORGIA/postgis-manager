"""pgRouting panel — shortest path, driving distance, isochrones (pgRoutingLayer pattern)."""

from __future__ import annotations
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QTabWidget, QMessageBox, QTextEdit, QHeaderView,
)
from PyQt5.QtCore import QThread, pyqtSignal

from ...db.connection import DBManager
from ...utils import i18n


class RoutingWorker(QThread):
    done = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, db, mode, **kwargs):
        super().__init__()
        self.db = db
        self.mode = mode
        self.kwargs = kwargs

    def run(self):
        try:
            if self.mode == "dijkstra":
                result = self.db.pgrouting_dijkstra(**self.kwargs)
            else:
                result = self.db.pgrouting_driving_distance(**self.kwargs)
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class pgRoutingPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._active_schema = ""
        self._active_table = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel(f"🛣  {i18n.t('routing_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Shortest Path (Dijkstra) ──
        sp_widget = QWidget()
        sp_layout = QVBoxLayout(sp_widget)
        form = QFormLayout()

        self._edge_table = QLineEdit()
        form.addRow(i18n.t("routing_edge_table"), self._edge_table)
        self._source_col = QLineEdit("source")
        form.addRow(i18n.t("routing_source_col"), self._source_col)
        self._target_col = QLineEdit("target")
        form.addRow(i18n.t("routing_target_col"), self._target_col)
        self._cost_col = QLineEdit("cost")
        form.addRow(i18n.t("routing_cost_col"), self._cost_col)
        self._rev_cost = QLineEdit()
        self._rev_cost.setPlaceholderText("(optional)")
        form.addRow(i18n.t("routing_reverse_cost"), self._rev_cost)
        self._from_node = QSpinBox()
        self._from_node.setMaximum(9999999)
        form.addRow(i18n.t("routing_from_node"), self._from_node)
        self._to_node = QSpinBox()
        self._to_node.setMaximum(9999999)
        form.addRow(i18n.t("routing_to_node"), self._to_node)

        self._algo_combo = QComboBox()
        self._algo_combo.addItems(["Dijkstra", "A*", "Bidirectional Dijkstra"])
        form.addRow(i18n.t("routing_algorithm"), self._algo_combo)

        sp_layout.addLayout(form)

        run_btn = QPushButton(f"🚀  {i18n.t('routing_run')}")
        run_btn.clicked.connect(self._run_dijkstra)
        sp_layout.addWidget(run_btn)

        self._sp_result_label = QLabel("")
        sp_layout.addWidget(self._sp_result_label)

        self._sp_table = QTableWidget()
        self._sp_table.setAlternatingRowColors(True)
        self._sp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        sp_layout.addWidget(self._sp_table)

        sp_load_btn = QPushButton(i18n.t("routing_load_result"))
        sp_load_btn.setProperty("class", "secondary")
        sp_load_btn.clicked.connect(self._load_result_in_qgis)
        sp_layout.addWidget(sp_load_btn)

        tabs.addTab(sp_widget, "Shortest Path")

        # ── Driving Distance / Isochrone ──
        dd_widget = QWidget()
        dd_layout = QVBoxLayout(dd_widget)
        dd_form = QFormLayout()

        self._dd_edge_table = QLineEdit()
        dd_form.addRow(i18n.t("routing_edge_table"), self._dd_edge_table)
        self._dd_source_col = QLineEdit("source")
        dd_form.addRow(i18n.t("routing_source_col"), self._dd_source_col)
        self._dd_target_col = QLineEdit("target")
        dd_form.addRow(i18n.t("routing_target_col"), self._dd_target_col)
        self._dd_cost_col = QLineEdit("cost")
        dd_form.addRow(i18n.t("routing_cost_col"), self._dd_cost_col)
        self._dd_from_node = QSpinBox()
        self._dd_from_node.setMaximum(9999999)
        dd_form.addRow(i18n.t("routing_from_node"), self._dd_from_node)
        self._dd_max_cost = QDoubleSpinBox()
        self._dd_max_cost.setMaximum(9999999)
        self._dd_max_cost.setValue(1000)
        dd_form.addRow(i18n.t("routing_max_cost"), self._dd_max_cost)

        dd_layout.addLayout(dd_form)
        dd_run_btn = QPushButton(f"🚀  Run Driving Distance")
        dd_run_btn.clicked.connect(self._run_driving_distance)
        dd_layout.addWidget(dd_run_btn)

        self._dd_result_label = QLabel("")
        dd_layout.addWidget(self._dd_result_label)

        self._dd_table = QTableWidget()
        self._dd_table.setAlternatingRowColors(True)
        dd_layout.addWidget(self._dd_table)

        tabs.addTab(dd_widget, "Driving Distance / Isochrone")

    def set_active_layer(self, schema: str, table: str):
        self._active_schema = schema
        self._active_table = table
        full = f'"{schema}"."{table}"'
        self._edge_table.setText(full)
        self._dd_edge_table.setText(full)

    def _run_dijkstra(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return
        worker = RoutingWorker(
            self.db, "dijkstra",
            edge_table=self._edge_table.text().strip(),
            source_col=self._source_col.text().strip(),
            target_col=self._target_col.text().strip(),
            cost_col=self._cost_col.text().strip(),
            from_node=self._from_node.value(),
            to_node=self._to_node.value(),
            directed=True,
            reverse_cost_col=self._rev_cost.text().strip() or None,
        )
        worker.done.connect(self._show_dijkstra_result)
        worker.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        worker.start()

    def _show_dijkstra_result(self, rows: list):
        if not rows:
            self._sp_result_label.setText("No path found.")
            return
        cols = list(rows[0].keys())
        self._sp_table.setRowCount(len(rows))
        self._sp_table.setColumnCount(len(cols))
        self._sp_table.setHorizontalHeaderLabels(cols)
        for r_idx, row in enumerate(rows):
            for c_idx, key in enumerate(cols):
                val = row.get(key)
                self._sp_table.setItem(r_idx, c_idx, QTableWidgetItem(str(val)))
        total_cost = rows[-1].get("agg_cost", "?")
        self._sp_result_label.setText(
            f"Path found: {len(rows)} edges  |  Total cost: {total_cost}")

    def _run_driving_distance(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return
        worker = RoutingWorker(
            self.db, "driving_distance",
            edge_table=self._dd_edge_table.text().strip(),
            source_col=self._dd_source_col.text().strip(),
            target_col=self._dd_target_col.text().strip(),
            cost_col=self._dd_cost_col.text().strip(),
            from_node=self._dd_from_node.value(),
            max_cost=self._dd_max_cost.value(),
        )
        worker.done.connect(self._show_dd_result)
        worker.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        worker.start()

    def _show_dd_result(self, rows: list):
        if not rows:
            self._dd_result_label.setText("No reachable nodes.")
            return
        cols = list(rows[0].keys())
        self._dd_table.setRowCount(len(rows))
        self._dd_table.setColumnCount(len(cols))
        self._dd_table.setHorizontalHeaderLabels(cols)
        for r_idx, row in enumerate(rows):
            for c_idx, key in enumerate(cols):
                self._dd_table.setItem(r_idx, c_idx, QTableWidgetItem(str(row.get(key))))
        self._dd_result_label.setText(f"Reachable nodes: {len(rows)}")

    def _load_result_in_qgis(self):
        parent = self.parent()
        if parent:
            parent.log("pgRouting result: load to QGIS not yet implemented "
                       "(build WKT result layer first)", "warn")
