"""Live Monitoring Panel — active queries, locks, bloat, index usage."""

from __future__ import annotations
import psycopg2
import psycopg2.extras
from PyQt6.QtCore import QThread, pyqtSignal, QTimer, Qt
from PyQt6.QtGui import QFont, QColor, QBrush
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QSpinBox,
    QAbstractItemView, QMessageBox,
    QCheckBox,
)


# ── Worker ────────────────────────────────────────────────────────────────

class MonitorWorker(QThread):
    done  = pyqtSignal(list, list, str)   # rows, cols, tag
    error = pyqtSignal(str, str)

    def __init__(self, conn_params: dict, sql: str, tag: str):
        super().__init__()
        self.conn_params = conn_params
        self.sql = sql
        self.tag = tag

    def run(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(self.sql)
            cols = [d[0] for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]
            cur.close(); conn.close()
            self.done.emit(rows, cols, self.tag)
        except Exception as e:
            self.error.emit(str(e), self.tag)


# ── SQL queries ───────────────────────────────────────────────────────────

SQL_ACTIVE = """
SELECT
  pid,
  usename,
  application_name,
  client_addr,
  state,
  ROUND(EXTRACT(epoch FROM (now() - query_start))::numeric, 1) AS duration_s,
  wait_event_type,
  wait_event,
  LEFT(query, 120) AS query
FROM pg_stat_activity
WHERE state != 'idle'
  AND pid != pg_backend_pid()
ORDER BY duration_s DESC NULLS LAST
"""

SQL_LOCKS = """
SELECT
  blocked.pid               AS blocked_pid,
  blocked.usename           AS blocked_user,
  blocking.pid              AS blocking_pid,
  blocking.usename          AS blocking_user,
  blocked.query             AS blocked_query,
  blocking.query            AS blocking_query,
  blocked_locks.locktype,
  blocked_locks.relation::regclass AS relation
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked
  ON blocked.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks
  ON blocking_locks.locktype = blocked_locks.locktype
  AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
  AND blocking_locks.page    IS NOT DISTINCT FROM blocked_locks.page
  AND blocking_locks.tuple   IS NOT DISTINCT FROM blocked_locks.tuple
  AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking
  ON blocking.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted
"""

SQL_BLOAT = """
SELECT
  schemaname,
  relname                            AS table_name,
  n_live_tup,
  n_dead_tup,
  CASE WHEN n_live_tup > 0
       THEN ROUND(100.0 * n_dead_tup / (n_live_tup + n_dead_tup), 1)
       ELSE 0 END                    AS dead_pct,
  pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
  last_autovacuum,
  last_autoanalyze
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC
LIMIT 50
"""

SQL_INDEXES = """
SELECT
  schemaname,
  relname         AS table_name,
  indexrelname    AS index_name,
  idx_scan,
  idx_tup_read,
  idx_tup_fetch,
  pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
ORDER BY idx_scan ASC, pg_relation_size(indexrelid) DESC
LIMIT 50
"""

SQL_CONNECTIONS = """
SELECT
  state,
  COUNT(*)                                          AS count,
  ROUND(AVG(EXTRACT(epoch FROM (now()-query_start)))::numeric,1) AS avg_duration_s
FROM pg_stat_activity
GROUP BY state
ORDER BY count DESC
"""

SQL_DB_STATS = """
SELECT
  pg_database.datname,
  pg_size_pretty(pg_database_size(pg_database.datname)) AS size,
  numbackends                                            AS connections,
  xact_commit,
  xact_rollback,
  blks_hit,
  blks_read,
  CASE WHEN blks_hit + blks_read > 0
       THEN ROUND(100.0 * blks_hit / (blks_hit + blks_read), 2)
       ELSE 0 END AS cache_hit_pct,
  tup_inserted,
  tup_updated,
  tup_deleted
FROM pg_stat_database
JOIN pg_database ON pg_database.oid = pg_stat_database.datid
WHERE pg_database.datname = current_database()
"""


# ── Main panel ────────────────────────────────────────────────────────────

class MonitorPanel(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._conn_params: dict = {}
        self._workers: list[MonitorWorker] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_active_tab)
        self._setup_ui()

    def set_connection(self, params: dict):
        self._conn_params = params
        self._refresh_all()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # Auto-refresh bar
        top = QHBoxLayout()
        top.setContentsMargins(6, 4, 6, 0)
        self._auto_chk = QCheckBox("Auto-refresh every")
        self._auto_chk.toggled.connect(self._toggle_autorefresh)
        self._interval = QSpinBox()
        self._interval.setRange(2, 300)
        self._interval.setValue(5)
        self._interval.setSuffix(" s")
        btn_refresh = QPushButton("↺ Refresh now")
        btn_refresh.clicked.connect(self._refresh_all)
        self._last_updated = QLabel("—")
        self._last_updated.setStyleSheet("color:gray; font-size:11px;")
        top.addWidget(self._auto_chk)
        top.addWidget(self._interval)
        top.addWidget(btn_refresh)
        top.addStretch()
        top.addWidget(QLabel("Last updated:"))
        top.addWidget(self._last_updated)
        root.addLayout(top)

        self._tabs = QTabWidget()
        self._tabs.currentChanged.connect(self._on_tab_change)
        self._tabs.addTab(self._build_active_tab(),  "⚡  Active Queries")
        self._tabs.addTab(self._build_locks_tab(),   "🔒  Lock Waits")
        self._tabs.addTab(self._build_bloat_tab(),   "🗑  Table Bloat")
        self._tabs.addTab(self._build_index_tab(),   "📊  Index Usage")
        self._tabs.addTab(self._build_stats_tab(),   "📈  DB Statistics")
        root.addWidget(self._tabs, 1)

    def _toggle_autorefresh(self, on: bool):
        if on:
            self._timer.start(self._interval.value() * 1000)
        else:
            self._timer.stop()

    def _refresh_active_tab(self):
        idx = self._tabs.currentIndex()
        tag_map = {0: "active", 1: "locks", 2: "bloat", 3: "indexes", 4: "stats"}
        tag = tag_map.get(idx)
        if tag:
            self._fetch(tag)

    def _on_tab_change(self, idx: int):
        self._refresh_active_tab()

    def _refresh_all(self):
        for tag in ("active", "locks", "bloat", "indexes", "stats"):
            self._fetch(tag)

    # ── Active queries ────────────────────────────────────────────────────

    def _build_active_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        self._active_table = self._make_table()
        lay.addWidget(self._active_table, 1)

        btn_row = QHBoxLayout()
        btn_kill = QPushButton("🛑 Terminate selected")
        btn_kill.clicked.connect(self._kill_selected)
        btn_kill_all = QPushButton("☢ Cancel all idle-in-transaction")
        btn_kill_all.clicked.connect(self._kill_idle_in_transaction)
        self._slow_threshold = QSpinBox()
        self._slow_threshold.setRange(0, 3600)
        self._slow_threshold.setValue(30)
        self._slow_threshold.setSuffix(" s")
        btn_kill_slow = QPushButton("Kill queries longer than")
        btn_kill_slow.clicked.connect(self._kill_slow)
        btn_row.addWidget(btn_kill)
        btn_row.addWidget(btn_kill_all)
        btn_row.addStretch()
        btn_row.addWidget(btn_kill_slow)
        btn_row.addWidget(self._slow_threshold)
        lay.addLayout(btn_row)
        return w

    def _kill_selected(self):
        t = self._active_table
        rows = {i.row() for i in t.selectedItems()}
        for r in rows:
            pid_item = t.item(r, 0)
            if pid_item:
                self._exec_term(pid_item.text())

    def _kill_idle_in_transaction(self):
        self._exec_sql(
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity "
            "WHERE state = 'idle in transaction'",
            "kill_idle")

    def _kill_slow(self):
        secs = self._slow_threshold.value()
        self._exec_sql(
            f"SELECT pg_terminate_backend(pid) "
            f"FROM pg_stat_activity "
            f"WHERE state != 'idle' "
            f"AND query_start < now() - interval '{secs} seconds' "
            f"AND pid != pg_backend_pid()",
            "kill_slow")

    def _exec_term(self, pid: str):
        self._exec_sql(
            f"SELECT pg_terminate_backend({pid})", "terminate")

    # ── Lock waits ────────────────────────────────────────────────────────

    def _build_locks_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        info = QLabel(
            "Shows processes blocked by other processes. "
            "Blocking PID can be terminated from Active Queries tab.")
        info.setWordWrap(True)
        info.setStyleSheet("font-size:11px; color:gray;")
        lay.addWidget(info)
        self._locks_table = self._make_table()
        lay.addWidget(self._locks_table, 1)
        return w

    # ── Table bloat ───────────────────────────────────────────────────────

    def _build_bloat_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        self._bloat_table = self._make_table()
        lay.addWidget(self._bloat_table, 1)

        btn_row = QHBoxLayout()
        btn_vacuum = QPushButton("🧹 VACUUM selected table")
        btn_vacuum.clicked.connect(self._vacuum_selected)
        btn_analyze = QPushButton("🔍 ANALYZE selected table")
        btn_analyze.clicked.connect(self._analyze_selected)
        btn_row.addWidget(btn_vacuum)
        btn_row.addWidget(btn_analyze)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return w

    def _vacuum_selected(self):
        row = self._bloat_table.currentRow()
        if row < 0: return
        schema = self._bloat_table.item(row, 0).text()
        table  = self._bloat_table.item(row, 1).text()
        self._exec_sql(f'VACUUM ANALYZE "{schema}"."{table}"', "vacuum")

    def _analyze_selected(self):
        row = self._bloat_table.currentRow()
        if row < 0: return
        schema = self._bloat_table.item(row, 0).text()
        table  = self._bloat_table.item(row, 1).text()
        self._exec_sql(f'ANALYZE "{schema}"."{table}"', "analyze")

    # ── Index usage ───────────────────────────────────────────────────────

    def _build_index_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        info = QLabel(
            "Sorted by fewest scans first — low idx_scan + large size = "
            "candidate for dropping.")
        info.setWordWrap(True)
        info.setStyleSheet("font-size:11px; color:gray;")
        lay.addWidget(info)
        self._index_table = self._make_table()
        lay.addWidget(self._index_table, 1)

        btn_row = QHBoxLayout()
        btn_drop = QPushButton("🗑 DROP selected index")
        btn_drop.clicked.connect(self._drop_index)
        btn_row.addWidget(btn_drop)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return w

    def _drop_index(self):
        row = self._index_table.currentRow()
        if row < 0: return
        schema = self._index_table.item(row, 0).text()
        idx    = self._index_table.item(row, 2).text()
        if QMessageBox.question(self, "Drop index",
                f'DROP INDEX CONCURRENTLY "{schema}"."{idx}"?'
                ) == QMessageBox.StandardButton.Yes:
            self._exec_sql(
                f'DROP INDEX CONCURRENTLY "{schema}"."{idx}"', "drop_idx")

    # ── DB stats ──────────────────────────────────────────────────────────

    def _build_stats_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        self._stats_table = self._make_table()
        lay.addWidget(self._stats_table, 1)
        return w

    # ── Fetch & populate ──────────────────────────────────────────────────

    def _fetch(self, tag: str):
        if not self._conn_params:
            return
        sql_map = {
            "active":  SQL_ACTIVE,
            "locks":   SQL_LOCKS,
            "bloat":   SQL_BLOAT,
            "indexes": SQL_INDEXES,
            "stats":   SQL_DB_STATS,
        }
        sql = sql_map.get(tag)
        if not sql:
            return
        w = MonitorWorker(self._conn_params, sql, tag)
        w.done.connect(self._on_data, Qt.ConnectionType.QueuedConnection)
        w.error.connect(self._on_error, Qt.ConnectionType.QueuedConnection)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _on_data(self, rows: list, cols: list, tag: str):
        from PyQt6.QtCore import QDateTime
        self._last_updated.setText(
            QDateTime.currentDateTime().toString("HH:mm:ss"))
        tbl_map = {
            "active":  self._active_table,
            "locks":   self._locks_table,
            "bloat":   self._bloat_table,
            "indexes": self._index_table,
            "stats":   self._stats_table,
        }
        tbl = tbl_map.get(tag)
        if not tbl:
            return
        tbl.blockSignals(True)
        tbl.clear()
        tbl.setColumnCount(len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setRowCount(0)
        for row in rows:
            r = tbl.rowCount(); tbl.insertRow(r)
            for c, val in enumerate(row):
                item = QTableWidgetItem(str(val) if val is not None else "")
                # colour coding
                if tag == "active" and c == 4:   # state
                    if str(val) == "active":
                        item.setForeground(QBrush(QColor("#3fb950")))
                    elif "transaction" in str(val):
                        item.setForeground(QBrush(QColor("#ff7b72")))
                if tag == "bloat" and c == 4:    # dead_pct
                    try:
                        pct = float(val)
                        if pct > 30:
                            item.setForeground(QBrush(QColor("#ff7b72")))
                        elif pct > 10:
                            item.setForeground(QBrush(QColor("#d29922")))
                    except (ValueError, TypeError):
                        pass
                if tag == "indexes" and c == 3:  # idx_scan
                    try:
                        if int(val) == 0:
                            item.setForeground(QBrush(QColor("#ff7b72")))
                    except (ValueError, TypeError):
                        pass
                tbl.setItem(r, c, item)
        tbl.blockSignals(False)

    def _on_error(self, error: str, tag: str):
        pass  # silently ignore (e.g. cron table not found)

    def _exec_sql(self, sql: str, tag: str):
        if not self._conn_params:
            return
        w = MonitorWorker(self._conn_params, sql, tag)
        w.done.connect(lambda r, c, t: self._refresh_all(), Qt.ConnectionType.QueuedConnection)
        w.error.connect(lambda e, t: QMessageBox.critical(self, "Error", e), Qt.ConnectionType.QueuedConnection)
        self._workers.append(w)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        w.start()

    @staticmethod
    def _make_table() -> QTableWidget:
        t = QTableWidget(0, 1)
        t.horizontalHeader().setStretchLastSection(True)
        t.setAlternatingRowColors(True)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setFont(QFont("Courier New", 10))
        return t
