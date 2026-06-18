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
    QSlider, QSpinBox, QDoubleSpinBox, QColorDialog, QMenu,
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
        self.db          = db
        self.schema      = layer_info["schema"]
        self.table       = layer_info["table"]
        self.geom_col    = layer_info["geom_col"]
        self.srid        = layer_info.get("srid", 0)
        self.filter_expr = layer_info.get("filter_expr", "").strip()

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

            where_extra = f"AND ({self.filter_expr})" if self.filter_expr else ""
            sql = f"""
                SELECT
                    ST_AsBinary(ST_Transform(
                        ST_Force2D("{self.geom_col}"::geometry), {target_srid}
                    )) AS _wkb,
                    ST_GeometryType("{self.geom_col}"::geometry) AS _gtype,
                    {col_sql}
                FROM "{self.schema}"."{self.table}"
                WHERE "{self.geom_col}" IS NOT NULL {where_extra}
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


# ── Layer style helpers ───────────────────────────────────────────────────

def _default_style(color: str, gtype: str = "") -> dict:
    """Return a default style dict for a layer."""
    gtype = gtype.upper()
    fill_alpha   = 0   if "LINE" in gtype else (255 if "POINT" in gtype else 120)
    stroke_alpha = 255
    return {
        "fill_color":    color,
        "fill_alpha":    fill_alpha,
        "stroke_color":  color,
        "stroke_alpha":  stroke_alpha,
        "stroke_width":  1.5,
        "point_size":    8,
    }


def _style_color(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(max(0, min(255, alpha)))
    return c


# ── Style dialog ──────────────────────────────────────────────────────────

class LayerStyleDialog(QDialog):
    """Simple style editor: fill, stroke, opacity, width."""

    style_changed = pyqtSignal(dict)   # emitted on Apply / live update

    def __init__(self, style: dict, layer_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Style — {layer_name}")
        self.setMinimumWidth(360)
        self._style = dict(style)

        lay = QVBoxLayout(self)

        # ── Fill ─────────────────────────────────────────────────────────
        fill_box = QGroupBox("Fill")
        fl = QFormLayout(fill_box)
        self._fill_btn = self._color_btn(self._style["fill_color"])
        self._fill_btn.clicked.connect(lambda: self._pick("fill_color", self._fill_btn))
        self._fill_alpha = self._slider(self._style["fill_alpha"])
        self._fill_alpha.valueChanged.connect(
            lambda v: self._style.update({"fill_alpha": v}))
        fl.addRow("Color:", self._fill_btn)
        fl.addRow("Opacity:", self._slider_row(self._fill_alpha))
        lay.addWidget(fill_box)

        # ── Stroke ───────────────────────────────────────────────────────
        stroke_box = QGroupBox("Stroke / Outline")
        sl = QFormLayout(stroke_box)
        self._stroke_btn = self._color_btn(self._style["stroke_color"])
        self._stroke_btn.clicked.connect(
            lambda: self._pick("stroke_color", self._stroke_btn))
        self._stroke_alpha = self._slider(self._style["stroke_alpha"])
        self._stroke_alpha.valueChanged.connect(
            lambda v: self._style.update({"stroke_alpha": v}))
        self._stroke_width = QDoubleSpinBox()
        self._stroke_width.setRange(0, 20)
        self._stroke_width.setSingleStep(0.5)
        self._stroke_width.setValue(self._style.get("stroke_width", 1.5))
        self._stroke_width.valueChanged.connect(
            lambda v: self._style.update({"stroke_width": v}))
        sl.addRow("Color:", self._stroke_btn)
        sl.addRow("Opacity:", self._slider_row(self._stroke_alpha))
        sl.addRow("Width (px):", self._stroke_width)
        lay.addWidget(stroke_box)

        # ── Point size ───────────────────────────────────────────────────
        pt_box = QGroupBox("Point Size")
        pl = QFormLayout(pt_box)
        self._pt_size = QSpinBox()
        self._pt_size.setRange(1, 50)
        self._pt_size.setValue(self._style.get("point_size", 8))
        self._pt_size.valueChanged.connect(
            lambda v: self._style.update({"point_size": v}))
        pl.addRow("Radius (px):", self._pt_size)
        lay.addWidget(pt_box)

        # ── Preview swatch ───────────────────────────────────────────────
        self._preview = QLabel()
        self._preview.setFixedHeight(28)
        self._preview.setFrameShape(QFrame.Shape.StyledPanel)
        self._update_preview()
        lay.addWidget(self._preview)

        # ── Buttons ──────────────────────────────────────────────────────
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply |
            QDialogButtonBox.StandardButton.Ok    |
            QDialogButtonBox.StandardButton.Cancel)
        bb.clicked.connect(self._on_btn)
        lay.addWidget(bb)

        # wire sliders/spinbox → update preview
        for w in (self._fill_alpha, self._stroke_alpha,
                  self._stroke_width, self._pt_size):
            if isinstance(w, QSlider):
                w.valueChanged.connect(self._update_preview)
            else:
                w.valueChanged.connect(self._update_preview)

    # ── helpers ───────────────────────────────────────────────────────────

    def _color_btn(self, hex_color: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(48, 24)
        btn.setStyleSheet(
            f"QPushButton{{background:{hex_color};border:1px solid #555;"
            f"border-radius:3px;}}")
        btn.setProperty("hex", hex_color)
        return btn

    def _pick(self, key: str, btn: QPushButton):
        current = QColor(self._style[key])
        color = QColorDialog.getColor(current, self, "Pick color")
        if color.isValid():
            h = color.name()
            self._style[key] = h
            btn.setStyleSheet(
                f"QPushButton{{background:{h};border:1px solid #555;"
                f"border-radius:3px;}}")
            self._update_preview()

    def _slider(self, value: int) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setRange(0, 255)
        s.setValue(value)
        return s

    def _slider_row(self, slider: QSlider) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel()
        lbl.setFixedWidth(28)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl.setText(str(slider.value()))
        slider.valueChanged.connect(lambda v: lbl.setText(str(v)))
        h.addWidget(slider, 1)
        h.addWidget(lbl)
        return w

    def _update_preview(self):
        fc = _style_color(self._style["fill_color"], self._style["fill_alpha"])
        sc = _style_color(self._style["stroke_color"], self._style["stroke_alpha"])
        self._preview.setStyleSheet(
            f"background:{fc.name(QColor.NameFormat.HexArgb)};"
            f"border:3px solid {sc.name()};")

    def _on_btn(self, btn):
        role = self.sender().standardButton(btn)
        if role == QDialogButtonBox.StandardButton.Apply:
            self.style_changed.emit(dict(self._style))
        elif role == QDialogButtonBox.StandardButton.Ok:
            self.style_changed.emit(dict(self._style))
            self.accept()
        elif role == QDialogButtonBox.StandardButton.Cancel:
            self.reject()

    def get_style(self) -> dict:
        return dict(self._style)


# ── Feature graphics item ─────────────────────────────────────────────────
class FeatureItem(QGraphicsPathItem):
    def __init__(self, path: QPainterPath, gtype: str, fid: int,
                 layer_color: Optional[str] = None,
                 style: Optional[dict] = None):
        super().__init__(path)
        self.fid   = fid
        self.gtype = gtype
        self._layer_color = layer_color
        self._style = style
        self._selected_state = False
        self._apply_style(False)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)

    def _apply_style(self, selected: bool):
        gtype = self.gtype.upper()
        if selected:
            pen   = QPen(QColor(_SEL_STROKE), 0)
            brush = QBrush(QColor(_SEL_FILL + "AA"))
            self.setPen(pen)
            self.setBrush(brush)
            return

        if self._style:
            s = self._style
            sc = _style_color(s.get("stroke_color", "#888"), s.get("stroke_alpha", 255))
            fc = _style_color(s.get("fill_color", "#3498db"), s.get("fill_alpha", 120))
            w  = s.get("stroke_width", 1.5)
            pen   = QPen(sc, w)
            pen.setCosmetic(True)
            brush = QBrush(fc) if fc.alpha() > 0 else QBrush(Qt.BrushStyle.NoBrush)
        elif self._layer_color:
            color = self._layer_color
            if "LINE" in gtype:
                pen   = QPen(QColor(color), 0)
                brush = QBrush(Qt.BrushStyle.NoBrush)
            elif "POINT" in gtype:
                pen   = QPen(QColor(color), 0)
                brush = QBrush(QColor(color))
            else:
                pen   = QPen(QColor(color), 0)
                brush = QBrush(QColor(color + "88"))
        else:
            stroke, fill = _COLOURS.get(gtype, ("#888", "#CCC"))
            if "LINE" in gtype:
                pen   = QPen(QColor(stroke), 0)
                brush = QBrush(Qt.BrushStyle.NoBrush)
            elif "POINT" in gtype:
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
    feature_clicked = pyqtSignal(int)          # fid
    feature_info    = pyqtSignal(int, object)  # fid, QPointF (screen)
    viewport_changed = pyqtSignal()            # emitted after zoom/pan
    coord_moved     = pyqtSignal(float, float) # lon, lat (WGS84)
    measure_point   = pyqtSignal(object)       # QPointF scene coords

    # Interaction modes
    MODE_SELECT       = "select"
    MODE_PAN          = "pan"
    MODE_MEASURE      = "measure"
    MODE_DRAW_POINT   = "draw_point"
    MODE_DRAW_LINE    = "draw_line"
    MODE_DRAW_POLYGON = "draw_polygon"

    geometry_drawn = pyqtSignal(str, str)  # wkt (EPSG:3857), geom_type

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
        self.setMouseTracking(True)
        self._panning    = False
        self._pan_start  = QPointF()
        self._pan_mode   = False     # True = always-pan on LMB
        self._mode       = self.MODE_SELECT
        self._items: list[FeatureItem] = []
        self._selected_fid: int = -1
        self._measure_pts: list = []   # scene QPointF list
        self._measure_items: list = [] # QGraphicsItems for measure overlay

        # Draw state
        self._draw_pts: list[QPointF] = []
        self._draw_items: list = []
        self._draw_preview = None   # rubber-band line item

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

    # ── drawForeground: scale bar + north arrow overlays ─────────────────
    def drawForeground(self, painter: QPainter, rect: QRectF):
        import math
        vp = self.viewport().rect()
        w = vp.width()
        h = vp.height()

        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ── Scale bar ─────────────────────────────────────────────────────
        if getattr(self, "_show_scalebar", True):
            p0 = self.mapToScene(0, 0)
            p1 = self.mapToScene(100, 0)
            metres_per_100px = abs(p1.x() - p0.x())
            if metres_per_100px > 0:
                raw = metres_per_100px
                magnitude = 10 ** math.floor(math.log10(max(raw, 1e-9)))
                bar_m = magnitude
                for factor in (1, 2, 5, 10):
                    candidate = factor * magnitude
                    if candidate >= raw * 0.5:
                        bar_m = candidate
                        break
                bar_px = int(bar_m / metres_per_100px * 100)
                if 20 <= bar_px <= w * 0.4:
                    margin = 14
                    bx = int(w - bar_px - margin)
                    by = int(h - margin - 14)
                    painter.setPen(QPen(QColor("#000000AA"), 3))
                    painter.drawLine(bx, by, bx + bar_px, by)
                    painter.drawLine(bx, by - 4, bx, by + 4)
                    painter.drawLine(bx + bar_px, by - 4, bx + bar_px, by + 4)
                    painter.setPen(QPen(QColor("#FFFFFF"), 2))
                    painter.drawLine(bx, by, bx + bar_px, by)
                    painter.drawLine(bx, by - 4, bx, by + 4)
                    painter.drawLine(bx + bar_px, by - 4, bx + bar_px, by + 4)
                    label = (f"{int(bar_m)} m" if bar_m < 1000
                             else f"{bar_m/1000:.0f} km")
                    painter.setPen(QPen(QColor("#FFFFFF")))
                    painter.setFont(QFont("Arial", 9))
                    painter.drawText(bx, by - 6, label)

        # ── North arrow ───────────────────────────────────────────────────
        if getattr(self, "_show_north_arrow", True):
            cx, cy = 30, 50   # top-left area
            r = 18
            # shadow circle
            painter.setPen(QPen(QColor("#00000066"), 1))
            painter.setBrush(QBrush(QColor("#00000044")))
            painter.drawEllipse(cx - r - 2, cy - r - 2, (r + 2) * 2, (r + 2) * 2)
            # N (north, white)
            north = [
                QPointF(cx,     cy - r),
                QPointF(cx + 7, cy + r * 0.4),
                QPointF(cx,     cy),
            ]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#FFFFFF")))
            from PyQt6.QtGui import QPolygonF
            painter.drawPolygon(QPolygonF(north))
            # S (south, dark)
            south = [
                QPointF(cx,     cy + r),
                QPointF(cx - 7, cy - r * 0.4),
                QPointF(cx,     cy),
            ]
            painter.setBrush(QBrush(QColor("#444444")))
            painter.drawPolygon(QPolygonF(south))
            # "N" label
            painter.setPen(QPen(QColor("#FFFFFF")))
            painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            painter.drawText(cx - 4, cy - r - 3, "N")

        painter.restore()

    def contextMenuEvent(self, e):
        """Right-click on canvas → coordinate / zoom menu."""
        scene_pos = self.mapToScene(e.pos())
        try:
            import math
            R = 6378137.0
            lon = math.degrees(scene_pos.x() / R)
            lat = math.degrees(2 * math.atan(math.exp(-scene_pos.y() / R))
                               - math.pi / 2)
            coord_str = f"{lon:.6f}, {lat:.6f}"
        except Exception:
            coord_str = ""

        menu = QMenu(self)
        if coord_str:
            copy_act = menu.addAction(f"📋 Copy coordinates  ({coord_str})")
        else:
            copy_act = None
        menu.addSeparator()
        zi_act  = menu.addAction("＋ Zoom in here")
        zo_act  = menu.addAction("－ Zoom out here")
        fit_act = menu.addAction("⊞ Fit all layers")
        menu.addSeparator()
        sb_act  = menu.addAction("📐 Toggle scale bar")
        na_act  = menu.addAction("🧭 Toggle north arrow")

        act = menu.exec(e.globalPos())
        if act is None:
            return
        if copy_act and act == copy_act:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(coord_str)
        elif act == zi_act:
            self.centerOn(scene_pos)
            self.scale(2.0, 2.0)
        elif act == zo_act:
            self.centerOn(scene_pos)
            self.scale(0.5, 0.5)
        elif act == fit_act:
            self.fit_extent()
        elif act == sb_act:
            self._show_scalebar = not getattr(self, "_show_scalebar", True)
            self.viewport().update()
        elif act == na_act:
            self._show_north_arrow = not getattr(self, "_show_north_arrow", True)
            self.viewport().update()

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
        self._layer_items: dict[int, list] = {}   # list_idx → [FeatureItem]

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
            style = data.get("style") or _default_style(color or "#3498db")
            label_col = data.get("label_col")
            bucket: list = []
            for fid, feat in enumerate(features):
                raw, gtype, attrs = feat[0], feat[1], feat[2]
                try:
                    if isinstance(raw, QPainterPath):
                        path = raw      # WFS pre-parsed path
                    else:
                        path, gtype = _wkb_to_path(raw)
                    if path.isEmpty():
                        continue
                    fi = FeatureItem(path, gtype, fid, layer_color=color, style=style)
                    fi.setZValue(list_idx * 10 + (0 if "POLY" in gtype else 1))
                    self._scene.addItem(fi)
                    self._items.append(fi)
                    bucket.append(fi)
                    # Labels
                    if label_col and attrs and label_col in attrs:
                        val = attrs[label_col]
                        if val is not None:
                            self._add_label(path, str(val),
                                            style.get("stroke_color", "#333"),
                                            list_idx * 10 + 2)
                except Exception:
                    continue
            self._layer_items[list_idx] = bucket

        if self._scene.items():
            self.fit_extent()

    def _add_label(self, path: QPainterPath, text: str,
                   color: str, z: int):
        from PyQt6.QtWidgets import QGraphicsSimpleTextItem
        center = path.boundingRect().center()
        ti = QGraphicsSimpleTextItem(text)
        ti.setPos(center)
        ti.setZValue(z)
        f = QFont()
        f.setPointSize(7)
        ti.setFont(f)
        ti.setBrush(QBrush(QColor(color)))
        self._scene.addItem(ti)

    def zoom_to_layer(self, list_idx: int):
        """Fit view to the bounding rect of all features in a given layer slot."""
        bucket = getattr(self, "_layer_items", {}).get(list_idx, [])
        if not bucket:
            return
        r = bucket[0].boundingRect()
        for fi in bucket[1:]:
            r = r.united(fi.boundingRect())
        pad_x = max(r.width() * .05, 1)
        pad_y = max(r.height() * .05, 1)
        self.fitInView(r.adjusted(-pad_x, -pad_y, pad_x, pad_y),
                       Qt.AspectRatioMode.KeepAspectRatio)

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
        self.set_mode(self.MODE_PAN if enabled else self.MODE_SELECT)

    def wheelEvent(self, e: QWheelEvent):
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)
        self._schedule_wms()

    # ── coordinate helpers ────────────────────────────────────────────────
    @staticmethod
    def _merc_to_wgs84(x: float, y: float):
        import math
        R = 6378137.0
        lon = math.degrees(x / R)
        lat = math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)
        return lon, lat

    def set_mode(self, mode: str):
        self._mode = mode
        cursors = {
            self.MODE_SELECT:       Qt.CursorShape.ArrowCursor,
            self.MODE_PAN:          Qt.CursorShape.OpenHandCursor,
            self.MODE_MEASURE:      Qt.CursorShape.CrossCursor,
            self.MODE_DRAW_POINT:   Qt.CursorShape.CrossCursor,
            self.MODE_DRAW_LINE:    Qt.CursorShape.CrossCursor,
            self.MODE_DRAW_POLYGON: Qt.CursorShape.CrossCursor,
        }
        self.setCursor(cursors.get(mode, Qt.CursorShape.ArrowCursor))
        if mode != self.MODE_MEASURE:
            self._clear_measure()
        if mode not in (self.MODE_DRAW_POINT, self.MODE_DRAW_LINE,
                        self.MODE_DRAW_POLYGON):
            self._clear_draw()

    def _clear_measure(self):
        for item in self._measure_items:
            self._scene.removeItem(item)
        self._measure_items.clear()

    # ── Drawing helpers ───────────────────────────────────────────────────
    def _clear_draw(self):
        for item in self._draw_items:
            self._scene.removeItem(item)
        self._draw_items.clear()
        if self._draw_preview:
            self._scene.removeItem(self._draw_preview)
            self._draw_preview = None
        self._draw_pts.clear()

    def _update_draw_preview(self, cursor_pos: QPointF):
        if self._draw_preview:
            self._scene.removeItem(self._draw_preview)
            self._draw_preview = None
        if not self._draw_pts:
            return
        pts = self._draw_pts + [cursor_pos]
        if self._mode == self.MODE_DRAW_POLYGON and len(pts) >= 2:
            pts = pts + [pts[0]]   # close preview
        path = QPainterPath(pts[0])
        for p in pts[1:]:
            path.lineTo(p)
        pen = QPen(QColor("#f39c12"), 1.5, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._draw_preview = self._scene.addPath(path, pen)
        self._draw_preview.setZValue(10002)

    def _finish_draw(self):
        pts = self._draw_pts
        if not pts:
            return
        mode = self._mode

        def _coord(p: QPointF) -> str:
            return f"{p.x()} {-p.y()}"

        if mode == self.MODE_DRAW_POINT:
            wkt = f"POINT({_coord(pts[0])})"
            gtype = "Point"
        elif mode == self.MODE_DRAW_LINE:
            if len(pts) < 2:
                self._clear_draw(); return
            coords = ", ".join(_coord(p) for p in pts)
            wkt = f"LINESTRING({coords})"
            gtype = "LineString"
        else:  # polygon
            if len(pts) < 3:
                self._clear_draw(); return
            coords = ", ".join(_coord(p) for p in pts)
            coords += f", {_coord(pts[0])}"   # close
            wkt = f"POLYGON(({coords}))"
            gtype = "Polygon"

        self._clear_draw()
        self.geometry_drawn.emit(wkt, gtype)
        self._measure_pts.clear()

    def _draw_measure_overlay(self):
        """Redraw measure lines and labels."""
        for item in self._measure_items:
            self._scene.removeItem(item)
        self._measure_items.clear()
        pts = self._measure_pts
        if len(pts) < 2:
            return
        import math
        total = 0.0
        for i in range(1, len(pts)):
            p0, p1 = pts[i - 1], pts[i]
            # line
            line = self._scene.addLine(
                p0.x(), p0.y(), p1.x(), p1.y(),
                QPen(QColor("#f39c12"), 2))
            line.setZValue(9999)
            self._measure_items.append(line)
            # segment distance in metres (Merc units ≈ metres at equator)
            dx = p1.x() - p0.x()
            dy = p1.y() - p0.y()
            seg_m = math.hypot(dx, dy)
            total += seg_m
            mid_x = (p0.x() + p1.x()) / 2
            mid_y = (p0.y() + p1.y()) / 2
            lbl = self._scene.addSimpleText(
                f"{seg_m/1000:.3f} km" if seg_m > 1000 else f"{seg_m:.1f} m")
            lbl.setPos(mid_x, mid_y)
            lbl.setZValue(10000)
            lbl.setBrush(QBrush(QColor("#f39c12")))
            self._measure_items.append(lbl)
        # total label near last point
        last = pts[-1]
        if len(pts) > 2:
            tot_lbl = self._scene.addSimpleText(
                f"Total: {total/1000:.3f} km" if total > 1000
                else f"Total: {total:.1f} m")
            tot_lbl.setPos(last.x() + 5, last.y() - 20)
            tot_lbl.setZValue(10000)
            tot_lbl.setBrush(QBrush(QColor("#e74c3c")))
            self._measure_items.append(tot_lbl)

    def mousePressEvent(self, e: QMouseEvent):
        scene_pos = self.mapToScene(e.position().toPoint())

        # Draw modes
        if self._mode in (self.MODE_DRAW_POINT, self.MODE_DRAW_LINE,
                          self.MODE_DRAW_POLYGON):
            if e.button() == Qt.MouseButton.RightButton:
                self._finish_draw()
                e.accept(); return
            if e.button() == Qt.MouseButton.LeftButton:
                self._draw_pts.append(scene_pos)
                dot = self._scene.addEllipse(
                    scene_pos.x() - 4, scene_pos.y() - 4, 8, 8,
                    QPen(Qt.PenStyle.NoPen),
                    QBrush(QColor("#e74c3c")))
                dot.setZValue(10001)
                self._draw_items.append(dot)
                if self._mode == self.MODE_DRAW_POINT:
                    self._finish_draw()
                e.accept(); return

        # Measure mode
        if self._mode == self.MODE_MEASURE:
            if e.button() == Qt.MouseButton.LeftButton:
                self._measure_pts.append(scene_pos)
                self._draw_measure_overlay()
                # dot
                dot = self._scene.addEllipse(
                    scene_pos.x() - 4, scene_pos.y() - 4, 8, 8,
                    QPen(Qt.PenStyle.NoPen),
                    QBrush(QColor("#f39c12")))
                dot.setZValue(10001)
                self._measure_items.append(dot)
                self.measure_point.emit(scene_pos)
                e.accept()
                return
            elif e.button() == Qt.MouseButton.RightButton:
                self._clear_measure()
                e.accept()
                return

        want_pan = (
            e.button() == Qt.MouseButton.MiddleButton or
            (e.button() == Qt.MouseButton.LeftButton and
             bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)) or
            (e.button() == Qt.MouseButton.LeftButton and
             self._mode == self.MODE_PAN)
        )
        if want_pan:
            self._panning = True
            self._pan_start = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            hit = self._scene.itemAt(scene_pos, QTransform())
            if isinstance(hit, FeatureItem):
                self.feature_clicked.emit(hit.fid)
                self.feature_info.emit(hit.fid, e.globalPosition().toPoint())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent):
        # Always emit coordinates
        scene_pos = self.mapToScene(e.position().toPoint())
        try:
            lon, lat = self._merc_to_wgs84(scene_pos.x(), -scene_pos.y())
            self.coord_moved.emit(lon, lat)
        except Exception:
            pass

        if self._mode in (self.MODE_DRAW_LINE, self.MODE_DRAW_POLYGON):
            self._update_draw_preview(scene_pos)

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
            cur = (Qt.CursorShape.OpenHandCursor if self._mode == self.MODE_PAN
                   else Qt.CursorShape.ArrowCursor)
            self.setCursor(cur)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        if self._mode in (self.MODE_DRAW_LINE, self.MODE_DRAW_POLYGON):
            if e.button() == Qt.MouseButton.LeftButton:
                self._finish_draw()
                e.accept(); return
        super().mouseDoubleClickEvent(e)

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

        # ── Quick filter bar ─────────────────────────────────────────────
        filter_row = QWidget()
        fr_lay = QHBoxLayout(filter_row)
        fr_lay.setContentsMargins(2, 0, 2, 2)
        fr_lay.setSpacing(4)
        self._filter_box = QLineEdit()
        self._filter_box.setPlaceholderText("🔍 Quick filter…  (searches all columns)")
        self._filter_box.setFixedHeight(22)
        self._filter_box.textChanged.connect(self._apply_quick_filter)
        self._filter_clear = QPushButton("✕")
        self._filter_clear.setFixedSize(22, 22)
        self._filter_clear.setToolTip("Clear filter")
        self._filter_clear.clicked.connect(lambda: self._filter_box.clear())
        self._filter_match_lbl = QLabel()
        self._filter_match_lbl.setStyleSheet("font-size:11px;color:#888;")
        fr_lay.addWidget(self._filter_box, 1)
        fr_lay.addWidget(self._filter_clear)
        fr_lay.addWidget(self._filter_match_lbl)
        layout.addWidget(filter_row)

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

        # Right-click context menu on cells
        self.table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # Right-click on column header → stats
        self.table.horizontalHeader().setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(
            self._show_column_stats)

        layout.addWidget(self.table)

        # Edit toolbar
        edit_bar = QWidget()
        eb_lay = QHBoxLayout(edit_bar)
        eb_lay.setContentsMargins(2, 0, 2, 2)
        eb_lay.setSpacing(4)
        self._edit_toggle = QPushButton("✏️ Edit mode OFF")
        self._edit_toggle.setCheckable(True)
        self._edit_toggle.setFixedHeight(22)
        self._edit_toggle.setToolTip(
            "Toggle edit mode — double-click a cell to edit, changes save to DB")
        self._edit_toggle.toggled.connect(self._on_edit_mode)
        eb_lay.addWidget(self._edit_toggle)
        self._edit_status = QLabel()
        self._edit_status.setStyleSheet("font-size:11px;color:#888;")
        eb_lay.addWidget(self._edit_status, 1)
        layout.addWidget(edit_bar)

        # Bottom status: row count
        self._row_lbl = QLabel()
        self._row_lbl.setStyleSheet("font-size:11px;color:#666;padding:2px 4px;")
        layout.addWidget(self._row_lbl)

        # Edit context (set externally)
        self._db = None
        self._edit_schema = ""
        self._edit_table = ""
        self._edit_pkey: list[str] = []
        self._edit_cols: list[str] = []
        self.table.itemChanged.connect(self._on_cell_changed)

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

    def _apply_quick_filter(self, text: str):
        query = text.strip().lower()
        rows = self.table.rowCount()
        cols = self.table.columnCount()
        visible = 0
        for r in range(rows):
            show = False
            if not query:
                show = True
            else:
                for c in range(cols):
                    cell = self.table.item(r, c)
                    if cell and query in cell.text().lower():
                        show = True
                        break
            self.table.setRowHidden(r, not show)
            if show:
                visible += 1
        if query:
            self._filter_match_lbl.setText(f"{visible}/{rows}")
        else:
            self._filter_match_lbl.setText("")

    def _show_column_stats(self, pos):
        col = self.table.horizontalHeader().logicalIndexAt(pos)
        if col < 0:
            return
        header = self.table.horizontalHeaderItem(col)
        col_name = header.text() if header else f"col {col}"
        rows = self.table.rowCount()
        values = []
        null_count = 0
        for r in range(rows):
            if self.table.isRowHidden(r):
                continue
            cell = self.table.item(r, col)
            if not cell or cell.text() in ("", "None", "NULL"):
                null_count += 1
            else:
                try:
                    values.append(float(cell.text()))
                except ValueError:
                    values.append(cell.text())

        # Build stats
        numeric = [v for v in values if isinstance(v, float)]
        text_vals = [v for v in values if isinstance(v, str)]

        lines = [f"<b>Column:</b> {col_name}",
                 f"<b>Total rows:</b> {rows}",
                 f"<b>Null / empty:</b> {null_count}",
                 f"<b>Non-null:</b> {len(values)}"]
        if numeric:
            lines += [
                f"<b>Min:</b> {min(numeric):.4g}",
                f"<b>Max:</b> {max(numeric):.4g}",
                f"<b>Sum:</b> {sum(numeric):.4g}",
                f"<b>Average:</b> {sum(numeric)/len(numeric):.4g}",
            ]
        if text_vals:
            unique = len(set(text_vals))
            lines.append(f"<b>Unique text values:</b> {unique}")

        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle(f"Statistics — {col_name}")
        msg.setText("<br>".join(lines))
        msg.setIcon(QMessageBox.Icon.Information)
        msg.exec()

    def set_edit_context(self, db, schema: str, table: str,
                         pkey_cols: list, all_cols: list):
        """Called by MapViewerPanel when layer selection changes."""
        self._db = db
        self._edit_schema = schema
        self._edit_table = table
        self._edit_pkey = pkey_cols
        self._edit_cols = all_cols
        has_pkey = bool(pkey_cols)
        self._edit_toggle.setEnabled(has_pkey)
        tip = (f"Primary key: {', '.join(pkey_cols)}" if has_pkey
               else "No primary key detected — editing disabled")
        self._edit_toggle.setToolTip(tip)
        if not has_pkey and self._edit_toggle.isChecked():
            self._edit_toggle.setChecked(False)

    def _on_edit_mode(self, enabled: bool):
        if enabled:
            self._edit_toggle.setText("✏️ Edit mode ON")
            self._edit_toggle.setStyleSheet(
                "QPushButton{background:#c0392b;color:#fff;border:none;"
                "border-radius:3px;padding:0 6px;}")
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked |
                QAbstractItemView.EditTrigger.SelectedClicked)
            self._edit_status.setText("Double-click a cell to edit")
        else:
            self._edit_toggle.setText("✏️ Edit mode OFF")
            self._edit_toggle.setStyleSheet("")
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.NoEditTriggers)
            self._edit_status.setText("")

    def _on_cell_changed(self, item: QTableWidgetItem):
        if not self._edit_toggle.isChecked():
            return
        if not self._db or not self._db.is_connected():
            return
        if not self._edit_pkey or not self._edit_cols:
            return
        row = item.row()
        col_idx = item.column()
        if col_idx >= len(self._edit_cols):
            return
        col_name = self._edit_cols[col_idx]
        if col_name in self._edit_pkey:
            self._edit_status.setText("⚠ Cannot edit primary key column")
            return
        new_val = item.text()
        # Build WHERE from pkey columns
        where_parts = []
        params = []
        for pk in self._edit_pkey:
            pk_idx = self._edit_cols.index(pk) if pk in self._edit_cols else -1
            if pk_idx < 0:
                return
            pk_cell = self.table.item(row, pk_idx)
            pk_val = pk_cell.text() if pk_cell else None
            where_parts.append(f'"{pk}" = %s')
            params.append(pk_val)
        params_val = [new_val if new_val != "" else None] + params
        sql = (f'UPDATE "{self._edit_schema}"."{self._edit_table}" '
               f'SET "{col_name}" = %s '
               f'WHERE {" AND ".join(where_parts)}')
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur = conn.cursor()
            cur.execute(sql, params_val)
            conn.commit()
            cur.close()
            conn.close()
            self._edit_status.setText(
                f"✓ Updated {col_name} at row {row + 1}")
        except Exception as e:
            self._edit_status.setText(f"✗ Error: {e}")

    def set_layer_title(self, title: str):
        self.setWindowTitle(f"Attributes — {title}")
        self._lbl.setText(title)

    def update_row_count(self, n: int):
        self._row_lbl.setText(f"{n} row{'s' if n != 1 else ''}")

    def closeEvent(self, e):
        # Hide instead of destroy so it can be re-shown
        e.ignore()
        self.hide()


# ── Label dialog ──────────────────────────────────────────────────────────

class _LabelDialog(QDialog):
    def __init__(self, columns: list, current: str | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Layer Labels")
        self.setMinimumWidth(280)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Show attribute as label on map:"))
        self._combo = QComboBox()
        self._combo.addItem("(none)", None)
        for col in columns:
            self._combo.addItem(col, col)
        if current:
            idx = self._combo.findData(current)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
        lay.addWidget(self._combo)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def selected_col(self):
        return self._combo.currentData()


# ── Filter dialog ─────────────────────────────────────────────────────────

class _FilterDialog(QDialog):
    def __init__(self, columns: list, current_expr: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Attribute Filter")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("SQL WHERE clause (without the word WHERE):"))
        self._expr = QLineEdit(current_expr)
        self._expr.setPlaceholderText('e.g.  population > 100000  or  name ILIKE \'%tbilisi%\'')
        lay.addWidget(self._expr)

        if columns:
            lay.addWidget(QLabel("Available columns (click to insert):"))
            col_row = QWidget()
            fl = QHBoxLayout(col_row)
            fl.setContentsMargins(0, 0, 0, 0)
            for col in columns[:12]:
                btn = QPushButton(col)
                btn.setFixedHeight(20)
                btn.setStyleSheet("font-size:10px;padding:0 4px;")
                btn.clicked.connect(lambda _=False, c=col: self._insert(c))
                fl.addWidget(btn)
            fl.addStretch()
            lay.addWidget(col_row)

        self._warn = QLabel()
        self._warn.setStyleSheet("color:#e74c3c;font-size:11px;")
        lay.addWidget(self._warn)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._validate)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _insert(self, col: str):
        pos = self._expr.cursorPosition()
        txt = self._expr.text()
        self._expr.setText(txt[:pos] + f'"{col}"' + txt[pos:])

    def _validate(self):
        expr = self._expr.text().strip()
        forbidden = ["drop ", "delete ", "truncate ", "insert ", "update ", "alter "]
        low = expr.lower()
        for word in forbidden:
            if word in low:
                self._warn.setText(f"Dangerous keyword detected: '{word.strip()}'")
                return
        self.accept()

    def expression(self) -> str:
        return self._expr.text().strip()


# ── Save Geometry Dialog ──────────────────────────────────────────────────
class _SaveGeometryDialog(QDialog):
    """Ask user for attribute values and INSERT drawn geometry into PostGIS."""

    def __init__(self, schema, table, geom_col, srid, wkt_3857, gtype, db, parent=None):
        super().__init__(parent)
        self._schema   = schema
        self._table    = table
        self._geom_col = geom_col
        self._srid     = srid
        self._wkt      = wkt_3857
        self._gtype    = gtype
        self._db       = db
        self.setWindowTitle(f"Save {gtype} → {schema}.{table}")
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.addWidget(QLabel(
            f"<b>{self._gtype}</b> will be inserted into "
            f"<code>{self._schema}.{self._table}.{self._geom_col}</code> "
            f"(EPSG:{self._srid})"
        ))

        # Fetch non-geometry columns to let user fill in
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute("""
                SELECT column_name, udt_name, column_default, is_nullable
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s
                ORDER BY ordinal_position
            """, (self._schema, self._table))
            self._col_info = [
                r for r in cur.fetchall()
                if r[0] != self._geom_col
                   and "serial" not in (r[2] or "")
                   and r[1] not in ("geometry","geography","raster")
            ]
            conn.close()
        except Exception:
            self._col_info = []

        form = QFormLayout()
        self._fields: dict[str, QLineEdit] = {}
        for col, typ, default, nullable in self._col_info[:12]:
            le = QLineEdit()
            le.setPlaceholderText(f"{typ}" + (" (optional)" if nullable == "YES" else ""))
            form.addRow(f"{col}:", le)
            self._fields[col] = le
        root.addLayout(form)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _save(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            extra_cols = [c for c in self._fields if self._fields[c].text().strip()]
            col_list   = [self._geom_col] + extra_cols
            placeholders = [f"ST_Transform(ST_GeomFromText(%s, 3857), {self._srid})"] + ["%s"] * len(extra_cols)
            values = [self._wkt] + [self._fields[c].text().strip() for c in extra_cols]
            sql = (
                f'INSERT INTO "{self._schema}"."{self._table}" '
                f'({", ".join(chr(34)+c+chr(34) for c in col_list)}) '
                f'VALUES ({", ".join(placeholders)})'
            )
            cur.execute(sql, values)
            conn.commit()
            conn.close()
            self.accept()
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Insert error", str(e))


# ── Main panel ────────────────────────────────────────────────────────────
class MapViewerPanel(QWidget):
    project_changed = pyqtSignal()   # emitted when layer stack changes

    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db

        # Available rows from geometry_columns (refreshed via LayerListWorker)
        self._available_rows: list = []

        # Per-layer feature data: layer_key → list of (wkb, gtype, attrs)
        self._layers_data: dict[str, list] = {}

        # Per-layer column names: layer_key → list[str]
        self._layers_columns: dict[str, list] = {}

        # Per-layer primary key columns: layer_key → list[str]
        self._layer_pkeys: dict[str, list] = {}

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

        self._act_measure = QAction("📏 Measure", self)
        self._act_measure.setToolTip(
            "Measure distance — left-click to add points, right-click to reset")
        self._act_measure.setCheckable(True)
        self._act_measure.toggled.connect(self._on_measure_toggled)
        tb.addAction(self._act_measure)

        tb.addSeparator()

        # Draw tools
        self._act_draw_pt = QAction("📍 Point", self)
        self._act_draw_pt.setToolTip("Draw point — click to place, saves to DB")
        self._act_draw_pt.setCheckable(True)
        self._act_draw_pt.toggled.connect(lambda c: self._set_draw_mode(
            MapCanvas.MODE_DRAW_POINT if c else MapCanvas.MODE_SELECT,
            self._act_draw_pt))
        tb.addAction(self._act_draw_pt)

        self._act_draw_ln = QAction("〰 Line", self)
        self._act_draw_ln.setToolTip("Draw line — click vertices, double-click/right-click to finish")
        self._act_draw_ln.setCheckable(True)
        self._act_draw_ln.toggled.connect(lambda c: self._set_draw_mode(
            MapCanvas.MODE_DRAW_LINE if c else MapCanvas.MODE_SELECT,
            self._act_draw_ln))
        tb.addAction(self._act_draw_ln)

        self._act_draw_pg = QAction("⬡ Polygon", self)
        self._act_draw_pg.setToolTip("Draw polygon — click vertices, double-click/right-click to finish")
        self._act_draw_pg.setCheckable(True)
        self._act_draw_pg.toggled.connect(lambda c: self._set_draw_mode(
            MapCanvas.MODE_DRAW_POLYGON if c else MapCanvas.MODE_SELECT,
            self._act_draw_pg))
        tb.addAction(self._act_draw_pg)

        self._draw_acts = [self._act_draw_pt, self._act_draw_ln, self._act_draw_pg]

        tb.addSeparator()

        self._act_export = QAction("🖼 Export PNG", self)
        self._act_export.setToolTip("Save current map view as PNG image")
        self._act_export.triggered.connect(self._export_map_image)
        tb.addAction(self._act_export)

        tb.addSeparator()

        # Search box (inline in toolbar via QWidget)
        search_w = QWidget()
        search_lay = QHBoxLayout(search_w)
        search_lay.setContentsMargins(2, 0, 2, 0)
        search_lay.setSpacing(2)
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("🔍 Find feature…")
        self._search_box.setFixedWidth(160)
        self._search_box.setFixedHeight(24)
        self._search_box.returnPressed.connect(self._find_feature)
        search_lay.addWidget(self._search_box)
        tb.addWidget(search_w)

        # Bookmarks button with dropdown
        self._act_bm = QAction("🔖 Bookmarks", self)
        self._act_bm.setToolTip("Save or jump to named map extents")
        self._act_bm.triggered.connect(self._show_bookmark_menu)
        tb.addAction(self._act_bm)

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
        self._btn_style = QPushButton("🎨 Style")
        self._btn_style.setFixedHeight(24)
        self._btn_style.setStyleSheet(_btn)
        self._btn_style.setToolTip("Edit style of selected layer")
        self._btn_style.clicked.connect(self._open_style_dialog)
        header_row.addWidget(self._btn_style)
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
        self._layer_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._layer_list.customContextMenuRequested.connect(self._layer_context_menu)
        self._layer_list.itemDoubleClicked.connect(self._zoom_to_current_layer)
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
        self._canvas.feature_info.connect(self._on_feature_info)
        self._canvas.coord_moved.connect(self._on_coord_moved)
        self._canvas.geometry_drawn.connect(self._on_geometry_drawn)
        h_splitter.addWidget(self._canvas)
        h_splitter.setSizes([210, 800])

        root.addWidget(h_splitter)

        # ── Floating attribute table window (created lazily) ──────────────
        self._attr_win: Optional[AttributeTableWindow] = None

        # ── Bottom bar: status + coordinates ─────────────────────────────
        bottom_bar = QWidget()
        bb_lay = QHBoxLayout(bottom_bar)
        bb_lay.setContentsMargins(0, 0, 0, 0)
        bb_lay.setSpacing(4)

        self._status = QLabel()
        self._status.setStyleSheet(
            "padding: 2px 8px; font-size: 12px; color: #888;")
        self._status.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bb_lay.addWidget(self._status, 1)

        self._coord_lbl = QLabel("Lon: — , Lat: —")
        self._coord_lbl.setStyleSheet(
            "padding: 2px 8px; font-size: 11px; color: #555; "
            "font-family: monospace;")
        self._coord_lbl.setFixedWidth(230)
        self._coord_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bb_lay.addWidget(self._coord_lbl)

        root.addWidget(bottom_bar)

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
        if checked and self._act_measure.isChecked():
            self._act_measure.setChecked(False)
        self._canvas.set_pan_mode(checked)

    def _on_measure_toggled(self, checked: bool):
        if checked:
            if self._act_pan.isChecked():
                self._act_pan.setChecked(False)
            self._canvas.set_mode(MapCanvas.MODE_MEASURE)
            self._status.setText("📏 Measure: left-click to add points, right-click to reset")
        else:
            self._canvas.set_mode(MapCanvas.MODE_SELECT)
            self._status.setText("")

    def _set_draw_mode(self, mode: str, active_act):
        """Switch draw mode, uncheck other draw actions."""
        for act in self._draw_acts:
            if act is not active_act and act.isChecked():
                act.blockSignals(True)
                act.setChecked(False)
                act.blockSignals(False)
        if self._act_pan.isChecked():
            self._act_pan.setChecked(False)
        if self._act_measure.isChecked():
            self._act_measure.setChecked(False)
        self._canvas.set_mode(mode)
        hints = {
            MapCanvas.MODE_DRAW_POINT:   "📍 Draw Point: click to place",
            MapCanvas.MODE_DRAW_LINE:    "〰 Draw Line: click vertices, double-click/right-click to finish",
            MapCanvas.MODE_DRAW_POLYGON: "⬡ Draw Polygon: click vertices, double-click/right-click to finish",
            MapCanvas.MODE_SELECT:       "",
        }
        self._status.setText(hints.get(mode, ""))

    def _on_geometry_drawn(self, wkt: str, gtype: str):
        """Called when user finishes drawing a geometry. Prompt to save."""
        # uncheck draw buttons
        for act in self._draw_acts:
            act.blockSignals(True); act.setChecked(False); act.blockSignals(False)
        self._canvas.set_mode(MapCanvas.MODE_SELECT)
        self._status.setText("")

        # find current layer
        item = self._layer_list.currentItem()
        if not item:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Draw", "Select a layer first.")
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(d, dict) or "schema" not in d:
            return

        dlg = _SaveGeometryDialog(
            d["schema"], d["table"], d["geom_col"],
            d.get("srid", 4326), wkt, gtype, self._db, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            item = self._layer_list.currentItem()
            if item:
                self._load_layer_data(item)

    def _on_coord_moved(self, lon: float, lat: float):
        self._coord_lbl.setText(f"Lon: {lon:.5f}  Lat: {lat:.5f}")

    def _on_feature_info(self, fid: int, screen_pt):
        """Show a small popup tooltip with feature attributes."""
        item = self._layer_list.currentItem()
        if not item:
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(d, dict) or "schema" not in d:
            return
        key = self._layer_key(d["schema"], d["table"], d["geom_col"])
        data = self._layers_data.get(key, [])
        cols = self._layers_columns.get(key, [])
        if fid >= len(data):
            return
        _wkb, _gtype, attrs = data[fid]
        if not attrs or not cols:
            return
        lines = []
        for col in cols[:8]:
            val = attrs.get(col, "")
            if val is None:
                val = ""
            lines.append(f"<b>{col}:</b> {val}")
        html = "<br>".join(lines)
        from PyQt6.QtWidgets import QToolTip
        QToolTip.showText(screen_pt, f"<div style='font-size:12px'>{html}</div>",
                          self._canvas)

    def _export_map_image(self):
        from PyQt6.QtWidgets import QFileDialog, QInputDialog
        path, sel_filter = QFileDialog.getSaveFileName(
            self, "Export Map", "map_export.png",
            "PNG Image (*.png);;JPEG Image (*.jpg);;PDF Print Layout (*.pdf)")
        if not path:
            return
        ext = path.lower().rsplit(".", 1)[-1]
        vp = self._canvas.viewport()

        if ext == "pdf":
            self._export_print_layout(path)
        else:
            img = QImage(vp.size(), QImage.Format.Format_ARGB32)
            img.fill(QColor("#1A1F2E"))
            painter = QPainter(img)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._canvas.render(painter)
            painter.end()
            img.save(path)
            self._status.setText(f"✓ Exported: {path}")

    def _export_print_layout(self, path: str):
        """Full print layout: title + map + legend + scale bar."""
        try:
            from PyQt6.QtPrintSupport import QPrinter
        except ImportError:
            self._status.setText("PDF export needs PyQt6.QtPrintSupport")
            return

        from PyQt6.QtWidgets import QInputDialog
        title, ok = QInputDialog.getText(
            self, "Map Title", "Title for the print layout:",
            text="Map Export")
        if not ok:
            title = "Map Export"

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        from PyQt6.QtGui import QPageSize
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(
            printer.pageLayout().orientation().Landscape
            if self._canvas.width() > self._canvas.height()
            else printer.pageLayout().orientation().Portrait)

        p = QPainter(printer)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        page = printer.pageRect(QPrinter.Unit.DevicePixel)
        pw = page.width()
        ph = page.height()
        margin = pw * 0.03

        # ── Title ─────────────────────────────────────────────────────────
        title_h = ph * 0.06
        p.setPen(QPen(QColor("#111111")))
        f = QFont("Arial", 18, QFont.Weight.Bold)
        p.setFont(f)
        from PyQt6.QtCore import QRectF as _QRF
        p.drawText(_QRF(margin, margin, pw - margin * 2, title_h),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   title)

        # ── Map area ──────────────────────────────────────────────────────
        legend_w = pw * 0.18
        map_x = margin
        map_y = margin + title_h + margin * 0.5
        map_w = pw - margin * 2 - legend_w - margin
        map_h = ph - map_y - margin * 3

        # render canvas viewport into this rect
        vp_size = self._canvas.viewport().size()
        map_img = QImage(vp_size, QImage.Format.Format_ARGB32)
        map_img.fill(QColor("#1A1F2E"))
        tmp = QPainter(map_img)
        tmp.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._canvas.render(tmp)
        tmp.end()
        p.drawImage(_QRF(map_x, map_y, map_w, map_h), map_img,
                    _QRF(0, 0, map_img.width(), map_img.height()))
        # border
        p.setPen(QPen(QColor("#888888"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(_QRF(map_x, map_y, map_w, map_h))

        # ── Legend ────────────────────────────────────────────────────────
        lx = map_x + map_w + margin
        ly = map_y
        p.setPen(QPen(QColor("#111111")))
        p.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        p.drawText(_QRF(lx, ly, legend_w, 20),
                   Qt.AlignmentFlag.AlignLeft, "Legend")
        ly += 24
        p.setFont(QFont("Arial", 8))
        dot_r = 7
        for i in range(self._layer_list.count()):
            item = self._layer_list.item(i)
            d = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(d, dict) or d.get("type") == "basemap":
                continue
            color = d.get("color", "#3498db")
            p.setBrush(QBrush(QColor(color)))
            p.setPen(QPen(QColor(color).darker(130), 1))
            p.drawEllipse(int(lx), int(ly), dot_r * 2, dot_r * 2)
            p.setPen(QPen(QColor("#111111")))
            lbl = f"{d.get('schema','')}.{d.get('table','')}"
            p.drawText(int(lx + dot_r * 2 + 4), int(ly + dot_r + 4), lbl)
            ly += dot_r * 2 + 6
            if ly > map_y + map_h:
                break

        # ── Scale text at bottom ──────────────────────────────────────────
        p.setFont(QFont("Arial", 8))
        p.setPen(QPen(QColor("#555555")))
        import datetime
        footer = (f"Generated by PostGIS Manager  •  "
                  f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
        p.drawText(_QRF(margin, ph - margin * 2, pw - margin * 2, margin * 2),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   footer)
        p.end()
        self._status.setText(f"✓ Print layout exported: {path}")

    # ── Search / Find feature ─────────────────────────────────────────────
    def _find_feature(self):
        query = self._search_box.text().strip().lower()
        if not query:
            return
        item = self._layer_list.currentItem()
        if not item:
            self._status.setText("Select a layer first.")
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(d, dict) or "schema" not in d:
            return
        key = self._layer_key(d["schema"], d["table"], d["geom_col"])
        data = self._layers_data.get(key, [])
        cols = self._layers_columns.get(key, [])
        for fid, (_wkb, _gtype, attrs) in enumerate(data):
            for col in cols:
                val = attrs.get(col)
                if val is not None and query in str(val).lower():
                    self._canvas.select_feature(fid)
                    self._on_feature_clicked(fid)
                    self._status.setText(
                        f"✓ Found '{query}' in {col} (feature {fid})")
                    return
        self._status.setText(f"'{query}' not found in current layer.")

    # ── Bookmarks ─────────────────────────────────────────────────────────
    def _bookmarks(self) -> dict:
        from PyQt6.QtCore import QSettings
        raw = QSettings("PostGISManager", "mapbookmarks").value(
            "bookmarks", {})
        return raw if isinstance(raw, dict) else {}

    def _save_bookmarks(self, bm: dict):
        from PyQt6.QtCore import QSettings
        QSettings("PostGISManager", "mapbookmarks").setValue("bookmarks", bm)

    def _current_extent(self) -> dict:
        vp = self._canvas.viewport().rect()
        tl = self._canvas.mapToScene(vp.topLeft())
        br = self._canvas.mapToScene(vp.bottomRight())
        return {"x": tl.x(), "y": tl.y(),
                "w": br.x() - tl.x(), "h": br.y() - tl.y()}

    def _show_bookmark_menu(self):
        bm = self._bookmarks()
        menu = QMenu(self)
        save_act = menu.addAction("💾 Save current extent…")
        if bm:
            menu.addSeparator()
            for name, ext in bm.items():
                act = menu.addAction(f"📍 {name}")
                act.setData(("goto", name, ext))
            menu.addSeparator()
            del_menu = menu.addMenu("🗑 Delete bookmark")
            for name in bm:
                da = del_menu.addAction(name)
                da.setData(("del", name))

        btn_widget = self.findChild(QWidget, "bm_btn")
        pos = self.mapToGlobal(
            self._canvas.rect().topRight())
        act = menu.exec(pos)
        if act is None:
            return
        data = act.data()
        if act == save_act:
            from PyQt6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(
                self, "Save Bookmark", "Bookmark name:")
            if ok and name.strip():
                bm[name.strip()] = self._current_extent()
                self._save_bookmarks(bm)
                self._status.setText(f"Bookmark '{name.strip()}' saved.")
        elif isinstance(data, tuple):
            if data[0] == "goto":
                ext = data[2]
                from PyQt6.QtCore import QRectF
                r = QRectF(ext["x"], ext["y"], ext["w"], ext["h"])
                self._canvas.fitInView(
                    r, Qt.AspectRatioMode.KeepAspectRatio)
            elif data[0] == "del":
                bm.pop(data[1], None)
                self._save_bookmarks(bm)
                self._status.setText(f"Bookmark '{data[1]}' deleted.")

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
            "srid": 3857, "color": color, "style": _default_style(color),
            "features": [], "key": key,
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
        style = _default_style(color)
        lw_item.setCheckState(Qt.CheckState.Checked)
        lw_item.setIcon(_make_dot_icon(color))
        lw_item.setData(Qt.ItemDataRole.UserRole, {
            "schema":   schema,
            "table":    table,
            "geom_col": geom_col,
            "srid":     srid,
            "color":    color,
            "style":    style,
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
        self.project_changed.emit()

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
        self.project_changed.emit()

    def _layer_context_menu(self, pos):
        item = self._layer_list.itemAt(pos)
        if not item:
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(d, dict) or d.get("type") == "basemap":
            return
        menu = QMenu(self)
        zoom_act   = menu.addAction("🔍 Zoom to Layer")
        style_act  = menu.addAction("🎨 Style…")
        label_act  = menu.addAction("🏷 Labels…")
        filter_act = menu.addAction("🔎 Filter…")
        menu.addSeparator()
        remove_act = menu.addAction("✕ Remove layer")
        act = menu.exec(self._layer_list.viewport().mapToGlobal(pos))
        self._layer_list.setCurrentItem(item)
        if act == zoom_act:
            self._zoom_to_current_layer(item)
        elif act == style_act:
            self._open_style_dialog()
        elif act == label_act:
            self._open_label_dialog()
        elif act == filter_act:
            self._open_filter_dialog()
        elif act == remove_act:
            self._on_remove_layer()

    def _zoom_to_current_layer(self, item=None):
        if item is None:
            item = self._layer_list.currentItem()
        if not item:
            return
        idx = self._layer_list.row(item)
        self._canvas.zoom_to_layer(idx)

    def _open_style_dialog(self):
        item = self._layer_list.currentItem()
        if not item:
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(d, dict) or d.get("type") == "basemap":
            return
        current_style = d.get("style") or _default_style(d.get("color", "#3498db"))
        layer_name = f"{d.get('schema','')}.{d.get('table','')}"
        dlg = LayerStyleDialog(current_style, layer_name, parent=self)

        def _on_style_changed(new_style: dict):
            d["style"] = new_style
            d["color"] = new_style["stroke_color"]
            item.setData(Qt.ItemDataRole.UserRole, d)
            item.setIcon(_make_dot_icon(new_style["fill_color"]))
            self._canvas.draw_all(self._layer_list)
            self.project_changed.emit()

        dlg.style_changed.connect(_on_style_changed)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            _on_style_changed(dlg.get_style())

    def _open_label_dialog(self):
        item = self._layer_list.currentItem()
        if not item:
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(d, dict) or d.get("type") == "basemap":
            return
        key = self._layer_key(d["schema"], d["table"], d["geom_col"])
        cols = self._layers_columns.get(key, [])
        dlg = _LabelDialog(cols, d.get("label_col"), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            col = dlg.selected_col()
            d["label_col"] = col
            item.setData(Qt.ItemDataRole.UserRole, d)
            tip_parts = []
            if d.get("filter_expr"):
                tip_parts.append(f"Filter: {d['filter_expr']}")
            if col:
                tip_parts.append(f"Label: {col}")
            item.setToolTip("\n".join(tip_parts))
            self._canvas.draw_all(self._layer_list)

    def _open_filter_dialog(self):
        item = self._layer_list.currentItem()
        if not item:
            return
        d = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(d, dict) or d.get("type") == "basemap":
            return
        key = self._layer_key(d["schema"], d["table"], d["geom_col"])
        cols = self._layers_columns.get(key, [])
        dlg = _FilterDialog(cols, d.get("filter_expr", ""), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            expr = dlg.expression()
            d["filter_expr"] = expr
            item.setData(Qt.ItemDataRole.UserRole, d)
            # Update tooltip to show active filter
            label = f"{d.get('schema','')}.{d.get('table','')}"
            if d.get("geom_col"):
                label += f" [{d['geom_col']}]"
            if expr:
                item.setText(f"🔎 {label}")
                item.setToolTip(f"Filter: {expr}")
            else:
                item.setText(label)
                item.setToolTip("")
            self._load_layer_data(item)

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
                # Pass edit context (detect primary key lazily)
                pkey = self._layer_pkeys.get(key, [])
                self._attr_win.set_edit_context(
                    self.db, d["schema"], d["table"], pkey, cols)

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
        # Load primary key for this layer if not cached
        if current and self.db.is_connected():
            d = current.data(Qt.ItemDataRole.UserRole)
            if isinstance(d, dict) and "schema" in d and "table" in d:
                key = self._layer_key(d["schema"], d["table"],
                                      d.get("geom_col", ""))
                if key not in self._layer_pkeys:
                    self._fetch_pkey(d["schema"], d["table"], key)

    def _fetch_pkey(self, schema: str, table: str, key: str):
        """Fetch primary key columns from DB in background."""
        class _PKWorker(QThread):
            done = pyqtSignal(str, list)
            def __init__(self, db, schema, table, key):
                super().__init__()
                self._db = db; self._s = schema
                self._t = table; self._key = key
            def run(self):
                try:
                    import psycopg2
                    conn = psycopg2.connect(**self._db.params)
                    cur = conn.cursor()
                    cur.execute("""
                        SELECT kcu.column_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                          ON tc.constraint_name = kcu.constraint_name
                          AND tc.table_schema = kcu.table_schema
                        WHERE tc.constraint_type = 'PRIMARY KEY'
                          AND tc.table_schema = %s
                          AND tc.table_name = %s
                        ORDER BY kcu.ordinal_position
                    """, (self._s, self._t))
                    pkeys = [r[0] for r in cur.fetchall()]
                    cur.close(); conn.close()
                    self.done.emit(self._key, pkeys)
                except Exception:
                    self.done.emit(self._key, [])
        w = _PKWorker(self.db, schema, table, key)
        w.done.connect(self._on_pkey_loaded)
        w.start()
        self._pkey_worker = w   # keep reference

    def _on_pkey_loaded(self, key: str, pkeys: list):
        self._layer_pkeys[key] = pkeys
        # Update attr win if this layer is still selected
        item = self._layer_list.currentItem()
        if item and self._attr_win:
            d = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(d, dict) and "schema" in d:
                k = self._layer_key(d["schema"], d["table"],
                                    d.get("geom_col", ""))
                if k == key:
                    cols = self._layers_columns.get(key, [])
                    self._attr_win.set_edit_context(
                        self.db, d["schema"], d["table"], pkeys, cols)

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
            if not isinstance(d, dict) or "schema" not in d:
                continue
            if (d["schema"] == schema and d["table"] == table and
                    (not geom_col or d.get("geom_col") == geom_col)):
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

    # ── SQL → Map ─────────────────────────────────────────────────────────

    def add_query_result(self, features: list, attr_cols: list, label: str) -> None:
        """Add SQL query result geometries as a temporary layer.

        features: list of (geojson_str, attrs_dict) from GeoQueryWorker
        """
        import json as _json

        # Remove previous SQL result layer if exists
        for i in range(self._layer_list.count()):
            item = self._layer_list.item(i)
            d = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(d, dict) and d.get("type") == "sql_result":
                key = d.get("key", "")
                self._layers_data.pop(key, None)
                self._layers_columns.pop(key, None)
                self._layer_list.takeItem(i)
                break

        if not features:
            self._status.setText("No geometry features in result.")
            return

        # Parse GeoJSON → QPainterPath
        parsed = []
        for geojson_str, attrs in features:
            if not geojson_str:
                continue
            try:
                geom = _json.loads(geojson_str)
                path, gtype = _geojson_to_path(geom)
                parsed.append((path, gtype, attrs))
            except Exception:
                continue

        if not parsed:
            self._status.setText("Could not parse geometry from result.")
            return

        color   = "#f39c12"   # orange — distinct from regular PostGIS layers
        key     = "sql_result::query"
        short   = label[:40] + ("…" if len(label) > 40 else "")
        lw_item = QListWidgetItem(f"⚡ {short}")
        lw_item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable |
            Qt.ItemFlag.ItemIsEnabled |
            Qt.ItemFlag.ItemIsSelectable |
            Qt.ItemFlag.ItemIsDragEnabled
        )
        lw_item.setCheckState(Qt.CheckState.Checked)
        lw_item.setIcon(_make_dot_icon(color))
        lw_item.setData(Qt.ItemDataRole.UserRole, {
            "type":     "sql_result",
            "schema":   "sql",
            "table":    "result",
            "geom_col": "geom",
            "srid":     4326,
            "color":    color,
            "style":    _default_style(color),
            "features": parsed,
            "key":      key,
        })

        self._layer_list.blockSignals(True)
        self._layer_list.insertItem(0, lw_item)  # top of stack
        self._layer_list.blockSignals(False)
        self._layer_list.setCurrentItem(lw_item)

        self._layers_data[key]    = parsed
        self._layers_columns[key] = list(attr_cols)

        self._canvas.draw_all(self._layer_list)

        # Fit view to the result extent
        from PyQt6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(100, lambda: self._canvas.fit_extent())

        self._status.setText(
            f"✓ {len(parsed)} features from SQL query added to map")

    def add_geojson_layer(self, geojson: dict, name: str) -> None:
        """Add a WFS/GeoJSON FeatureCollection as a temporary layer."""
        import json as _json
        parsed = []
        for feat in geojson.get("features", []):
            geom = feat.get("geometry")
            if not geom:
                continue
            try:
                geom_str = _json.dumps(geom)
                path, gtype = _geojson_to_path(geom)
                attrs = feat.get("properties") or {}
                parsed.append((path, gtype, attrs))
            except Exception:
                continue

        if not parsed:
            self._status.setText("No valid geometries in WFS response.")
            return

        color = "#3498db"
        key   = f"wfs::{name}"
        short = name[:40] + ("…" if len(name) > 40 else "")
        lw_item = QListWidgetItem(f"🌊 {short}")
        lw_item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable |
            Qt.ItemFlag.ItemIsEnabled |
            Qt.ItemFlag.ItemIsSelectable |
            Qt.ItemFlag.ItemIsDragEnabled
        )
        lw_item.setCheckState(Qt.CheckState.Checked)
        lw_item.setIcon(_make_dot_icon(color))
        lw_item.setData(Qt.ItemDataRole.UserRole, {
            "type":     "wfs",
            "schema":   "wfs",
            "table":    name,
            "geom_col": "geom",
            "srid":     4326,
            "color":    color,
            "style":    _default_style(color),
            "features": parsed,
            "key":      key,
        })

        # Remove previous layer with same name
        for i in range(self._layer_list.count()):
            item = self._layer_list.item(i)
            d = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(d, dict) and d.get("key") == key:
                self._layer_list.takeItem(i)
                break

        self._layer_list.blockSignals(True)
        self._layer_list.insertItem(0, lw_item)
        self._layer_list.blockSignals(False)
        self._layer_list.setCurrentItem(lw_item)

        self._layers_data[key] = parsed
        self._layers_columns[key] = list(
            (geojson.get("features") or [{}])[0].get("properties", {}).keys())

        self._canvas.draw_all(self._layer_list)
        from PyQt6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(100, lambda: self._canvas.fit_extent())
        self._status.setText(f"✔ WFS: {len(parsed)} features from {name}")

    def apply_thematic_style(self, style: dict) -> None:
        """Apply classified color breaks from StyleGeneratorPanel to a loaded layer."""
        schema = style.get("schema", "")
        table  = style.get("table",  "")
        column = style.get("column", "")
        classes = style.get("classes", [])
        if not classes:
            return
        key = f"{schema}::{table}"
        for i in range(self._layer_list.count()):
            item = self._layer_list.item(i)
            d = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(d, dict):
                continue
            if d.get("schema") == schema and d.get("table") == table:
                d["thematic"] = {"column": column, "classes": classes}
                item.setData(Qt.ItemDataRole.UserRole, d)
                break
        self._canvas.draw_all(self._layer_list)
        self._status.setText(
            f"✔ Thematic style applied: {schema}.{table} [{column}]")

    # ── Project save / restore ────────────────────────────────────────────

    def get_map_state(self) -> dict:
        """Return a JSON-serialisable dict describing current viewer state."""
        layers = []
        for i in range(self._layer_list.count()):
            item = self._layer_list.item(i)
            d = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(d, dict):
                continue
            if d.get("type") == "basemap":
                continue  # stored separately
            layers.append({
                "schema":   d.get("schema", ""),
                "table":    d.get("table", ""),
                "geom_col": d.get("geom_col", ""),
                "srid":     d.get("srid", 0),
                "color":       d.get("color", "#3498db"),
                "style":       d.get("style"),
                "label_col":   d.get("label_col"),
                "filter_expr": d.get("filter_expr", ""),
                "visible":     item.checkState() == Qt.CheckState.Checked,
            })

        # canvas viewport extent in scene (EPSG:3857) coordinates
        vp = self._canvas.viewport().rect()
        tl = self._canvas.mapToScene(vp.topLeft())
        br = self._canvas.mapToScene(vp.bottomRight())
        extent = {"x": tl.x(), "y": tl.y(),
                  "w": br.x() - tl.x(), "h": br.y() - tl.y()}

        return {
            "basemap": self._basemap_combo.currentText(),
            "layers":  layers,
            "extent":  extent,
        }

    def restore_map_state(self, state: dict) -> None:
        """Restore layer stack and basemap from a previously saved state dict."""
        # Clear existing layers
        self._layer_list.blockSignals(True)
        self._layer_list.clear()
        self._layer_list.blockSignals(False)
        self._layers_data.clear()
        self._layers_columns.clear()

        # Restore basemap first
        basemap_name = state.get("basemap", "None")
        idx = self._basemap_combo.findText(basemap_name)
        if idx >= 0:
            self._basemap_combo.setCurrentIndex(idx)
        else:
            self._basemap_combo.setCurrentText("None")

        # Restore layers (bottom-of-list first = drawn first)
        for layer in state.get("layers", []):
            schema   = layer.get("schema", "")
            table    = layer.get("table", "")
            geom_col = layer.get("geom_col", "")
            srid     = layer.get("srid", 0)
            color    = layer.get("color")
            style    = layer.get("style")
            visible  = layer.get("visible", True)
            if not schema or not table:
                continue
            lw_item = self._add_layer_to_list(schema, table, geom_col, srid, color)
            d = lw_item.data(Qt.ItemDataRole.UserRole)
            if style:
                d["style"] = style
            if layer.get("label_col"):
                d["label_col"] = layer["label_col"]
            if layer.get("filter_expr"):
                d["filter_expr"] = layer["filter_expr"]
            lw_item.setData(Qt.ItemDataRole.UserRole, d)
            lw_item.setCheckState(
                Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
            if self.db.is_connected():
                self._load_layer_data(lw_item)

        # Restore viewport extent after a short delay (let canvas settle)
        extent = state.get("extent")
        if extent:
            from PyQt6.QtCore import QTimer, QRectF
            from PyQt6.QtCore import Qt as _Qt
            def _apply_extent():
                r = QRectF(extent["x"], extent["y"], extent["w"], extent["h"])
                self._canvas.fitInView(r, _Qt.AspectRatioMode.KeepAspectRatio)
            QTimer.singleShot(200, _apply_extent)
