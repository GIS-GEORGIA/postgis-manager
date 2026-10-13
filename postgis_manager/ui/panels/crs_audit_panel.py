"""CRS Audit — detect SRID mismatches across all geometry layers."""

from __future__ import annotations
from ...qtcompat.QtCore import Qt, QThread, pyqtSignal
from ...qtcompat.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QGroupBox, QFormLayout, QComboBox, QMessageBox, QCheckBox,
)
from ...qtcompat.QtGui import QColor


class _AuditWorker(QThread):
    done     = pyqtSignal(list)   # list of row dicts
    progress = pyqtSignal(int, str)
    error    = pyqtSignal(str)

    def __init__(self, db, schema_filter: str):
        super().__init__()
        self._db = db
        self._schema_filter = schema_filter

    def run(self):
        try:
            import psycopg2
            from ...utils import pg_tools
            conn = psycopg2.connect(**self._db.params)
            try:
                rows = pg_tools.audit_crs(
                    conn, self._schema_filter,
                    progress=lambda p, msg: self.progress.emit(p, msg))
            finally:
                conn.close()
            self.done.emit(rows)
        except Exception as e:
            self.error.emit(str(e))


class _FixWorker(QThread):
    done  = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, db, fixes: list):
        super().__init__()
        self._db = db; self._fixes = fixes

    def run(self):
        try:
            import psycopg2
            from ...utils import pg_tools
            conn = psycopg2.connect(**self._db.params)
            try:
                n = pg_tools.fix_crs(conn, self._fixes)
            finally:
                conn.close()
            self.done.emit(n)
        except Exception as e:
            self.error.emit(str(e))


# ── panel ─────────────────────────────────────────────────────────────────────

class CRSAuditPanel(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._rows: list = []
        self._workers = []
        self._build_ui()

    def set_db(self, db):
        self._db = db

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # filter row
        top = QHBoxLayout()
        top.addWidget(QLabel("Schema:"))
        self._schema_combo = QComboBox(); self._schema_combo.setMinimumWidth(130)
        self._schema_combo.addItem("(all)")
        top.addWidget(self._schema_combo)
        self._run_btn = QPushButton("🔍 Run Audit")
        self._run_btn.setFixedHeight(28)
        self._run_btn.clicked.connect(self._run)
        top.addWidget(self._run_btn)
        top.addStretch()
        self._summary_lbl = QLabel("")
        top.addWidget(self._summary_lbl)
        root.addLayout(top)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setMaximumHeight(6)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # results table
        cols = ["Schema", "Table", "Geometry col", "Type",
                "Declared SRID", "Actual SRID", "Unique SRIDs", "Status", "Issue"]
        self._table = QTableWidget(0, len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setStretchLastSection(True)
        root.addWidget(self._table)

        # fix group
        fix_box = QGroupBox("Fix selected mismatches")
        fb = QHBoxLayout(fix_box)
        self._fix_chk = QCheckBox("Only fix rows where actual SRID is known")
        self._fix_chk.setChecked(True)
        fb.addWidget(self._fix_chk)
        fix_btn = QPushButton("🔧 Apply UpdateGeometrySRID")
        fix_btn.clicked.connect(self._fix)
        fb.addWidget(fix_btn)
        fb.addStretch()
        root.addWidget(fix_box)

    def refresh_schemas(self):
        if not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') "
                "ORDER BY schema_name")
            schemas = [r[0] for r in cur.fetchall()]
            conn.close()
            self._schema_combo.clear()
            self._schema_combo.addItem("(all)")
            for s in schemas: self._schema_combo.addItem(s)
        except Exception:
            pass

    def _run(self):
        if not self._db or not self._db.is_connected():
            QMessageBox.warning(self, "Not connected", "Connect to a database first.")
            return
        self._run_btn.setEnabled(False)
        self._progress.setVisible(True)
        schema = self._schema_combo.currentText()
        w = _AuditWorker(self._db, schema)
        w.done.connect(self._on_done)
        w.error.connect(lambda e: (
            QMessageBox.critical(self, "Audit error", e),
            self._run_btn.setEnabled(True),
            self._progress.setVisible(False),
        ))
        w.progress.connect(lambda v, msg: (
            self._progress.setValue(v), self._summary_lbl.setText(msg)))
        w.start(); self._workers.append(w)

    _STATUS_COLORS = {
        "OK":    QColor(200, 240, 200),
        "WARN":  QColor(255, 240, 180),
        "ERROR": QColor(255, 200, 200),
        "EMPTY": QColor(220, 220, 220),
    }

    def _on_done(self, rows: list):
        self._rows = rows
        self._run_btn.setEnabled(True)
        self._progress.setVisible(False)

        errors = sum(1 for r in rows if r["status"] == "ERROR")
        warns  = sum(1 for r in rows if r["status"] == "WARN")
        self._summary_lbl.setText(
            f"{len(rows)} layers — {errors} errors, {warns} warnings")

        self._table.setRowCount(len(rows))
        for ri, r in enumerate(rows):
            vals = [r["schema"], r["table"], r["col"], r["type"],
                    str(r["declared"]), str(r["actual"] or ""),
                    str(r["unique_srids"]), r["status"], r["issue"]]
            color = self._STATUS_COLORS.get(r["status"])
            for ci, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if color: item.setBackground(color)
                self._table.setItem(ri, ci, item)

    def _fix(self):
        selected = {r.row() for r in self._table.selectionModel().selectedRows()}
        if not selected:
            QMessageBox.information(self, "No selection", "Select rows to fix.")
            return
        fixes = []
        for ri in selected:
            row = self._rows[ri]
            if row["status"] not in ("ERROR", "WARN"): continue
            if self._fix_chk.isChecked() and row["actual"] is None: continue
            fixes.append(row)
        if not fixes:
            QMessageBox.information(self, "Nothing to fix",
                                    "Selected rows have no actionable mismatch.")
            return
        ok = QMessageBox.question(
            self, "Confirm fix",
            f"Apply UpdateGeometrySRID to {len(fixes)} column(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ok != QMessageBox.StandardButton.Yes: return

        w = _FixWorker(self._db, fixes)
        w.done.connect(lambda n: (
            QMessageBox.information(self, "Done", f"Fixed {n} column(s)."),
            self._run(),
        ))
        w.error.connect(lambda e: QMessageBox.critical(self, "Fix error", e))
        w.start(); self._workers.append(w)
