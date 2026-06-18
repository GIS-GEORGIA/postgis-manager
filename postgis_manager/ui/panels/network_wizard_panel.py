"""pgRouting Network Setup Wizard — prepare edge table for pgRouting."""

from __future__ import annotations
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QLineEdit, QProgressBar, QTextEdit, QWizard,
    QWizardPage, QFormLayout, QCheckBox, QSpinBox, QGroupBox,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
)


# ── workers ───────────────────────────────────────────────────────────────────

class _InspectWorker(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, db, schema, table):
        super().__init__()
        self._db = db; self._schema = schema; self._table = table

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            s, t = self._schema, self._table

            cur.execute("""
                SELECT column_name, udt_name
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s
                ORDER BY ordinal_position
            """, (s, t))
            cols = [(r[0], r[1]) for r in cur.fetchall()]

            cur.execute(f'SELECT COUNT(*) FROM "{s}"."{t}"')
            total = cur.fetchone()[0]

            # detect geometry col
            geom_cols = [c for c, typ in cols if typ in ("geometry","geography")]

            # check for pgrouting extension
            cur.execute("SELECT COUNT(*) FROM pg_extension WHERE extname='pgrouting'")
            has_pgrouting = cur.fetchone()[0] > 0

            conn.close()
            self.done.emit({
                "cols": cols, "total": total,
                "geom_cols": geom_cols, "has_pgrouting": has_pgrouting,
            })
        except Exception as e:
            self.error.emit(str(e))


class _PrepareWorker(QThread):
    done     = pyqtSignal(str)
    error    = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, db, schema, table, config: dict):
        super().__init__()
        self._db = db; self._schema = schema
        self._table = table; self._config = config

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            s, t = self._schema, self._table
            c = self._config
            log = []

            self.progress.emit(5, "Checking pgRouting…")
            cur.execute("SELECT COUNT(*) FROM pg_extension WHERE extname='pgrouting'")
            if cur.fetchone()[0] == 0:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
                conn.commit()
                log.append("✔ Installed pgRouting extension")

            # Add source/target columns
            self.progress.emit(20, "Adding source/target columns…")
            for col in ("source", "target"):
                cur.execute(f"""
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_schema=%s AND table_name=%s AND column_name=%s
                """, (s, t, col))
                if cur.fetchone()[0] == 0:
                    cur.execute(f'ALTER TABLE "{s}"."{t}" ADD COLUMN "{col}" INTEGER')
                    conn.commit()
                    log.append(f"✔ Added column: {col}")

            # Add cost column
            self.progress.emit(35, "Adding cost column…")
            cost_col = c.get("cost_col", "cost")
            cost_expr = c.get("cost_expr", "ST_Length(geom)")
            cur.execute(f"""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s AND column_name=%s
            """, (s, t, cost_col))
            if cur.fetchone()[0] == 0:
                cur.execute(f'ALTER TABLE "{s}"."{t}" ADD COLUMN "{cost_col}" FLOAT')
                conn.commit()
                log.append(f"✔ Added cost column: {cost_col}")

            # Populate cost
            geom_col = c.get("geom_col", "geom")
            self.progress.emit(45, "Calculating cost values…")
            if c.get("cost_is_length"):
                cur.execute(f'UPDATE "{s}"."{t}" SET "{cost_col}" = ST_Length("{geom_col}")')
                conn.commit()
                log.append(f"✔ Cost = ST_Length({geom_col})")

            # Reverse cost
            if c.get("reverse_cost"):
                rcol = c.get("reverse_cost_col", "reverse_cost")
                cur.execute(f"""
                    SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_schema=%s AND table_name=%s AND column_name=%s
                """, (s, t, rcol))
                if cur.fetchone()[0] == 0:
                    cur.execute(f'ALTER TABLE "{s}"."{t}" ADD COLUMN "{rcol}" FLOAT')
                    conn.commit()
                    log.append(f"✔ Added reverse_cost column: {rcol}")
                cur.execute(f'UPDATE "{s}"."{t}" SET "{rcol}" = "{cost_col}"')
                conn.commit()
                log.append(f"✔ reverse_cost = {cost_col}")

            # Run pgr_createTopology
            self.progress.emit(60, "Creating topology (pgr_createTopology)…")
            tolerance = c.get("tolerance", 0.001)
            cur.execute(f"""
                SELECT pgr_createTopology('{s}.{t}', {tolerance},
                    '{geom_col}', 'id',
                    'source', 'target')
            """)
            res = cur.fetchone()[0]
            conn.commit()
            log.append(f"✔ pgr_createTopology: {res}")

            # Analyze topology
            self.progress.emit(80, "Analyzing topology…")
            cur.execute(f"""
                SELECT pgr_analyzeGraph('{s}.{t}', {tolerance},
                    '{geom_col}', 'id', 'source', 'target')
            """)
            res2 = cur.fetchone()[0]
            log.append(f"✔ pgr_analyzeGraph: {res2}")

            # Create spatial index if missing
            self.progress.emit(90, "Creating spatial index…")
            idx_name = f"{t}_geom_idx"
            cur.execute(f"""
                SELECT COUNT(*) FROM pg_indexes
                WHERE schemaname=%s AND tablename=%s AND indexname=%s
            """, (s, t, idx_name))
            if cur.fetchone()[0] == 0:
                cur.execute(
                    f'CREATE INDEX "{idx_name}" ON "{s}"."{t}" USING GIST ("{geom_col}")')
                conn.commit()
                log.append(f"✔ Created spatial index: {idx_name}")

            conn.close()
            self.progress.emit(100, "Done")
            self.done.emit("\n".join(log))
        except Exception as e:
            self.error.emit(str(e))


# ── isochrone worker ──────────────────────────────────────────────────────────

class _IsochroneWorker(QThread):
    done  = pyqtSignal(dict)   # GeoJSON
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, geom_col, start_x, start_y,
                 costs: list[float], directed: bool):
        super().__init__()
        self._db = db; self._schema = schema; self._table = table
        self._geom_col = geom_col; self._start_x = start_x
        self._start_y = start_y; self._costs = costs; self._directed = directed

    def run(self):
        try:
            import psycopg2, json
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            s, t, g = self._schema, self._table, self._geom_col

            # Find nearest node
            cur.execute(f"""
                SELECT id FROM "{s}"."{t}_vertices_pgr"
                ORDER BY the_geom <-> ST_SetSRID(ST_Point(%s,%s),4326)
                LIMIT 1
            """, (self._start_x, self._start_y))
            row = cur.fetchone()
            if not row:
                self.error.emit("No topology vertices found. Run preparation first.")
                return
            start_id = row[0]

            directed = str(self._directed).lower()
            costs_str = ", ".join(str(c) for c in self._costs)

            cur.execute(f"""
                SELECT agg_cost,
                       ST_AsGeoJSON(ST_Buffer(
                           ST_Collect(v.the_geom)::geography, 0
                       )::geometry) AS geom
                FROM pgr_drivingDistance(
                    'SELECT id, source, target, cost FROM "{s}"."{t}"',
                    {start_id}, ARRAY[{costs_str}]::float[], {directed}
                ) dd
                JOIN "{s}"."{t}_vertices_pgr" v ON dd.node = v.id
                GROUP BY agg_cost
                ORDER BY agg_cost
            """)
            features = []
            for agg_cost, geom_json in cur.fetchall():
                if geom_json:
                    features.append({
                        "type": "Feature",
                        "geometry": json.loads(geom_json),
                        "properties": {"cost": agg_cost}
                    })
            conn.close()
            self.done.emit({"type": "FeatureCollection", "features": features})
        except Exception as e:
            self.error.emit(str(e))


# ── panel ─────────────────────────────────────────────────────────────────────

class NetworkWizardPanel(QWidget):
    isochrone_ready = pyqtSignal(dict, str)   # GeoJSON, layer name

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._inspect_data: dict = {}
        self._worker = None
        self._schemas: dict = {}
        self._build_ui()

    def set_db(self, db):
        self._db = db

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # ── Step 1: select table ──────────────────────────────────────────
        step1 = QGroupBox("Step 1 — Select edge table")
        s1 = QFormLayout(step1)
        self._schema_combo = QComboBox(); self._schema_combo.setMinimumWidth(120)
        self._schema_combo.currentTextChanged.connect(self._fill_tables)
        self._table_combo  = QComboBox(); self._table_combo.setMinimumWidth(160)
        self._inspect_btn  = QPushButton("🔎 Inspect")
        self._inspect_btn.clicked.connect(self._inspect)
        s1.addRow("Schema:", self._schema_combo)
        s1.addRow("Table:",  self._table_combo)
        s1.addRow("",        self._inspect_btn)
        self._inspect_result = QLabel("")
        self._inspect_result.setWordWrap(True)
        s1.addRow("Info:", self._inspect_result)
        root.addWidget(step1)

        # ── Step 2: config ────────────────────────────────────────────────
        step2 = QGroupBox("Step 2 — Configuration")
        s2 = QFormLayout(step2)
        self._geom_combo = QComboBox()
        self._cost_col   = QLineEdit("cost")
        self._cost_is_length = QCheckBox("Calculate from ST_Length (geometry)")
        self._cost_is_length.setChecked(True)
        self._reverse_cost = QCheckBox("Add reverse_cost column")
        self._reverse_cost.setChecked(True)
        self._tolerance  = QLineEdit("0.001")
        self._tolerance.setPlaceholderText("Snapping tolerance (degrees or metres)")
        s2.addRow("Geometry col:", self._geom_combo)
        s2.addRow("Cost column:", self._cost_col)
        s2.addRow("", self._cost_is_length)
        s2.addRow("", self._reverse_cost)
        s2.addRow("Snap tolerance:", self._tolerance)
        root.addWidget(step2)

        # ── Step 3: run ───────────────────────────────────────────────────
        step3 = QGroupBox("Step 3 — Prepare Network")
        s3v = QVBoxLayout(step3)
        self._prepare_btn = QPushButton("⚙ Prepare Network (pgr_createTopology)")
        self._prepare_btn.setFixedHeight(32)
        self._prepare_btn.clicked.connect(self._prepare)
        s3v.addWidget(self._prepare_btn)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setMaximumHeight(6)
        self._progress.setVisible(False)
        s3v.addWidget(self._progress)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(160)
        self._log.setFont(__import__("PyQt6.QtGui", fromlist=["QFont"]).QFont("Courier New", 9))
        s3v.addWidget(self._log)
        root.addWidget(step3)

        # ── Step 4: isochrone ─────────────────────────────────────────────
        iso_box = QGroupBox("Step 4 — Generate Isochrones")
        iso_form = QFormLayout(iso_box)
        self._iso_x = QLineEdit(); self._iso_x.setPlaceholderText("Start X (lon)")
        self._iso_y = QLineEdit(); self._iso_y.setPlaceholderText("Start Y (lat)")
        self._iso_costs = QLineEdit("500, 1000, 2000")
        self._iso_costs.setPlaceholderText("Costs (metres), comma-separated")
        self._iso_directed = QCheckBox("Directed graph")
        iso_form.addRow("Start X:", self._iso_x)
        iso_form.addRow("Start Y:", self._iso_y)
        iso_form.addRow("Cost levels:", self._iso_costs)
        iso_form.addRow("", self._iso_directed)
        iso_btn = QPushButton("🗺 Generate Isochrones → Map")
        iso_btn.clicked.connect(self._gen_isochrone)
        iso_form.addRow("", iso_btn)
        root.addWidget(iso_box)

        root.addStretch()

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
            for s in schemas: self._schema_combo.addItem(s)
        except Exception:
            pass

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
            self._table_combo.clear()
            for r in cur.fetchall(): self._table_combo.addItem(r[0])
            conn.close()
        except Exception:
            pass

    def _inspect(self):
        s = self._schema_combo.currentText()
        t = self._table_combo.currentText()
        if not s or not t: return
        w = _InspectWorker(self._db, s, t)
        w.done.connect(self._on_inspected)
        w.error.connect(lambda e: self._inspect_result.setText(f"Error: {e}"))
        w.start(); self._inspect_w = w

    def _on_inspected(self, data: dict):
        self._inspect_data = data
        self._geom_combo.clear()
        for g in data.get("geom_cols", []):
            self._geom_combo.addItem(g)
        pgr = "✔ pgRouting installed" if data["has_pgrouting"] else "⚠ pgRouting NOT installed (will be added)"
        self._inspect_result.setText(
            f"{data['total']:,} rows | {len(data['cols'])} columns | {pgr}")

    def _prepare(self):
        s = self._schema_combo.currentText()
        t = self._table_combo.currentText()
        if not s or not t:
            QMessageBox.warning(self, "Missing", "Select a table first."); return
        try:
            tol = float(self._tolerance.text())
        except ValueError:
            tol = 0.001
        config = {
            "geom_col":       self._geom_combo.currentText(),
            "cost_col":       self._cost_col.text().strip() or "cost",
            "cost_is_length": self._cost_is_length.isChecked(),
            "reverse_cost":   self._reverse_cost.isChecked(),
            "reverse_cost_col": "reverse_cost",
            "tolerance":      tol,
        }
        self._prepare_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._log.clear()
        self._worker = _PrepareWorker(self._db, s, t, config)
        self._worker.done.connect(self._on_prepared)
        self._worker.error.connect(self._on_error)
        self._worker.progress.connect(lambda v, msg: (
            self._progress.setValue(v), self._log.append(f"[{v}%] {msg}")))
        self._worker.start()

    def _on_prepared(self, log: str):
        self._prepare_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._log.setPlainText(log)

    def _on_error(self, msg: str):
        self._prepare_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._log.append(f"❌ ERROR: {msg}")

    def _gen_isochrone(self):
        s = self._schema_combo.currentText()
        t = self._table_combo.currentText()
        g = self._geom_combo.currentText()
        try:
            x = float(self._iso_x.text())
            y = float(self._iso_y.text())
            costs = [float(v.strip()) for v in self._iso_costs.text().split(",") if v.strip()]
        except ValueError:
            QMessageBox.warning(self, "Invalid input", "Enter valid numbers.")
            return
        w = _IsochroneWorker(self._db, s, t, g, x, y, costs,
                             self._iso_directed.isChecked())
        w.done.connect(lambda gj: self.isochrone_ready.emit(gj, f"Isochrone {t}"))
        w.error.connect(lambda e: QMessageBox.critical(self, "Isochrone error", e))
        w.start(); self._iso_worker = w
