"""Spatial Analysis panel — Buffer/Union/Intersection, Nearest Neighbor,
Spatial Join, Topology Validator, pgRouting (A* / Dijkstra)."""

from __future__ import annotations

import psycopg2
import psycopg2.extras
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QLineEdit,
    QComboBox, QTextEdit, QGroupBox, QFormLayout, QSpinBox,
    QDoubleSpinBox, QMessageBox, QSplitter, QAbstractItemView, QCheckBox, QRadioButton, QButtonGroup,
    QScrollArea,
)

from ...utils.workers import launch


# ── Generic SQL worker ────────────────────────────────────────────────────

class AnalysisWorker(QThread):
    log      = pyqtSignal(str, str)          # msg, level
    rows     = pyqtSignal(list, list)        # rows, column_names
    finished = pyqtSignal(bool)

    def __init__(self, conn_params: dict, sql: str,
                 fetch: bool = False):
        super().__init__()
        self.conn_params = conn_params
        self.sql  = sql
        self.fetch = fetch

    def run(self):
        ok = True
        try:
            conn = psycopg2.connect(**self.conn_params)
            conn.autocommit = True
            cur = conn.cursor()
            self.log.emit(f"SQL:\n{self.sql}", "sql")
            cur.execute(self.sql)
            if self.fetch and cur.description:
                cols = [d[0] for d in cur.description]
                data = cur.fetchmany(500)
                self.rows.emit([list(r) for r in data], cols)
            elif not self.fetch:
                self.log.emit(
                    f"✓ Done — {cur.rowcount if cur.rowcount >= 0 else '?'} rows affected",
                    "success")
            cur.close()
            conn.close()
        except Exception as e:
            self.log.emit(f"✗ {e}", "error")
            ok = False
        finally:
            self.finished.emit(ok)


# ── Shared helpers ────────────────────────────────────────────────────────

def _schema_table_combo(schema_edit: QLineEdit,
                        table_combo: QComboBox,
                        conn_params: dict) -> None:
    """Reload table list for given schema."""
    schema = schema_edit.text().strip() or "public"
    if not conn_params:
        return
    try:
        conn = psycopg2.connect(**conn_params)
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s ORDER BY table_name", (schema,))
        tables = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        table_combo.clear()
        table_combo.addItems(tables)
    except Exception:
        pass


def _geom_cols(schema: str, table: str, conn_params: dict) -> list[str]:
    if not conn_params:
        return []
    try:
        conn = psycopg2.connect(**conn_params)
        cur = conn.cursor()
        cur.execute(
            "SELECT f_geometry_column FROM geometry_columns "
            "WHERE f_table_schema=%s AND f_table_name=%s",
            (schema, table))
        cols = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        return cols
    except Exception:
        return []


# ── Main Panel ────────────────────────────────────────────────────────────

class SpatialAnalysisPanel(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._conn_params: dict = {}
        self._worker: AnalysisWorker | None = None
        self._setup_ui()

    def set_connection(self, params: dict):
        self._conn_params = params

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.addTab(self._build_overlay_tab(),   "🔲  Buffer / Overlay")
        tabs.addTab(self._build_nn_tab(),         "📍  Nearest Neighbor")
        tabs.addTab(self._build_join_tab(),       "🔗  Spatial Join")
        tabs.addTab(self._build_topology_tab(),   "✅  Topology")
        tabs.addTab(self._build_routing_tab(),    "🛣  Routing")
        root.addWidget(tabs, 1)

        # Shared log + results
        bottom = QSplitter(Qt.Orientation.Vertical)

        self._result_table = QTableWidget(0, 1)
        self._result_table.horizontalHeader().setStretchLastSection(True)
        self._result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._result_table.setAlternatingRowColors(True)
        self._result_table.setMaximumHeight(160)
        bottom.addWidget(self._result_table)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 10))
        self._log.setMaximumHeight(100)
        bottom.addWidget(self._log)
        bottom.setSizes([160, 100])
        root.addWidget(bottom)

    # ── helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _wrap_scroll(w: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(w)
        return scroll

    # ── Tab 1: Buffer / Overlay ───────────────────────────────────────────

    def _build_overlay_tab(self) -> QScrollArea:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        # Operation selector
        op_box = QGroupBox("Operation")
        op_lay = QHBoxLayout(op_box)
        self._op_group = QButtonGroup(self)
        for i, label in enumerate(
                ["Buffer", "Union", "Intersection", "Difference",
                 "Sym. Difference", "Dissolve"]):
            rb = QRadioButton(label)
            if i == 0:
                rb.setChecked(True)
            self._op_group.addButton(rb, i)
            op_lay.addWidget(rb)
        op_lay.addStretch()
        lay.addWidget(op_box)

        # Layer A
        a_box = QGroupBox("Layer A (input)")
        afl = QFormLayout(a_box)
        self._ov_a_schema = QLineEdit("public")
        self._ov_a_table  = QComboBox()
        self._ov_a_geom   = QComboBox()
        self._ov_a_schema.editingFinished.connect(
            lambda: _schema_table_combo(
                self._ov_a_schema, self._ov_a_table, self._conn_params))
        self._ov_a_table.currentTextChanged.connect(self._reload_ov_a_geom)
        btn_a = QPushButton("↺")
        btn_a.setFixedWidth(28)
        btn_a.clicked.connect(
            lambda: _schema_table_combo(
                self._ov_a_schema, self._ov_a_table, self._conn_params))
        afl.addRow("Schema:", self._ov_a_schema)
        afl.addRow("Table:",  self._hrow(self._ov_a_table, btn_a))
        afl.addRow("Geom:",   self._ov_a_geom)
        lay.addWidget(a_box)

        # Layer B (hidden for Buffer/Union/Dissolve)
        self._ov_b_box = QGroupBox("Layer B (overlay)")
        bfl = QFormLayout(self._ov_b_box)
        self._ov_b_schema = QLineEdit("public")
        self._ov_b_table  = QComboBox()
        self._ov_b_geom   = QComboBox()
        self._ov_b_schema.editingFinished.connect(
            lambda: _schema_table_combo(
                self._ov_b_schema, self._ov_b_table, self._conn_params))
        self._ov_b_table.currentTextChanged.connect(self._reload_ov_b_geom)
        btn_b = QPushButton("↺")
        btn_b.setFixedWidth(28)
        btn_b.clicked.connect(
            lambda: _schema_table_combo(
                self._ov_b_schema, self._ov_b_table, self._conn_params))
        bfl.addRow("Schema:", self._ov_b_schema)
        bfl.addRow("Table:",  self._hrow(self._ov_b_table, btn_b))
        bfl.addRow("Geom:",   self._ov_b_geom)
        lay.addWidget(self._ov_b_box)

        # Buffer options
        self._buf_box = QGroupBox("Buffer Options")
        bfl2 = QFormLayout(self._buf_box)
        self._buf_dist  = QDoubleSpinBox()
        self._buf_dist.setRange(0.0001, 1e9)
        self._buf_dist.setValue(100)
        self._buf_dist.setSuffix("  (map units)")
        self._buf_segs  = QSpinBox()
        self._buf_segs.setRange(4, 64)
        self._buf_segs.setValue(16)
        self._buf_dissolve = QCheckBox("Dissolve result")
        bfl2.addRow("Distance:", self._buf_dist)
        bfl2.addRow("Segments:", self._buf_segs)
        bfl2.addRow("",          self._buf_dissolve)
        lay.addWidget(self._buf_box)

        self._op_group.idToggled.connect(self._on_op_changed)

        # Output
        out_box = QGroupBox("Save result to")
        ofl = QFormLayout(out_box)
        self._ov_out_schema = QLineEdit("public")
        self._ov_out_table  = QLineEdit("result")
        self._ov_drop_first = QCheckBox("DROP TABLE IF EXISTS first")
        self._ov_drop_first.setChecked(True)
        ofl.addRow("Schema:", self._ov_out_schema)
        ofl.addRow("Table:",  self._ov_out_table)
        ofl.addRow("",        self._ov_drop_first)
        lay.addWidget(out_box)

        run_row = QHBoxLayout()
        btn_run = QPushButton("▶  Run Analysis")
        btn_run.setStyleSheet("font-weight:bold;")
        btn_run.clicked.connect(self._run_overlay)
        btn_preview = QPushButton("Preview SQL")
        btn_preview.clicked.connect(self._preview_overlay_sql)
        run_row.addWidget(btn_run)
        run_row.addWidget(btn_preview)
        run_row.addStretch()
        lay.addLayout(run_row)
        lay.addStretch()
        return self._wrap_scroll(w)

    def _on_op_changed(self, op_id: int, checked: bool):
        if not checked:
            return
        # Buffer=0, Union=1, Dissolve=5 need no layer B
        needs_b = op_id in (2, 3, 4)  # Intersection, Difference, SymDiff
        self._ov_b_box.setVisible(needs_b)
        self._buf_box.setVisible(op_id == 0)

    def _reload_ov_a_geom(self, table: str):
        schema = self._ov_a_schema.text().strip()
        cols = _geom_cols(schema, table, self._conn_params)
        self._ov_a_geom.clear()
        self._ov_a_geom.addItems(cols or ["geom"])

    def _reload_ov_b_geom(self, table: str):
        schema = self._ov_b_schema.text().strip()
        cols = _geom_cols(schema, table, self._conn_params)
        self._ov_b_geom.clear()
        self._ov_b_geom.addItems(cols or ["geom"])

    def _build_overlay_sql(self) -> str:
        op_id = self._op_group.checkedId()
        sa = self._ov_a_schema.text().strip()
        ta = self._ov_a_table.currentText()
        ga = self._ov_a_geom.currentText() or "geom"
        sb = self._ov_b_schema.text().strip()
        tb = self._ov_b_table.currentText()
        gb = self._ov_b_geom.currentText() or "geom"
        so = self._ov_out_schema.text().strip()
        to_ = self._ov_out_table.text().strip()
        drop = (f'DROP TABLE IF EXISTS "{so}"."{to_}";\n'
                if self._ov_drop_first.isChecked() else "")

        a = f'"{sa}"."{ta}"'
        b = f'"{sb}"."{tb}"'

        if op_id == 0:   # Buffer
            d = self._buf_dist.value()
            segs = self._buf_segs.value()
            inner = (
                f'SELECT ST_Union(ST_Buffer("{ga}", {d}, {segs})) AS geom FROM {a}'
                if self._buf_dissolve.isChecked()
                else
                f'SELECT ST_Buffer("{ga}", {d}, {segs}) AS geom FROM {a}')
        elif op_id == 1: # Union
            inner = f'SELECT ST_Union("{ga}") AS geom FROM {a}'
        elif op_id == 2: # Intersection
            inner = (f'SELECT ST_Intersection(a."{ga}", b."{gb}") AS geom '
                     f'FROM {a} a JOIN {b} b '
                     f'ON ST_Intersects(a."{ga}", b."{gb}")')
        elif op_id == 3: # Difference
            inner = (f'SELECT ST_Difference(a."{ga}", b."{gb}") AS geom '
                     f'FROM {a} a JOIN {b} b '
                     f'ON ST_Intersects(a."{ga}", b."{gb}")')
        elif op_id == 4: # Sym Difference
            inner = (f'SELECT ST_SymDifference(a."{ga}", b."{gb}") AS geom '
                     f'FROM {a} a JOIN {b} b '
                     f'ON ST_Intersects(a."{ga}", b."{gb}")')
        else:            # Dissolve
            inner = f'SELECT ST_Union("{ga}") AS geom FROM {a}'

        return (f'{drop}'
                f'CREATE TABLE "{so}"."{to_}" AS\n'
                f'{inner};\n'
                f'CREATE INDEX ON "{so}"."{to_}" USING GIST (geom);')

    def _preview_overlay_sql(self):
        self._log.setPlainText(self._build_overlay_sql())

    def _run_overlay(self):
        self._exec_sql(self._build_overlay_sql())

    # ── Tab 2: Nearest Neighbor ───────────────────────────────────────────

    def _build_nn_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        src_box = QGroupBox("Source Layer")
        sfl = QFormLayout(src_box)
        self._nn_src_schema = QLineEdit("public")
        self._nn_src_table  = QComboBox()
        self._nn_src_geom   = QComboBox()
        self._nn_src_schema.editingFinished.connect(
            lambda: _schema_table_combo(
                self._nn_src_schema, self._nn_src_table, self._conn_params))
        self._nn_src_table.currentTextChanged.connect(
            lambda t: self._reload_geom_combo(
                self._nn_src_schema, t, self._nn_src_geom))
        btn = QPushButton("↺"); btn.setFixedWidth(28)
        btn.clicked.connect(
            lambda: _schema_table_combo(
                self._nn_src_schema, self._nn_src_table, self._conn_params))
        sfl.addRow("Schema:", self._nn_src_schema)
        sfl.addRow("Table:",  self._hrow(self._nn_src_table, btn))
        sfl.addRow("Geom:",   self._nn_src_geom)
        lay.addWidget(src_box)

        tgt_box = QGroupBox("Target Layer (find nearest in)")
        tfl = QFormLayout(tgt_box)
        self._nn_tgt_schema = QLineEdit("public")
        self._nn_tgt_table  = QComboBox()
        self._nn_tgt_geom   = QComboBox()
        self._nn_tgt_schema.editingFinished.connect(
            lambda: _schema_table_combo(
                self._nn_tgt_schema, self._nn_tgt_table, self._conn_params))
        self._nn_tgt_table.currentTextChanged.connect(
            lambda t: self._reload_geom_combo(
                self._nn_tgt_schema, t, self._nn_tgt_geom))
        btn2 = QPushButton("↺"); btn2.setFixedWidth(28)
        btn2.clicked.connect(
            lambda: _schema_table_combo(
                self._nn_tgt_schema, self._nn_tgt_table, self._conn_params))
        tfl.addRow("Schema:", self._nn_tgt_schema)
        tfl.addRow("Table:",  self._hrow(self._nn_tgt_table, btn2))
        tfl.addRow("Geom:",   self._nn_tgt_geom)
        lay.addWidget(tgt_box)

        opt_box = QGroupBox("Options")
        ofl = QFormLayout(opt_box)
        self._nn_k        = QSpinBox()
        self._nn_k.setRange(1, 100)
        self._nn_k.setValue(1)
        self._nn_use_dwithin = QCheckBox("Limit to max distance")
        self._nn_max_dist    = QDoubleSpinBox()
        self._nn_max_dist.setRange(0, 1e9)
        self._nn_max_dist.setValue(1000)
        self._nn_max_dist.setEnabled(False)
        self._nn_use_dwithin.toggled.connect(self._nn_max_dist.setEnabled)
        self._nn_out_table = QLineEdit("nearest_neighbor")
        ofl.addRow("k (neighbours):", self._nn_k)
        ofl.addRow("",                self._nn_use_dwithin)
        ofl.addRow("Max distance:",   self._nn_max_dist)
        ofl.addRow("Output table:",   self._nn_out_table)
        lay.addWidget(opt_box)

        run_row = QHBoxLayout()
        btn_run     = QPushButton("▶  Find Nearest")
        btn_preview = QPushButton("Preview SQL")
        btn_run.setStyleSheet("font-weight:bold;")
        btn_run.clicked.connect(self._run_nn)
        btn_preview.clicked.connect(self._preview_nn)
        run_row.addWidget(btn_run)
        run_row.addWidget(btn_preview)
        run_row.addStretch()
        lay.addLayout(run_row)
        lay.addStretch()
        return self._wrap_scroll(w)

    def _build_nn_sql(self) -> str:
        ss = self._nn_src_schema.text().strip()
        st = self._nn_src_table.currentText()
        sg = self._nn_src_geom.currentText() or "geom"
        ts = self._nn_tgt_schema.text().strip()
        tt = self._nn_tgt_table.currentText()
        tg = self._nn_tgt_geom.currentText() or "geom"
        k  = self._nn_k.value()
        out = self._nn_out_table.text().strip() or "nn_result"
        dwithin = ""
        if self._nn_use_dwithin.isChecked():
            dwithin = (f' AND ST_DWithin(s."{sg}", t."{tg}", '
                       f'{self._nn_max_dist.value()})')
        return (
            f'DROP TABLE IF EXISTS public."{out}";\n'
            f'CREATE TABLE public."{out}" AS\n'
            f'SELECT\n'
            f'  s.ctid        AS src_id,\n'
            f'  t.ctid        AS tgt_id,\n'
            f'  ST_Distance(s."{sg}", t."{tg}") AS distance,\n'
            f'  s."{sg}"      AS src_geom,\n'
            f'  t."{tg}"      AS tgt_geom,\n'
            f'  ST_MakeLine(ST_Centroid(s."{sg}"),\n'
            f'              ST_Centroid(t."{tg}")) AS connector\n'
            f'FROM "{ss}"."{st}" s\n'
            f'CROSS JOIN LATERAL (\n'
            f'  SELECT *\n'
            f'  FROM "{ts}"."{tt}" t\n'
            f'  WHERE true{dwithin}\n'
            f'  ORDER BY s."{sg}" <-> t."{tg}"\n'
            f'  LIMIT {k}\n'
            f') t;\n'
            f'CREATE INDEX ON public."{out}" USING GIST (connector);'
        )

    def _preview_nn(self):
        self._log.setPlainText(self._build_nn_sql())

    def _run_nn(self):
        self._exec_sql(self._build_nn_sql())

    # ── Tab 3: Spatial Join ───────────────────────────────────────────────

    def _build_join_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        left_box = QGroupBox("Left Layer")
        lfl = QFormLayout(left_box)
        self._sj_l_schema = QLineEdit("public")
        self._sj_l_table  = QComboBox()
        self._sj_l_geom   = QComboBox()
        self._sj_l_schema.editingFinished.connect(
            lambda: _schema_table_combo(
                self._sj_l_schema, self._sj_l_table, self._conn_params))
        self._sj_l_table.currentTextChanged.connect(
            lambda t: self._reload_geom_combo(
                self._sj_l_schema, t, self._sj_l_geom))
        btn_l = QPushButton("↺"); btn_l.setFixedWidth(28)
        btn_l.clicked.connect(
            lambda: _schema_table_combo(
                self._sj_l_schema, self._sj_l_table, self._conn_params))
        lfl.addRow("Schema:", self._sj_l_schema)
        lfl.addRow("Table:",  self._hrow(self._sj_l_table, btn_l))
        lfl.addRow("Geom:",   self._sj_l_geom)
        lay.addWidget(left_box)

        right_box = QGroupBox("Right Layer")
        rfl = QFormLayout(right_box)
        self._sj_r_schema = QLineEdit("public")
        self._sj_r_table  = QComboBox()
        self._sj_r_geom   = QComboBox()
        self._sj_r_schema.editingFinished.connect(
            lambda: _schema_table_combo(
                self._sj_r_schema, self._sj_r_table, self._conn_params))
        self._sj_r_table.currentTextChanged.connect(
            lambda t: self._reload_geom_combo(
                self._sj_r_schema, t, self._sj_r_geom))
        btn_r = QPushButton("↺"); btn_r.setFixedWidth(28)
        btn_r.clicked.connect(
            lambda: _schema_table_combo(
                self._sj_r_schema, self._sj_r_table, self._conn_params))
        rfl.addRow("Schema:", self._sj_r_schema)
        rfl.addRow("Table:",  self._hrow(self._sj_r_table, btn_r))
        rfl.addRow("Geom:",   self._sj_r_geom)
        lay.addWidget(right_box)

        opt_box = QGroupBox("Join Options")
        ofl = QFormLayout(opt_box)
        self._sj_predicate = QComboBox()
        self._sj_predicate.addItems([
            "ST_Intersects",
            "ST_Within",
            "ST_Contains",
            "ST_Overlaps",
            "ST_Touches",
            "ST_Crosses",
            "ST_Covers",
            "ST_CoveredBy",
            "ST_Equals",
        ])
        self._sj_join_type = QComboBox()
        self._sj_join_type.addItems(["INNER JOIN", "LEFT JOIN"])
        self._sj_aggregate = QComboBox()
        self._sj_aggregate.addItems([
            "none (keep all columns)",
            "COUNT(*)",
            "SUM",
            "AVG",
            "MIN / MAX",
        ])
        self._sj_agg_col = QLineEdit()
        self._sj_agg_col.setPlaceholderText("column to aggregate (SUM/AVG/MIN/MAX)")
        self._sj_out = QLineEdit("spatial_join_result")
        ofl.addRow("Predicate:",  self._sj_predicate)
        ofl.addRow("Join type:",  self._sj_join_type)
        ofl.addRow("Aggregate:",  self._sj_aggregate)
        ofl.addRow("Agg column:", self._sj_agg_col)
        ofl.addRow("Output table:", self._sj_out)
        lay.addWidget(opt_box)

        run_row = QHBoxLayout()
        btn_run     = QPushButton("▶  Run Join")
        btn_preview = QPushButton("Preview SQL")
        btn_run.setStyleSheet("font-weight:bold;")
        btn_run.clicked.connect(self._run_sjoin)
        btn_preview.clicked.connect(self._preview_sjoin)
        run_row.addWidget(btn_run)
        run_row.addWidget(btn_preview)
        run_row.addStretch()
        lay.addLayout(run_row)
        lay.addStretch()
        return self._wrap_scroll(w)

    def _build_sjoin_sql(self) -> str:
        ls  = self._sj_l_schema.text().strip()
        lt  = self._sj_l_table.currentText()
        lg  = self._sj_l_geom.currentText() or "geom"
        rs  = self._sj_r_schema.text().strip()
        rt  = self._sj_r_table.currentText()
        rg  = self._sj_r_geom.currentText() or "geom"
        pred = self._sj_predicate.currentText()
        jt   = self._sj_join_type.currentText()
        agg  = self._sj_aggregate.currentText()
        acol = self._sj_agg_col.text().strip()
        out  = self._sj_out.text().strip() or "sj_result"

        if agg == "none (keep all columns)":
            select = f'l.*, r."{rg}" AS right_geom'
            group  = ""
        elif agg == "COUNT(*)":
            select = 'l.*, COUNT(*) AS n_matched'
            group  = f'GROUP BY l.ctid, l."{lg}"'
        elif agg == "SUM":
            select = f'l.*, SUM(r."{acol}") AS sum_{acol}'
            group  = f'GROUP BY l.ctid, l."{lg}"'
        elif agg == "AVG":
            select = f'l.*, AVG(r."{acol}") AS avg_{acol}'
            group  = f'GROUP BY l.ctid, l."{lg}"'
        else:  # MIN/MAX
            select = (f'l.*, MIN(r."{acol}") AS min_{acol}, '
                      f'MAX(r."{acol}") AS max_{acol}')
            group  = f'GROUP BY l.ctid, l."{lg}"'

        return (
            f'DROP TABLE IF EXISTS public."{out}";\n'
            f'CREATE TABLE public."{out}" AS\n'
            f'SELECT {select}\n'
            f'FROM "{ls}"."{lt}" l\n'
            f'{jt} "{rs}"."{rt}" r\n'
            f'  ON {pred}(l."{lg}", r."{rg}")\n'
            f'{group};\n'
            f'CREATE INDEX ON public."{out}" USING GIST ("{lg}");'
        )

    def _preview_sjoin(self):
        self._log.setPlainText(self._build_sjoin_sql())

    def _run_sjoin(self):
        self._exec_sql(self._build_sjoin_sql())

    # ── Tab 4: Topology Validator ─────────────────────────────────────────

    def _build_topology_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        src_box = QGroupBox("Layer to validate")
        sfl = QFormLayout(src_box)
        self._tp_schema = QLineEdit("public")
        self._tp_table  = QComboBox()
        self._tp_geom   = QComboBox()
        self._tp_schema.editingFinished.connect(
            lambda: _schema_table_combo(
                self._tp_schema, self._tp_table, self._conn_params))
        self._tp_table.currentTextChanged.connect(
            lambda t: self._reload_geom_combo(
                self._tp_schema, t, self._tp_geom))
        btn = QPushButton("↺"); btn.setFixedWidth(28)
        btn.clicked.connect(
            lambda: _schema_table_combo(
                self._tp_schema, self._tp_table, self._conn_params))
        sfl.addRow("Schema:", self._tp_schema)
        sfl.addRow("Table:",  self._hrow(self._tp_table, btn))
        sfl.addRow("Geom:",   self._tp_geom)
        lay.addWidget(src_box)

        checks_box = QGroupBox("Checks to run")
        cfl = QVBoxLayout(checks_box)
        self._tp_check_valid     = QCheckBox("ST_IsValid — invalid geometries")
        self._tp_check_simple    = QCheckBox("ST_IsSimple — self-intersecting")
        self._tp_check_empty     = QCheckBox("ST_IsEmpty — empty geometries")
        self._tp_check_ring      = QCheckBox("ST_IsRing — (lines only)")
        self._tp_check_duplicate = QCheckBox("Duplicate geometries (ST_Equals)")
        for cb in (self._tp_check_valid, self._tp_check_simple,
                   self._tp_check_empty, self._tp_check_duplicate):
            cb.setChecked(True)
            cfl.addWidget(cb)
        cfl.addWidget(self._tp_check_ring)
        lay.addWidget(checks_box)

        fix_box = QGroupBox("Auto-fix")
        ffl = QFormLayout(fix_box)
        self._tp_fix_cb   = QCheckBox("Fix invalid with ST_MakeValid and save to:")
        self._tp_fix_tbl  = QLineEdit("fixed_geometries")
        ffl.addRow("", self._tp_fix_cb)
        ffl.addRow("Table:", self._tp_fix_tbl)
        lay.addWidget(fix_box)

        run_row = QHBoxLayout()
        btn_run = QPushButton("▶  Validate")
        btn_fix = QPushButton("🔧  Fix Invalid")
        btn_run.setStyleSheet("font-weight:bold;")
        btn_run.clicked.connect(self._run_topology)
        btn_fix.clicked.connect(self._run_fix)
        run_row.addWidget(btn_run)
        run_row.addWidget(btn_fix)
        run_row.addStretch()
        lay.addLayout(run_row)
        lay.addStretch()
        return self._wrap_scroll(w)

    def _run_topology(self):
        schema = self._tp_schema.text().strip()
        table  = self._tp_table.currentText()
        geom   = self._tp_geom.currentText() or "geom"
        tbl    = f'"{schema}"."{table}"'
        parts  = []

        if self._tp_check_valid.isChecked():
            parts.append(
                f"SELECT ctid, 'INVALID' AS issue, "
                f"ST_IsValidReason(\"{geom}\") AS detail, "
                f'"{geom}" FROM {tbl} WHERE NOT ST_IsValid("{geom}")')
        if self._tp_check_simple.isChecked():
            parts.append(
                f"SELECT ctid, 'NOT_SIMPLE' AS issue, "
                f"'Self-intersecting' AS detail, "
                f'"{geom}" FROM {tbl} WHERE NOT ST_IsSimple("{geom}")')
        if self._tp_check_empty.isChecked():
            parts.append(
                f"SELECT ctid, 'EMPTY' AS issue, "
                f"'Empty geometry' AS detail, "
                f'"{geom}" FROM {tbl} WHERE ST_IsEmpty("{geom}")')
        if self._tp_check_duplicate.isChecked():
            parts.append(
                f"SELECT a.ctid, 'DUPLICATE' AS issue, "
                f"'Exact duplicate' AS detail, "
                f'a."{geom}" FROM {tbl} a '
                f'JOIN {tbl} b ON a.ctid < b.ctid '
                f'AND ST_Equals(a."{geom}", b."{geom}")')

        if not parts:
            self._log_msg("Select at least one check.", "warn")
            return
        sql = "\nUNION ALL\n".join(parts) + "\nORDER BY issue;"
        self._exec_sql(sql, fetch=True)

    def _run_fix(self):
        schema = self._tp_schema.text().strip()
        table  = self._tp_table.currentText()
        geom   = self._tp_geom.currentText() or "geom"
        out    = self._tp_fix_tbl.text().strip() or "fixed_geometries"
        sql = (
            f'DROP TABLE IF EXISTS public."{out}";\n'
            f'CREATE TABLE public."{out}" AS\n'
            f'SELECT ctid AS src_id, '
            f'ST_MakeValid("{geom}") AS geom\n'
            f'FROM "{schema}"."{table}"\n'
            f'WHERE NOT ST_IsValid("{geom}");\n'
            f'CREATE INDEX ON public."{out}" USING GIST (geom);'
        )
        self._exec_sql(sql)

    # ── Tab 5: Routing ────────────────────────────────────────────────────

    def _build_routing_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        info = QLabel(
            "Requires <b>pgRouting</b> extension.  "
            "Run <code>CREATE EXTENSION pgrouting;</code> first.")
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setStyleSheet("color: #d29922; font-size: 11px;")
        lay.addWidget(info)

        net_box = QGroupBox("Network (edge) table")
        nfl = QFormLayout(net_box)
        self._rt_schema  = QLineEdit("public")
        self._rt_table   = QComboBox()
        self._rt_schema.editingFinished.connect(
            lambda: _schema_table_combo(
                self._rt_schema, self._rt_table, self._conn_params))
        btn = QPushButton("↺"); btn.setFixedWidth(28)
        btn.clicked.connect(
            lambda: _schema_table_combo(
                self._rt_schema, self._rt_table, self._conn_params))
        self._rt_id_col   = QLineEdit("id")
        self._rt_src_col  = QLineEdit("source")
        self._rt_tgt_col  = QLineEdit("target")
        self._rt_cost_col = QLineEdit("cost")
        self._rt_rcost    = QLineEdit("-1")
        self._rt_rcost.setPlaceholderText("reverse_cost (-1 = directed)")
        self._rt_geom_col = QLineEdit("geom")
        nfl.addRow("Schema:",       self._rt_schema)
        nfl.addRow("Table:",        self._hrow(self._rt_table, btn))
        nfl.addRow("id column:",    self._rt_id_col)
        nfl.addRow("source col:",   self._rt_src_col)
        nfl.addRow("target col:",   self._rt_tgt_col)
        nfl.addRow("cost col:",     self._rt_cost_col)
        nfl.addRow("reverse cost:", self._rt_rcost)
        nfl.addRow("geom col:",     self._rt_geom_col)
        lay.addWidget(net_box)

        query_box = QGroupBox("Route Query")
        qfl = QFormLayout(query_box)
        self._rt_algo = QComboBox()
        self._rt_algo.addItems(["pgr_dijkstra", "pgr_aStar", "pgr_bdDijkstra"])
        self._rt_start = QSpinBox()
        self._rt_start.setRange(0, 10_000_000)
        self._rt_end   = QSpinBox()
        self._rt_end.setRange(0, 10_000_000)
        self._rt_end.setValue(100)
        self._rt_directed = QCheckBox("Directed graph")
        self._rt_directed.setChecked(True)
        self._rt_out = QLineEdit("route_result")
        qfl.addRow("Algorithm:",   self._rt_algo)
        qfl.addRow("Start node:",  self._rt_start)
        qfl.addRow("End node:",    self._rt_end)
        qfl.addRow("",             self._rt_directed)
        qfl.addRow("Output table:", self._rt_out)
        lay.addWidget(query_box)

        prep_box = QGroupBox("Prepare topology (first time)")
        pfl = QHBoxLayout(prep_box)
        self._rt_tolerance = QDoubleSpinBox()
        self._rt_tolerance.setRange(0, 100)
        self._rt_tolerance.setValue(0.001)
        self._rt_tolerance.setDecimals(6)
        btn_prep = QPushButton("pgr_createTopology")
        btn_prep.clicked.connect(self._run_create_topology)
        pfl.addWidget(QLabel("Tolerance:"))
        pfl.addWidget(self._rt_tolerance)
        pfl.addWidget(btn_prep)
        pfl.addStretch()
        lay.addWidget(prep_box)

        run_row = QHBoxLayout()
        btn_run     = QPushButton("▶  Find Route")
        btn_preview = QPushButton("Preview SQL")
        btn_run.setStyleSheet("font-weight:bold;")
        btn_run.clicked.connect(self._run_routing)
        btn_preview.clicked.connect(self._preview_routing)
        run_row.addWidget(btn_run)
        run_row.addWidget(btn_preview)
        run_row.addStretch()
        lay.addLayout(run_row)
        lay.addStretch()
        return self._wrap_scroll(w)

    def _build_routing_sql(self) -> str:
        schema = self._rt_schema.text().strip()
        table  = self._rt_table.currentText()
        idc    = self._rt_id_col.text().strip()
        src    = self._rt_src_col.text().strip()
        tgt    = self._rt_tgt_col.text().strip()
        cost   = self._rt_cost_col.text().strip()
        rcost  = self._rt_rcost.text().strip()
        geom   = self._rt_geom_col.text().strip()
        algo   = self._rt_algo.currentText()
        start  = self._rt_start.value()
        end    = self._rt_end.value()
        direc  = str(self._rt_directed.isChecked()).lower()
        out    = self._rt_out.text().strip() or "route_result"

        # Edge SQL for pgRouting
        if rcost.strip() == "-1":
            edge_sql = (
                f"'SELECT {idc} AS id, {src} AS source, {tgt} AS target, "
                f"{cost} AS cost FROM \\\"{schema}\\\".\\\"{table}\\\"'")
        else:
            edge_sql = (
                f"'SELECT {idc} AS id, {src} AS source, {tgt} AS target, "
                f"{cost} AS cost, {rcost} AS reverse_cost "
                f"FROM \\\"{schema}\\\".\\\"{table}\\\"'")

        if algo == "pgr_aStar":
            route_fn = (
                f"{algo}({edge_sql}, {start}, {end}, directed := {direc})")
        else:
            route_fn = (
                f"{algo}({edge_sql}, {start}, {end}, directed := {direc})")

        return (
            f'DROP TABLE IF EXISTS public."{out}";\n'
            f'CREATE TABLE public."{out}" AS\n'
            f'SELECT r.seq, r.path_seq, r.node, r.edge,\n'
            f'       r.cost AS step_cost, r.agg_cost,\n'
            f'       e."{geom}" AS geom\n'
            f'FROM {route_fn} r\n'
            f'LEFT JOIN "{schema}"."{table}" e\n'
            f'  ON e."{idc}" = r.edge\n'
            f'ORDER BY r.seq;\n'
            f'CREATE INDEX ON public."{out}" USING GIST (geom);'
        )

    def _preview_routing(self):
        self._log.setPlainText(self._build_routing_sql())

    def _run_routing(self):
        self._exec_sql(self._build_routing_sql())

    def _run_create_topology(self):
        schema  = self._rt_schema.text().strip()
        table   = self._rt_table.currentText()
        geom    = self._rt_geom_col.text().strip()
        tol     = self._rt_tolerance.value()
        sql = (
            f"SELECT pgr_createTopology("
            f"'{schema}.{table}', {tol}, "
            f"'{geom}', 'id');")
        self._exec_sql(sql)

    # ── Shared exec ───────────────────────────────────────────────────────

    def _exec_sql(self, sql: str, fetch: bool = False):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection",
                                "Open a DB connection first.")
            return
        if self._worker and self._worker.isRunning():
            self._log_msg("Previous operation still running…", "warn")
            return
        w = AnalysisWorker(self._conn_params, sql, fetch=fetch)
        w.log.connect(self._log_msg)
        w.rows.connect(self._show_rows)
        self._worker = w
        launch(w)

    def _show_rows(self, rows: list, cols: list):
        t = self._result_table
        t.clear()
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setRowCount(0)
        for row in rows:
            r = t.rowCount()
            t.insertRow(r)
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(str(val) if val is not None else ""))
        if len(rows) == 500:
            self._log_msg("Showing first 500 rows.", "warn")

    def _log_msg(self, msg: str, level: str = "info"):
        colors = {
            "sql":     "#d2a8ff",
            "success": "#3fb950",
            "error":   "#ff7b72",
            "warn":    "#d29922",
            "info":    "#aaa",
        }
        self._log.append(
            f'<span style="color:{colors.get(level,"#aaa")};">{msg}</span>')

    # ── Helpers ───────────────────────────────────────────────────────────

    def _reload_geom_combo(self, schema_edit: QLineEdit,
                           table: str, combo: QComboBox):
        schema = schema_edit.text().strip()
        cols = _geom_cols(schema, table, self._conn_params)
        combo.clear()
        combo.addItems(cols or ["geom"])

    def _hrow(self, *widgets) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        for ww in widgets:
            lay.addWidget(ww)
        return w
