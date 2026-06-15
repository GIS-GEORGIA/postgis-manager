"""pgRouting panel."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QMessageBox, QHeaderView,
)
from PyQt6.QtCore import QThread, pyqtSignal
from ...db.connection import DBManager
from ...utils import i18n


class RoutingWorker(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, db, mode, **kw):
        super().__init__()
        self.db = db; self.mode = mode; self.kw = kw

    def run(self):
        try:
            if self.mode == "dijkstra":
                r = self.db.pgrouting_dijkstra(**self.kw)
            else:
                r = self.db.pgrouting_driving_distance(**self.kw)
            self.done.emit(r)
        except Exception as e:
            self.error.emit(str(e))


class pgRoutingPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db; self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel(f"🛣  {i18n.t('routing_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ── Dijkstra ──
        sp = QWidget()
        sp_l = QVBoxLayout(sp)
        form = QFormLayout()
        self._edge  = QLineEdit(); form.addRow(i18n.t("routing_edge_table"), self._edge)
        self._src   = QLineEdit("source"); form.addRow(i18n.t("routing_source_col"), self._src)
        self._tgt   = QLineEdit("target"); form.addRow(i18n.t("routing_target_col"), self._tgt)
        self._cost  = QLineEdit("cost");   form.addRow(i18n.t("routing_cost_col"), self._cost)
        self._revcost = QLineEdit(); self._revcost.setPlaceholderText("(optional)")
        form.addRow(i18n.t("routing_reverse_cost"), self._revcost)
        self._from  = QSpinBox(); self._from.setMaximum(9999999)
        form.addRow(i18n.t("routing_from_node"), self._from)
        self._to    = QSpinBox(); self._to.setMaximum(9999999)
        form.addRow(i18n.t("routing_to_node"), self._to)
        sp_l.addLayout(form)
        run_btn = QPushButton(f"🚀  {i18n.t('routing_run')}")
        run_btn.clicked.connect(self._run_dijkstra)
        sp_l.addWidget(run_btn)
        self._sp_label = QLabel("")
        sp_l.addWidget(self._sp_label)
        self._sp_table = QTableWidget()
        self._sp_table.setAlternatingRowColors(True)
        self._sp_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        sp_l.addWidget(self._sp_table)
        tabs.addTab(sp, "Shortest Path")

        # ── Driving Distance ──
        dd = QWidget()
        dd_l = QVBoxLayout(dd)
        form2 = QFormLayout()
        self._dd_edge = QLineEdit(); form2.addRow(i18n.t("routing_edge_table"), self._dd_edge)
        self._dd_src  = QLineEdit("source"); form2.addRow(i18n.t("routing_source_col"), self._dd_src)
        self._dd_tgt  = QLineEdit("target"); form2.addRow(i18n.t("routing_target_col"), self._dd_tgt)
        self._dd_cost = QLineEdit("cost");   form2.addRow(i18n.t("routing_cost_col"), self._dd_cost)
        self._dd_from = QSpinBox(); self._dd_from.setMaximum(9999999)
        form2.addRow(i18n.t("routing_from_node"), self._dd_from)
        self._dd_max  = QDoubleSpinBox()
        self._dd_max.setMaximum(9999999); self._dd_max.setValue(1000)
        form2.addRow(i18n.t("routing_max_cost"), self._dd_max)
        dd_l.addLayout(form2)
        dd_btn = QPushButton("🚀  Run Driving Distance")
        dd_btn.clicked.connect(self._run_dd)
        dd_l.addWidget(dd_btn)
        self._dd_label = QLabel("")
        dd_l.addWidget(self._dd_label)
        self._dd_table = QTableWidget()
        self._dd_table.setAlternatingRowColors(True)
        dd_l.addWidget(self._dd_table)
        tabs.addTab(dd, "Driving Distance")

    def set_active_layer(self, schema: str, table: str):
        full = f'"{schema}"."{table}"'
        self._edge.setText(full)
        self._dd_edge.setText(full)

    def _run_dijkstra(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected")); return
        w = RoutingWorker(self.db, "dijkstra",
            edge_table=self._edge.text().strip(),
            source_col=self._src.text().strip(),
            target_col=self._tgt.text().strip(),
            cost_col=self._cost.text().strip(),
            from_node=self._from.value(),
            to_node=self._to.value(),
            directed=True,
            reverse_cost_col=self._revcost.text().strip() or None)
        w.done.connect(self._show_sp)
        w.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        w.start()

    def _show_sp(self, rows):
        if not rows:
            self._sp_label.setText("No path found."); return
        cols = list(rows[0].keys())
        self._sp_table.setRowCount(len(rows))
        self._sp_table.setColumnCount(len(cols))
        self._sp_table.setHorizontalHeaderLabels(cols)
        for r, row in enumerate(rows):
            for c, k in enumerate(cols):
                self._sp_table.setItem(r, c, QTableWidgetItem(str(row.get(k))))
        self._sp_label.setText(
            f"Path: {len(rows)} edges  |  Cost: {rows[-1].get('agg_cost','?')}")

    def _run_dd(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected")); return
        w = RoutingWorker(self.db, "driving_distance",
            edge_table=self._dd_edge.text().strip(),
            source_col=self._dd_src.text().strip(),
            target_col=self._dd_tgt.text().strip(),
            cost_col=self._dd_cost.text().strip(),
            from_node=self._dd_from.value(),
            max_cost=self._dd_max.value())
        w.done.connect(self._show_dd)
        w.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        w.start()

    def _show_dd(self, rows):
        if not rows:
            self._dd_label.setText("No reachable nodes."); return
        cols = list(rows[0].keys())
        self._dd_table.setRowCount(len(rows))
        self._dd_table.setColumnCount(len(cols))
        self._dd_table.setHorizontalHeaderLabels(cols)
        for r, row in enumerate(rows):
            for c, k in enumerate(cols):
                self._dd_table.setItem(r, c, QTableWidgetItem(str(row.get(k))))
        self._dd_label.setText(f"Reachable nodes: {len(rows)}")
