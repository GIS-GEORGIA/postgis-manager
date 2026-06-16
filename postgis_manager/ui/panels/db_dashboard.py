"""DB Dashboard — server statistics, table sizes, connections, health."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QFrame, QProgressBar, QSplitter,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont

from ...db.connection import DBManager
from ...utils import i18n


class DashboardWorker(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager):
        super().__init__()
        self.db = db

    def run(self):
        try:
            self.done.emit(_collect_stats(self.db))
        except Exception as e:
            self.error.emit(str(e))


def _q(db, sql, params=()):
    with db.conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _collect_stats(db: DBManager) -> dict:
    stats: dict = {}

    # ── Server info ───────────────────────────────────────────────────────────
    try:
        rows = _q(db, "SELECT version()")
        stats["pg_version"] = rows[0][0].split(",")[0] if rows else "?"
    except Exception:
        stats["pg_version"] = "?"

    try:
        rows = _q(db, "SELECT PostGIS_Lib_Version()")
        stats["postgis_version"] = rows[0][0] if rows else "N/A"
    except Exception:
        stats["postgis_version"] = "N/A"

    try:
        rows = _q(db, "SELECT pg_postmaster_start_time()")
        stats["start_time"] = str(rows[0][0])[:19] if rows else "?"
    except Exception:
        stats["start_time"] = "?"

    # ── Database size ─────────────────────────────────────────────────────────
    try:
        rows = _q(db, "SELECT pg_size_pretty(pg_database_size(current_database()))")
        stats["db_size"] = rows[0][0] if rows else "?"
    except Exception:
        stats["db_size"] = "?"

    # ── Active connections ────────────────────────────────────────────────────
    try:
        rows = _q(db, """
            SELECT count(*), max_conn
            FROM pg_stat_activity,
                 (SELECT setting::int AS max_conn FROM pg_settings
                  WHERE name='max_connections') s
            GROUP BY max_conn
        """)
        if rows:
            stats["conn_active"] = rows[0][0]
            stats["conn_max"]    = rows[0][1]
        else:
            stats["conn_active"] = 0; stats["conn_max"] = 100
    except Exception:
        stats["conn_active"] = 0; stats["conn_max"] = 100

    # ── Cache hit ratio ───────────────────────────────────────────────────────
    try:
        rows = _q(db, """
            SELECT round(100.0 * sum(blks_hit) /
                         NULLIF(sum(blks_hit) + sum(blks_read), 0), 2)
            FROM pg_stat_database
            WHERE datname = current_database()
        """)
        stats["cache_hit"] = float(rows[0][0] or 0) if rows else 0.0
    except Exception:
        stats["cache_hit"] = 0.0

    # ── Transactions per second (approx) ─────────────────────────────────────
    try:
        rows = _q(db, """
            SELECT xact_commit + xact_rollback
            FROM pg_stat_database WHERE datname = current_database()
        """)
        stats["total_xacts"] = rows[0][0] if rows else 0
    except Exception:
        stats["total_xacts"] = 0

    # ── Table sizes (top 20) ──────────────────────────────────────────────────
    try:
        stats["table_sizes"] = _q(db, """
            SELECT
                schemaname,
                relname,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                pg_size_pretty(pg_relation_size(relid))        AS table_size,
                pg_size_pretty(pg_total_relation_size(relid) -
                               pg_relation_size(relid))        AS index_size,
                n_live_tup AS row_estimate
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            LIMIT 20
        """)
    except Exception:
        stats["table_sizes"] = []

    # ── Index health (unused indexes) ─────────────────────────────────────────
    try:
        stats["unused_indexes"] = _q(db, """
            SELECT schemaname, relname, indexrelname,
                   idx_scan, pg_size_pretty(pg_relation_size(indexrelid)) AS idx_size
            FROM pg_stat_user_indexes
            WHERE idx_scan = 0
              AND indexrelname NOT LIKE '%_pkey'
            ORDER BY pg_relation_size(indexrelid) DESC
            LIMIT 15
        """)
    except Exception:
        stats["unused_indexes"] = []

    # ── Active queries ────────────────────────────────────────────────────────
    try:
        stats["active_queries"] = _q(db, """
            SELECT pid, usename, state,
                   round(EXTRACT(EPOCH FROM (now()-query_start))::numeric, 1) AS secs,
                   left(query, 100) AS query
            FROM pg_stat_activity
            WHERE state IS NOT NULL AND pid <> pg_backend_pid()
            ORDER BY query_start NULLS LAST
            LIMIT 20
        """)
    except Exception:
        stats["active_queries"] = []

    # ── Extensions installed ──────────────────────────────────────────────────
    try:
        stats["extensions"] = _q(db, """
            SELECT extname, extversion
            FROM pg_extension ORDER BY extname
        """)
    except Exception:
        stats["extensions"] = []

    return stats


# ── Metric card widget ─────────────────────────────────────────────────────────

class MetricCard(QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(140)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self._lbl = QLabel(label)
        self._lbl.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(self._lbl)
        self._val = QLabel("—")
        f = QFont(); f.setPointSize(16); f.setBold(True)
        self._val.setFont(f)
        layout.addWidget(self._val)

    def set_value(self, text: str, color: str = ""):
        self._val.setText(text)
        if color:
            self._val.setStyleSheet(f"color: {color};")


# ── Main panel ────────────────────────────────────────────────────────────────

class DBDashboardPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._worker: DashboardWorker | None = None
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self.refresh)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel(f"📊  {i18n.t('dash_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        hdr.addWidget(title)
        hdr.addStretch()
        self._refresh_btn = QPushButton(f"↻  {i18n.t('action_refresh')}")
        self._refresh_btn.clicked.connect(self.refresh)
        hdr.addWidget(self._refresh_btn)
        self._auto_lbl = QLabel("")
        hdr.addWidget(self._auto_lbl)
        layout.addLayout(hdr)

        # ── Metric cards ──────────────────────────────────────────────────────
        cards_layout = QHBoxLayout()
        self._card_pg      = MetricCard("PostgreSQL")
        self._card_postgis = MetricCard("PostGIS")
        self._card_dbsize  = MetricCard(i18n.t("dash_db_size"))
        self._card_conns   = MetricCard(i18n.t("dash_connections"))
        self._card_cache   = MetricCard(i18n.t("dash_cache_hit"))
        self._card_ext     = MetricCard(i18n.t("dash_extensions"))
        for card in [self._card_pg, self._card_postgis, self._card_dbsize,
                     self._card_conns, self._card_cache, self._card_ext]:
            cards_layout.addWidget(card)
        layout.addLayout(cards_layout)

        # Connection bar
        conn_box = QGroupBox(i18n.t("dash_connection_usage"))
        conn_l = QVBoxLayout(conn_box)
        self._conn_bar = QProgressBar()
        self._conn_bar.setTextVisible(True)
        self._conn_bar.setFormat("%v / %m  (%p%)")
        conn_l.addWidget(self._conn_bar)
        layout.addWidget(conn_box)

        # Cache bar
        cache_box = QGroupBox(i18n.t("dash_cache_hit_ratio"))
        cache_l = QVBoxLayout(cache_box)
        self._cache_bar = QProgressBar()
        self._cache_bar.setRange(0, 100)
        self._cache_bar.setFormat("%p%")
        cache_l.addWidget(self._cache_bar)
        layout.addWidget(cache_box)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── Table sizes ───────────────────────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel(f"<b>{i18n.t('dash_table_sizes')}</b>"))
        self._size_table = _make_table(
            ["Schema", "Table", "Total", "Data", "Indexes", "~Rows"])
        ll.addWidget(self._size_table)
        splitter.addWidget(left)

        # ── Right column: active queries + extensions ─────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right); rl.setContentsMargins(4, 0, 0, 0)
        rl.addWidget(QLabel(f"<b>{i18n.t('dash_active_queries')}</b>"))
        self._query_table = _make_table(["PID", "User", "State", "Secs", "Query"])
        rl.addWidget(self._query_table)

        rl.addWidget(QLabel(f"<b>{i18n.t('dash_unused_indexes')}</b>"))
        self._idx_table = _make_table(
            ["Schema", "Table", "Index", "Scans", "Size"])
        self._idx_table.setMaximumHeight(140)
        rl.addWidget(self._idx_table)
        splitter.addWidget(right)
        splitter.setSizes([600, 500])

        self._status_lbl = QLabel(i18n.t("dash_not_connected"))
        layout.addWidget(self._status_lbl)

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        if not self.db.is_connected():
            self._status_lbl.setText(i18n.t("dash_not_connected"))
            return
        self._refresh_btn.setEnabled(False)
        self._status_lbl.setText(i18n.t("status_loading"))
        w = DashboardWorker(self.db)
        w.done.connect(self._on_data)
        w.error.connect(self._on_error)
        w.finished.connect(lambda: self._refresh_btn.setEnabled(True))
        w.start(); self._worker = w

    def _on_data(self, stats: dict):
        # Cards
        pg_ver = stats.get("pg_version", "?")
        self._card_pg.set_value(pg_ver.replace("PostgreSQL ", ""))
        self._card_postgis.set_value(stats.get("postgis_version", "N/A"),
                                     "#1B5E20")
        self._card_dbsize.set_value(stats.get("db_size", "?"))
        active = stats.get("conn_active", 0)
        maxc   = stats.get("conn_max", 100)
        self._card_conns.set_value(
            f"{active} / {maxc}",
            "#C62828" if active > maxc * 0.8 else "#2E7D32")
        cache = stats.get("cache_hit", 0.0)
        self._card_cache.set_value(
            f"{cache:.1f}%",
            "#2E7D32" if cache >= 95 else "#E65100" if cache >= 80 else "#C62828")
        ext_count = len(stats.get("extensions", []))
        self._card_ext.set_value(str(ext_count))

        # Bars
        self._conn_bar.setRange(0, maxc)
        self._conn_bar.setValue(active)
        self._cache_bar.setValue(int(cache))
        if cache >= 95:
            self._cache_bar.setStyleSheet("QProgressBar::chunk{background:#2E7D32}")
        elif cache >= 80:
            self._cache_bar.setStyleSheet("QProgressBar::chunk{background:#E65100}")
        else:
            self._cache_bar.setStyleSheet("QProgressBar::chunk{background:#C62828}")

        # Table sizes
        _fill(self._size_table, stats.get("table_sizes", []),
              col_formats={5: lambda v: f"{v:,}" if v else "0"})

        # Active queries
        _fill(self._query_table, stats.get("active_queries", []))

        # Unused indexes
        unused = stats.get("unused_indexes", [])
        _fill(self._idx_table, unused)
        if unused:
            self._idx_table.horizontalHeader().parent().parent() \
                if False else None  # just a note
            for r in range(self._idx_table.rowCount()):
                item = self._idx_table.item(r, 3)
                if item:
                    item.setForeground(QColor("#C62828"))

        self._status_lbl.setText(
            i18n.t("dash_updated", db=self.db.params.get("dbname", "")))

    def _on_error(self, msg: str):
        self._status_lbl.setText(f"Error: {msg}")

    def showEvent(self, event):
        super().showEvent(event)
        if self.db.is_connected() and not self._worker:
            self.refresh()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget()
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    t.setSortingEnabled(True)
    hh = t.horizontalHeader()
    for i in range(len(headers) - 1):
        hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
    hh.setSectionResizeMode(len(headers) - 1, QHeaderView.ResizeMode.Stretch)
    return t


def _fill(table: QTableWidget, rows: list, col_formats: dict = None):
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            if col_formats and c in col_formats:
                text = col_formats[c](val)
            else:
                text = "" if val is None else str(val)
            table.setItem(r, c, QTableWidgetItem(text))
    table.setSortingEnabled(True)
