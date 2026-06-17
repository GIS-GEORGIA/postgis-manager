"""Database Health Dashboard.

Single-panel view of every geometry table in the database:
  • row count & size
  • GIST spatial index presence
  • geometry validity %
  • last VACUUM / ANALYZE date
  • one-click fixes: create index, ST_MakeValid, VACUUM ANALYZE
"""

from __future__ import annotations
import time
from typing import Optional

import psycopg2
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QProgressBar, QApplication, QMessageBox, QAbstractItemView,
    QFrame, QSizePolicy,
)

from ...utils.workers import launch


# ── SQL ───────────────────────────────────────────────────────────────────

_SQL_TABLES = """
SELECT
    f.f_table_schema                                           AS schema,
    f.f_table_name                                             AS tbl,
    f.f_geometry_column                                        AS gcol,
    COALESCE(f.srid, 0)                                        AS srid,
    f.type                                                     AS gtype,
    COALESCE(s.n_live_tup, 0)                                  AS row_est,
    pg_total_relation_size(
        quote_ident(f.f_table_schema)||'.'||
        quote_ident(f.f_table_name))                           AS size_bytes,
    pg_size_pretty(
        pg_total_relation_size(
            quote_ident(f.f_table_schema)||'.'||
            quote_ident(f.f_table_name)))                      AS size_pretty,
    s.last_analyze,
    s.last_vacuum
FROM geometry_columns f
LEFT JOIN pg_stat_user_tables s
       ON s.schemaname = f.f_table_schema
      AND s.relname    = f.f_table_name
WHERE f.f_table_schema NOT IN ('pg_catalog','information_schema',
                                'topology','tiger','tiger_data')
ORDER BY f.f_table_schema, f.f_table_name;
"""

_SQL_HAS_GIST = """
SELECT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = %s
      AND tablename  = %s
      AND indexdef   ILIKE '%%USING gist%%'
      AND indexdef   ILIKE %s
);
"""

_SQL_VALIDITY = """
SELECT
    COUNT(*)                                          AS total,
    COUNT(*) FILTER (WHERE ST_IsValid({gcol}))        AS valid
FROM {schema}.{tbl}
LIMIT 50000;
"""


# ── Workers ───────────────────────────────────────────────────────────────

class ScanWorker(QThread):
    """Scans all geometry tables; emits one row dict at a time."""
    row_ready = pyqtSignal(dict)
    finished  = pyqtSignal(int)   # total rows scanned
    error     = pyqtSignal(str)

    def __init__(self, conn_params: dict):
        super().__init__()
        self.conn_params = conn_params
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            cur  = conn.cursor()
            cur.execute(_SQL_TABLES)
            tables = cur.fetchall()
            cols   = [d[0] for d in cur.description]

            count = 0
            for raw in tables:
                if self._cancelled:
                    break
                row = dict(zip(cols, raw))

                # Check GIST index
                like_pat = f"%{row['gcol']}%"
                cur.execute(_SQL_HAS_GIST, (row['schema'], row['tbl'], like_pat))
                row['has_gist'] = cur.fetchone()[0]

                self.row_ready.emit(row)
                count += 1

            cur.close()
            conn.close()
            self.finished.emit(count)
        except Exception as e:
            self.error.emit(str(e))


class ValidityWorker(QThread):
    """Checks ST_IsValid for one table; emits (total, valid)."""
    done  = pyqtSignal(int, int)
    error = pyqtSignal(str)

    def __init__(self, conn_params: dict, schema: str, tbl: str, gcol: str):
        super().__init__()
        self.conn_params = conn_params
        self.schema = schema
        self.tbl    = tbl
        self.gcol   = gcol

    def run(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            cur  = conn.cursor()
            sql  = _SQL_VALIDITY.format(
                gcol   = f'"{self.gcol}"',
                schema = f'"{self.schema}"',
                tbl    = f'"{self.tbl}"',
            )
            cur.execute(sql)
            total, valid = cur.fetchone()
            cur.close()
            conn.close()
            self.done.emit(int(total or 0), int(valid or 0))
        except Exception as e:
            self.error.emit(str(e))


class FixWorker(QThread):
    """Runs one of: CREATE GIST INDEX / ST_MakeValid update / VACUUM ANALYZE."""
    log      = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, conn_params: dict, action: str,
                 schema: str, tbl: str, gcol: str):
        super().__init__()
        self.conn_params = conn_params
        self.action = action
        self.schema = schema
        self.tbl    = tbl
        self.gcol   = gcol

    def run(self):
        sch = f'"{self.schema}"'
        tbl = f'"{self.tbl}"'
        col = f'"{self.gcol}"'
        try:
            conn = psycopg2.connect(**self.conn_params)
            conn.autocommit = True
            cur  = conn.cursor()

            if self.action == "gist":
                idx = f"idx_{self.tbl}_{self.gcol}_gist"
                sql = f'CREATE INDEX IF NOT EXISTS "{idx}" ON {sch}.{tbl} USING GIST ({col});'
                self.log.emit(f"Creating GIST index on {self.schema}.{self.tbl} ({self.gcol})…")
                cur.execute(sql)
                self.log.emit(f"✓ Index created: {idx}")

            elif self.action == "makevalid":
                sql = (f"UPDATE {sch}.{tbl} "
                       f"SET {col} = ST_MakeValid({col}) "
                       f"WHERE NOT ST_IsValid({col});")
                self.log.emit(f"Running ST_MakeValid on {self.schema}.{self.tbl}…")
                cur.execute(sql)
                self.log.emit(f"✓ ST_MakeValid done — {cur.rowcount} rows updated")

            elif self.action == "vacuum":
                sql = f"VACUUM ANALYZE {sch}.{tbl};"
                self.log.emit(f"VACUUM ANALYZE {self.schema}.{self.tbl}…")
                cur.execute(sql)
                self.log.emit("✓ VACUUM ANALYZE done")

            cur.close()
            conn.close()
            self.finished.emit(True)
        except Exception as e:
            self.log.emit(f"✗ {e}")
            self.finished.emit(False)


# ── Column indices ────────────────────────────────────────────────────────
C_SCHEMA  = 0
C_TABLE   = 1
C_GCOL    = 2
C_SRID    = 3
C_ROWS    = 4
C_SIZE    = 5
C_GIST    = 6
C_VALID   = 7
C_ANALYZE = 8
C_ACTIONS = 9

HEADERS = ["Schema", "Table", "Geom Column", "SRID",
           "Rows", "Size", "GIST Index", "Valid %",
           "Last Analyzed", "Actions"]

# ── Button style ──────────────────────────────────────────────────────────
_BTN = ("QPushButton{background-color:palette(button);color:palette(button-text);"
        "border:1px solid palette(mid);border-radius:3px;padding:1px 6px;font-size:11px;}"
        "QPushButton:hover{background-color:palette(light);}"
        "QPushButton:pressed{background-color:palette(midlight);}")

_BTN_WARN = ("QPushButton{background-color:#e65100;color:#fff;"
             "border:none;border-radius:3px;padding:1px 6px;font-size:11px;}"
             "QPushButton:hover{background-color:#ef6c00;}")


# ── Summary card ──────────────────────────────────────────────────────────

class _Card(QFrame):
    def __init__(self, title: str, value: str = "—", color: str = "#58a6ff"):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(130)
        self.setMaximumHeight(72)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(2)

        self._val = QLabel(value)
        f = QFont()
        f.setPointSize(20)
        f.setBold(True)
        self._val.setFont(f)
        self._val.setStyleSheet(f"color:{color};")

        self._title = QLabel(title)
        self._title.setStyleSheet("color:gray;font-size:11px;")

        lay.addWidget(self._val)
        lay.addWidget(self._title)

    def set_value(self, v: str, color: str | None = None):
        self._val.setText(v)
        if color:
            self._val.setStyleSheet(f"color:{color};")


# ── Main panel ────────────────────────────────────────────────────────────

class HealthPanel(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._scan_worker: Optional[ScanWorker] = None
        self._fix_workers: list = []
        self._validity_workers: list = []
        self._row_count = 0
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Summary cards ─────────────────────────────────────────────────
        card_row = QHBoxLayout()
        self._card_total   = _Card("Geometry Tables", "—", "#58a6ff")
        self._card_no_idx  = _Card("Missing GIST Index", "—", "#9e9e9e")
        self._card_invalid = _Card("Tables w/ Invalid Geom", "—", "#9e9e9e")
        self._card_size    = _Card("Total DB Size", "—", "#58a6ff")
        for c in (self._card_total, self._card_no_idx,
                  self._card_invalid, self._card_size):
            card_row.addWidget(c)
        card_row.addStretch()
        root.addLayout(card_row)

        # ── Toolbar ───────────────────────────────────────────────────────
        tb = QHBoxLayout()
        self._btn_scan = QPushButton("🔍  Scan Database")
        self._btn_scan.setStyleSheet(_BTN)
        self._btn_scan.clicked.connect(self._scan)

        self._btn_fix_all_idx = QPushButton("⚡ Create All Missing Indexes")
        self._btn_fix_all_idx.setStyleSheet(_BTN)
        self._btn_fix_all_idx.setEnabled(False)
        self._btn_fix_all_idx.clicked.connect(self._fix_all_indexes)

        self._btn_vacuum_all = QPushButton("🧹 VACUUM ANALYZE All")
        self._btn_vacuum_all.setStyleSheet(_BTN)
        self._btn_vacuum_all.setEnabled(False)
        self._btn_vacuum_all.clicked.connect(self._vacuum_all)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.hide()

        self._status_lbl = QLabel("Click 'Scan Database' to analyse all geometry tables")
        self._status_lbl.setStyleSheet("color:gray;font-size:11px;")

        tb.addWidget(self._btn_scan)
        tb.addWidget(self._btn_fix_all_idx)
        tb.addWidget(self._btn_vacuum_all)
        tb.addStretch()
        tb.addWidget(self._status_lbl)
        root.addLayout(tb)
        root.addWidget(self._progress)

        # ── Table ─────────────────────────────────────────────────────────
        self._tbl = QTableWidget(0, len(HEADERS))
        self._tbl.setHorizontalHeaderLabels(HEADERS)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setSortingEnabled(True)
        self._tbl.verticalHeader().setDefaultSectionSize(28)

        hh = self._tbl.horizontalHeader()
        hh.setSectionResizeMode(C_TABLE,  QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(C_GCOL,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_SCHEMA, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_SRID,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_ROWS,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_SIZE,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_GIST,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_VALID,  QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_ANALYZE,QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(C_ACTIONS,QHeaderView.ResizeMode.ResizeToContents)

        root.addWidget(self._tbl, 1)

        # ── Log ───────────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(110)
        self._log.setPlaceholderText("Fix actions output will appear here…")
        root.addWidget(self._log)

    # ── Scan ──────────────────────────────────────────────────────────────

    def _scan(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Not connected",
                                "Connect to a PostGIS database first.")
            return
        if self._scan_worker and self._scan_worker.isRunning():
            self._scan_worker.cancel()
            return

        self._tbl.setSortingEnabled(False)
        self._tbl.setRowCount(0)
        self._row_count = 0
        self._progress.setRange(0, 0)
        self._progress.show()
        self._btn_scan.setText("⏹  Stop")
        self._btn_fix_all_idx.setEnabled(False)
        self._btn_vacuum_all.setEnabled(False)
        self._status_lbl.setText("Scanning…")

        # Reset cards
        for c in (self._card_total, self._card_no_idx,
                  self._card_invalid, self._card_size):
            c.set_value("…")

        worker = ScanWorker(dict(self.db.params))
        worker.row_ready.connect(self._on_row)
        worker.finished.connect(self._on_scan_done)
        worker.error.connect(lambda e: (
            self._log.append(f"Scan error: {e}"),
            self._on_scan_done(0)))
        self._scan_worker = worker
        launch(worker)

    def _on_row(self, row: dict):
        self._row_count += 1
        t = self._tbl
        r = t.rowCount()
        t.insertRow(r)

        def _item(text, align=Qt.AlignmentFlag.AlignLeft):
            it = QTableWidgetItem(text)
            it.setTextAlignment(
                align | Qt.AlignmentFlag.AlignVCenter)
            return it

        def _num_item(val: int):
            it = QTableWidgetItem()
            it.setData(Qt.ItemDataRole.DisplayRole, val)
            it.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return it

        t.setItem(r, C_SCHEMA, _item(row['schema']))
        t.setItem(r, C_TABLE,  _item(row['tbl']))
        t.setItem(r, C_GCOL,   _item(row['gcol']))
        t.setItem(r, C_SRID,   _item(str(row['srid'])))
        t.setItem(r, C_ROWS,   _num_item(int(row['row_est'])))
        t.setItem(r, C_SIZE,   _item(row['size_pretty'],
                                     Qt.AlignmentFlag.AlignRight))

        # GIST index
        has_gist = row['has_gist']
        gist_item = _item("✅ Yes" if has_gist else "❌ No",
                          Qt.AlignmentFlag.AlignCenter)
        if not has_gist:
            gist_item.setForeground(QColor("#f44336"))
        t.setItem(r, C_GIST, gist_item)

        # Valid % — deferred (expensive)
        valid_item = _item("—", Qt.AlignmentFlag.AlignCenter)
        valid_item.setToolTip("Click 'Check' to run ST_IsValid scan")
        t.setItem(r, C_VALID, valid_item)

        # Last analyzed
        last = row.get('last_analyze')
        analyze_text = last.strftime("%Y-%m-%d") if last else "Never"
        analyze_item = _item(analyze_text, Qt.AlignmentFlag.AlignCenter)
        if not last:
            analyze_item.setForeground(QColor("#ff9800"))
        t.setItem(r, C_ANALYZE, analyze_item)

        # Action buttons cell
        action_w = QWidget()
        action_lay = QHBoxLayout(action_w)
        action_lay.setContentsMargins(2, 1, 2, 1)
        action_lay.setSpacing(3)

        btn_valid = QPushButton("Check")
        btn_valid.setToolTip("Run ST_IsValid check (may be slow on large tables)")
        btn_valid.setStyleSheet(_BTN)

        schema = row['schema']
        tbl    = row['tbl']
        gcol   = row['gcol']

        btn_valid.clicked.connect(
            lambda _, rr=r, s=schema, tb=tbl, g=gcol:
                self._check_validity(rr, s, tb, g))

        if not has_gist:
            btn_idx = QPushButton("➕ Index")
            btn_idx.setToolTip("CREATE INDEX USING GIST")
            btn_idx.setStyleSheet(_BTN_WARN)
            btn_idx.clicked.connect(
                lambda _, rr=r, s=schema, tb=tbl, g=gcol:
                    self._fix_one("gist", rr, s, tb, g))
            action_lay.addWidget(btn_idx)

        btn_vacuum = QPushButton("🧹")
        btn_vacuum.setToolTip("VACUUM ANALYZE this table")
        btn_vacuum.setStyleSheet(_BTN)
        btn_vacuum.clicked.connect(
            lambda _, s=schema, tb=tbl, g=gcol:
                self._fix_one("vacuum", -1, s, tb, g))

        action_lay.addWidget(btn_valid)
        action_lay.addWidget(btn_vacuum)
        t.setCellWidget(r, C_ACTIONS, action_w)

        self._status_lbl.setText(
            f"Found {self._row_count} geometry table(s)…")

    def _on_scan_done(self, count: int):
        self._progress.hide()
        self._btn_scan.setText("🔍  Scan Database")
        self._tbl.setSortingEnabled(True)
        self._btn_fix_all_idx.setEnabled(True)
        self._btn_vacuum_all.setEnabled(True)
        self._status_lbl.setText(
            f"✓ Scan complete — {count} geometry table(s) found")

        # Update summary cards
        self._card_total.set_value(str(count), "#58a6ff")

        no_idx = sum(
            1 for r in range(self._tbl.rowCount())
            if self._tbl.item(r, C_GIST) and
               "No" in (self._tbl.item(r, C_GIST).text()))
        self._card_no_idx.set_value(
            str(no_idx),
            "#f44336" if no_idx > 0 else "#4caf50")

        self._card_invalid.set_value("Run Check", "#9e9e9e")
        self._card_size.set_value("—", "#58a6ff")

    # ── Validity check ────────────────────────────────────────────────────

    def _check_validity(self, row: int, schema: str, tbl: str, gcol: str):
        it = self._tbl.item(row, C_VALID)
        if it:
            it.setText("…")
        w = ValidityWorker(dict(self.db.params), schema, tbl, gcol)
        w.done.connect(lambda total, valid, rr=row:
                       self._on_validity(rr, total, valid))
        w.error.connect(lambda e, rr=row: (
            self._tbl.item(rr, C_VALID) and
            self._tbl.item(rr, C_VALID).setText("Error")))
        self._validity_workers.append(w)
        launch(w)

    def _on_validity(self, row: int, total: int, valid: int):
        it = self._tbl.item(row, C_VALID)
        if not it:
            return
        if total == 0:
            it.setText("—")
            return
        pct = valid / total * 100
        it.setText(f"{pct:.1f}%  ({valid}/{total})")
        if pct < 100:
            it.setForeground(QColor("#f44336"))
            # add MakeValid button
            action_w = self._tbl.cellWidget(row, C_ACTIONS)
            if action_w:
                lay = action_w.layout()
                schema = self._tbl.item(row, C_SCHEMA).text()
                tbl    = self._tbl.item(row, C_TABLE).text()
                gcol   = self._tbl.item(row, C_GCOL).text()
                btn_mv = QPushButton("🔧 Fix Geom")
                btn_mv.setToolTip("Run ST_MakeValid on invalid geometries")
                btn_mv.setStyleSheet(_BTN_WARN)
                btn_mv.clicked.connect(
                    lambda _, rr=row, s=schema, t=tbl, g=gcol:
                        self._fix_one("makevalid", rr, s, t, g))
                lay.insertWidget(0, btn_mv)
        else:
            it.setForeground(QColor("#4caf50"))

        # update invalid card
        invalid_tables = sum(
            1 for r in range(self._tbl.rowCount())
            if self._tbl.item(r, C_VALID) and
               "100.0%" not in (self._tbl.item(r, C_VALID).text() or "") and
               "—" not in (self._tbl.item(r, C_VALID).text() or "") and
               "…" not in (self._tbl.item(r, C_VALID).text() or ""))
        self._card_invalid.set_value(
            str(invalid_tables),
            "#f44336" if invalid_tables > 0 else "#4caf50")

    # ── Fix actions ───────────────────────────────────────────────────────

    def _fix_one(self, action: str, row: int,
                 schema: str, tbl: str, gcol: str):
        self._log.append(f"→ {action.upper()}: {schema}.{tbl} ({gcol})")
        w = FixWorker(dict(self.db.params), action, schema, tbl, gcol)
        w.log.connect(self._log.append)
        w.finished.connect(lambda ok, rr=row, a=action:
                           self._on_fix_done(ok, rr, a))
        self._fix_workers.append(w)
        launch(w)

    def _on_fix_done(self, ok: bool, row: int, action: str):
        if ok and row >= 0:
            if action == "gist":
                it = self._tbl.item(row, C_GIST)
                if it:
                    it.setText("✅ Yes")
                    it.setForeground(QColor())
                # remove the ➕ Index button
                action_w = self._tbl.cellWidget(row, C_ACTIONS)
                if action_w:
                    lay = action_w.layout()
                    for i in range(lay.count()):
                        w = lay.itemAt(i)
                        if w and isinstance(w.widget(), QPushButton):
                            if "Index" in (w.widget().text() or ""):
                                w.widget().deleteLater()
                                break

    def _fix_all_indexes(self):
        missing = []
        for r in range(self._tbl.rowCount()):
            gist_it = self._tbl.item(r, C_GIST)
            if gist_it and "No" in gist_it.text():
                missing.append((
                    r,
                    self._tbl.item(r, C_SCHEMA).text(),
                    self._tbl.item(r, C_TABLE).text(),
                    self._tbl.item(r, C_GCOL).text(),
                ))
        if not missing:
            QMessageBox.information(self, "All good",
                                    "All geometry tables already have GIST indexes.")
            return
        reply = QMessageBox.question(
            self, "Create indexes",
            f"Create GIST index on {len(missing)} table(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        for row, schema, tbl, gcol in missing:
            self._fix_one("gist", row, schema, tbl, gcol)

    def _vacuum_all(self):
        reply = QMessageBox.question(
            self, "VACUUM ANALYZE",
            f"Run VACUUM ANALYZE on all {self._tbl.rowCount()} geometry table(s)?\n"
            "This updates row statistics for the query planner.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        for r in range(self._tbl.rowCount()):
            schema = self._tbl.item(r, C_SCHEMA).text()
            tbl    = self._tbl.item(r, C_TABLE).text()
            gcol   = self._tbl.item(r, C_GCOL).text()
            self._fix_one("vacuum", r, schema, tbl, gcol)

    # ── Called by main window ─────────────────────────────────────────────

    def set_connection(self, params: dict) -> None:
        pass   # connection available via self.db

    def refresh(self):
        if self.db.is_connected():
            self._scan()
