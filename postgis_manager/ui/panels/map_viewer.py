"""Map Viewer panel — renders PostGIS geometries with attribute table."""

from __future__ import annotations
import json
import math
import struct
import urllib.request
import urllib.parse
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QComboBox, QLabel, QTableWidget, QTableWidgetItem,
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsPathItem, QAbstractItemView,
    QFrame, QProgressBar, QListWidget, QListWidgetItem,
    QPushButton, QDialog, QDialogButtonBox, QSizePolicy,
    QLineEdit, QFormLayout, QGroupBox, QRadioButton, QButtonGroup,
    QTabWidget, QScrollArea, QTextEdit, QCheckBox,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QPointF, QRectF, QObject, QTimer,
)
from PyQt6.QtGui import (
    QPainter, QPainterPath, QColor, QPen, QBrush,
    QWheelEvent, QMouseEvent, QAction, QTransform, QPixmap, QIcon, QImage,
    QFont,
)

from ...db.connection import DBManager
from ...utils import i18n
from ...utils.workers import launch


# ── Tile / Mercator math ──────────────────────────────────────────────────
_MAX_MERC = 20037508.342789244   # EPSG:3857 world half-extent in metres
_EARTH_R  = 6378137.0

# Predefined basemaps (name → XYZ URL template or None)
PREDEFINED_BASEMAPS: dict[str, Optional[str]] = {
    "None":             None,
    "OpenStreetMap":    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "CartoDB Light":    "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
    "CartoDB Dark":     "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
    "CartoDB Voyager":  "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
    "Stadia Alidade":   "https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}.png",
}


def _geo_to_merc(lon: float, lat: float) -> tuple[float, float]:
    lat = max(-85.0511, min(85.0511, lat))
    x = math.radians(lon) * _EARTH_R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * _EARTH_R
    return x, y


def _zoom_for_ppm(pixels_per_meter: float) -> int:
    """Best OSM zoom level for given scene scale (pixels per metre)."""
    if pixels_per_meter <= 0:
        return 2
    z = math.log2(max(1e-15, pixels_per_meter) * 2 * _MAX_MERC / 256)
    return max(0, min(19, round(z)))


def _tile_scene_rect(tx: int, ty: int, tz: int) -> QRectF:
    """QRectF in scene coords (y negated) for XYZ tile."""
    n = 1 << tz
    w = 2 * _MAX_MERC / n
    x_min = -_MAX_MERC + tx * w
    y_max_merc = _MAX_MERC - ty * w
    return QRectF(x_min, -y_max_merc, w, w)   # scene y = −merc_y


def _tiles_for_scene_rect(scene_rect: QRectF, tz: int) -> list[tuple[int, int, int]]:
    """All (tx,ty,tz) tiles that intersect the given scene QRectF."""
    n = 1 << tz
    w = 2 * _MAX_MERC / n
    xmin = scene_rect.left()
    xmax = scene_rect.right()
    # scene top is the most-negative y → maps to largest merc_y (north)
    ymax_merc = -scene_rect.top()
    ymin_merc = -scene_rect.bottom()
    tx0 = max(0, int((xmin + _MAX_MERC) / w))
    tx1 = min(n - 1, int((xmax + _MAX_MERC) / w))
    ty0 = max(0, int((_MAX_MERC - ymax_merc) / w))
    ty1 = min(n - 1, int((_MAX_MERC - ymin_merc) / w))
    return [(tx, ty, tz)
            for ty in range(ty0, ty1 + 1)
            for tx in range(tx0, tx1 + 1)]


# ── Async tile cache (background thread, no Qt network needed) ────────────
class TileFetchWorker(QThread):
    """Fetches a single tile in a background thread."""
    done = pyqtSignal(int, int, int, QPixmap)   # tx, ty, tz, pixmap

    def __init__(self, tx: int, ty: int, tz: int, url: str):
        super().__init__()
        self.tx, self.ty, self.tz = tx, ty, tz
        self.url = url

    def run(self):
        try:
            req = urllib.request.Request(
                self.url, headers={"User-Agent": "PostGIS-Manager/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = r.read()
            px = QPixmap()
            px.loadFromData(data)
            if not px.isNull():
                self.done.emit(self.tx, self.ty, self.tz, px)
        except Exception:
            pass


class TileCache(QObject):
    """Manages async XYZ tile downloads and caches the results."""
    tile_ready = pyqtSignal()

    MAX_CACHE = 512
    MAX_PARALLEL = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._url_template: str = ""
        self._cache: dict[tuple, QPixmap] = {}
        self._pending: set[tuple] = set()
        self._workers: list[TileFetchWorker] = []

    def set_template(self, template: str):
        if template != self._url_template:
            self._url_template = template
            self._cache.clear()
            self._pending.clear()

    def get(self, tx: int, ty: int, tz: int) -> Optional[QPixmap]:
        key = (tx, ty, tz)
        px = self._cache.get(key)
        if px:
            return px
        if key not in self._pending and self._url_template:
            if len(self._pending) < self.MAX_PARALLEL:
                self._pending.add(key)
                url = self._url_template.format(z=tz, x=tx, y=ty)
                w = TileFetchWorker(tx, ty, tz, url)
                w.done.connect(self._on_done)
                w.finished.connect(lambda ww=w: self._workers.remove(ww)
                                   if ww in self._workers else None)
                self._workers.append(w)
                w.start()
        return None

    def _on_done(self, tx: int, ty: int, tz: int, px: QPixmap):
        self._pending.discard((tx, ty, tz))
        if len(self._cache) >= self.MAX_CACHE:
            # evict oldest (dict insertion-order in Python 3.7+)
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[(tx, ty, tz)] = px
        self.tile_ready.emit()

    def clear(self):
        self._cache.clear()
        self._pending.clear()


# ── WMS fetcher ───────────────────────────────────────────────────────────
class WMSFetcher(QThread):
    """Fetches a WMS GetMap image for a given EPSG:3857 bounding box."""
    done = pyqtSignal(QPixmap, float, float, float, float)  # px, xmin,ymin,xmax,ymax

    def __init__(self, url: str, layer: str, styles: str,
                 xmin: float, ymin: float, xmax: float, ymax: float,
                 width: int, height: int):
        super().__init__()
        self.url, self.layer, self.styles = url, layer, styles
        self.xmin, self.ymin, self.xmax, self.ymax = xmin, ymin, xmax, ymax
        self.width, self.height = width, height

    def run(self):
        try:
            params = {
                "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap",
                "LAYERS": self.layer, "STYLES": self.styles,
                "CRS": "EPSG:3857",
                "BBOX": f"{self.xmin},{self.ymin},{self.xmax},{self.ymax}",
                "WIDTH": str(self.width), "HEIGHT": str(self.height),
                "FORMAT": "image/png", "TRANSPARENT": "TRUE",
            }
            full_url = self.url.rstrip("?") + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(
                full_url, headers={"User-Agent": "PostGIS-Manager/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            px = QPixmap()
            px.loadFromData(data)
            if not px.isNull():
                self.done.emit(px, self.xmin, self.ymin, self.xmax, self.ymax)
        except Exception:
            pass


# ── WFS load worker ───────────────────────────────────────────────────────
class WFSLoadWorker(QThread):
    """Fetches WFS GetFeature (GeoJSON), returns features like MapLoadWorker."""
    progress = pyqtSignal(int)
    features = pyqtSignal(list)
    columns  = pyqtSignal(list)
    error    = pyqtSignal(str)

    LIMIT = 5000

    def __init__(self, url: str, type_name: str, max_features: int = 5000):
        super().__init__()
        self.url = url.rstrip("?")
        self.type_name = type_name
        self.max_features = max_features

    def run(self):
        try:
            params = {
                "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
                "TYPENAMES": self.type_name,
                "OUTPUTFORMAT": "application/json",
                "COUNT": str(self.max_features),
                "SRSNAME": "EPSG:4326",
            }
            full_url = self.url + "?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(
                full_url, headers={"User-Agent": "PostGIS-Manager/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read())

            features_json = data.get("features", [])
            if not features_json:
                self.features.emit([])
                return

            # Collect all property keys from first 10 features
            col_set: dict[str, None] = {}
            for f in features_json[:10]:
                for k in f.get("properties", {}).keys():
                    col_set[k] = None
            cols = list(col_set.keys())
            self.columns.emit(cols)

            result = []
            total = len(features_json)
            for i, feat in enumerate(features_json):
                geom_json = feat.get("geometry")
                if not geom_json:
                    continue
                path, gtype = _geojson_to_path(geom_json)
                if path.isEmpty():
                    continue
                # Encode as a fake "wkb" placeholder — we store the path directly
                attrs = {k: feat.get("properties", {}).get(k) for k in cols}
                result.append((path, gtype, attrs))   # path instead of wkb bytes
                if i % 200 == 0:
                    self.progress.emit(int(i / max(total, 1) * 100))

            self.progress.emit(100)
            self.features.emit(result)
        except Exception as e:
            self.error.emit(str(e))


def _geojson_to_path(geom: dict) -> tuple[QPainterPath, str]:
    """Convert a GeoJSON geometry (in EPSG:4326) to (QPainterPath, gtype)."""
    gtype = geom.get("type", "Unknown").upper()
    coords = geom.get("coordinates", [])
    path = QPainterPath()

    if gtype == "POINT":
        x, y = _geo_to_merc(*coords[:2])
        path.addEllipse(QPointF(x, -y), 4, 4)
    elif gtype == "LINESTRING":
        _ring_to_path(coords, path, move=True)
    elif gtype == "POLYGON":
        for ring in coords:
            sub = QPainterPath()
            _ring_to_path(ring, sub, move=True)
            sub.closeSubpath()
            path.addPath(sub)
    elif gtype in ("MULTIPOINT", "MULTILINESTRING", "MULTIPOLYGON",
                   "GEOMETRYCOLLECTION"):
        inner_type = gtype[5:] if gtype.startswith("MULTI") else ""
        parts = geom.get("geometries", coords) if gtype == "GEOMETRYCOLLECTION" else coords
        for part in parts:
            if gtype == "GEOMETRYCOLLECTION":
                sub, _ = _geojson_to_path(part)
            else:
                sub, _ = _geojson_to_path({"type": inner_type, "coordinates": part})
            path.addPath(sub)

    return path, gtype


def _ring_to_path(coords, path: QPainterPath, move: bool = False):
    for i, c in enumerate(coords):
        x, y = _geo_to_merc(*c[:2])
        if i == 0 and move:
            path.moveTo(x, -y)
        else:
            path.lineTo(x, -y)


# ── Add-service dialog ────────────────────────────────────────────────────
class _ServiceDialog(QDialog):
    """Dialog to add a WMS, WFS, or custom XYZ tile service."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Map Service")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)

        # Service type tabs
        tabs = QTabWidget()

        # ── XYZ tile tab ──
        xyz_w = QWidget()
        xyz_l = QFormLayout(xyz_w)
        self._xyz_url = QLineEdit()
        self._xyz_url.setPlaceholderText(
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        self._xyz_name = QLineEdit()
        self._xyz_name.setPlaceholderText("My Basemap")
        xyz_l.addRow("URL template:", self._xyz_url)
        xyz_l.addRow("Name:", self._xyz_name)
        tabs.addTab(xyz_w, "XYZ Tiles")

        # ── WMS tab ──
        wms_w = QWidget()
        wms_l = QFormLayout(wms_w)
        self._wms_url = QLineEdit()
        self._wms_url.setPlaceholderText("https://example.com/wms")
        self._wms_layer = QLineEdit()
        self._wms_layer.setPlaceholderText("layer_name")
        self._wms_styles = QLineEdit()
        self._wms_name = QLineEdit()
        self._wms_name.setPlaceholderText("My WMS")
        wms_l.addRow("URL:", self._wms_url)
        wms_l.addRow("Layer:", self._wms_layer)
        wms_l.addRow("Styles:", self._wms_styles)
        wms_l.addRow("Name:", self._wms_name)
        tabs.addTab(wms_w, "WMS")

        # ── WFS tab ──
        wfs_w = QWidget()
        wfs_l = QFormLayout(wfs_w)
        self._wfs_url = QLineEdit()
        self._wfs_url.setPlaceholderText("https://example.com/wfs")
        self._wfs_type = QLineEdit()
        self._wfs_type.setPlaceholderText("namespace:TypeName")
        self._wfs_name = QLineEdit()
        self._wfs_name.setPlaceholderText("My WFS Layer")
        wfs_l.addRow("URL:", self._wfs_url)
        wfs_l.addRow("TypeName:", self._wfs_type)
        wfs_l.addRow("Name:", self._wfs_name)
        tabs.addTab(wfs_w, "WFS")

        layout.addWidget(tabs)
        self._tabs = tabs

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def result_data(self) -> dict:
        """Return {'type': 'xyz'|'wms'|'wfs', ...} depending on active tab."""
        idx = self._tabs.currentIndex()
        if idx == 0:
            return {"type": "xyz",
                    "url": self._xyz_url.text().strip(),
                    "name": self._xyz_name.text().strip() or "Custom XYZ"}
        elif idx == 1:
            return {"type": "wms",
                    "url": self._wms_url.text().strip(),
                    "layer": self._wms_layer.text().strip(),
                    "styles": self._wms_styles.text().strip(),
                    "name": self._wms_name.text().strip() or "WMS Layer"}
        else:
            return {"type": "wfs",
                    "url": self._wfs_url.text().strip(),
                    "type_name": self._wfs_type.text().strip(),
                    "name": self._wfs_name.text().strip() or "WFS Layer"}


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
            # Render in EPSG:3857 (Web Mercator) so tile basemaps align
            target_srid = 3857

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
    viewport_changed = pyqtSignal()     # emitted after zoom/pan

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
        self._panning    = False
        self._pan_start  = QPointF()
        self._pan_mode   = False     # True = always-pan on LMB
        self._items: list[FeatureItem] = []
        self._selected_fid: int = -1

        # ── Basemap state ─────────────────────────────────────────────────
        self._tile_cache = TileCache(self)
        self._tile_cache.tile_ready.connect(self.viewport().update)
        self._tile_url: str = ""          # empty = no tile basemap

        # WMS state
        self._wms_url: str = ""
        self._wms_layer: str = ""
        self._wms_styles: str = ""
        self._wms_pixmap: Optional[QPixmap] = None
        self._wms_rect: Optional[QRectF] = None   # scene coords of last WMS image
        self._wms_worker: Optional[WMSFetcher] = None
        self._wms_timer = QTimer(self)
        self._wms_timer.setSingleShot(True)
        self._wms_timer.timeout.connect(self._fetch_wms)

    # ── Basemap control ───────────────────────────────────────────────────
    def set_tile_basemap(self, url: str):
        """Set XYZ tile basemap URL template (empty string to disable)."""
        self._tile_url = url
        self._tile_cache.set_template(url)
        self._wms_url = ""
        self._wms_pixmap = None
        self.viewport().update()

    def set_wms_basemap(self, url: str, layer: str, styles: str = ""):
        """Set WMS basemap."""
        self._tile_url = ""
        self._tile_cache.set_template("")
        self._wms_url = url
        self._wms_layer = layer
        self._wms_styles = styles
        self._wms_pixmap = None
        self._wms_rect = None
        self._schedule_wms()

    def clear_basemap(self):
        self._tile_url = ""
        self._tile_cache.set_template("")
        self._wms_url = ""
        self._wms_pixmap = None
        self._wms_rect = None
        self.viewport().update()

    def _schedule_wms(self):
        """Debounce WMS fetch — wait 400 ms after last viewport change."""
        if self._wms_url:
            self._wms_timer.start(400)

    def _fetch_wms(self):
        if not self._wms_url:
            return
        vp = self.viewport().rect()
        scene_tl = self.mapToScene(vp.topLeft())
        scene_br = self.mapToScene(vp.bottomRight())
        # scene y = −merc_y, so north = −scene_top, south = −scene_bottom
        xmin = scene_tl.x()
        xmax = scene_br.x()
        ymin = -scene_br.y()   # south in merc
        ymax = -scene_tl.y()   # north in merc
        if xmax <= xmin or ymax <= ymin:
            return
        if self._wms_worker and self._wms_worker.isRunning():
            return
        self._wms_worker = WMSFetcher(
            self._wms_url, self._wms_layer, self._wms_styles,
            xmin, ymin, xmax, ymax,
            vp.width(), vp.height())
        self._wms_worker.done.connect(self._on_wms_done)
        launch(self._wms_worker)

    def _on_wms_done(self, px: QPixmap,
                     xmin: float, ymin: float, xmax: float, ymax: float):
        self._wms_pixmap = px
        # scene rect (y negated): top = −ymax, height = ymax−ymin
        self._wms_rect = QRectF(xmin, -ymax, xmax - xmin, ymax - ymin)
        self.viewport().update()

    # ── drawBackground ────────────────────────────────────────────────────
    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, QBrush(QColor("#1A1F2E")))

        # XYZ tiles
        if self._tile_url:
            scale = self.transform().m11()   # pixels per scene unit (metre)
            tz = _zoom_for_ppm(scale)
            for tx, ty, tz_ in _tiles_for_scene_rect(rect, tz):
                px = self._tile_cache.get(tx, ty, tz_)
                if px:
                    tile_r = _tile_scene_rect(tx, ty, tz_)
                    painter.drawPixmap(tile_r, px, QRectF(px.rect()))

        # WMS image
        elif self._wms_pixmap and self._wms_rect:
            painter.drawPixmap(self._wms_rect, self._wms_pixmap,
                               QRectF(self._wms_pixmap.rect()))

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
            for fid, feat in enumerate(features):
                raw, gtype, _attrs = feat[0], feat[1], feat[2]
                try:
                    if isinstance(raw, QPainterPath):
                        path = raw      # WFS pre-parsed path
                    else:
                        path, gtype = _wkb_to_path(raw)
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

    def fit_world(self):
        """Fit to full world extent (EPSG:3857). Used when basemap loads with no data."""
        pad = _MAX_MERC * 1.05
        self.fitInView(QRectF(-pad, -pad, pad * 2, pad * 2),
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
    def set_pan_mode(self, enabled: bool):
        self._pan_mode = enabled
        if enabled:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self._panning = False

    def wheelEvent(self, e: QWheelEvent):
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)
        self._schedule_wms()

    def mousePressEvent(self, e: QMouseEvent):
        want_pan = (
            e.button() == Qt.MouseButton.MiddleButton or
            (e.button() == Qt.MouseButton.LeftButton and
             bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)) or
            (e.button() == Qt.MouseButton.LeftButton and self._pan_mode)
        )
        if want_pan:
            self._panning = True
            self._pan_start = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()          # stop Qt from doing rubber-band / selection
            return
        if e.button() == Qt.MouseButton.LeftButton:
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
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._panning and e.button() in (
                Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._panning = False
            self.setCursor(
                Qt.CursorShape.OpenHandCursor if self._pan_mode
                else Qt.CursorShape.ArrowCursor)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def zoom_in(self):
        self.scale(1.5, 1.5)
        self._schedule_wms()

    def zoom_out(self):
        self.scale(1 / 1.5, 1 / 1.5)
        self._schedule_wms()

    def scrollContentsBy(self, dx: int, dy: int):
        super().scrollContentsBy(dx, dy)
        self._schedule_wms()


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
        # Use a font with Georgian / full Unicode coverage
        for _fam in ("Segoe UI", "Sylfaen", "Arial Unicode MS", "Noto Sans"):
            _f = QFont(_fam, 9)
            if _f.exactMatch():
                self.table.setFont(_f)
                break
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(22)

        # Right-click context menu
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table)

        # Bottom status: row count
        self._row_lbl = QLabel()
        self._row_lbl.setStyleSheet("font-size:11px;color:#666;padding:2px 4px;")
        layout.addWidget(self._row_lbl)

    def _show_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu, QApplication
        item = self.table.itemAt(pos)
        menu = QMenu(self.table)

        act_copy_cell = menu.addAction("📋 Copy cell")
        act_copy_row  = menu.addAction("📋 Copy row (tab-separated)")
        menu.addSeparator()
        act_copy_all  = menu.addAction("📋 Copy all rows")

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))

        if chosen == act_copy_cell:
            if item:
                QApplication.clipboard().setText(item.text())

        elif chosen == act_copy_row:
            row = self.table.currentRow()
            if row < 0 and item:
                row = item.row()
            if row >= 0:
                cols = self.table.columnCount()
                headers = [self.table.horizontalHeaderItem(c).text()
                           if self.table.horizontalHeaderItem(c) else ""
                           for c in range(cols)]
                values  = [(self.table.item(row, c).text()
                            if self.table.item(row, c) else "")
                           for c in range(cols)]
                text = "\t".join(headers) + "\n" + "\t".join(values)
                QApplication.clipboard().setText(text)

        elif chosen == act_copy_all:
            cols = self.table.columnCount()
            rows = self.table.rowCount()
            headers = [self.table.horizontalHeaderItem(c).text()
                       if self.table.horizontalHeaderItem(c) else ""
                       for c in range(cols)]
            lines = ["\t".join(headers)]
            for r in range(rows):
                line = "\t".join(
                    (self.table.item(r, c).text()
                     if self.table.item(r, c) else "")
                    for c in range(cols))
                lines.append(line)
            QApplication.clipboard().setText("\n".join(lines))

    def set_layer_title(self, title: str):
        self.setWindowTitle(f"Attributes — {title}")
        self._lbl.setText(title)

    def update_row_count(self, n: int):
        self._row_lbl.setText(f"{n} row{'s' if n != 1 else ''}")

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
        self._act_fit.setToolTip("Fit all features in view")
        self._act_fit.triggered.connect(lambda: self._canvas.fit_extent())
        tb.addAction(self._act_fit)

        self._act_zi = QAction("＋", self)
        self._act_zi.setToolTip("Zoom in")
        self._act_zi.triggered.connect(lambda: self._canvas.zoom_in())
        tb.addAction(self._act_zi)

        self._act_zo = QAction("－", self)
        self._act_zo.setToolTip("Zoom out")
        self._act_zo.triggered.connect(lambda: self._canvas.zoom_out())
        tb.addAction(self._act_zo)

        # Pan toggle (checkable)
        self._act_pan = QAction("🖐 Pan", self)
        self._act_pan.setToolTip(
            "Pan mode — drag map with left mouse button\n"
            "(Alt+drag always pans regardless of mode)")
        self._act_pan.setCheckable(True)
        self._act_pan.toggled.connect(self._on_pan_toggled)
        tb.addAction(self._act_pan)

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

        # ── Left: layer panel ─────────────────────────────────────────────
        layer_panel = QWidget()
        layer_panel.setMinimumWidth(170)
        layer_panel.setMaximumWidth(320)
        lp_layout = QVBoxLayout(layer_panel)
        lp_layout.setContentsMargins(4, 4, 4, 4)
        lp_layout.setSpacing(3)

        # Theme-adaptive button style: uses Qt palette so it works in light & dark
        _btn = ("QPushButton{"
                "background-color:palette(button);"
                "color:palette(button-text);"
                "border:1px solid palette(mid);"
                "border-radius:3px;padding:1px 6px;}"
                "QPushButton:hover{background-color:palette(light);}"
                "QPushButton:pressed{background-color:palette(midlight);}")

        # "Layers" header with Add / Remove / Up / Down buttons
        header_row = QHBoxLayout()
        header_row.setSpacing(2)
        lbl_layers = QLabel("Layers")
        lbl_layers.setStyleSheet("font-weight:600;")
        header_row.addWidget(lbl_layers)
        header_row.addStretch()
        self._btn_add = QPushButton("+ Add layer")
        self._btn_add.setFixedHeight(24)
        self._btn_add.setStyleSheet(_btn)
        self._btn_add.setToolTip("Add PostGIS layer to map")
        self._btn_add.clicked.connect(self._on_add_layer)
        header_row.addWidget(self._btn_add)
        self._btn_remove = QPushButton("− Remove")
        self._btn_remove.setFixedHeight(24)
        self._btn_remove.setStyleSheet(_btn)
        self._btn_remove.setToolTip("Remove selected layer from map")
        self._btn_remove.clicked.connect(self._on_remove_layer)
        header_row.addWidget(self._btn_remove)
        lp_layout.addLayout(header_row)

        # Up / Down order row (separate so they're clearly for reordering)
        order_row = QHBoxLayout()
        order_row.setSpacing(2)
        order_row.addWidget(QLabel("Order:"))
        self._btn_up = QPushButton("▲ Up")
        self._btn_up.setFixedHeight(22)
        self._btn_up.setStyleSheet(_btn)
        self._btn_up.setToolTip("Move selected layer up (renders on top)")
        self._btn_up.clicked.connect(self._move_layer_up)
        order_row.addWidget(self._btn_up)
        self._btn_dn = QPushButton("▼ Down")
        self._btn_dn.setFixedHeight(22)
        self._btn_dn.setStyleSheet(_btn)
        self._btn_dn.setToolTip("Move selected layer down (renders below)")
        self._btn_dn.clicked.connect(self._move_layer_down)
        order_row.addWidget(self._btn_dn)
        order_row.addStretch()
        lp_layout.addLayout(order_row)

        self._layer_list = QListWidget()
        self._layer_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._layer_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self._layer_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._layer_list.currentItemChanged.connect(self._on_layer_selected)
        self._layer_list.itemChanged.connect(self._on_layer_check_changed)
        self._layer_list.model().rowsMoved.connect(
            lambda: self._canvas.draw_all(self._layer_list))
        lp_layout.addWidget(self._layer_list)

        # ── Basemap section (separator + controls) ────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        lp_layout.addWidget(sep)

        bm_header = QHBoxLayout()
        bm_header.addWidget(QLabel("🗺 Basemap"))
        bm_header.addStretch()
        self._btn_add_svc = QPushButton("+ Add service")
        self._btn_add_svc.setFixedHeight(22)
        self._btn_add_svc.setStyleSheet(_btn)
        self._btn_add_svc.setToolTip("Add WMS, WFS or custom XYZ tile basemap")
        self._btn_add_svc.clicked.connect(self._add_service)
        bm_header.addWidget(self._btn_add_svc)
        lp_layout.addLayout(bm_header)

        self._basemap_combo = QComboBox()
        for name in PREDEFINED_BASEMAPS:
            self._basemap_combo.addItem(name)
        self._basemap_combo.currentTextChanged.connect(self._on_basemap_changed)
        lp_layout.addWidget(self._basemap_combo)

        h_splitter.addWidget(layer_panel)

        self._canvas = MapCanvas()
        self._canvas.feature_clicked.connect(self._on_feature_clicked)
        h_splitter.addWidget(self._canvas)
        h_splitter.setSizes([210, 800])

        root.addWidget(h_splitter)

        # ── Floating attribute table window (created lazily) ──────────────
        self._attr_win: Optional[AttributeTableWindow] = None

        # ── Status bar ────────────────────────────────────────────────────
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

    def _on_pan_toggled(self, checked: bool):
        self._canvas.set_pan_mode(checked)

    def _move_layer_up(self):
        row = self._layer_list.currentRow()
        if row <= 0:
            return
        self._swap_layers(row, row - 1)

    def _move_layer_down(self):
        row = self._layer_list.currentRow()
        if row < 0 or row >= self._layer_list.count() - 1:
            return
        self._swap_layers(row, row + 1)

    def _swap_layers(self, row_a: int, row_b: int):
        lst = self._layer_list
        lst.blockSignals(True)
        item_a = lst.takeItem(row_a)
        lst.insertItem(row_b, item_a)
        lst.setCurrentRow(row_b)
        lst.blockSignals(False)
        self._canvas.draw_all(lst)

    # ── Basemap handlers ──────────────────────────────────────────────────
    def _on_basemap_changed(self, name: str):
        idx = self._basemap_combo.currentIndex()
        user_data = self._basemap_combo.itemData(idx)
        if isinstance(user_data, dict):
            self._canvas.set_wms_basemap(
                user_data["url"], user_data["layer"],
                user_data.get("styles", ""))
            self._upsert_basemap_list_item(name, "wms")
            if not self._has_vector_features():
                self._canvas.fit_world()
        elif isinstance(user_data, str):
            self._canvas.set_tile_basemap(user_data)
            self._upsert_basemap_list_item(name, "xyz")
            if not self._has_vector_features():
                self._canvas.fit_world()
        else:
            url = PREDEFINED_BASEMAPS.get(name)
            if url is None:
                self._canvas.clear_basemap()
                self._remove_basemap_list_item()
            else:
                self._canvas.set_tile_basemap(url)
                self._upsert_basemap_list_item(name, "xyz")
                if not self._has_vector_features():
                    self._canvas.fit_world()

    def _has_vector_features(self) -> bool:
        """Return True if any non-basemap layer has features loaded."""
        return bool(self._layers_data)

    def _upsert_basemap_list_item(self, name: str, bm_type: str):
        """Create or update the basemap entry at position 0 of the layer list."""
        item = self._find_basemap_list_item()
        if item is None:
            item = QListWidgetItem()
            item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable |
                Qt.ItemFlag.ItemIsEnabled |
                Qt.ItemFlag.ItemIsSelectable |
                Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked)
            self._layer_list.blockSignals(True)
            self._layer_list.insertItem(0, item)
            self._layer_list.blockSignals(False)
        icon_px = QPixmap(14, 14)
        icon_px.fill(QColor("#607D8B"))
        item.setIcon(QIcon(icon_px))
        item.setText(f"🗺 {name}")
        item.setData(Qt.ItemDataRole.UserRole,
                     {"type": "basemap", "name": name, "bm_type": bm_type})

    def _remove_basemap_list_item(self):
        item = self._find_basemap_list_item()
        if item is not None:
            self._layer_list.takeItem(self._layer_list.row(item))

    def _find_basemap_list_item(self) -> Optional[QListWidgetItem]:
        for i in range(self._layer_list.count()):
            d = self._layer_list.item(i).data(Qt.ItemDataRole.UserRole)
            if isinstance(d, dict) and d.get("type") == "basemap":
                return self._layer_list.item(i)
        return None

    def _add_service(self):
        dlg = _ServiceDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        if not data.get("url"):
            return

        stype = data["type"]
        if stype == "xyz":
            # Add as a custom basemap entry in the combo
            name = data["name"]
            self._basemap_combo.addItem(name, data["url"])
            self._basemap_combo.setCurrentText(name)
            self._canvas.set_tile_basemap(data["url"])
        elif stype == "wms":
            name = data["name"]
            self._basemap_combo.addItem(name, data)
            self._basemap_combo.setCurrentText(name)
            self._canvas.set_wms_basemap(
                data["url"], data["layer"], data.get("styles", ""))
        elif stype == "wfs":
            self._load_wfs_layer(
                data["url"], data["type_name"], data["name"])

    def _add_wfs_layer(self):
        """Open service dialog pre-set to WFS tab."""
        dlg = _ServiceDialog(self)
        dlg._tabs.setCurrentIndex(2)   # WFS tab
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        if data["type"] == "wfs" and data.get("url"):
            self._load_wfs_layer(data["url"], data["type_name"], data["name"])

    def _load_wfs_layer(self, url: str, type_name: str, name: str):
        color = self._next_color()
        lw_item = QListWidgetItem(f"🌐 {name}")
        lw_item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable |
            Qt.ItemFlag.ItemIsEnabled |
            Qt.ItemFlag.ItemIsSelectable |
            Qt.ItemFlag.ItemIsDragEnabled)
        lw_item.setCheckState(Qt.CheckState.Checked)
        lw_item.setIcon(_make_dot_icon(color))
        key = f"wfs::{url}::{type_name}"
        lw_item.setData(Qt.ItemDataRole.UserRole, {
            "schema": "wfs", "table": type_name, "geom_col": "geometry",
            "srid": 3857, "color": color, "features": [], "key": key,
        })
        self._layer_list.blockSignals(True)
        self._layer_list.addItem(lw_item)
        self._layer_list.blockSignals(False)
        self._layer_list.setCurrentItem(lw_item)

        self._progress.show()
        self._progress.setValue(0)
        self._status.setText(f"Loading WFS: {type_name}…")

        worker = WFSLoadWorker(url, type_name)
        worker.progress.connect(self._progress.setValue)
        worker.columns.connect(
            lambda cols, item=lw_item: self._on_columns(cols, item))
        worker.features.connect(
            lambda data, item=lw_item: self._on_features(data, item))
        worker.error.connect(self._on_error)
        launch(worker)

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
            Qt.ItemFlag.ItemIsSelectable |
            Qt.ItemFlag.ItemIsDragEnabled
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
        if isinstance(d, dict) and d.get("type") == "basemap":
            # Removing basemap item → reset combo to "None"
            self._basemap_combo.blockSignals(True)
            self._basemap_combo.setCurrentText("None")
            self._basemap_combo.blockSignals(False)
            self._canvas.clear_basemap()
            self._layer_list.takeItem(self._layer_list.row(item))
            return
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
        if not isinstance(d, dict) or not all(
                k in d for k in ("schema", "table", "geom_col")):
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
        if not isinstance(d, dict) or not all(
                k in d for k in ("schema", "table", "geom_col")):
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
        if not isinstance(d, dict) or not all(
                k in d for k in ("schema", "table", "geom_col")):
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
                self._attr_win.update_row_count(len(data))

    # ── Layer list signals ────────────────────────────────────────────────
    def _on_layer_selected(self, current: Optional[QListWidgetItem],
                           previous: Optional[QListWidgetItem]):
        """User clicked a different layer in the list."""
        if current:
            d = current.data(Qt.ItemDataRole.UserRole)
            if isinstance(d, dict) and d.get("type") == "basemap":
                return   # basemap row — no attribute table
        self._populate_attr_table(current)
        if current and self._attr_win:
            d = current.data(Qt.ItemDataRole.UserRole)
            if d and "geom_col" in d:
                self._attr_win.set_layer_title(
                    f'{d["schema"]}.{d["table"]}  [{d["geom_col"]}]')

    def _on_layer_check_changed(self, item: QListWidgetItem):
        """Checkbox toggled — redraw canvas or toggle basemap visibility."""
        d = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(d, dict) and d.get("type") == "basemap":
            if item.checkState() == Qt.CheckState.Checked:
                # Re-apply the current basemap
                self._on_basemap_changed(self._basemap_combo.currentText())
            else:
                self._canvas.clear_basemap()
            return
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
