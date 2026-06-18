"""CRS Browser — search EPSG codes, preview projections, reproject layers."""

from __future__ import annotations
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QTextEdit, QComboBox, QDialog, QFormLayout,
    QDialogButtonBox, QMessageBox, QSpinBox, QProgressBar,
)


class _SearchWorker(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, db, query: str):
        super().__init__()
        self._db    = db
        self._query = query

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            q = f"%{self._query}%"
            cur.execute("""
                SELECT srid, auth_name, auth_srid, srtext, proj4text
                FROM spatial_ref_sys
                WHERE CAST(srid AS TEXT) ILIKE %s
                   OR auth_name ILIKE %s
                   OR CAST(auth_srid AS TEXT) ILIKE %s
                   OR srtext ILIKE %s
                ORDER BY
                    CASE WHEN CAST(srid AS TEXT) = %s THEN 0 ELSE 1 END,
                    srid
                LIMIT 200
            """, (q, q, q, q, self._query))
            rows = cur.fetchall()
            conn.close()
            self.done.emit(rows)
        except Exception as e:
            self.error.emit(str(e))


class _ReprojectWorker(QThread):
    done  = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, geom_col, old_srid, new_srid):
        super().__init__()
        self._db       = db
        self._schema   = schema
        self._table    = table
        self._geom_col = geom_col
        self._old_srid = old_srid
        self._new_srid = new_srid

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            s, t, c = self._schema, self._table, self._geom_col
            cur.execute(f"""
                UPDATE "{s}"."{t}"
                SET "{c}" = ST_Transform(
                    ST_SetSRID("{c}", {self._old_srid}),
                    {self._new_srid}
                )
            """)
            # update geometry_columns / type modifier
            cur.execute(f"""
                SELECT UpdateGeometrySRID('{s}','{t}','{c}',{self._new_srid})
            """)
            conn.commit()
            conn.close()
            self.done.emit()
        except Exception as e:
            self.error.emit(str(e))


class CRSBrowserPanel(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db     = db
        self._worker = None
        self._rows:  list = []
        self._build_ui()

    def set_db(self, db):
        self._db = db

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # ── Search bar ────────────────────────────────────────────────────
        tb = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search EPSG code or name… e.g. 4326 or WGS 84")
        self._search.returnPressed.connect(self._search_crs)
        tb.addWidget(self._search)
        self._search_btn = QPushButton("🔍 Search")
        self._search_btn.clicked.connect(self._search_crs)
        tb.addWidget(self._search_btn)
        self._status = QLabel("")
        tb.addWidget(self._status)
        root.addLayout(tb)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setMaximumHeight(4)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Results table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["SRID", "Auth", "Auth SRID", "Name/Description"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        for i in range(3):
            self._table.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents)
        self._table.currentRowChanged.connect(self._on_select)
        splitter.addWidget(self._table)

        # Detail pane
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)
        rv.addWidget(QLabel("WKT / Proj4:"))
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setMaximumHeight(220)
        rv.addWidget(self._detail)

        rv.addWidget(QLabel("Reproject layer to this CRS:"))
        repr_form = QFormLayout()
        self._repr_schema = QComboBox(); self._repr_schema.setMinimumWidth(100)
        self._repr_schema.currentTextChanged.connect(self._fill_tables)
        self._repr_table  = QComboBox(); self._repr_table.setMinimumWidth(140)
        self._repr_table.currentTextChanged.connect(self._fill_geom_cols)
        self._repr_col    = QComboBox(); self._repr_col.setMinimumWidth(120)
        self._repr_from_srid = QSpinBox()
        self._repr_from_srid.setRange(1, 999999); self._repr_from_srid.setValue(4326)
        repr_form.addRow("Schema:", self._repr_schema)
        repr_form.addRow("Table:",  self._repr_table)
        repr_form.addRow("Geom col:", self._repr_col)
        repr_form.addRow("From SRID:", self._repr_from_srid)
        rv.addLayout(repr_form)

        self._repr_btn = QPushButton("⚙ Reproject Layer")
        self._repr_btn.clicked.connect(self._reproject)
        rv.addWidget(self._repr_btn)
        rv.addStretch()
        splitter.addWidget(right)
        splitter.setSizes([460, 340])
        root.addWidget(splitter)

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
            self._repr_schema.clear()
            for s in schemas:
                self._repr_schema.addItem(s)
        except Exception:
            pass
        # Seed with common searches
        self._search.setText("4326")
        self._search_crs()

    def _fill_tables(self, schema: str):
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
            self._repr_table.clear()
            for t in tables: self._repr_table.addItem(t)
        except Exception:
            pass

    def _fill_geom_cols(self, table: str):
        schema = self._repr_schema.currentText()
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
            self._repr_col.clear()
            for c in cols: self._repr_col.addItem(c)
            # auto-detect source SRID
            if cols:
                cur2 = psycopg2.connect(**self._db.params).cursor()
                try:
                    cur2.execute(
                        f'SELECT ST_SRID("{cols[0]}") FROM "{schema}"."{table}" LIMIT 1')
                    r = cur2.fetchone()
                    if r: self._repr_from_srid.setValue(r[0])
                except Exception:
                    pass
        except Exception:
            pass

    def _search_crs(self):
        q = self._search.text().strip()
        if not q:
            return
        if not self._db or not self._db.is_connected():
            self._status.setText("Not connected"); return
        self._search_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._worker = _SearchWorker(self._db, q)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, rows: list):
        self._rows = rows
        self._progress.setVisible(False)
        self._search_btn.setEnabled(True)
        self._table.setRowCount(len(rows))
        for ri, (srid, auth, auth_srid, srtext, proj4) in enumerate(rows):
            # extract name from srtext
            name = srtext.split('"')[1] if srtext and '"' in srtext else str(srid)
            for ci, v in enumerate([str(srid), auth or "", str(auth_srid or ""), name]):
                self._table.setItem(ri, ci, QTableWidgetItem(v))
        self._status.setText(f"{len(rows)} CRS found")

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._search_btn.setEnabled(True)
        self._status.setText(f"Error: {msg[:60]}")

    def _on_select(self, row: int):
        if row < 0 or row >= len(self._rows):
            return
        srid, auth, auth_srid, srtext, proj4 = self._rows[row]
        self._detail.setPlainText(
            f"SRID: {srid}\nAuth: {auth}:{auth_srid}\n\n"
            f"WKT:\n{srtext or '—'}\n\nProj4:\n{proj4 or '—'}"
        )

    def _selected_srid(self) -> int | None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row][0]

    def _reproject(self):
        new_srid = self._selected_srid()
        if new_srid is None:
            QMessageBox.information(self, "Select CRS", "Select a target CRS first.")
            return
        schema  = self._repr_schema.currentText()
        table   = self._repr_table.currentText()
        col     = self._repr_col.currentText()
        old_srid = self._repr_from_srid.value()
        if not all([schema, table, col]):
            QMessageBox.information(self, "Select layer", "Select schema/table/column.")
            return
        if QMessageBox.question(
            self, "Reproject",
            f"Reproject {schema}.{table}.{col}\n"
            f"from EPSG:{old_srid} → EPSG:{new_srid}?\n\n"
            f"This will UPDATE the table in-place.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        self._repr_btn.setEnabled(False)
        self._progress.setVisible(True)
        worker = _ReprojectWorker(self._db, schema, table, col, old_srid, new_srid)
        worker.done.connect(lambda: (
            self._progress.setVisible(False),
            self._repr_btn.setEnabled(True),
            self._status.setText(f"✔ Reprojected to EPSG:{new_srid}"),
        ))
        worker.error.connect(lambda e: (
            self._progress.setVisible(False),
            self._repr_btn.setEnabled(True),
            QMessageBox.critical(self, "Reproject error", e),
        ))
        worker.start()
        self._repr_worker = worker  # keep ref
