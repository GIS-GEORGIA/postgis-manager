"""Spatial Join Panel — visual ST_Intersects/Contains/DWithin builder."""

from __future__ import annotations
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QDoubleSpinBox, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QRadioButton, QButtonGroup,
    QGroupBox, QFormLayout, QCheckBox, QProgressBar,
    QMessageBox, QSplitter, QTextEdit,
)


_PREDICATES = {
    "Intersects":      "ST_Intersects(a.{left_geom}, b.{right_geom})",
    "Contains":        "ST_Contains(a.{left_geom}, b.{right_geom})",
    "Within":          "ST_Within(a.{left_geom}, b.{right_geom})",
    "Overlaps":        "ST_Overlaps(a.{left_geom}, b.{right_geom})",
    "Touches":         "ST_Touches(a.{left_geom}, b.{right_geom})",
    "Crosses":         "ST_Crosses(a.{left_geom}, b.{right_geom})",
    "DWithin (metres)":"ST_DWithin(a.{left_geom}::geography, b.{right_geom}::geography, {dist})",
    "Nearest (KNN)":   "a.{left_geom} <-> b.{right_geom}",
}


class _JoinWorker(QThread):
    done    = pyqtSignal(list, list, str)   # rows, cols, sql
    error   = pyqtSignal(str)
    progress = pyqtSignal(int)

    def __init__(self, db, sql: str, save_as: str | None):
        super().__init__()
        self._db      = db
        self._sql     = sql
        self._save_as = save_as

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            if self._save_as:
                cur.execute(f"CREATE TABLE {self._save_as} AS {self._sql}")
                conn.commit()
                self.done.emit([], [], self._sql)
            else:
                cur.execute(self._sql)
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                conn.rollback()
                self.done.emit([list(r) for r in rows], cols, self._sql)
            conn.close()
        except Exception as e:
            self.error.emit(str(e))


class SpatialJoinPanel(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db      = db
        self._worker  = None
        self._schemas: dict = {}
        self._build_ui()

    def set_db(self, db):
        self._db = db

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        top = QHBoxLayout()

        # ── Left table ────────────────────────────────────────────────────
        left_box = QGroupBox("Left table  (a)")
        lf = QFormLayout(left_box)
        self._la_schema = QComboBox(); self._la_schema.setMinimumWidth(110)
        self._la_table  = QComboBox(); self._la_table.setMinimumWidth(150)
        self._la_geom   = QComboBox(); self._la_geom.setMinimumWidth(110)
        self._la_cols   = QLineEdit("*"); self._la_cols.setPlaceholderText("columns to select, e.g. id, name")
        self._la_schema.currentTextChanged.connect(
            lambda s: self._fill_tables(s, self._la_table, self._la_geom))
        self._la_table.currentTextChanged.connect(
            lambda t: self._fill_geom(self._la_schema.currentText(), t, self._la_geom))
        lf.addRow("Schema:", self._la_schema)
        lf.addRow("Table:",  self._la_table)
        lf.addRow("Geom:",   self._la_geom)
        lf.addRow("Cols:",   self._la_cols)
        top.addWidget(left_box)

        # ── Predicate ────────────────────────────────────────────────────
        pred_box = QGroupBox("Relationship")
        pv = QVBoxLayout(pred_box)
        self._pred_combo = QComboBox()
        for p in _PREDICATES:
            self._pred_combo.addItem(p)
        self._pred_combo.currentTextChanged.connect(self._on_pred_changed)
        pv.addWidget(self._pred_combo)

        dist_row = QHBoxLayout()
        dist_row.addWidget(QLabel("Distance (m):"))
        self._dist_spin = QDoubleSpinBox()
        self._dist_spin.setRange(0.01, 1e9)
        self._dist_spin.setValue(1000)
        self._dist_spin.setEnabled(False)
        dist_row.addWidget(self._dist_spin)
        pv.addLayout(dist_row)

        pv.addWidget(QLabel("Join type:"))
        self._jt_combo = QComboBox()
        for jt in ["INNER JOIN", "LEFT JOIN", "LEFT JOIN (keep non-matching)"]:
            self._jt_combo.addItem(jt)
        pv.addWidget(self._jt_combo)

        self._count_only = QCheckBox("Count only (fast preview)")
        pv.addWidget(self._count_only)
        pv.addStretch()
        top.addWidget(pred_box)

        # ── Right table ───────────────────────────────────────────────────
        right_box = QGroupBox("Right table  (b)")
        rf = QFormLayout(right_box)
        self._rb_schema = QComboBox(); self._rb_schema.setMinimumWidth(110)
        self._rb_table  = QComboBox(); self._rb_table.setMinimumWidth(150)
        self._rb_geom   = QComboBox(); self._rb_geom.setMinimumWidth(110)
        self._rb_cols   = QLineEdit("*"); self._rb_cols.setPlaceholderText("columns to select")
        self._rb_schema.currentTextChanged.connect(
            lambda s: self._fill_tables(s, self._rb_table, self._rb_geom))
        self._rb_table.currentTextChanged.connect(
            lambda t: self._fill_geom(self._rb_schema.currentText(), t, self._rb_geom))
        rf.addRow("Schema:", self._rb_schema)
        rf.addRow("Table:",  self._rb_table)
        rf.addRow("Geom:",   self._rb_geom)
        rf.addRow("Cols:",   self._rb_cols)
        top.addWidget(right_box)

        root.addLayout(top)

        # ── SQL preview + run ─────────────────────────────────────────────
        sql_row = QHBoxLayout()
        self._sql_preview = QLineEdit()
        self._sql_preview.setReadOnly(True)
        self._sql_preview.setPlaceholderText("SQL preview…")
        sql_row.addWidget(self._sql_preview, 3)

        self._save_as = QLineEdit()
        self._save_as.setPlaceholderText("Save as table (optional)")
        self._save_as.setMaximumWidth(200)
        sql_row.addWidget(self._save_as)

        self._run_btn = QPushButton("▶ Run Join")
        self._run_btn.clicked.connect(self._run)
        sql_row.addWidget(self._run_btn)

        self._preview_btn = QPushButton("👁 Preview SQL")
        self._preview_btn.clicked.connect(self._build_sql_preview)
        sql_row.addWidget(self._preview_btn)

        self._status = QLabel("")
        sql_row.addWidget(self._status)
        root.addLayout(sql_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setMaximumHeight(4)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # ── Results ───────────────────────────────────────────────────────
        self._result_table = QTableWidget()
        self._result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._result_table.setAlternatingRowColors(True)
        self._result_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self._result_table)

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
            for combo in (self._la_schema, self._rb_schema):
                combo.clear()
                for s in schemas: combo.addItem(s)
        except Exception:
            pass

    def _fill_tables(self, schema: str, tbl_combo: QComboBox, geom_combo: QComboBox):
        if not schema or not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name",
                (schema,))
            tables = [r[0] for r in cur.fetchall()]
            conn.close()
            tbl_combo.clear()
            for t in tables: tbl_combo.addItem(t)
            if tables:
                self._fill_geom(schema, tables[0], geom_combo)
        except Exception:
            pass

    def _fill_geom(self, schema: str, table: str, geom_combo: QComboBox):
        if not schema or not table or not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s
                  AND udt_name IN ('geometry','geography')
                ORDER BY ordinal_position
            """, (schema, table))
            cols = [r[0] for r in cur.fetchall()]
            conn.close()
            geom_combo.clear()
            for c in cols: geom_combo.addItem(c)
        except Exception:
            pass

    def _on_pred_changed(self, pred: str):
        self._dist_spin.setEnabled("DWithin" in pred)

    def _build_sql(self) -> str:
        la_s  = self._la_schema.currentText()
        la_t  = self._la_table.currentText()
        la_g  = self._la_geom.currentText()
        la_c  = self._la_cols.text().strip() or "*"
        rb_s  = self._rb_schema.currentText()
        rb_t  = self._rb_table.currentText()
        rb_g  = self._rb_geom.currentText()
        rb_c  = self._rb_cols.text().strip() or "*"
        pred  = self._pred_combo.currentText()
        jtype = self._jt_combo.currentText().split(" (")[0]
        dist  = self._dist_spin.value()

        predicate = _PREDICATES[pred].format(
            left_geom=f'"{la_g}"', right_geom=f'"{rb_g}"', dist=dist)

        if self._count_only.isChecked():
            select = "COUNT(*)"
        else:
            a_cols = ", ".join(f'a."{c.strip()}"' for c in la_c.split(",")) if la_c != "*" else "a.*"
            b_cols = ", ".join(f'b."{c.strip()}"' for c in rb_c.split(",")) if rb_c != "*" else "b.*"
            select = f"{a_cols}, {b_cols}"

        if pred == "Nearest (KNN)":
            sql = (
                f'SELECT {select}\n'
                f'FROM "{la_s}"."{la_t}" a\n'
                f'CROSS JOIN LATERAL (\n'
                f'  SELECT * FROM "{rb_s}"."{rb_t}" b\n'
                f'  ORDER BY {predicate} LIMIT 1\n'
                f') b'
            )
        else:
            sql = (
                f'SELECT {select}\n'
                f'FROM "{la_s}"."{la_t}" a\n'
                f'{jtype} "{rb_s}"."{rb_t}" b\n'
                f'  ON {predicate}'
            )
        return sql

    def _build_sql_preview(self):
        try:
            sql = self._build_sql()
            self._sql_preview.setText(sql.replace("\n", " "))
        except Exception as e:
            self._status.setText(str(e))

    def _run(self):
        if not self._db or not self._db.is_connected():
            self._status.setText("Not connected"); return
        try:
            sql = self._build_sql()
        except Exception as e:
            self._status.setText(str(e)); return
        save_as = self._save_as.text().strip() or None
        self._run_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("Running…")
        self._worker = _JoinWorker(self._db, sql, save_as)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, rows: list, cols: list, sql: str):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._sql_preview.setText(sql.replace("\n", " "))
        if not rows and not cols:
            self._status.setText(f"✔ Table created: {self._save_as.text()}")
            return
        self._result_table.setColumnCount(len(cols))
        self._result_table.setHorizontalHeaderLabels(cols)
        self._result_table.setRowCount(len(rows))
        for ri, row in enumerate(rows):
            for ci, val in enumerate(row):
                self._result_table.setItem(ri, ci, QTableWidgetItem(
                    str(val) if val is not None else "NULL"))
        self._status.setText(f"{len(rows)} rows matched")

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._run_btn.setEnabled(True)
        self._status.setText(f"Error: {msg[:80]}")
        QMessageBox.critical(self, "Spatial Join error", msg)
