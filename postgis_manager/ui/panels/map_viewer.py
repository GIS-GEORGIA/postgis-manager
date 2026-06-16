"""Map Viewer panel — renders PostGIS geometries with attribute table."""

from __future__ import annotations
import struct
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QSplitter,
    QToolBar, QComboBox, QLabel, QTableWidget, QTableWidgetItem,
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsPathItem, QAbstractItemView,
    QFrame, QProgressBar,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QPointF,
)
from PyQt6.QtGui import (
    QPainter, QPainterPath, QColor, QPen, QBrush,
    QWheelEvent, QMouseEvent, QAction, QTransform,
)

from ...db.connection import DBManager
from ...utils import i18n


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

    def __init__(self, db: DBManager, schema: str, table: str,
                 geom_col: str, srid: int):
        super().__init__()
        self.db = db
        self.schema = schema
        self.table  = table
        self.geom_col = geom_col
        self.srid   = srid

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
                    ST_AsWKB(ST_Transform(
                        ST_Force2D("{self.geom_col}"), {target_srid}
                    )) AS _wkb,
                    ST_GeometryType("{self.geom_col}") AS _gtype,
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
    def __init__(self, path: QPainterPath, gtype: str, fid: int):
        super().__init__(path)
        self.fid   = fid
        self.gtype = gtype
        self._selected_state = False
        self._apply_style(False)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

    def _apply_style(self, selected: bool):
        stroke, fill = _COLOURS.get(self.gtype, ("#888", "#CCC"))
        if selected:
            pen   = QPen(QColor(_SEL_STROKE), 0)
            brush = QBrush(QColor(_SEL_FILL + "AA"))
        elif "LINE" in self.gtype:
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


# ── Main panel ────────────────────────────────────────────────────────────
class MapViewerPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._feature_data: list = []
        self._columns: list = []
        self._load_worker:  Optional[MapLoadWorker]  = None
        self._layer_worker: Optional[LayerListWorker] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ───────────────────────────────────────────────────────
        tb = QToolBar()
        tb.setMovable(False)

        self._layer_combo = QComboBox()
        self._layer_combo.setMinimumWidth(260)
        self._layer_combo.setPlaceholderText(i18n.t("mv_select_layer"))
        self._layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        tb.addWidget(QLabel(f"  {i18n.t('mv_layer')}:  "))
        tb.addWidget(self._layer_combo)
        tb.addSeparator()

        self._act_refresh = QAction("↻", self)
        self._act_refresh.setToolTip(i18n.t("action_refresh"))
        self._act_refresh.triggered.connect(self._load_layers)
        tb.addAction(self._act_refresh)

        self._act_load = QAction("⬇ Load", self)
        self._act_load.setToolTip(i18n.t("mv_load"))
        self._act_load.triggered.connect(self._load_map)
        tb.addAction(self._act_load)

        tb.addSeparator()

        self._act_fit = QAction("⊞ Fit", self)
        self._act_fit.triggered.connect(lambda: self._canvas.fit_extent())
        tb.addAction(self._act_fit)

        self._act_zi = QAction("＋", self)
        self._act_zi.triggered.connect(lambda: self._canvas.zoom_in())
        tb.addAction(self._act_zi)

        self._act_zo = QAction("－", self)
        self._act_zo.triggered.connect(lambda: self._canvas.zoom_out())
        tb.addAction(self._act_zo)

        layout.addWidget(tb)

        # ── Progress bar ──────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.hide()
        layout.addWidget(self._progress)

        # ── Splitter: map + table ─────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._canvas = MapCanvas()
        self._canvas.feature_clicked.connect(self._on_feature_clicked)
        splitter.addWidget(self._canvas)

        # Attribute table
        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.itemChanged.connect(self._on_cell_edit)
        splitter.addWidget(self._table)

        splitter.setSizes([480, 220])
        layout.addWidget(splitter)

        # ── Status bar ────────────────────────────────────────────────────
        self._status = QLabel()
        self._status.setStyleSheet(
            "padding: 2px 8px; font-size: 12px; color: #888;")
        layout.addWidget(self._status)

    # ── DB interaction ────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        if self.db.is_connected():
            self._load_layers()

    def _load_layers(self):
        if not self.db.is_connected():
            return
        self._layer_combo.clear()
        self._layer_worker = LayerListWorker(self.db)
        self._layer_worker.done.connect(self._on_layers_loaded)
        self._layer_worker.start()

    def _on_layers_loaded(self, rows: list):
        self._layer_combo.blockSignals(True)
        self._layer_combo.clear()
        for r in rows:
            label = f'{r["f_table_schema"]}.{r["f_table_name"]} ({r["f_geometry_column"]})'
            self._layer_combo.addItem(label, r)
        self._layer_combo.blockSignals(False)
        self._status.setText(
            i18n.t("mv_layers_found", n=self._layer_combo.count()))

    def _on_layer_changed(self, _):
        pass

    def _load_map(self):
        data = self._layer_combo.currentData()
        if not data or not self.db.is_connected():
            return
        if self._load_worker and self._load_worker.isRunning():
            self._load_worker.quit()

        self._canvas.clear()
        self._table.clearContents()
        self._table.setRowCount(0)
        self._feature_data = []
        self._progress.show()
        self._progress.setValue(0)
        self._status.setText(i18n.t("mv_loading"))

        self._load_worker = MapLoadWorker(
            self.db,
            data["f_table_schema"],
            data["f_table_name"],
            data["f_geometry_column"],
            data.get("srid", 0),
        )
        self._load_worker.progress.connect(self._progress.setValue)
        self._load_worker.columns.connect(self._on_columns)
        self._load_worker.features.connect(self._on_features)
        self._load_worker.error.connect(self._on_error)
        self._load_worker.start()

    def _on_columns(self, cols: list):
        self._columns = cols
        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)

    def _on_features(self, data: list):
        self._feature_data = data
        self._progress.hide()
        self._canvas.load_features(data)

        # Fill table
        self._table.blockSignals(True)
        self._table.setRowCount(len(data))
        for row_idx, (_wkb, _gtype, attrs) in enumerate(data):
            for col_idx, col in enumerate(self._columns):
                val = attrs.get(col, "")
                item = QTableWidgetItem(
                    "" if val is None else str(val))
                self._table.setItem(row_idx, col_idx, item)
        self._table.blockSignals(False)

        n = len(data)
        limit_note = f" (limit {MapLoadWorker.LIMIT})" if n == MapLoadWorker.LIMIT else ""
        self._status.setText(
            i18n.t("mv_loaded", n=n) + limit_note)

    def _on_error(self, msg: str):
        self._progress.hide()
        self._status.setText(f"Error: {msg}")

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
        if not self.db.is_connected() or not self._feature_data:
            return
        fid = item.row()
        col = self._columns[item.column()]
        new_val = item.text()

        layer_data = self._layer_combo.currentData()
        if not layer_data:
            return

        schema = layer_data["f_table_schema"]
        table  = layer_data["f_table_name"]

        # Find primary key (first column)
        pk_col = self._columns[0] if self._columns else None
        if not pk_col:
            return
        pk_val = self._feature_data[fid][2].get(pk_col)
        if pk_val is None:
            return

        try:
            with self.db.conn.cursor() as cur:
                cur.execute(
                    f'UPDATE "{schema}"."{table}" SET "{col}" = %s '
                    f'WHERE "{pk_col}" = %s',
                    (new_val, pk_val)
                )
            self._feature_data[fid][2][col] = new_val
            self._status.setText(
                i18n.t("mv_saved", col=col, pk=pk_val))
        except Exception as e:
            self.db.conn.rollback()
            self._status.setText(f"Error: {e}")

    # ── External API ──────────────────────────────────────────────────────
    def set_active_layer(self, schema: str, table: str, geom_col: str = ""):
        """Called from browser panel when user selects a layer."""
        for i in range(self._layer_combo.count()):
            d = self._layer_combo.itemData(i)
            if (d and d["f_table_schema"] == schema
                    and d["f_table_name"] == table):
                self._layer_combo.setCurrentIndex(i)
                self._load_map()
                return
        if self.db.is_connected():
            self._load_layers()

    def refresh(self):
        self._load_layers()
