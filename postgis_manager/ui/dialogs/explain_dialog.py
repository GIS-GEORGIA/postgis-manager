"""EXPLAIN ANALYZE visual dialog — parses JSON plan, shows tree + graph + stats."""

from __future__ import annotations
import json
import math
import psycopg2
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QTextEdit, QSplitter, QWidget,
    QHeaderView, QProgressBar, QMessageBox, QTabWidget,
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsRectItem, QGraphicsTextItem, QGraphicsPathItem,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui import (
    QColor, QFont, QBrush, QPainter, QPen, QPainterPath,
)

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


# ── Graph canvas ─────────────────────────────────────────────────────────

_NODE_W = 200
_NODE_H = 60
_GAP_X  = 40
_GAP_Y  = 80


def _layout_nodes(node: dict, depth: int = 0, pos: list = None) -> list[dict]:
    """Returns flat list of {node, depth, x, y} with simple recursive layout."""
    if pos is None:
        pos = [0]
    x = pos[0]
    pos[0] += 1
    result = [{"node": node, "col": x, "depth": depth}]
    for child in node.get("Plans", []):
        result += _layout_nodes(child, depth + 1, pos)
    return result


class PlanGraphView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self._scene = QGraphicsScene()
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setStyleSheet("background: #263238;")

    def wheelEvent(self, e):
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def load(self, root_node: dict, max_time: float):
        self._scene.clear()
        items_by_id: dict[int, QRectF] = {}

        flat = _layout_nodes(root_node)
        # compute positions: depth → y, col → x
        for entry in flat:
            col   = entry["col"]
            depth = entry["depth"]
            x = col  * (_NODE_W + _GAP_X)
            y = depth * (_NODE_H + _GAP_Y)
            entry["x"] = x
            entry["y"] = y
            entry["id"] = id(entry["node"])

        # draw edges first (z=-1)
        id_to_rect: dict[int, tuple] = {e["id"]: (e["x"], e["y"]) for e in flat}

        def draw_edges(node, parent_pos=None):
            nid   = id(node)
            nx, ny = id_to_rect[nid]
            cx = nx + _NODE_W / 2
            cy = ny + _NODE_H / 2
            if parent_pos:
                px, py = parent_pos
                path = QPainterPath(QPointF(px, py))
                path.cubicTo(
                    QPointF(px, py + _GAP_Y / 2),
                    QPointF(cx, cy - _GAP_Y / 2),
                    QPointF(cx, cy)
                )
                pi = QGraphicsPathItem(path)
                pen = QPen(QColor("#607D8B"), 1.5)
                pen.setCosmetic(True)
                pi.setPen(pen)
                pi.setZValue(-1)
                self._scene.addItem(pi)
            for child in node.get("Plans", []):
                draw_edges(child, (cx, cy))

        draw_edges(root_node)

        # draw nodes
        for entry in flat:
            node   = entry["node"]
            x, y   = entry["x"], entry["y"]
            ntype  = node.get("Node Type", "?")
            t_ms   = node.get("Actual Total Time", 0) * (node.get("Actual Loops", 1) or 1)
            rows   = node.get("Actual Rows", 0)
            pct    = t_ms / max_time if max_time > 0 else 0
            color  = _cost_color(pct)

            # box
            rect = QRectF(x, y, _NODE_W, _NODE_H)
            box  = self._scene.addRect(rect, QPen(color, 1.5), QBrush(QColor(38, 50, 56)))

            # header strip
            hdr = QRectF(x, y, _NODE_W, 22)
            self._scene.addRect(hdr, QPen(Qt.PenStyle.NoPen), QBrush(color.darker(120)))

            # type label
            ti = QGraphicsTextItem(ntype)
            ti.setDefaultTextColor(QColor("#ECEFF1"))
            ti.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            ti.setPos(x + 4, y + 2)
            self._scene.addItem(ti)

            # stats
            rel = node.get("Relation Name", "")
            detail = f"{rel}  " if rel else ""
            detail += f"{t_ms:.1f}ms  rows={rows}"
            di = QGraphicsTextItem(detail)
            di.setDefaultTextColor(QColor("#B0BEC5"))
            di.setFont(QFont("Arial", 7))
            di.setPos(x + 4, y + 24)
            self._scene.addItem(di)

        r = self._scene.itemsBoundingRect()
        self.fitInView(r.adjusted(-20, -20, 20, 20), Qt.AspectRatioMode.KeepAspectRatio)


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

        # tabs: Tree | Graph | JSON
        self._tabs = QTabWidget()

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
        self._tabs.addTab(self._tree, "🌲 Tree")

        # graph view
        self._graph_view = PlanGraphView()
        self._tabs.addTab(self._graph_view, "🔷 Graph")

        # raw JSON
        self._json_view = QTextEdit()
        self._json_view.setReadOnly(True)
        self._json_view.setFont(QFont("Courier New", 10))
        self._tabs.addTab(self._json_view, "{} JSON")

        lay.addWidget(self._tabs, 1)

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

        # graph
        self._graph_view.load(root_node, max_time or 1)

        # raw JSON
        self._json_view.setPlainText(json.dumps(plan, indent=2))

    def _on_error(self, error: str):
        self._progress.setVisible(False)
        self._status.setText(f"Error: {error}")
        QMessageBox.critical(self, "EXPLAIN error", error)
