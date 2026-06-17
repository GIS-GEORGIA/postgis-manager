"""Map Viewer panel — renders PostGIS geometries with attribute table."""

from __future__ import annotations
import struct
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QComboBox, QLabel, QTableWidget, QTableWidgetItem,
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsPathItem, QAbstractItemView,
    QFrame, QProgressBar, QListWidget, QListWidgetItem,
    QPushButton, QDialog, QDialogButtonBox, QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QPointF,
)
from PyQt6.QtGui import (
    QPainter, QPainterPath, QColor, QPen, QBrush,
    QWheelEvent, QMouseEvent, QAction, QTransform, QPixmap, QIcon,
)

from ...db.connection import DBManager
from ...utils import i18n
from ...utils.workers import launch


# ── Colours by geometry type ──────────────────────────────────────────────
_COLOURS = {
    "POINT":           ("#2979FF", "#2979FF"),
    "MULTIPOINT":      ("#2979FF", "#2979FF"),
    "LINESTRING":      ("#00BCD4", "#00BCD4"),
    "MULTILINESTRING": ("#00BCD4", "#00BCD4"),
    "POLYGON":         ("#1565C0", "#4FC3F7"),
    "MULTIPOLYGON":    ("#1565C0", "#4FC3F7"),
    "GEOMETRYCOLLECTION": ("#9C27B0", "#CE93D8"),
}
_SEL_STROKE = "#FF6D00"
_SEL_FILL   = "#FFD180"

# ── Layer colour palette (cycles for new layers) ──────────────────────────
_LAYER_COLORS = [
    "#2979FF", "#00C853", "#FF6D00", "#AA00FF",
    "#D50000", "#00BCD4", "#FFD600", "#76FF03",
]


# ── WKB parser (no external dep) ─────────────────────────────────────────
def _wkb_to_path(data: bytes) -> tuple[QPainterPath, str]:
    """Parse WKB bytes → (QPainterPath, geom_type_str)."""
    if not data:
        return QPainterPath(), "UNKNOWN"
    mv = memoryview(data)
    path, gtype = _parse_wkb(mv, 0)
    return path, gtype


def _read_uint32(mv, pos, le):
    fmt = "<I" if le else ">I"
    return struct.unpack_from(fmt, mv, pos)[0], pos + 4


def _read_float64(mv, pos, le):
    fmt = "<d" if le else ">d"
    return struct.unpack_from(fmt, mv, pos)[0], pos + 8


def _parse_wkb(mv, pos):
    le = mv[pos] == 1
    pos += 1
    type_id, pos = _read_uint32(mv, pos, le)
    type_id = type_id & 0xFFFF      # strip SRID flag etc.

    _TYPES = {
        1: "POINT", 2: "LINESTRING", 3: "POLYGON",
        4: "MULTIPOINT", 5: "MULTILINESTRING", 6: "MULTIPOLYGON",
        7: "GEOMETRYCOLLECTION",
    }
    gtype = _TYPES.get(type_id, "UNKNOWN")
    path = QPainterPath()

    if gtype == "POINT":
        x, pos = _read_float64(mv, pos, le)
        y, pos = _read_float64(mv, pos, le)
        r = 3
        path.addEllipse(QPointF(x, -y), r, r)

    elif gtype == "LINESTRING":
        n, pos = _read_uint32(mv, pos, le)
        for i in range(n):
            x, pos = _read_float64(mv, pos, le)
            y, pos = _read_float64(mv, pos, le)
            if i == 0:
                path.moveTo(x, -y)
            else:
                path.lineTo(x, -y)

    elif gtype == "POLYGON":
        rings, pos = _read_uint32(mv, pos, le)
        for r in range(rings):
            n, pos = _read_uint32(mv, pos, le)
            sub = QPainterPath()
            for i in range(n):
                x, pos = _read_float64(mv, pos, le)
                y, pos = _read_float64(mv, pos, le)
                if i == 0:
                    sub.moveTo(x, -y)
                else:
                    sub.lineTo(x, -y)
            sub.closeSubpath()
            path = path.united(sub) if r > 0 else sub

    elif gtype in ("MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON",
                   "GEOMETRYCOLLECTION"):
        n, pos = _read_uint32(mv, pos, le)
        for _ in range(n):
            sub, _ = _parse_wkb(mv, pos)
            # advance pos by re-parsing length (simpler than returning pos)
            inner, pos2 = _parse_wkb_advance(mv, pos)
            pos = pos2
            path.addPath(sub)

    return path, gtype


def _parse_wkb_advance(mv, pos):
    """Parse WKB and return (path, new_pos)."""
    le = mv[pos] == 1
    pos += 1
    type_id, pos = _read_uint32(mv, pos, le)
    type_id = type_id & 0xFFFF
    path = QPainterPath()

    if type_id == 1:                # POINT
        x, pos = _read_float64(mv, pos, le)
        y, pos = _read_float64(mv, pos, le)
        path.addEllipse(QPointF(x, -y), 3, 3)
    elif type_id == 2:              # LINESTRING
        n, pos = _read_uint32(mv, pos, le)
        for i in range(n):
            x, pos = _read_float64(mv, pos, le)
            y, pos = _read_float64(mv, pos, le)
            if i == 0: path.moveTo(x, -y)
            else:       path.lineTo(x, -y)
    elif type_id == 3:              # POLYGON
        rings, pos = _read_uint32(mv, pos, le)
        for _ in range(rings):
            n, pos = _read_uint32(mv, pos, le)
            for i in range(n):
                x, pos = _read_float64(mv, pos, le)
                y, pos = _read_float64(mv, pos, le)
    elif type_id in (4, 5, 6, 7):  # MULTI / COLLECTION
        n, pos = _read_uint32(mv, pos, le)
        for _ in range(n):
            _, pos = _parse_wkb_advance(mv, pos)
    return path, pos


# ── Workers ───────────────────────────────────────────────────────────────
class LayerListWorker(QThread):
    done = pyqtSignal(list)

    def __init__(self, db: DBManager):
        super().__init__()
        self.db = db

    def run(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT f_table_schema, f_table_name,
                           f_geometry_column, type, srid
                    FROM geometry_columns
                    ORDER BY f_table_schema, f_table_name
                """)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            self.done.emit(rows)
        except Exception:
            self.db.conn.rollback()
            self.done.emit([])


class MapLoadWorker(QThread):
    progress  = pyqtSignal(int)          # 0-100
    features  = pyqtSignal(list)         # list of (wkb_bytes, gtype, attrs)
    columns   = pyqtSignal(list)         # column names
    error     = pyqtSignal(str)

    LIMIT = 5000

    def __init__(self, db: DBManager, layer_info: dict):
        """layer_info keys: schema, table, geom_col, srid, color (hex str)."""
        super().__init__()
        self.db       = db
        self.schema   = layer_info["schema"]
        self.table    = layer_info["table"]
        self.geom_col = layer_info["geom_col"]
        self.srid     = layer_info.get("srid", 0)

    def _q(self, sql, params=None):
        import psycopg2.extras
        with self.db.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def run(self):
        try:
            col_rows = self._q("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                  AND column_name != %s
                ORDER BY ordinal_position
                LIMIT 20
            """, (self.schema, self.table, self.geom_col))

            cols = [r["column_name"] for r in col_rows]
            self.columns.emit(cols)

            col_sql = ", ".join(f'"{c}"' for c in cols) if cols else "NULL as _no_cols"
            target_srid = 4326

            sql = f"""
                SELECT
                    ST_AsBinary(ST_Transform(
                        ST_Force2D("{self.geom_col}"::geometry), {target_srid}
                    )) AS _wkb,
                    ST_GeometryType("{self.geom_col}"::geometry) AS _gtype,
                    {col_sql}
                FROM "{self.schema}"."{self.table}"
                WHERE "{self.geom_col}" IS NOT NULL
                LIMIT {self.LIMIT}
            """
            rows = self._q(sql)
            result = []
            total = len(rows)
            for i, row in enumerate(rows):
                wkb   = bytes(row["_wkb"]) if row["_wkb"] else b""
                gtype = (row["_gtype"] or "UNKNOWN").replace("ST_", "").upper()
                attrs = {c: row.get(c) for c in cols}
                result.append((wkb, gtype, attrs))
                if i % 200 == 0:
                    self.progress.emit(int(i / max(total, 1) * 100))

            self.progress.emit(100)
            self.features.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ── Feature graphics item ─────────────────────────────────────────────────
class FeatureItem(QGraphicsPathItem):
    def __init__(self, path: QPainterPath, gtype: str, fid: int,
                 layer_color: Optional[str] = None):
        super().__init__(path)
        self.fid   = fid
        self.gtype = gtype
        self._layer_color = layer_color
        self._selected_state = False
        self._apply_style(False)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

    def _apply_style(self, selected: bool):
        if selected:
            pen   = QPen(QColor(_SEL_STROKE), 0)
            brush = QBrush(QColor(_SEL_FILL + "AA"))
        elif self._layer_color:
            color = self._layer_color
            if "LINE" in self.gtype:
                pen   = QPen(QColor(color), 0)
                brush = QBrush(Qt.BrushStyle.NoBrush)
            elif "POINT" in self.gtype:
                pen   = QPen(QColor(color), 0)
                brush = QBrush(QColor(color))
            else:
                pen   = QPen(QColor(color), 0)
                brush = QBrush(QColor(color + "88"))
        else:
            stroke, fill = _COLOURS.get(self.gtype, ("#888", "#CCC"))
            if "LINE" in self.gtype:
                pen   = QPen(QColor(stroke), 0)
                brush = QBrush(Qt.BrushStyle.NoBrush)
            elif "POINT" in self.gtype:
                pen   = QPen(QColor(stroke), 0)
                brush = QBrush(QColor(fill))
            else:
                pen   = QPen(QColor(stroke), 0)
                brush = QBrush(QColor(fill + "88"))
        self.setPen(pen)
        self.setBrush(brush)

    def set_selected_state(self, sel: bool):
        self._selected_state = sel
        self._apply_style(sel)
        self.update()

    def hoverEnterEvent(self, e):
        if not self._selected_state:
            pen = self.pen()
            pen.setColor(QColor(_SEL_STROKE))
            self.setPen(pen)
        super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        if not self._selected_state:
            self._apply_style(False)
        super().hoverLeaveEvent(e)


# ── Map Canvas ────────────────────────────────────────────────────────────
class MapCanvas(QGraphicsView):
    feature_clicked = pyqtSignal(int)   # fid

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(
            QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor("#1A1F2E")))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._panning   = False
        self._pan_start = QPointF()
        self._items: list[FeatureItem] = []
        self._selected_fid: int = -1

    def load_features(self, feature_data: list):
        self._scene.clear()
        self._items.clear()
        self._selected_fid = -1
        for fid, (wkb, gtype, _attrs) in enumerate(feature_data):
            try:
                path, resolved = _wkb_to_path(wkb)
                if path.isEmpty():
                    continue
                item = FeatureItem(path, gtype, fid)
                item.setZValue(0 if "POLY" in gtype else 1)
                self._scene.addItem(item)
                self._items.append(item)
            except Exception:
                continue
        self.fit_extent()

    def draw_all(self, layer_list: QListWidget):
        """Redraw canvas from all visible layers in order (bottom to top)."""
        self._scene.clear()
        self._items.clear()
        self._selected_fid = -1

        count = layer_list.count()
        for list_idx in range(count):
            item = layer_list.item(list_idx)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if not data:
                continue
            features = data.get("features", [])
            color = data.get("color", None)
            for fid, (wkb, gtype, _attrs) in enumerate(features):
                try:
                    path, resolved = _wkb_to_path(wkb)
                    if path.isEmpty():
                        continue
                    fi = FeatureItem(path, gtype, fid, layer_color=color)
                    fi.setZValue(list_idx * 10 + (0 if "POLY" in gtype else 1))
                    self._scene.addItem(fi)
                    self._items.append(fi)
                except Exception:
                    continue

        if self._scene.items():
            self.fit_extent()

    def fit_extent(self):
        if self._scene.items():
            r = self._scene.itemsBoundingRect()
            self.fitInView(r.adjusted(-r.width()*.05, -r.height()*.05,
                                       r.width()*.05,  r.height()*.05),
                           Qt.AspectRatioMode.KeepAspectRatio)

    def select_feature(self, fid: int):
        for item in self._items:
            item.set_selected_state(item.fid == fid)
        self._selected_fid = fid
        # Pan to selected
        for item in self._items:
            if item.fid == fid:
                self.centerOn(item.boundingRect().center())
                break

    def clear(self):
        self._scene.clear()
        self._items.clear()

    # ── Mouse events ──────────────────────────────────────────────────────
    def wheelEvent(self, e: QWheelEvent):
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.MiddleButton or (
                e.button() == Qt.MouseButton.LeftButton and
                e.modifiers() & Qt.KeyboardModifier.AltModifier):
            self._panning = True
            self._pan_start = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif e.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(e.position().toPoint())
            hit = self._scene.itemAt(scene_pos, QTransform())
            if isinstance(hit, FeatureItem):
                self.feature_clicked.emit(hit.fid)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._panning:
            delta = e.position() - self._pan_start
            self._pan_start = e.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y()))
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(e)

    def zoom_in(self):  self.scale(1.5, 1.5)
    def zoom_out(self): self.scale(1/1.5, 1/1.5)


# ── Add-layer dialog ──────────────────────────────────────────────────────
def _make_dot_icon(color: str, size: int = 14) -> QIcon:
    """Create a small filled-circle icon for the given hex color."""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(px)


class _AddLayerDialog(QDialog):
    """Small dialog that lets the user pick a layer from geometry_columns."""

    def __init__(self, available_rows: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Layer")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Select a layer to add:"))
        self._combo = QComboBox()
        self._combo.setMinimumWidth(320)
        for r in available_rows:
            label = (f'{r["f_table_schema"]}.{r["f_table_name"]}'
                     f' ({r["f_geometry_column"]})')
            self._combo.addItem(label, r)
        layout.addWidget(self._combo)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_row(self) -> Optional[dict]:
        return self._combo.currentData()


# ── Floating attribute table window ──────────────────────────────────────
class AttributeTableWindow(QDialog):
    """Non-modal floating window that shows feature attributes."""

    def __init__(self, parent=None):
        super().__init__(parent,
                         Qt.WindowType.Window |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Attribute Table")
        self.setMinimumSize(640, 300)
        self.resize(800, 350)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._lbl = QLabel()
        self._lbl.setStyleSheet("font-size:12px;color:#888;padding:2px 4px;")
        layout.addWidget(self._lbl)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(22)
        layout.addWidget(self.table)

    def set_layer_title(self, title: str):
        self.setWindowTitle(f"Attributes — {title}")
        self._lbl.setText(title)

    def closeEvent(self, e):
        # Hide instead of destroy so it can be re-shown
        e.ignore()
        self.hide()


# ── Main panel ────────────────────────────────────────────────────────────
class MapViewerPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db

        # Available rows from geometry_columns (refreshed via LayerListWorker)
        self._available_rows: list = []

        # Per-layer feature data: layer_key → list of (wkb, gtype, attrs)
        self._layers_data: dict[str, list] = {}

        # Per-layer column names: layer_key → list[str]
        self._layers_columns: dict[str, list] = {}

        # Currently active load worker
        self._load_worker: Optional[MapLoadWorker] = None
        self._layer_worker: Optional[LayerListWorker] = None

        # Color assignment counter
        self._color_idx: int = 0

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Toolbar ───────────────────────────────────────────────────────
        tb = QToolBar()
        tb.setMovable(False)

        self._act_fit = QAction("⊞ Fit", self)
        self._act_fit.triggered.connect(lambda: self._canvas.fit_extent())
        tb.addAction(self._act_fit)

        self._act_zi = QAction("＋", self)
        self._act_zi.triggered.connect(lambda: self._canvas.zoom_in())
        tb.addAction(self._act_zi)

        self._act_zo = QAction("－", self)
        self._act_zo.triggered.connect(lambda: self._canvas.zoom_out())
        tb.addAction(self._act_zo)

        tb.addSeparator()

        self._act_attr = QAction("📋 Attributes", self)
        self._act_attr.setToolTip("Open floating attribute table")
        self._act_attr.triggered.connect(self._show_attr_window)
        tb.addAction(self._act_attr)

        root.addWidget(tb)

        # ── Progress bar ──────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.hide()
        root.addWidget(self._progress)

        # ── Main splitter: layer panel | canvas ───────────────────────────
        h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: layer panel
        layer_panel = QWidget()
        layer_panel.setMinimumWidth(160)
        layer_panel.setMaximumWidth(300)
        lp_layout = QVBoxLayout(layer_panel)
        lp_layout.setContentsMargins(4, 4, 4, 4)
        lp_layout.setSpacing(4)

        # Header row: "Layers" label + "+" + "-" buttons
        _btn_style = ("QPushButton{background:#2979FF;color:#fff;border:none;"
                      "border-radius:3px;font-weight:bold;font-size:14px;}"
                      "QPushButton:hover{background:#1565C0;}")
        header_row = QHBoxLayout()
        lbl_layers = QLabel("Layers")
        lbl_layers.setStyleSheet("font-weight:600;")
        header_row.addWidget(lbl_layers)
        header_row.addStretch()
        self._btn_add = QPushButton("+")
        self._btn_add.setFixedSize(24, 24)
        self._btn_add.setToolTip("Add layer")
        self._btn_add.setStyleSheet(_btn_style)
        self._btn_add.clicked.connect(self._on_add_layer)
        header_row.addWidget(self._btn_add)
        self._btn_remove = QPushButton("−")
        self._btn_remove.setFixedSize(24, 24)
        self._btn_remove.setToolTip("Remove selected layer")
        self._btn_remove.setStyleSheet(_btn_style)
        self._btn_remove.clicked.connect(self._on_remove_layer)
        header_row.addWidget(self._btn_remove)
        lp_layout.addLayout(header_row)

        self._layer_list = QListWidget()
        self._layer_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._layer_list.currentItemChanged.connect(self._on_layer_selected)
        self._layer_list.itemChanged.connect(self._on_layer_check_changed)
        lp_layout.addWidget(self._layer_list)

        h_splitter.addWidget(layer_panel)

        self._canvas = MapCanvas()
        self._canvas.feature_clicked.connect(self._on_feature_clicked)
        h_splitter.addWidget(self._canvas)
        h_splitter.setSizes([200, 800])

        root.addWidget(h_splitter)

        # ── Floating attribute table window (created lazily) ──────────────
        self._attr_win: Optional[AttributeTableWindow] = None

        # ── Status bar (minimal) ──────────────────────────────────────────
        self._status = QLabel()
        self._status.setStyleSheet(
            "padding: 2px 8px; font-size: 12px; color: #888;")
        self._status.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        root.addWidget(self._status)

    def _get_attr_win(self) -> AttributeTableWindow:
        """Lazily create the floating attribute table window."""
        if self._attr_win is None:
            self._attr_win = AttributeTableWindow(None)   # no parent = top-level
            self._attr_win.table.itemSelectionChanged.connect(
                self._on_table_selection)
            self._attr_win.table.itemChanged.connect(self._on_cell_edit)
        return self._attr_win

    @property
    def _table(self) -> QTableWidget:
        """Proxy property so all existing code still works via self._table."""
        return self._get_attr_win().table

    def _show_attr_window(self):
        win = self._get_attr_win()
        win.show()
        win.raise_()

    # ── Layer list helpers ────────────────────────────────────────────────
    def _layer_key(self, schema: str, table: str, geom_col: str) -> str:
        return f"{schema}.{table}.{geom_col}"

    def _next_color(self) -> str:
        color = _LAYER_COLORS[self._color_idx % len(_LAYER_COLORS)]
        self._color_idx += 1
        return color

    def _find_list_item(self, key: str) -> Optional[QListWidgetItem]:
        for i in range(self._layer_list.count()):
            item = self._layer_list.item(i)
            d = item.data(Qt.ItemDataRole.UserRole)
            if d and self._layer_key(d["schema"], d["table"], d["geom_col"]) == key:
                return item
        return None

    def _add_layer_to_list(self, schema: str, table: str,
                           geom_col: str, srid: int,
                           color: Optional[str] = None) -> QListWidgetItem:
        """Create and append a QListWidgetItem for the given layer."""
        if color is None:
            color = self._next_color()
        key = self._layer_key(schema, table, geom_col)
        label = f"{schema}.{table}"
        if geom_col:
            label += f" [{geom_col}]"

        lw_item = QListWidgetItem(label)
        lw_item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable |
            Qt.ItemFlag.ItemIsEnabled |
            Qt.ItemFlag.ItemIsSelectable
        )
        lw_item.setCheckState(Qt.CheckState.Checked)
        lw_item.setIcon(_make_dot_icon(color))
        lw_item.setData(Qt.ItemDataRole.UserRole, {
            "schema":   schema,
            "table":    table,
            "geom_col": geom_col,
            "srid":     srid,
            "color":    color,
            "features": [],
        })
        self._layer_list.blockSignals(True)
        self._layer_list.addItem(lw_item)
        self._layer_list.blockSignals(False)
        return lw_item

    # ── DB interaction ────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        if self.db.is_connected():
            self._load_layers()

    def _load_layers(self):
        """Refresh the available layer list from geometry_columns."""
        if not self.db.is_connected():
            return
        self._layer_worker = LayerListWorker(self.db)
        self._layer_worker.done.connect(self._on_layers_loaded)
        launch(self._layer_worker)

    def _on_layers_loaded(self, rows: list):
        self._available_rows = rows
        # No status update needed — the count is not useful to the user

    # ── Add / remove layer buttons ────────────────────────────────────────
    def _on_add_layer(self):
        if not self._available_rows:
            if self.db.is_connected():
                self._load_layers()
            self._status.setText("No available layers yet. Try again in a moment.")
            return

        dlg = _AddLayerDialog(self._available_rows, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        row = dlg.selected_row()
        if not row:
            return

        schema   = row["f_table_schema"]
        table    = row["f_table_name"]
        geom_col = row["f_geometry_column"]
        srid     = row.get("srid", 0)
        key      = self._layer_key(schema, table, geom_col)

        # Don't add duplicates
        if self._find_list_item(key):
            self._status.setText(f"Layer {schema}.{table} already in list.")
            return

        lw_item = self._add_layer_to_list(schema, table, geom_col, srid)
        self._layer_list.setCurrentItem(lw_item)
        self._load_layer_data(lw_item)

    def _on_remove_layer(self):
        item = self._layer_list.currentItem()
        if not item:
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if d:
            key = self._layer_key(d["schema"], d["table"], d["geom_col"])
            self._layers_data.pop(key, None)
            self._layers_columns.pop(key, None)
        row = self._layer_list.row(item)
        self._layer_list.takeItem(row)
        self._canvas.draw_all(self._layer_list)
        self._table.clearContents()
        self._table.setRowCount(0)

    # ── Load data for a layer item ────────────────────────────────────────
    def _load_layer_data(self, lw_item: QListWidgetItem):
        """Start a MapLoadWorker for the given list item."""
        if not self.db.is_connected():
            return
        d = lw_item.data(Qt.ItemDataRole.UserRole)
        if not d:
            return

        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.quit()
            self._load_worker.wait()

        self._progress.show()
        self._progress.setValue(0)
        self._status.setText(i18n.t("mv_loading"))

        layer_info = {
            "schema":   d["schema"],
            "table":    d["table"],
            "geom_col": d["geom_col"],
            "srid":     d.get("srid", 0),
            "color":    d.get("color"),
        }
        worker = MapLoadWorker(self.db, layer_info)
        # Capture references for closures
        worker.progress.connect(self._progress.setValue)
        worker.columns.connect(
            lambda cols, item=lw_item: self._on_columns(cols, item))
        worker.features.connect(
            lambda data, item=lw_item: self._on_features(data, item))
        worker.error.connect(self._on_error)
        self._load_worker = worker
        launch(worker)

    def _on_columns(self, cols: list, lw_item: QListWidgetItem):
        d = lw_item.data(Qt.ItemDataRole.UserRole)
        if not d:
            return
        key = self._layer_key(d["schema"], d["table"], d["geom_col"])
        self._layers_columns[key] = cols
        # If this is the currently selected layer, update table headers
        if self._layer_list.currentItem() is lw_item:
            self._table.setColumnCount(len(cols))
            self._table.setHorizontalHeaderLabels(cols)

    def _on_features(self, data: list, lw_item: QListWidgetItem):
        self._progress.hide()

        d = lw_item.data(Qt.ItemDataRole.UserRole)
        if not d:
            return
        key = self._layer_key(d["schema"], d["table"], d["geom_col"])
        self._layers_data[key] = data

        # Store features inside the item's UserRole data too (for draw_all)
        d["features"] = data
        lw_item.setData(Qt.ItemDataRole.UserRole, d)

        # Redraw all visible layers
        self._canvas.draw_all(self._layer_list)

        # If this is the active layer, populate the attribute table
        if self._layer_list.currentItem() is lw_item:
            self._populate_attr_table(lw_item)

        n = len(data)
        limit_note = f" (limit {MapLoadWorker.LIMIT})" if n == MapLoadWorker.LIMIT else ""
        self._status.setText(
            i18n.t("mv_loaded", n=n) + limit_note)

    def _on_error(self, msg: str):
        self._progress.hide()
        self._status.setText(f"Error: {msg}")

    # ── Attribute table population ────────────────────────────────────────
    def _populate_attr_table(self, lw_item: Optional[QListWidgetItem]):
        self._table.blockSignals(True)
        self._table.clearContents()
        self._table.setRowCount(0)

        if lw_item is None:
            self._table.setColumnCount(0)
            self._table.blockSignals(False)
            return

        d = lw_item.data(Qt.ItemDataRole.UserRole)
        if not d:
            self._table.blockSignals(False)
            return

        key = self._layer_key(d["schema"], d["table"], d["geom_col"])
        cols = self._layers_columns.get(key, [])
        data = self._layers_data.get(key, [])

        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.setRowCount(len(data))

        for row_idx, (_wkb, _gtype, attrs) in enumerate(data):
            for col_idx, col in enumerate(cols):
                val = attrs.get(col, "")
                cell = QTableWidgetItem("" if val is None else str(val))
                self._table.setItem(row_idx, col_idx, cell)

        self._table.blockSignals(False)

        if self._attr_win and lw_item:
            d = lw_item.data(Qt.ItemDataRole.UserRole)
            if d:
                self._attr_win.set_layer_title(
                    f'{d["schema"]}.{d["table"]}  [{d["geom_col"]}]  '
                    f'({len(data)} features)')

    # ── Layer list signals ────────────────────────────────────────────────
    def _on_layer_selected(self, current: Optional[QListWidgetItem],
                           previous: Optional[QListWidgetItem]):
        """User clicked a different layer in the list."""
        self._populate_attr_table(current)
        if current and self._attr_win:
            d = current.data(Qt.ItemDataRole.UserRole)
            if d:
                self._attr_win.set_layer_title(
                    f'{d["schema"]}.{d["table"]}  [{d["geom_col"]}]')

    def _on_layer_check_changed(self, item: QListWidgetItem):
        """Checkbox toggled — redraw canvas."""
        self._canvas.draw_all(self._layer_list)

    # ── Sync: canvas ↔ table ──────────────────────────────────────────────
    def _on_feature_clicked(self, fid: int):
        self._table.blockSignals(True)
        self._table.selectRow(fid)
        self._table.blockSignals(False)
        self._canvas.select_feature(fid)

    def _on_table_selection(self):
        rows = self._table.selectedItems()
        if not rows:
            return
        fid = self._table.currentRow()
        self._canvas.select_feature(fid)

    # ── Inline edit ───────────────────────────────────────────────────────
    def _on_cell_edit(self, item: QTableWidgetItem):
        if not self.db.is_connected():
            return
        current = self._layer_list.currentItem()
        if current is None:
            return
        d = current.data(Qt.ItemDataRole.UserRole)
        if not d:
            return
        key = self._layer_key(d["schema"], d["table"], d["geom_col"])
        feature_data = self._layers_data.get(key, [])
        cols = self._layers_columns.get(key, [])
        if not feature_data or not cols:
            return

        fid = item.row()
        col_idx = item.column()
        if col_idx >= len(cols):
            return
        col = cols[col_idx]
        new_val = item.text()

        schema = d["schema"]
        table  = d["table"]

        pk_col = cols[0] if cols else None
        if not pk_col:
            return
        pk_val = feature_data[fid][2].get(pk_col)
        if pk_val is None:
            return

        try:
            with self.db.conn.cursor() as cur:
                cur.execute(
                    f'UPDATE "{schema}"."{table}" SET "{col}" = %s '
                    f'WHERE "{pk_col}" = %s',
                    (new_val, pk_val)
                )
            self.db.conn.commit()
            feature_data[fid][2][col] = new_val
            self._status.setText(
                i18n.t("mv_saved", col=col, pk=pk_val))
        except Exception as e:
            self.db.conn.rollback()
            self._status.setText(f"Error: {e}")

    # ── External API ──────────────────────────────────────────────────────
    def set_active_layer(self, schema: str, table: str, geom_col: str = ""):
        """Called from browser panel when user selects a layer."""
        # Try to find the layer in the list first
        # Use geom_col if given, else match on schema+table
        for i in range(self._layer_list.count()):
            lw_item = self._layer_list.item(i)
            d = lw_item.data(Qt.ItemDataRole.UserRole)
            if not d:
                continue
            if (d["schema"] == schema and d["table"] == table and
                    (not geom_col or d["geom_col"] == geom_col)):
                self._layer_list.setCurrentItem(lw_item)
                return

        # Not in list yet — need to find the row in available_rows and add it
        def _add_after_load(rows: list):
            for r in rows:
                if (r["f_table_schema"] == schema and
                        r["f_table_name"] == table and
                        (not geom_col or r["f_geometry_column"] == geom_col)):
                    gcol = r["f_geometry_column"]
                    srid = r.get("srid", 0)
                    key  = self._layer_key(schema, table, gcol)
                    existing = self._find_list_item(key)
                    if existing:
                        self._layer_list.setCurrentItem(existing)
                        return
                    lw_item = self._add_layer_to_list(schema, table, gcol, srid)
                    self._layer_list.setCurrentItem(lw_item)
                    self._load_layer_data(lw_item)
                    return

        if self._available_rows:
            _add_after_load(self._available_rows)
        elif self.db.is_connected():
            # Trigger a refresh; when done, retry
            worker = LayerListWorker(self.db)

            def _on_done(rows):
                self._available_rows = rows
                _add_after_load(rows)

            worker.done.connect(_on_done)
            launch(worker)

    def refresh(self):
        self._load_layers()
