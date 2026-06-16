"""EXPLAIN ANALYZE visual dialog — parses JSON plan, shows tree + stats."""

from __future__ import annotations
import json
import psycopg2
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QTextEdit, QSplitter, QWidget,
    QHeaderView, QProgressBar, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QBrush

from ...db.connection import DBManager


# ── Cost colour scale ─────────────────────────────────────────────────────

def _cost_color(pct: float) -> QColor:
    """Return colour from green→yellow→red based on fraction 0..1."""
    if pct < 0.3:
        return QColor("#388E3C")
    elif pct < 0.6:
        return QColor("#F57C00")
    else:
        return QColor("#C62828")


# ── Worker ────────────────────────────────────────────────────────────────

class ExplainWorker(QThread):
    done  = pyqtSignal(list)   # list of JSON plan dicts
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, sql: str):
        super().__init__()
        self.db = db
        self.sql = sql

    def run(self):
        try:
            conn = psycopg2.connect(**self.db.params)
            cur = conn.cursor()
            wrapped = (
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON, VERBOSE) {self.sql}"
            )
            cur.execute(wrapped)
            plan = cur.fetchone()[0]   # list with one element
            cur.close()
            conn.rollback()            # don't commit any side effects
            conn.close()
            self.done.emit(plan)
        except Exception as e:
            self.error.emit(str(e))


# ── Tree builder ──────────────────────────────────────────────────────────

def _total_actual(node: dict) -> float:
    """Recursively sum actual_total_time * loops for worst leaf."""
    loops = node.get("Actual Loops", 1) or 1
    t = node.get("Actual Total Time", 0) * loops
    for child in node.get("Plans", []):
        child_t = _total_actual(child)
        if child_t > t:
            t = child_t
    return t


def _build_tree(parent_item: QTreeWidgetItem, node: dict, max_time: float):
    node_type   = node.get("Node Type", "?")
    relation    = node.get("Relation Name", "")
    alias       = node.get("Alias", "")
    total       = node.get("Actual Total Time", 0)
    loops       = node.get("Actual Loops", 1) or 1
    plan_rows   = node.get("Plan Rows", "?")
    actual_rows = node.get("Actual Rows", "?")
    total_cost  = node.get("Total Cost", 0)
    shared_hit  = node.get("Shared Hit Blocks", 0)
    shared_read = node.get("Shared Read Blocks", 0)
    filter_     = node.get("Filter", "")
    index_name  = node.get("Index Name", "")

    label_parts = [node_type]
    if relation:
        label_parts.append(f"on {relation}")
        if alias and alias != relation:
            label_parts.append(f"({alias})")
    if index_name:
        label_parts.append(f"[{index_name}]")
    label = " ".join(label_parts)

    actual_total_ms = total * loops
    pct = actual_total_ms / max_time if max_time > 0 else 0

    col_time   = f"{actual_total_ms:.2f} ms"
    col_rows   = f"{actual_rows} / {plan_rows}"
    col_cost   = f"{total_cost:.1f}"
    col_loops  = str(loops)
    col_bufs   = f"hit={shared_hit} read={shared_read}"
    col_filter = filter_ or ""

    item = QTreeWidgetItem(parent_item, [
        label, col_time, col_rows, col_cost, col_loops, col_bufs, col_filter
    ])
    color = _cost_color(pct)
    for col in range(7):
        item.setForeground(col, QBrush(color))
    item.setExpanded(True)

    for child in node.get("Plans", []):
        _build_tree(item, child, max_time)


# ── Dialog ────────────────────────────────────────────────────────────────

class ExplainDialog(QDialog):
    def __init__(self, db: DBManager, sql: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.sql = sql
        self.setWindowTitle("EXPLAIN ANALYZE — Query Plan Viewer")
        self.resize(1100, 700)
        self._build_ui()
        self._run()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        # header
        hdr = QHBoxLayout()
        self._status = QLabel("Running EXPLAIN ANALYZE…")
        self._status.setStyleSheet("font-weight:bold;")
        hdr.addWidget(self._status)
        hdr.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        # summary cards
        card_row = QHBoxLayout()
        self._card_total   = self._make_card("Total time", "—")
        self._card_rows    = self._make_card("Actual rows", "—")
        self._card_cost    = self._make_card("Plan cost", "—")
        self._card_buffers = self._make_card("Shared hit / read", "—")
        for c in (self._card_total, self._card_rows,
                  self._card_cost, self._card_buffers):
            card_row.addWidget(c)
        lay.addLayout(card_row)

        # splitter: tree | raw JSON
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # plan tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels([
            "Node", "Actual time", "Rows (act/est)",
            "Cost", "Loops", "Buffers", "Filter"
        ])
        self._tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 7):
            self._tree.header().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setAlternatingRowColors(True)
        self._tree.setFont(QFont("Courier New", 10))
        splitter.addWidget(self._tree)

        # raw JSON
        self._json_view = QTextEdit()
        self._json_view.setReadOnly(True)
        self._json_view.setFont(QFont("Courier New", 10))
        self._json_view.setMaximumWidth(380)
        splitter.addWidget(self._json_view)
        splitter.setSizes([720, 380])

        lay.addWidget(splitter, 1)

        # progress bar (shown while running)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setMaximumHeight(4)
        lay.addWidget(self._progress)

    def _make_card(self, label: str, value: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 6, 10, 6)
        w.setStyleSheet(
            "background: rgba(128,128,128,0.08); border-radius:6px;")
        lbl = QLabel(label)
        lbl.setStyleSheet("font-size:11px; color: gray;")
        val = QLabel(value)
        val.setStyleSheet("font-size:16px; font-weight:bold;")
        val.setObjectName("val")
        v.addWidget(lbl)
        v.addWidget(val)
        return w

    def _set_card(self, card: QWidget, text: str):
        card.findChild(QLabel, "val").setText(text)

    def _run(self):
        self._worker = ExplainWorker(self.db, self.sql)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, plan: list):
        self._progress.setVisible(False)
        try:
            root_node = plan[0]["Plan"]
        except (IndexError, KeyError, TypeError):
            self._status.setText("Could not parse plan JSON.")
            return

        # summary
        total_ms   = plan[0].get("Execution Time",
                     plan[0].get("Total Runtime", 0))
        plan_rows  = root_node.get("Actual Rows", "?")
        plan_cost  = root_node.get("Total Cost", 0)
        shared_hit  = root_node.get("Shared Hit Blocks", 0)
        shared_read = root_node.get("Shared Read Blocks", 0)

        self._set_card(self._card_total,   f"{total_ms:.1f} ms")
        self._set_card(self._card_rows,    str(plan_rows))
        self._set_card(self._card_cost,    f"{plan_cost:.1f}")
        self._set_card(self._card_buffers, f"{shared_hit} / {shared_read}")

        self._status.setText(
            f"EXPLAIN ANALYZE completed — {total_ms:.1f} ms")

        max_time = _total_actual(root_node) or 1
        _build_tree(self._tree.invisibleRootItem(), root_node, max_time)
        self._tree.expandAll()

        # colour legend in status
        self._status.setText(
            self._status.text()
            + "   🟢 fast  🟡 medium  🔴 slow (relative to slowest node)")

        # raw JSON
        self._json_view.setPlainText(json.dumps(plan, indent=2))

    def _on_error(self, error: str):
        self._progress.setVisible(False)
        self._status.setText(f"Error: {error}")
        QMessageBox.critical(self, "EXPLAIN error", error)
