"""3DCityDB integration panel — explore, query and export 3DCityDB schemas."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QComboBox, QTextEdit, QSplitter, QTreeWidget, QTreeWidgetItem,
    QLineEdit, QMessageBox, QProgressBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from ...db.connection import DBManager
from ...utils import i18n


# ── Known 3DCityDB feature tables ─────────────────────────────────────────────

CITYDB_FEATURE_TABLES = {
    "Core": [
        "cityobject", "citymodel", "cityobject_genericattrib",
        "external_reference", "appearance", "surface_data", "textureparam",
    ],
    "Building": [
        "building", "building_installation", "thematic_surface",
        "opening", "building_furniture", "room",
    ],
    "Bridge": ["bridge", "bridge_installation", "bridge_thematic_surface",
               "bridge_opening", "bridge_furniture", "bridge_room",
               "bridge_constr_element"],
    "Tunnel": ["tunnel", "tunnel_installation", "tunnel_thematic_surface",
               "tunnel_opening", "tunnel_furniture", "tunnel_hollow_space"],
    "Transportation": ["transportation_complex", "traffic_area",
                       "auxiliary_traffic_area"],
    "Vegetation": ["plant_cover", "solitary_vegetation_object"],
    "Water": ["waterbody", "waterboundary_surface"],
    "Relief": ["relief_feature", "relief_component", "tin_relief",
               "masspoint_relief", "breakline_relief", "raster_relief"],
    "City Furniture": ["city_furniture"],
    "Generic": ["generic_cityobject"],
    "Land Use": ["land_use"],
    "Geometry": ["surface_geometry", "implicit_geometry"],
    "Address": ["address"],
}

CITYDB_SCHEMAS = ("citydb", "citydb_pkg", "public")

CITYDB_SIGNATURE_TABLES = {"cityobject", "surface_geometry", "citymodel"}


class CityDBWorker(QThread):
    stats_ready = pyqtSignal(dict)
    query_ready = pyqtSignal(list, list)  # rows, columns
    error       = pyqtSignal(str)

    def __init__(self, db: DBManager, task: str, **kwargs):
        super().__init__()
        self.db = db
        self.task = task
        self.kwargs = kwargs

    def run(self):
        try:
            if self.task == "detect":
                self.stats_ready.emit(_detect_citydb(self.db))
            elif self.task == "feature_counts":
                schema = self.kwargs["schema"]
                self.stats_ready.emit(_feature_counts(self.db, schema))
            elif self.task == "query":
                sql = self.kwargs["sql"]
                rows, cols = _run_query(self.db, sql)
                self.query_ready.emit(rows, cols)
        except Exception as e:
            self.error.emit(str(e))


def _q(db, sql, params=()):
    with db.conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _detect_citydb(db: DBManager) -> dict:
    result: dict = {"found": False, "schema": None, "version": None,
                    "tables": [], "srs": None}
    for schema in CITYDB_SCHEMAS:
        try:
            rows = _q(db, """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s
            """, (schema,))
            found = {r[0] for r in rows}
            if CITYDB_SIGNATURE_TABLES.issubset(found):
                result["found"] = True
                result["schema"] = schema
                result["tables"] = sorted(found)
                break
        except Exception:
            continue

    if not result["found"]:
        return result

    schema = result["schema"]

    # Try version
    for vtable in ("citydb_version", "version", "schema_version"):
        try:
            rows = _q(db, f'SELECT * FROM "{schema}"."{vtable}" LIMIT 1')
            if rows:
                result["version"] = str(rows[0])
                break
        except Exception:
            continue

    # SRS / CRS
    try:
        rows = _q(db, f"""
            SELECT srid, coord_ref_sys_name
            FROM "{schema}".database_srs LIMIT 1
        """)
        if rows:
            result["srs"] = f"SRID {rows[0][0]} – {rows[0][1]}"
    except Exception:
        pass

    return result


def _feature_counts(db: DBManager, schema: str) -> dict:
    counts: dict = {}
    try:
        rows = _q(db, f"""
            SELECT objectclass_id, count(*)
            FROM "{schema}".cityobject
            GROUP BY objectclass_id ORDER BY count(*) DESC
        """)
        counts["by_class"] = rows
    except Exception:
        counts["by_class"] = []

    try:
        rows = _q(db, f'SELECT count(*) FROM "{schema}".cityobject')
        counts["total"] = rows[0][0] if rows else 0
    except Exception:
        counts["total"] = 0

    try:
        rows = _q(db, f'SELECT count(*) FROM "{schema}".surface_geometry')
        counts["geometries"] = rows[0][0] if rows else 0
    except Exception:
        counts["geometries"] = 0

    try:
        rows = _q(db, f'SELECT count(*) FROM "{schema}".appearance')
        counts["appearances"] = rows[0][0] if rows else 0
    except Exception:
        counts["appearances"] = 0

    # Try objectclass lookup
    try:
        rows = _q(db, f"""
            SELECT o.objectclass_id, oc.classname, count(*) AS cnt
            FROM "{schema}".cityobject o
            JOIN "{schema}".objectclass oc ON oc.id = o.objectclass_id
            GROUP BY o.objectclass_id, oc.classname
            ORDER BY cnt DESC
        """)
        counts["named_classes"] = rows
    except Exception:
        counts["named_classes"] = []

    return counts


def _run_query(db: DBManager, sql: str):
    with db.conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchmany(500)
        return [list(r) for r in rows], cols


# ── Panel ──────────────────────────────────────────────────────────────────────

class CityDBPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._schema: str | None = None
        self._worker: CityDBWorker | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        title = QLabel("🏙  3DCityDB")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        hdr.addWidget(title)
        hdr.addStretch()
        self._detect_btn = QPushButton(f"🔍  {i18n.t('cdb_detect')}")
        self._detect_btn.clicked.connect(self._detect)
        hdr.addWidget(self._detect_btn)
        layout.addLayout(hdr)

        self._info_lbl = QLabel(i18n.t("cdb_not_found"))
        self._info_lbl.setStyleSheet("color: #888;")
        layout.addWidget(self._info_lbl)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setMaximumHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── Left: feature tree + stats ─────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 4, 0)

        # Info cards row
        cards = QHBoxLayout()
        self._card_total  = _card("Total Objects", "—")
        self._card_geoms  = _card("Geometries", "—")
        self._card_appear = _card("Appearances", "—")
        self._card_srs    = _card("CRS", "—")
        for c in [self._card_total, self._card_geoms,
                  self._card_appear, self._card_srs]:
            cards.addWidget(c)
        ll.addLayout(cards)

        ll.addWidget(QLabel(f"<b>{i18n.t('cdb_feature_counts')}</b>"))
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Feature Type", "Count"])
        self._tree.setColumnWidth(0, 220)
        self._tree.setSortingEnabled(True)
        self._tree.itemClicked.connect(self._on_tree_click)
        ll.addWidget(self._tree)
        splitter.addWidget(left)

        # ── Right: query area ─────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right); rl.setContentsMargins(4, 0, 0, 0)

        qbox = QGroupBox(i18n.t("cdb_query"))
        ql = QVBoxLayout(qbox)
        tpl_row = QHBoxLayout()
        tpl_row.addWidget(QLabel(i18n.t("cdb_template")))
        self._tpl_combo = QComboBox()
        self._tpl_combo.addItems([
            i18n.t("cdb_tpl_all_objects"),
            i18n.t("cdb_tpl_buildings"),
            i18n.t("cdb_tpl_lod2"),
            i18n.t("cdb_tpl_surfaces"),
            i18n.t("cdb_tpl_address"),
            i18n.t("cdb_tpl_ext_refs"),
        ])
        self._tpl_combo.currentIndexChanged.connect(self._load_template)
        tpl_row.addWidget(self._tpl_combo)
        ql.addLayout(tpl_row)

        self._sql_edit = QTextEdit()
        self._sql_edit.setFont(QFont("Courier New", 10))
        self._sql_edit.setPlaceholderText("-- SQL …")
        self._sql_edit.setMaximumHeight(140)
        ql.addWidget(self._sql_edit)

        run_row = QHBoxLayout()
        run_row.addStretch()
        self._run_btn = QPushButton(f"▶  {i18n.t('action_run')}  [F5]")
        self._run_btn.clicked.connect(self._run_query)
        self._run_btn.setShortcut("F5")
        run_row.addWidget(self._run_btn)
        ql.addLayout(run_row)
        rl.addWidget(qbox)

        rl.addWidget(QLabel(f"<b>{i18n.t('cdb_results')}</b>"))
        self._result_table = QTableWidget()
        self._result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._result_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._result_table.setAlternatingRowColors(True)
        hh = self._result_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        rl.addWidget(self._result_table)

        self._result_lbl = QLabel("")
        rl.addWidget(self._result_lbl)
        splitter.addWidget(right)
        splitter.setSizes([420, 580])

    # ── Actions ───────────────────────────────────────────────────────────────

    def _detect(self):
        if not self.db.is_connected():
            self._info_lbl.setText(i18n.t("cdb_not_connected"))
            return
        self._start_worker(CityDBWorker(self.db, "detect"))
        self._detect_btn.setEnabled(False)

    def _start_worker(self, w: CityDBWorker):
        self._progress.show()
        w.stats_ready.connect(self._on_stats)
        w.query_ready.connect(self._on_query_results)
        w.error.connect(self._on_error)
        w.finished.connect(self._on_done)
        w.start(); self._worker = w

    def _on_stats(self, data: dict):
        if data.get("found"):
            schema = data["schema"]
            self._schema = schema
            ver = data.get("version") or i18n.t("cdb_unknown")
            srs = data.get("srs") or "?"
            self._info_lbl.setText(
                f"✅  3DCityDB  •  schema: <b>{schema}</b>  •  {i18n.t('cdb_version')}: {ver}  •  {srs}")
            self._info_lbl.setStyleSheet("color: #2E7D32;")
            self._card_srs[1].setText(srs.split("–")[0].strip())

            # Load feature counts
            w = CityDBWorker(self.db, "feature_counts", schema=schema)
            self._start_worker(w)
        elif "total" in data:
            # feature counts response
            total = data.get("total", 0)
            geoms = data.get("geometries", 0)
            apps  = data.get("appearances", 0)
            self._card_total[1].setText(f"{total:,}")
            self._card_geoms[1].setText(f"{geoms:,}")
            self._card_appear[1].setText(f"{apps:,}")
            self._fill_tree(data)
            self._load_template(0)
        else:
            self._info_lbl.setText(i18n.t("cdb_not_found"))
            self._info_lbl.setStyleSheet("color: #C62828;")

    def _fill_tree(self, data: dict):
        self._tree.clear()
        named = {row[1]: row[2] for row in data.get("named_classes", [])}
        by_class = data.get("by_class", [])

        if named:
            for feature_group, tables in CITYDB_FEATURE_TABLES.items():
                group_item = QTreeWidgetItem([feature_group, ""])
                group_item.setFont(0, _bold_font())
                total_cnt = 0
                for cls_name in tables:
                    cnt = named.get(cls_name, 0)
                    if cnt:
                        child = QTreeWidgetItem([cls_name, f"{cnt:,}"])
                        child.setForeground(1, QColor("#1B5E20"))
                        group_item.addChild(child)
                        total_cnt += cnt
                if total_cnt:
                    group_item.setText(1, f"{total_cnt:,}")
                    self._tree.addTopLevelItem(group_item)
                    group_item.setExpanded(True)
        else:
            for oid, cnt in by_class:
                item = QTreeWidgetItem([f"class {oid}", f"{cnt:,}"])
                self._tree.addTopLevelItem(item)

    def _on_tree_click(self, item: QTreeWidgetItem, col: int):
        cls_name = item.text(0)
        if self._schema and not item.childCount():
            sql = f'SELECT * FROM "{self._schema}"."{cls_name}" LIMIT 100'
            self._sql_edit.setPlainText(sql)

    def _load_template(self, idx: int):
        s = self._schema or "citydb"
        templates = [
            f'SELECT id, objectclass_id, gmlid, name, creation_date\nFROM "{s}".cityobject\nLIMIT 100;',
            f'SELECT b.id, b.gmlid, b.measured_height, b.lod2_solid_id\nFROM "{s}".building b\nJOIN "{s}".cityobject co ON co.id = b.id\nLIMIT 100;',
            f'SELECT co.id, co.gmlid, sg.geometry_type, sg.is_solid\nFROM "{s}".surface_geometry sg\nJOIN "{s}".cityobject co ON co.id = sg.cityobject_id\nWHERE sg.lod = 2\nLIMIT 100;',
            f'SELECT ts.id, ts.objectclass_id, ts.lod2_multi_surface_id\nFROM "{s}".thematic_surface ts\nLIMIT 100;',
            f'SELECT ad.id, ad.street, ad.house_number, ad.city, ad.country\nFROM "{s}".address ad\nLIMIT 100;',
            f'SELECT er.id, er.infosys, er.name, er.uri\nFROM "{s}".external_reference er\nLIMIT 100;',
        ]
        if 0 <= idx < len(templates):
            self._sql_edit.setPlainText(templates[idx])

    def _run_query(self):
        sql = self._sql_edit.toPlainText().strip()
        if not sql or not self.db.is_connected():
            return
        self._run_btn.setEnabled(False)
        self._start_worker(CityDBWorker(self.db, "query", sql=sql))

    def _on_query_results(self, rows: list, cols: list):
        t = self._result_table
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(
                    "" if val is None else str(val)))
        self._result_lbl.setText(
            f"{len(rows)} {i18n.t('result_rows')} (max 500)")

    def _on_error(self, msg: str):
        self._info_lbl.setText(f"Error: {msg}")
        self._info_lbl.setStyleSheet("color: #C62828;")

    def _on_done(self):
        self._progress.hide()
        self._detect_btn.setEnabled(True)
        self._run_btn.setEnabled(True)

    def showEvent(self, event):
        super().showEvent(event)
        if self.db.is_connected() and not self._schema:
            self._detect()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _card(label: str, value: str):
    from PyQt6.QtWidgets import QFrame
    frame = QFrame()
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    frame.setMinimumWidth(100)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(8, 6, 8, 6)
    lbl = QLabel(label); lbl.setStyleSheet("font-size: 10px; color:#888;")
    val = QLabel(value); val.setFont(_bold_font())
    lay.addWidget(lbl); lay.addWidget(val)
    return frame, val


def _bold_font():
    f = QFont(); f.setBold(True); return f
