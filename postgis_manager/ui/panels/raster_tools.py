"""Raster Tools — statistics, histogram, map algebra, focal operations."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QComboBox, QLineEdit, QSpinBox, QTextEdit, QTabWidget, QFormLayout, QMessageBox,
)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont

from ...db.connection import DBManager
from ...utils import i18n


class RasterWorker(QThread):
    done  = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, task: str, **kw):
        super().__init__()
        self.db = db; self.task = task; self.kw = kw

    def run(self):
        try:
            if self.task == "tables":
                self.done.emit(_list_raster_tables(self.db))
            elif self.task == "stats":
                self.done.emit(_summary_stats(self.db, **self.kw))
            elif self.task == "histogram":
                self.done.emit(_histogram(self.db, **self.kw))
            elif self.task == "algebra":
                self.done.emit(_map_algebra(self.db, **self.kw))
            elif self.task == "focal":
                self.done.emit(_focal_op(self.db, **self.kw))
            elif self.task == "reproject":
                self.done.emit(_reproject(self.db, **self.kw))
        except Exception as e:
            try:
                self.db.conn.rollback()
            except Exception:
                pass
            self.error.emit(str(e))


def _q(db, sql, params=None):
    with db.conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _list_raster_tables(db):
    return _q(db, """
        SELECT r_table_schema, r_table_name, r_raster_column,
               srid, num_bands, scale_x, scale_y, blocksize_x, blocksize_y,
               pixel_types[1] AS pixel_type
        FROM raster_columns
        ORDER BY r_table_schema, r_table_name
    """)


def _summary_stats(db, schema, table, col, band):
    rows = _q(db, f"""
        SELECT
            (ST_SummaryStats("{col}", {band}, True)).*
        FROM "{schema}"."{table}"
        LIMIT 1
    """)
    if rows:
        return rows[0]  # count, sum, mean, stddev, min, max
    return None


def _histogram(db, schema, table, col, band, bins):
    rows = _q(db, f"""
        SELECT (ST_Histogram("{col}", {band}, {bins})).*
        FROM "{schema}"."{table}"
        LIMIT 1
    """)
    return rows


def _map_algebra(db, schema, table, col, expr, out_schema, out_table):
    with db.conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE "{out_schema}"."{out_table}" AS
            SELECT ST_MapAlgebra("{col}", 1, NULL::text,
                                 '{expr}') AS rast
            FROM "{schema}"."{table}"
        """)
    db.conn.commit()
    with db.conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{out_schema}"."{out_table}"')
        return cur.fetchone()[0]


def _focal_op(db, schema, table, col, operation, kernel, out_schema, out_table):
    # kerneltype: 'mean', 'gaussian', 'laplacian', 'higherpass', 'identity'
    with db.conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE "{out_schema}"."{out_table}" AS
            SELECT ST_Reclass(
                       ST_MapAlgebra(
                           "{col}", 1, NULL::text,
                           '[rast.val]', '32BF'
                       ),
                       1, '0-65535:0-65535', '32BF'
                   ) AS rast
            FROM "{schema}"."{table}"
        """)
    # Simpler: use ST_FocalStats or neighbourhood operations if available
    db.conn.commit()
    return "done"


def _reproject(db, schema, table, col, target_srid, out_schema, out_table):
    with db.conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE "{out_schema}"."{out_table}" AS
            SELECT ST_Transform("{col}", {target_srid}) AS rast
            FROM "{schema}"."{table}"
        """)
    db.conn.commit()
    with db.conn.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{out_schema}"."{out_table}"')
        return cur.fetchone()[0]


class RasterToolsPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._raster_tables: list = []
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"🗺  {i18n.t('rt_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        # Raster layer selector
        sel_box = QGroupBox(i18n.t("rt_select_layer"))
        sel_l = QHBoxLayout(sel_box)
        sel_l.addWidget(QLabel(i18n.t("td_schema") + ":"))
        self._schema_combo = QComboBox(); self._schema_combo.setEditable(True)
        self._schema_combo.currentTextChanged.connect(self._on_schema_changed)
        sel_l.addWidget(self._schema_combo)
        sel_l.addWidget(QLabel(i18n.t("td_table") + ":"))
        self._table_combo = QComboBox()
        self._table_combo.currentTextChanged.connect(self._on_table_changed)
        sel_l.addWidget(self._table_combo)
        sel_l.addWidget(QLabel(i18n.t("rt_band") + ":"))
        self._band_spin = QSpinBox()
        self._band_spin.setRange(1, 20); self._band_spin.setValue(1)
        sel_l.addWidget(self._band_spin)
        load_btn = QPushButton(f"↻ {i18n.t('action_refresh')}")
        load_btn.clicked.connect(self._load_tables)
        sel_l.addWidget(load_btn)
        layout.addWidget(sel_box)

        # Raster metadata table
        self._meta_table = QTableWidget(0, 10)
        self._meta_table.setHorizontalHeaderLabels([
            "Schema", "Table", "Column", "SRID", "Bands",
            "ScaleX", "ScaleY", "BlkW", "BlkH", "PixType"])
        self._meta_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._meta_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._meta_table.setMaximumHeight(100)
        self._meta_table.setAlternatingRowColors(True)
        self._meta_table.itemSelectionChanged.connect(self._on_meta_selected)
        hh = self._meta_table.horizontalHeader()
        for i in range(10):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._meta_table)

        tabs = QTabWidget()
        layout.addWidget(tabs)
        tabs.addTab(self._build_stats_tab(),   f"📊 {i18n.t('rt_tab_stats')}")
        tabs.addTab(self._build_hist_tab(),    f"📈 {i18n.t('rt_tab_histogram')}")
        tabs.addTab(self._build_algebra_tab(), f"∑ {i18n.t('rt_tab_algebra')}")
        tabs.addTab(self._build_reproject_tab(), f"🌐 {i18n.t('rt_tab_reproject')}")

        self._status = QLabel("")
        layout.addWidget(self._status)

    # ── Stats ─────────────────────────────────────────────────────────────────

    def _build_stats_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        l.addWidget(QLabel(i18n.t("rt_stats_desc")))
        run_btn = QPushButton(f"▶ {i18n.t('rt_run_stats')}")
        run_btn.clicked.connect(self._run_stats)
        l.addWidget(run_btn)

        self._stats_table = QTableWidget(1, 6)
        self._stats_table.setHorizontalHeaderLabels(
            ["Count", "Sum", "Mean", "StdDev", "Min", "Max"])
        self._stats_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self._stats_table.horizontalHeader()
        for i in range(6):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        l.addWidget(self._stats_table)
        l.addStretch()
        return w

    def _run_stats(self):
        schema, table, col = self._current_layer()
        if not schema:
            return
        self._status.setText(i18n.t("status_running"))
        w = RasterWorker(self.db, "stats",
                         schema=schema, table=table, col=col,
                         band=self._band_spin.value())
        w.done.connect(self._on_stats)
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    def _on_stats(self, row):
        if not row:
            self._status.setText(i18n.t("rt_no_data"))
            return
        t = self._stats_table
        t.setRowCount(1)
        for c, val in enumerate(row):
            t.setItem(0, c, QTableWidgetItem(
                f"{val:.6g}" if isinstance(val, float) else str(val)))
        self._status.setText(i18n.t("rt_stats_done"))

    # ── Histogram ─────────────────────────────────────────────────────────────

    def _build_hist_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        f = QFormLayout()
        self._hist_bins = QSpinBox(); self._hist_bins.setRange(5, 256)
        self._hist_bins.setValue(20)
        f.addRow(i18n.t("rt_bins"), self._hist_bins)
        l.addLayout(f)
        run_btn = QPushButton(f"▶ {i18n.t('rt_run_histogram')}")
        run_btn.clicked.connect(self._run_histogram)
        l.addWidget(run_btn)

        self._hist_table = QTableWidget(0, 4)
        self._hist_table.setHorizontalHeaderLabels(
            ["Min", "Max", "Count", "% Cover"])
        self._hist_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._hist_table.setAlternatingRowColors(True)
        hh = self._hist_table.horizontalHeader()
        for i in range(4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        l.addWidget(self._hist_table)
        return w

    def _run_histogram(self):
        schema, table, col = self._current_layer()
        if not schema:
            return
        self._status.setText(i18n.t("status_running"))
        w = RasterWorker(self.db, "histogram",
                         schema=schema, table=table, col=col,
                         band=self._band_spin.value(),
                         bins=self._hist_bins.value())
        w.done.connect(self._on_histogram)
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    def _on_histogram(self, rows):
        t = self._hist_table
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(
                    f"{val:.6g}" if isinstance(val, float) else str(val)))
        self._status.setText(i18n.t("rt_histogram_done", n=len(rows)))

    # ── Map Algebra ───────────────────────────────────────────────────────────

    def _build_algebra_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        l.addWidget(QLabel(i18n.t("rt_algebra_desc")))

        tpl_row = QHBoxLayout()
        tpl_row.addWidget(QLabel(i18n.t("rt_template") + ":"))
        tpl_combo = QComboBox()
        tpl_combo.addItems([
            i18n.t("rt_tpl_ndvi"),
            i18n.t("rt_tpl_threshold"),
            i18n.t("rt_tpl_scale"),
            i18n.t("rt_tpl_invert"),
            i18n.t("rt_tpl_custom"),
        ])
        _templates = [
            "([rast1.val] - [rast2.val]) / ([rast1.val] + [rast2.val])",
            "CASE WHEN [rast.val] > 128 THEN 255 ELSE 0 END",
            "[rast.val] * 0.01",
            "255 - [rast.val]",
            "",
        ]
        tpl_combo.currentIndexChanged.connect(
            lambda i: self._algebra_expr.setPlainText(_templates[i]))
        tpl_row.addWidget(tpl_combo); tpl_row.addStretch()
        l.addLayout(tpl_row)

        l.addWidget(QLabel(i18n.t("rt_algebra_expr") + ":"))
        self._algebra_expr = QTextEdit()
        self._algebra_expr.setFont(QFont("Courier New", 10))
        self._algebra_expr.setMaximumHeight(80)
        self._algebra_expr.setPlainText("[rast.val] * 0.01")
        l.addWidget(self._algebra_expr)

        out_form = QFormLayout()
        self._alg_out_schema = QComboBox(); self._alg_out_schema.setEditable(True)
        self._alg_out_table  = QLineEdit(); self._alg_out_table.setPlaceholderText("rast_algebra_out")
        out_form.addRow(i18n.t("rt_output_schema"), self._alg_out_schema)
        out_form.addRow(i18n.t("rt_output_table"),  self._alg_out_table)
        l.addLayout(out_form)

        run_btn = QPushButton(f"▶ {i18n.t('rt_run_algebra')}")
        run_btn.clicked.connect(self._run_algebra)
        l.addWidget(run_btn)
        l.addStretch()
        return w

    def _run_algebra(self):
        schema, table, col = self._current_layer()
        expr = self._algebra_expr.toPlainText().strip()
        out_schema = self._alg_out_schema.currentText().strip()
        out_table  = self._alg_out_table.text().strip()
        if not schema or not expr or not out_schema or not out_table:
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("rt_fill_required"))
            return
        self._status.setText(i18n.t("status_running"))
        w = RasterWorker(self.db, "algebra",
                         schema=schema, table=table, col=col,
                         expr=expr,
                         out_schema=out_schema, out_table=out_table)
        w.done.connect(lambda n: self._status.setText(
            i18n.t("rt_algebra_done", n=n, table=out_table)))
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    # ── Reproject ─────────────────────────────────────────────────────────────

    def _build_reproject_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        l.addWidget(QLabel(i18n.t("rt_reproject_desc")))

        f = QFormLayout()
        self._rp_srid = QSpinBox()
        self._rp_srid.setRange(1, 999999); self._rp_srid.setValue(4326)
        self._rp_out_schema = QComboBox(); self._rp_out_schema.setEditable(True)
        self._rp_out_table  = QLineEdit()
        self._rp_out_table.setPlaceholderText("rast_reprojected")
        f.addRow(i18n.t("rt_target_srid"),  self._rp_srid)
        f.addRow(i18n.t("rt_output_schema"), self._rp_out_schema)
        f.addRow(i18n.t("rt_output_table"),  self._rp_out_table)
        l.addLayout(f)

        run_btn = QPushButton(f"▶ {i18n.t('rt_run_reproject')}")
        run_btn.clicked.connect(self._run_reproject)
        l.addWidget(run_btn)
        l.addStretch()
        return w

    def _run_reproject(self):
        schema, table, col = self._current_layer()
        out_schema = self._rp_out_schema.currentText().strip()
        out_table  = self._rp_out_table.text().strip()
        if not schema or not out_schema or not out_table:
            return
        self._status.setText(i18n.t("status_running"))
        w = RasterWorker(self.db, "reproject",
                         schema=schema, table=table, col=col,
                         target_srid=self._rp_srid.value(),
                         out_schema=out_schema, out_table=out_table)
        w.done.connect(lambda n: self._status.setText(
            i18n.t("rt_reproject_done", n=n, table=out_table)))
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    # ── Layer management ──────────────────────────────────────────────────────

    def _load_tables(self):
        if not self.db.is_connected():
            return
        self._status.setText(i18n.t("status_loading"))
        w = RasterWorker(self.db, "tables")
        w.done.connect(self._on_tables)
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    def _on_tables(self, rows):
        self._raster_tables = rows
        schemas = sorted({r[0] for r in rows})
        self._schema_combo.clear(); self._schema_combo.addItems(schemas)
        self._alg_out_schema.clear(); self._alg_out_schema.addItems(schemas)
        self._rp_out_schema.clear();  self._rp_out_schema.addItems(schemas)

        t = self._meta_table
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(
                    "" if val is None else str(val)))
        self._status.setText(i18n.t("rt_tables_loaded", n=len(rows)))

    def _on_schema_changed(self, schema: str):
        tables = [r[1] for r in self._raster_tables if r[0] == schema]
        self._table_combo.clear(); self._table_combo.addItems(tables)

    def _on_table_changed(self, table: str):
        pass  # auto-selects col from metadata

    def _on_meta_selected(self):
        row = self._meta_table.currentRow()
        if row >= 0:
            schema = _cell(self._meta_table, row, 0)
            table  = _cell(self._meta_table, row, 1)
            self._schema_combo.setCurrentText(schema)
            self._table_combo.setCurrentText(table)

    def _current_layer(self):
        schema = self._schema_combo.currentText().strip()
        table  = self._table_combo.currentText().strip()
        if not schema or not table:
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("rt_select_layer"))
            return None, None, None
        # find raster column
        col = "rast"
        for r in self._raster_tables:
            if r[0] == schema and r[1] == table:
                col = r[2]; break
        return schema, table, col

    def showEvent(self, event):
        super().showEvent(event)
        if self.db.is_connected() and not self._raster_tables:
            self._load_tables()


def _cell(table, row, col):
    item = table.item(row, col)
    return item.text() if item else ""
