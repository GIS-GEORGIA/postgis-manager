"""Spatial Data Quality Dashboard — layer health checks with batch fix."""

from __future__ import annotations
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QSplitter, QTextEdit, QTabWidget,
    QMessageBox, QCheckBox, QSpinBox, QGroupBox, QFormLayout,
)


# ── worker ────────────────────────────────────────────────────────────────────

class _QualityWorker(QThread):
    done    = pyqtSignal(dict)
    error   = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, db, schema: str, table: str, geom_col: str,
                 checks: dict, outlier_sigma: float):
        super().__init__()
        self._db           = db
        self._schema       = schema
        self._table        = table
        self._geom_col     = geom_col
        self._checks       = checks
        self._outlier_sigma = outlier_sigma

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            s, t, g = self._schema, self._table, self._geom_col
            result = {}

            self.progress.emit(5, "Counting rows…")
            cur.execute(f'SELECT COUNT(*) FROM "{s}"."{t}"')
            result["total"] = cur.fetchone()[0]

            if self._checks.get("null_geom"):
                self.progress.emit(15, "Checking null geometries…")
                cur.execute(f'SELECT COUNT(*) FROM "{s}"."{t}" WHERE "{g}" IS NULL')
                result["null_geom"] = cur.fetchone()[0]

            if self._checks.get("invalid"):
                self.progress.emit(30, "Checking invalid geometries…")
                cur.execute(f'''
                    SELECT COUNT(*) FROM "{s}"."{t}"
                    WHERE NOT ST_IsValid("{g}") AND "{g}" IS NOT NULL
                ''')
                result["invalid"] = cur.fetchone()[0]
                cur.execute(f'''
                    SELECT ctid::text, ST_IsValidReason("{g}")
                    FROM "{s}"."{t}"
                    WHERE NOT ST_IsValid("{g}") AND "{g}" IS NOT NULL
                    LIMIT 100
                ''')
                result["invalid_detail"] = cur.fetchall()

            if self._checks.get("empty_geom"):
                self.progress.emit(40, "Checking empty geometries…")
                cur.execute(f'''
                    SELECT COUNT(*) FROM "{s}"."{t}"
                    WHERE ST_IsEmpty("{g}") AND "{g}" IS NOT NULL
                ''')
                result["empty_geom"] = cur.fetchone()[0]

            if self._checks.get("wrong_srid"):
                self.progress.emit(50, "Checking SRIDs…")
                cur.execute(f'''
                    SELECT DISTINCT ST_SRID("{g}"), COUNT(*)
                    FROM "{s}"."{t}"
                    WHERE "{g}" IS NOT NULL
                    GROUP BY ST_SRID("{g}")
                    ORDER BY COUNT(*) DESC
                ''')
                result["srid_dist"] = cur.fetchall()

            if self._checks.get("duplicates"):
                self.progress.emit(60, "Checking duplicate geometries…")
                cur.execute(f'''
                    SELECT COUNT(*) FROM (
                        SELECT "{g}"
                        FROM "{s}"."{t}"
                        WHERE "{g}" IS NOT NULL
                        GROUP BY "{g}"
                        HAVING COUNT(*) > 1
                    ) sub
                ''')
                result["duplicates"] = cur.fetchone()[0]

            if self._checks.get("outliers"):
                self.progress.emit(75, "Detecting coordinate outliers…")
                sigma = self._outlier_sigma
                cur.execute(f'''
                    WITH stats AS (
                        SELECT
                            AVG(ST_X(ST_Centroid("{g}"))) AS mx,
                            STDDEV(ST_X(ST_Centroid("{g}"))) AS sx,
                            AVG(ST_Y(ST_Centroid("{g}"))) AS my,
                            STDDEV(ST_Y(ST_Centroid("{g}"))) AS sy
                        FROM "{s}"."{t}" WHERE "{g}" IS NOT NULL
                    )
                    SELECT COUNT(*) FROM "{s}"."{t}", stats
                    WHERE "{g}" IS NOT NULL
                      AND (
                        ABS(ST_X(ST_Centroid("{g}")) - mx) > {sigma} * NULLIF(sx,0)
                        OR
                        ABS(ST_Y(ST_Centroid("{g}")) - my) > {sigma} * NULLIF(sy,0)
                      )
                ''')
                result["outliers"] = cur.fetchone()[0]

            if self._checks.get("self_intersect"):
                self.progress.emit(88, "Checking self-intersections…")
                cur.execute(f'''
                    SELECT COUNT(*) FROM "{s}"."{t}"
                    WHERE "{g}" IS NOT NULL
                      AND NOT ST_IsSimple("{g}")
                      AND ST_GeometryType("{g}") IN (
                          'ST_LineString','ST_MultiLineString',
                          'ST_Polygon','ST_MultiPolygon')
                ''')
                result["self_intersect"] = cur.fetchone()[0]

            conn.close()
            self.progress.emit(100, "Done")
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class _FixWorker(QThread):
    done  = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, geom_col, fix_type):
        super().__init__()
        self._db = db; self._schema = schema
        self._table = table; self._geom_col = geom_col
        self._fix_type = fix_type

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            s, t, g = self._schema, self._table, self._geom_col

            if self._fix_type == "make_valid":
                cur.execute(f'''
                    UPDATE "{s}"."{t}"
                    SET "{g}" = ST_MakeValid("{g}")
                    WHERE NOT ST_IsValid("{g}") AND "{g}" IS NOT NULL
                ''')
            elif self._fix_type == "remove_null":
                cur.execute(f'DELETE FROM "{s}"."{t}" WHERE "{g}" IS NULL')
            elif self._fix_type == "remove_empty":
                cur.execute(f'DELETE FROM "{s}"."{t}" WHERE ST_IsEmpty("{g}")')
            elif self._fix_type == "remove_duplicates":
                cur.execute(f'''
                    DELETE FROM "{s}"."{t}" a USING "{s}"."{t}" b
                    WHERE a.ctid > b.ctid AND ST_Equals(a."{g}", b."{g}")
                ''')

            n = cur.rowcount
            conn.commit()
            conn.close()
            self.done.emit(n)
        except Exception as e:
            self.error.emit(str(e))


# ── score card ────────────────────────────────────────────────────────────────

def _score(result: dict) -> tuple[int, str]:
    total = max(result.get("total", 1), 1)
    issues = (
        result.get("null_geom", 0) +
        result.get("invalid", 0) +
        result.get("empty_geom", 0) +
        result.get("duplicates", 0) +
        result.get("self_intersect", 0)
    )
    pct = issues / total
    if pct == 0:
        return 100, "✅ Excellent"
    elif pct < 0.01:
        return 90, "🟡 Good"
    elif pct < 0.05:
        return 70, "🟠 Fair"
    else:
        return max(0, int(100 - pct * 100)), "🔴 Poor"


# ── panel ─────────────────────────────────────────────────────────────────────

class SpatialQualityPanel(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._worker = self._fix_worker = None
        self._result: dict = {}
        self._schema = self._table = self._geom_col = ""
        self._schemas: dict = {}
        self._build_ui()

    def set_db(self, db):
        self._db = db

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # toolbar
        tb = QHBoxLayout()
        tb.addWidget(QLabel("Schema:"))
        self._schema_combo = QComboBox(); self._schema_combo.setMinimumWidth(120)
        self._schema_combo.currentTextChanged.connect(self._fill_tables)
        tb.addWidget(self._schema_combo)
        tb.addWidget(QLabel("Table:"))
        self._table_combo = QComboBox(); self._table_combo.setMinimumWidth(160)
        self._table_combo.currentTextChanged.connect(self._fill_geom_col)
        tb.addWidget(self._table_combo)
        tb.addWidget(QLabel("Geom:"))
        self._geom_combo = QComboBox(); self._geom_combo.setMinimumWidth(110)
        tb.addWidget(self._geom_combo)
        self._run_btn = QPushButton("🔍 Analyze")
        self._run_btn.clicked.connect(self._run)
        tb.addWidget(self._run_btn)
        self._status = QLabel("")
        tb.addWidget(self._status)
        tb.addStretch()
        root.addLayout(tb)

        # checks row
        chk_row = QHBoxLayout()
        self._chk = {}
        for key, label in [
            ("null_geom",     "Null geom"),
            ("invalid",       "Invalid"),
            ("empty_geom",    "Empty"),
            ("wrong_srid",    "SRID dist"),
            ("duplicates",    "Duplicates"),
            ("self_intersect","Self-intersect"),
            ("outliers",      "Outliers"),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self._chk[key] = cb
            chk_row.addWidget(cb)
        chk_row.addWidget(QLabel("Outlier σ:"))
        self._sigma_spin = QSpinBox()
        self._sigma_spin.setRange(2, 10); self._sigma_spin.setValue(3)
        chk_row.addWidget(self._sigma_spin)
        chk_row.addStretch()
        root.addLayout(chk_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setMaximumHeight(6)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # score + summary
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)

        self._score_lbl = QLabel("—")
        self._score_lbl.setStyleSheet("font-size: 40px; font-weight: bold;")
        self._score_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lv.addWidget(self._score_lbl)

        self._grade_lbl = QLabel("")
        self._grade_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._grade_lbl.setStyleSheet("font-size: 16px;")
        lv.addWidget(self._grade_lbl)

        self._summary_table = QTableWidget(0, 3)
        self._summary_table.setHorizontalHeaderLabels(["Check", "Issues", "% of total"])
        self._summary_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        lv.addWidget(self._summary_table)

        # fix buttons
        fix_box = QGroupBox("Quick Fix")
        fv = QVBoxLayout(fix_box)
        for fix_type, label in [
            ("make_valid",        "🔧 Fix invalid (ST_MakeValid)"),
            ("remove_null",       "🗑 Remove NULL geometries"),
            ("remove_empty",      "🗑 Remove empty geometries"),
            ("remove_duplicates", "🗑 Remove duplicate geometries"),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, ft=fix_type: self._fix(ft))
            fv.addWidget(btn)
        lv.addWidget(fix_box)
        lv.addStretch()
        splitter.addWidget(left)

        # detail tabs
        self._tabs = QTabWidget()
        self._invalid_table = QTableWidget(0, 2)
        self._invalid_table.setHorizontalHeaderLabels(["Row ID", "Reason"])
        self._invalid_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._tabs.addTab(self._invalid_table, "Invalid geom detail")

        self._srid_table = QTableWidget(0, 2)
        self._srid_table.setHorizontalHeaderLabels(["SRID", "Count"])
        self._srid_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._tabs.addTab(self._srid_table, "SRID distribution")

        splitter.addWidget(self._tabs)
        splitter.setSizes([280, 520])
        root.addWidget(splitter)

    def refresh_schemas(self):
        if not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute("""
                SELECT n.nspname, c.relname, a.attname
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_type t ON t.oid = a.atttypid
                WHERE t.typname IN ('geometry','geography')
                  AND c.relkind = 'r'
                  AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
                  AND a.attnum > 0
                ORDER BY n.nspname, c.relname
            """)
            self._schemas = {}
            for s, t, g in cur.fetchall():
                self._schemas.setdefault(s, {}).setdefault(t, []).append(g)
            conn.close()
            self._schema_combo.clear()
            for s in sorted(self._schemas):
                self._schema_combo.addItem(s)
        except Exception:
            pass

    def _fill_tables(self, schema: str):
        self._table_combo.clear()
        for t in sorted(self._schemas.get(schema, {})):
            self._table_combo.addItem(t)

    def _fill_geom_col(self, table: str):
        schema = self._schema_combo.currentText()
        self._geom_combo.clear()
        for g in self._schemas.get(schema, {}).get(table, []):
            self._geom_combo.addItem(g)

    def _run(self):
        if not self._db or not self._db.is_connected():
            return
        s = self._schema_combo.currentText()
        t = self._table_combo.currentText()
        g = self._geom_combo.currentText()
        if not all([s, t, g]):
            return
        self._schema, self._table, self._geom_col = s, t, g
        self._run_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        checks = {k: cb.isChecked() for k, cb in self._chk.items()}
        self._worker = _QualityWorker(
            self._db, s, t, g, checks, self._sigma_spin.value())
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.progress.connect(lambda v, msg: (
            self._progress.setValue(v), self._status.setText(msg)))
        self._worker.start()

    def _on_done(self, result: dict):
        self._result = result
        self._run_btn.setEnabled(True)
        self._progress.setVisible(False)
        total = result.get("total", 0)

        score, grade = _score(result)
        self._score_lbl.setText(str(score))
        color = "#388E3C" if score >= 90 else "#F57C00" if score >= 70 else "#C62828"
        self._score_lbl.setStyleSheet(f"font-size:40px;font-weight:bold;color:{color};")
        self._grade_lbl.setText(grade)

        rows = [
            ("Total features",   total,                          ""),
            ("Null geometries",  result.get("null_geom",  "—"),  self._pct(result.get("null_geom",0), total)),
            ("Invalid geom",     result.get("invalid",    "—"),  self._pct(result.get("invalid",0), total)),
            ("Empty geom",       result.get("empty_geom", "—"),  self._pct(result.get("empty_geom",0), total)),
            ("Duplicates",       result.get("duplicates", "—"),  self._pct(result.get("duplicates",0), total)),
            ("Self-intersects",  result.get("self_intersect","—"),self._pct(result.get("self_intersect",0), total)),
            ("Outliers (>σ)",    result.get("outliers",   "—"),  self._pct(result.get("outliers",0), total)),
        ]
        self._summary_table.setRowCount(len(rows))
        for ri, (check, val, pct) in enumerate(rows):
            self._summary_table.setItem(ri, 0, QTableWidgetItem(check))
            item = QTableWidgetItem(str(val))
            if isinstance(val, int) and val > 0 and ri > 0:
                item.setForeground(QBrush(QColor("#C62828")))
                item.setFont(QFont("", -1, QFont.Weight.Bold))
            self._summary_table.setItem(ri, 1, item)
            self._summary_table.setItem(ri, 2, QTableWidgetItem(pct))

        # invalid detail
        detail = result.get("invalid_detail", [])
        self._invalid_table.setRowCount(len(detail))
        for ri, (ctid, reason) in enumerate(detail):
            self._invalid_table.setItem(ri, 0, QTableWidgetItem(str(ctid)))
            self._invalid_table.setItem(ri, 1, QTableWidgetItem(reason or ""))

        # srid distribution
        srid_dist = result.get("srid_dist", [])
        self._srid_table.setRowCount(len(srid_dist))
        for ri, (srid, cnt) in enumerate(srid_dist):
            self._srid_table.setItem(ri, 0, QTableWidgetItem(str(srid)))
            self._srid_table.setItem(ri, 1, QTableWidgetItem(str(cnt)))

        self._status.setText(
            f"Score: {score}/100 | {total:,} features | "
            f"{result.get('invalid',0)} invalid, "
            f"{result.get('duplicates',0)} duplicates")

    def _pct(self, n, total) -> str:
        if not isinstance(n, int) or total == 0:
            return ""
        return f"{n/total*100:.1f}%"

    def _on_error(self, msg: str):
        self._run_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText(f"Error: {msg[:80]}")

    def _fix(self, fix_type: str):
        if not self._schema or not self._table:
            QMessageBox.information(self, "Select layer", "Run analysis first.")
            return
        labels = {
            "make_valid":        "Fix invalid geometries with ST_MakeValid",
            "remove_null":       "Delete rows with NULL geometry",
            "remove_empty":      "Delete rows with empty geometry",
            "remove_duplicates": "Delete duplicate geometries",
        }
        if QMessageBox.question(
            self, "Confirm fix",
            f"{labels[fix_type]}\n\nThis will modify the table. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        self._fix_worker = _FixWorker(
            self._db, self._schema, self._table, self._geom_col, fix_type)
        self._fix_worker.done.connect(lambda n: (
            self._status.setText(f"✔ Fixed {n} rows"),
            self._run()
        ))
        self._fix_worker.error.connect(lambda e: QMessageBox.critical(self, "Fix error", e))
        self._fix_worker.start()
