"""Layer Snapshot + Change Diff View — save/compare spatial snapshots."""

from __future__ import annotations
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QProgressBar, QGroupBox, QFormLayout, QLineEdit,
    QTabWidget, QSplitter, QTextEdit,
)
from PyQt6.QtGui import QColor


SNAPSHOT_SCHEMA = "_postgis_manager_snapshots"


class _SnapshotWorker(QThread):
    done  = pyqtSignal(str)  # snapshot table name
    error = pyqtSignal(str)

    def __init__(self, db, src_schema, src_table, label):
        super().__init__()
        self._db = db; self._src_schema = src_schema
        self._src_table = src_table; self._label = label

    def run(self):
        try:
            import psycopg2, re
            from datetime import datetime
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()

            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SNAPSHOT_SCHEMA}")

            safe = re.sub(r"[^a-z0-9_]", "_",
                          f"{self._src_schema}__{self._src_table}".lower())
            ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            snap_table = f"snap_{safe}_{ts}"

            cur.execute(f"""
                CREATE TABLE {SNAPSHOT_SCHEMA}."{snap_table}" AS
                SELECT *, now() AS _snap_time, %s AS _snap_label
                FROM "{self._src_schema}"."{self._src_table}"
            """, (self._label,))

            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {SNAPSHOT_SCHEMA}._snapshot_registry (
                    id SERIAL PRIMARY KEY,
                    src_schema TEXT, src_table TEXT,
                    snap_table TEXT, label TEXT,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    row_count  INTEGER
                )
            """)
            cur.execute(f"SELECT count(*) FROM {SNAPSHOT_SCHEMA}.\"{snap_table}\"")
            rc = cur.fetchone()[0]
            cur.execute(f"""
                INSERT INTO {SNAPSHOT_SCHEMA}._snapshot_registry
                    (src_schema, src_table, snap_table, label, row_count)
                VALUES (%s,%s,%s,%s,%s)
            """, (self._src_schema, self._src_table, snap_table,
                  self._label, rc))
            conn.commit(); conn.close()
            self.done.emit(snap_table)
        except Exception as e:
            self.error.emit(str(e))


class _DiffWorker(QThread):
    done  = pyqtSignal(dict)  # {added, removed, changed, geom_changed}
    error = pyqtSignal(str)

    def __init__(self, db, snap_a, snap_b, id_col, geom_col):
        super().__init__()
        self._db = db; self._a = snap_a; self._b = snap_b
        self._id = id_col; self._geom = geom_col

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            S = SNAPSHOT_SCHEMA

            # added: in B not in A
            cur.execute(f"""
                SELECT count(*) FROM {S}."{self._b}" b
                WHERE NOT EXISTS (
                    SELECT 1 FROM {S}."{self._a}" a WHERE a."{self._id}" = b."{self._id}"
                )
            """); added = cur.fetchone()[0]

            # removed: in A not in B
            cur.execute(f"""
                SELECT count(*) FROM {S}."{self._a}" a
                WHERE NOT EXISTS (
                    SELECT 1 FROM {S}."{self._b}" b WHERE b."{self._id}" = a."{self._id}"
                )
            """); removed = cur.fetchone()[0]

            # geometry changed
            cur.execute(f"""
                SELECT count(*) FROM {S}."{self._a}" a
                JOIN {S}."{self._b}" b ON a."{self._id}" = b."{self._id}"
                WHERE NOT ST_Equals(
                    ST_SnapToGrid(a."{self._geom}", 0.0001),
                    ST_SnapToGrid(b."{self._geom}", 0.0001)
                )
            """); geom_changed = cur.fetchone()[0]

            # sample of changed geometries as GeoJSON for map
            cur.execute(f"""
                SELECT json_build_object(
                    'type','FeatureCollection',
                    'features', json_agg(
                        json_build_object(
                            'type','Feature',
                            'geometry', ST_AsGeoJSON(b."{self._geom}")::json,
                            'properties', json_build_object(
                                'id', b."{self._id}", 'change_type','geom_changed'
                            )
                        )
                    )
                )
                FROM {S}."{self._a}" a
                JOIN {S}."{self._b}" b ON a."{self._id}" = b."{self._id}"
                WHERE NOT ST_Equals(
                    ST_SnapToGrid(a."{self._geom}", 0.0001),
                    ST_SnapToGrid(b."{self._geom}", 0.0001)
                )
                LIMIT 500
            """)
            row = cur.fetchone()
            diff_geojson = row[0] if row and row[0] else None

            conn.close()
            self.done.emit({
                "added": added, "removed": removed,
                "geom_changed": geom_changed,
                "diff_geojson": diff_geojson,
            })
        except Exception as e:
            self.error.emit(str(e))


# ── panel ─────────────────────────────────────────────────────────────────────

class LayerSnapshotPanel(QWidget):
    diff_layer_ready = pyqtSignal(dict, str)  # geojson, name

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._snapshots: list = []
        self._workers = []
        self._build_ui()

    def set_db(self, db):
        self._db = db

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        tabs = QTabWidget()

        # ── Tab 1: Create Snapshot ──────────────────────────────────────────
        snap_w = QWidget()
        sl = QVBoxLayout(snap_w)
        snap_form = QGroupBox("Create snapshot")
        sf = QFormLayout(snap_form)
        self._snap_schema = QComboBox(); self._snap_schema.setMinimumWidth(120)
        self._snap_schema.currentTextChanged.connect(self._on_snap_schema)
        sf.addRow("Schema:", self._snap_schema)
        self._snap_table = QComboBox()
        sf.addRow("Table:", self._snap_table)
        self._snap_label = QLineEdit()
        self._snap_label.setPlaceholderText("Optional label…")
        sf.addRow("Label:", self._snap_label)
        sl.addWidget(snap_form)

        self._snap_progress = QProgressBar()
        self._snap_progress.setRange(0, 0)
        self._snap_progress.setMaximumHeight(6)
        self._snap_progress.setVisible(False)
        sl.addWidget(self._snap_progress)

        snap_btn_row = QHBoxLayout()
        self._snap_btn = QPushButton("📸 Take Snapshot")
        self._snap_btn.setFixedHeight(30)
        self._snap_btn.clicked.connect(self._take_snapshot)
        snap_btn_row.addWidget(self._snap_btn)
        self._snap_status = QLabel("")
        snap_btn_row.addWidget(self._snap_status)
        snap_btn_row.addStretch()
        sl.addLayout(snap_btn_row)
        sl.addStretch()
        tabs.addTab(snap_w, "📸 Snapshot")

        # ── Tab 2: Snapshot Registry ────────────────────────────────────────
        reg_w = QWidget()
        rl = QVBoxLayout(reg_w)
        ref_btn = QPushButton("🔄 Refresh registry")
        ref_btn.clicked.connect(self._load_registry)
        rl.addWidget(ref_btn)
        cols = ["ID", "Source schema", "Source table", "Snapshot table",
                "Label", "Rows", "Created"]
        self._reg_table = QTableWidget(0, len(cols))
        self._reg_table.setHorizontalHeaderLabels(cols)
        self._reg_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._reg_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._reg_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._reg_table.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self._reg_table)
        tabs.addTab(reg_w, "📋 Registry")

        # ── Tab 3: Diff ─────────────────────────────────────────────────────
        diff_w = QWidget()
        dl = QVBoxLayout(diff_w)
        diff_form = QGroupBox("Compare two snapshots")
        dff = QFormLayout(diff_form)
        self._diff_a = QComboBox()
        dff.addRow("Snapshot A (older):", self._diff_a)
        self._diff_b = QComboBox()
        dff.addRow("Snapshot B (newer):", self._diff_b)
        self._diff_id   = QLineEdit("id");    dff.addRow("ID column:", self._diff_id)
        self._diff_geom = QLineEdit("geom");  dff.addRow("Geometry col:", self._diff_geom)
        dl.addWidget(diff_form)

        diff_btn_row = QHBoxLayout()
        diff_run = QPushButton("🔍 Compare")
        diff_run.setFixedHeight(30)
        diff_run.clicked.connect(self._run_diff)
        diff_btn_row.addWidget(diff_run)
        show_map = QPushButton("🗺 Show diff on map")
        show_map.clicked.connect(self._show_diff_map)
        diff_btn_row.addWidget(show_map)
        diff_btn_row.addStretch()
        dl.addLayout(diff_btn_row)

        self._diff_result = QTextEdit()
        self._diff_result.setReadOnly(True)
        self._diff_result.setMaximumHeight(120)
        dl.addWidget(self._diff_result)
        dl.addStretch()
        tabs.addTab(diff_w, "🔄 Diff")

        root.addWidget(tabs)
        self._last_diff: dict = {}

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
            self._snap_schema.blockSignals(True)
            self._snap_schema.clear()
            for s in schemas: self._snap_schema.addItem(s)
            self._snap_schema.blockSignals(False)
            self._on_snap_schema(self._snap_schema.currentText())
            self._load_registry()
        except Exception:
            pass

    def _on_snap_schema(self, schema: str):
        if not schema or not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=%s ORDER BY table_name", (schema,))
            tables = [r[0] for r in cur.fetchall()]
            conn.close()
            self._snap_table.clear()
            for t in tables: self._snap_table.addItem(t)
        except Exception:
            pass

    def _take_snapshot(self):
        schema = self._snap_schema.currentText()
        table  = self._snap_table.currentText()
        if not schema or not table:
            QMessageBox.warning(self, "Missing", "Select schema and table.")
            return
        self._snap_btn.setEnabled(False)
        self._snap_progress.setVisible(True)
        label = self._snap_label.text().strip() or f"{schema}.{table}"
        w = _SnapshotWorker(self._db, schema, table, label)
        w.done.connect(self._on_snapped)
        w.error.connect(lambda e: (
            QMessageBox.critical(self, "Error", e),
            self._snap_btn.setEnabled(True),
            self._snap_progress.setVisible(False),
        ))
        w.start(); self._workers.append(w)

    def _on_snapped(self, snap_table: str):
        self._snap_btn.setEnabled(True)
        self._snap_progress.setVisible(False)
        self._snap_status.setText(f"✔ Saved: {snap_table}")
        self._load_registry()

    def _load_registry(self):
        if not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute(f"""
                SELECT id, src_schema, src_table, snap_table, label, row_count, created_at
                FROM {SNAPSHOT_SCHEMA}._snapshot_registry
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()
            conn.close()
            self._reg_table.setRowCount(len(rows))
            snap_names = []
            for ri, row in enumerate(rows):
                for ci, val in enumerate(row):
                    self._reg_table.setItem(ri, ci, QTableWidgetItem(str(val or "")))
                snap_names.append(row[3])
            self._diff_a.clear(); self._diff_b.clear()
            for n in snap_names:
                self._diff_a.addItem(n); self._diff_b.addItem(n)
        except Exception:
            pass

    def _run_diff(self):
        a = self._diff_a.currentText()
        b = self._diff_b.currentText()
        if not a or not b:
            QMessageBox.warning(self, "Missing", "Select two snapshots."); return
        if a == b:
            QMessageBox.warning(self, "Same snapshot", "Select two different snapshots."); return
        id_col   = self._diff_id.text().strip()   or "id"
        geom_col = self._diff_geom.text().strip() or "geom"
        w = _DiffWorker(self._db, a, b, id_col, geom_col)
        w.done.connect(self._on_diff)
        w.error.connect(lambda e: QMessageBox.critical(self, "Diff error", e))
        w.start(); self._workers.append(w)

    def _on_diff(self, result: dict):
        self._last_diff = result
        self._diff_result.setHtml(
            f"<b>Added:</b> {result['added']}<br>"
            f"<b>Removed:</b> {result['removed']}<br>"
            f"<b>Geometry changed:</b> {result['geom_changed']}<br>"
        )

    def _show_diff_map(self):
        gj = self._last_diff.get("diff_geojson")
        if not gj:
            QMessageBox.information(self, "No data",
                                    "Run a diff first, or no geometry changes found.")
            return
        a = self._diff_a.currentText()
        b = self._diff_b.currentText()
        self.diff_layer_ready.emit(gj, f"Diff: {a} → {b}")
