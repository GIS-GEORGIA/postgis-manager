"""DB Setup panel — cross-platform PostgreSQL discovery, extension activation,
and environment variable management."""

from __future__ import annotations
import os
import platform

import psycopg2
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QLineEdit,
    QTextEdit, QGroupBox, QFormLayout, QCheckBox, QMessageBox, QFileDialog, QScrollArea,
    QInputDialog,
)

from ...utils.workers import launch
from ...utils.pg_engine import (
    discover_instances, start_instance, stop_instance,
    reload_instance, PGInstance,
)
from ...utils.env_manager import (
    get_persistent, set_persistent, delete_persistent, all_suggestions,
)

PLATFORM = platform.system()

# PostGIS extension bundle
EXTENSIONS = [
    ("postgis",              "PostGIS — geometry/geography types + functions"),
    ("postgis_topology",     "PostGIS Topology — topology data model"),
    ("postgis_raster",       "PostGIS Raster — raster data support"),
    ("fuzzystrmatch",        "FuzzyStrMatch — string similarity (needed by Tiger)"),
    ("address_standardizer", "Address Standardizer"),
    ("postgis_tiger_geocoder","PostGIS Tiger Geocoder"),
    ("pgrouting",            "pgRouting — graph routing algorithms"),
    ("ogr_fdw",              "OGR FDW — foreign data wrapper for OGR sources"),
    ("pointcloud",           "Point Cloud — LIDAR/point cloud data"),
    ("h3",                   "H3 — Uber's hexagonal geospatial indexing"),
    ("mobilitydb",           "MobilityDB — moving-object data"),
    ("pg_sphere",            "pgSphere — spherical geometry"),
    ("uuid-ossp",            "uuid-ossp — UUID generation"),
    ("pg_trgm",              "pg_trgm — trigram text search"),
    ("hstore",               "hstore — key-value store in columns"),
    ("btree_gist",           "btree_gist — GiST operators for B-tree types"),
]


# ── Workers ──────────────────────────────────────────────────────────────

class DiscoverWorker(QThread):
    done = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        try:
            instances = discover_instances()
            self.done.emit(instances)
        except Exception as e:
            self.error.emit(str(e))


class ExtensionWorker(QThread):
    row_done = pyqtSignal(str, str, str)  # ext_name, installed_version, default_version
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, conn_params: dict, extensions: list[str]):
        super().__init__()
        self.conn_params = conn_params
        self.extensions = extensions

    def run(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            conn.autocommit = True
            cur = conn.cursor()
            names = ",".join(f"'{e}'" for e in self.extensions)
            cur.execute(f"""
                SELECT name, installed_version, default_version
                FROM pg_available_extensions
                WHERE name IN ({names})
                ORDER BY name
            """)
            available = {row[0]: row for row in cur.fetchall()}
            for ext in self.extensions:
                if ext in available:
                    _, inst, default = available[ext]
                    self.row_done.emit(ext, inst or "", default or "")
                else:
                    self.row_done.emit(ext, "", "N/A")
            cur.close()
            conn.close()
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


class InstallExtWorker(QThread):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, conn_params: dict, extensions: list[str]):
        super().__init__()
        self.conn_params = conn_params
        self.extensions = extensions

    def run(self):
        ok = True
        try:
            conn = psycopg2.connect(**self.conn_params)
            conn.autocommit = True
            cur = conn.cursor()
            for ext in self.extensions:
                try:
                    cur.execute(f"CREATE EXTENSION IF NOT EXISTS \"{ext}\" CASCADE;")
                    self.log.emit(f"✓ {ext}")
                except Exception as e:
                    self.log.emit(f"✗ {ext}: {e}")
                    ok = False
            cur.close()
            conn.close()
        except Exception as e:
            self.log.emit(f"Connection error: {e}")
            ok = False
        self.finished.emit(ok)


class CreateDBWorker(QThread):
    log = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, conn_params: dict, db_name: str,
                 owner: str, extensions: list[str]):
        super().__init__()
        self.conn_params = dict(conn_params)
        self.db_name = db_name
        self.owner = owner
        self.extensions = extensions

    def run(self):
        ok = True
        try:
            # connect to postgres DB to create new DB
            params = dict(self.conn_params)
            params["dbname"] = "postgres"
            conn = psycopg2.connect(**params)
            conn.autocommit = True
            cur = conn.cursor()
            owner_clause = f" OWNER \"{self.owner}\"" if self.owner else ""
            cur.execute(
                f"CREATE DATABASE \"{self.db_name}\"{owner_clause} "
                f"ENCODING 'UTF8';"
            )
            self.log.emit(f"✓ Database '{self.db_name}' created")
            cur.close()
            conn.close()

            # now connect to new DB and install extensions
            params["dbname"] = self.db_name
            conn2 = psycopg2.connect(**params)
            conn2.autocommit = True
            cur2 = conn2.cursor()
            for ext in self.extensions:
                try:
                    cur2.execute(
                        f"CREATE EXTENSION IF NOT EXISTS \"{ext}\" CASCADE;")
                    self.log.emit(f"  ✓ {ext}")
                except Exception as e:
                    self.log.emit(f"  ✗ {ext}: {e}")
                    ok = False
            cur2.close()
            conn2.close()
        except Exception as e:
            self.log.emit(f"Error: {e}")
            ok = False
        self.finished.emit(ok)


# ── Panel ─────────────────────────────────────────────────────────────────

class DBSetupPanel(QWidget):
    connect_to = pyqtSignal(dict)   # emit connection info for main window

    def __init__(self, parent=None):
        super().__init__(parent)
        self._instances: list[PGInstance] = []
        self._selected_inst: PGInstance | None = None
        self._ext_worker = None
        self._conn_params: dict = {}
        self._setup_ui()

    # ── UI Construction ───────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabs = QTabWidget()
        tabs.addTab(self._build_engine_tab(),    "Engine Discovery")
        tabs.addTab(self._build_create_db_tab(), "Create Spatial DB")
        tabs.addTab(self._build_ext_tab(),       "PostGIS Extensions")
        tabs.addTab(self._build_env_tab(),        "Environment / GDAL")
        root.addWidget(tabs)

    # ── Tab 1: Engine Discovery ───────────────────────────────────────────

    def _build_engine_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # Toolbar
        bar = QHBoxLayout()
        btn_scan = QPushButton("Scan for PostgreSQL Installations")
        btn_scan.clicked.connect(self._scan_instances)
        bar.addWidget(btn_scan)
        bar.addStretch()
        self._scan_status = QLabel("Click Scan to discover PostgreSQL installations")
        self._scan_status.setStyleSheet("color: gray;")
        bar.addWidget(self._scan_status)
        lay.addLayout(bar)

        # Table
        t = QTableWidget(0, 5)
        t.setHorizontalHeaderLabels(
            ["Version", "Status", "Port", "Bin Dir", "Data Dir"])
        t.horizontalHeader().setStretchLastSection(True)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.itemSelectionChanged.connect(self._on_inst_selected)
        self._inst_table = t
        lay.addWidget(t)

        # Service control
        ctrl_box = QGroupBox("Service Control")
        ctrl_lay = QHBoxLayout(ctrl_box)
        self._btn_start  = QPushButton("▶  Start")
        self._btn_stop   = QPushButton("■  Stop")
        self._btn_reload = QPushButton("↺  Reload")
        self._btn_start.clicked.connect(self._svc_start)
        self._btn_stop.clicked.connect(self._svc_stop)
        self._btn_reload.clicked.connect(self._svc_reload)
        for b in (self._btn_start, self._btn_stop, self._btn_reload):
            b.setEnabled(False)
            ctrl_lay.addWidget(b)
        ctrl_lay.addStretch()
        self._svc_output = QLabel("")
        ctrl_lay.addWidget(self._svc_output)
        lay.addWidget(ctrl_box)

        # pg_hba.conf quick access
        hba_box = QGroupBox("pg_hba.conf  —  Client Authentication Config")
        hba_lay = QVBoxLayout(hba_box)

        path_row = QHBoxLayout()
        self._hba_path = QLineEdit()
        self._hba_path.setPlaceholderText("Select an instance above — path fills automatically")
        self._hba_path.setReadOnly(False)
        path_row.addWidget(self._hba_path, 1)

        btn_browse = QPushButton("Browse…")
        btn_browse.setFixedWidth(80)
        btn_browse.clicked.connect(self._browse_hba)
        path_row.addWidget(btn_browse)
        hba_lay.addLayout(path_row)

        hint = QLabel(
            "Add a line like:  <code>host&nbsp;&nbsp;all&nbsp;&nbsp;all&nbsp;&nbsp;"
            "192.168.0.0/24&nbsp;&nbsp;md5</code>  to allow remote connections.")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        hint.setWordWrap(True)
        hba_lay.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_open   = QPushButton("Open in Editor")
        btn_view   = QPushButton("View Contents")
        btn_reload = QPushButton("Reload Config (pg_reload_conf)")
        btn_add    = QPushButton("Add Allow Rule…")
        btn_open.clicked.connect(self._open_hba)
        btn_view.clicked.connect(self._view_hba)
        btn_reload.clicked.connect(self._reload_hba_sql)
        btn_add.clicked.connect(self._add_hba_rule)
        for b in (btn_open, btn_view, btn_reload, btn_add):
            btn_row.addWidget(b)
        btn_row.addStretch()
        hba_lay.addLayout(btn_row)
        lay.addWidget(hba_box)

        # Connection form
        conn_box = QGroupBox("Connect to Selected Instance")
        fl = QFormLayout(conn_box)
        self._conn_host = QLineEdit("localhost")
        self._conn_port = QLineEdit("5432")
        self._conn_user = QLineEdit("postgres")
        self._conn_pass = QLineEdit()
        self._conn_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._conn_db   = QLineEdit("postgres")
        fl.addRow("Host:", self._conn_host)
        fl.addRow("Port:", self._conn_port)
        fl.addRow("User:", self._conn_user)
        fl.addRow("Password:", self._conn_pass)
        fl.addRow("Database:", self._conn_db)
        btn_connect = QPushButton("Test Connection")
        btn_connect.clicked.connect(self._test_connection)
        fl.addRow("", btn_connect)
        self._conn_result = QLabel("")
        fl.addRow("", self._conn_result)
        lay.addWidget(conn_box)

        return w

    # ── Tab 2: Create Spatial DB ──────────────────────────────────────────

    def _build_create_db_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        fl = QFormLayout()
        self._new_db_name  = QLineEdit("spatial_db")
        self._new_db_owner = QLineEdit()
        self._new_db_owner.setPlaceholderText("leave empty = current user")
        fl.addRow("Database name:", self._new_db_name)
        fl.addRow("Owner role:",    self._new_db_owner)
        lay.addLayout(fl)

        # Extension checkboxes
        ext_box = QGroupBox("Extensions to install automatically")
        ext_scroll = QScrollArea()
        ext_scroll.setWidgetResizable(True)
        ext_content = QWidget()
        ext_lay = QVBoxLayout(ext_content)
        self._create_ext_checks: dict[str, QCheckBox] = {}
        for name, desc in EXTENSIONS:
            cb = QCheckBox(f"{name}  —  {desc}")
            cb.setChecked(name in ("postgis", "postgis_topology"))
            self._create_ext_checks[name] = cb
            ext_lay.addWidget(cb)
        ext_lay.addStretch()
        ext_scroll.setWidget(ext_content)
        ext_vlay = QVBoxLayout(ext_box)
        ext_vlay.addWidget(ext_scroll)
        lay.addWidget(ext_box, 1)

        # Buttons
        btn_lay = QHBoxLayout()
        btn_create = QPushButton("Create Database + Install Extensions")
        btn_create.clicked.connect(self._create_db)
        btn_lay.addWidget(btn_create)
        btn_lay.addStretch()
        lay.addLayout(btn_lay)

        # Log
        self._create_log = QTextEdit()
        self._create_log.setReadOnly(True)
        self._create_log.setMaximumHeight(180)
        lay.addWidget(QLabel("Output:"))
        lay.addWidget(self._create_log)
        return w

    # ── Tab 3: Extensions ─────────────────────────────────────────────────

    def _build_ext_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        bar = QHBoxLayout()
        btn_probe = QPushButton("Probe Available Extensions")
        btn_probe.clicked.connect(self._probe_extensions)
        btn_install = QPushButton("Install Checked Extensions")
        btn_install.clicked.connect(self._install_extensions)
        bar.addWidget(btn_probe)
        bar.addWidget(btn_install)
        bar.addStretch()
        lay.addLayout(bar)

        t = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(
            ["Install", "Extension", "Available", "Installed"])
        t.setColumnWidth(0, 60)
        t.setColumnWidth(1, 200)
        t.setColumnWidth(2, 90)
        t.horizontalHeader().setStretchLastSection(True)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._ext_table = t
        lay.addWidget(t, 1)

        self._ext_log = QTextEdit()
        self._ext_log.setReadOnly(True)
        self._ext_log.setMaximumHeight(120)
        lay.addWidget(QLabel("Output:"))
        lay.addWidget(self._ext_log)
        return w

    # ── Tab 4: Environment ────────────────────────────────────────────────

    def _build_env_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        info = QLabel(
            "Set environment variables that PostGIS / GDAL / PROJ need.\n"
            "Changes are persisted to the OS "
            + ("(HKCU\\Environment + WM_SETTINGCHANGE)." if PLATFORM == "Windows"
               else "(~/.bashrc / ~/.zshrc etc.).")
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        self._env_rows: dict[str, tuple[QLineEdit, QLabel]] = {}
        suggestions = all_suggestions()

        for var, desc in [
            ("PROJ_LIB",     "PROJ datum/grid data directory"),
            ("GDAL_DATA",    "GDAL shared data directory"),
            ("SSL_CERT_FILE","CA certificate bundle for SSL"),
            ("PGPASSWORD",   "Default PostgreSQL password (optional)"),
        ]:
            box = QGroupBox(f"{var}  —  {desc}")
            fl = QFormLayout(box)
            current = get_persistent(var) or os.environ.get(var, "")
            le = QLineEdit(current)
            suggested = suggestions.get(var, "")
            hint = QLabel(f"Suggested: {suggested}" if suggested else "No suggestion found")
            hint.setStyleSheet("color: gray; font-size: 11px;")

            btn_lay = QHBoxLayout()
            btn_apply  = QPushButton("Apply")
            btn_detect = QPushButton("Auto-detect")
            btn_clear  = QPushButton("Clear")

            def _make_apply(v=var, field=le):
                def _():
                    val = field.text().strip()
                    if val:
                        set_persistent(v, val)
                        self._env_log.append(f"✓ {v} = {val}")
                    else:
                        QMessageBox.warning(self, "Empty value",
                                            "Enter a value before applying.")
                return _

            def _make_detect(v=var, field=le, sug=suggested):
                def _():
                    if sug:
                        field.setText(sug)
                    else:
                        self._env_log.append(f"No suggestion found for {v}.")
                return _

            def _make_clear(v=var, field=le):
                def _():
                    reply = QMessageBox.question(
                        self, "Clear", f"Remove persistent {v}?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                    if reply == QMessageBox.StandardButton.Yes:
                        delete_persistent(v)
                        field.clear()
                        self._env_log.append(f"✓ {v} cleared")
                return _

            btn_apply.clicked.connect(_make_apply())
            btn_detect.clicked.connect(_make_detect())
            btn_clear.clicked.connect(_make_clear())

            for b in (btn_apply, btn_detect, btn_clear):
                btn_lay.addWidget(b)
            btn_lay.addStretch()

            fl.addRow("Value:", le)
            fl.addRow("", hint)
            fl.addRow("", self._wrap_hlay(btn_lay))
            lay.addWidget(box)
            self._env_rows[var] = (le, hint)

        self._env_log = QTextEdit()
        self._env_log.setReadOnly(True)
        self._env_log.setMaximumHeight(100)
        lay.addWidget(QLabel("Output:"))
        lay.addWidget(self._env_log)
        lay.addStretch()
        return w

    def _wrap_hlay(self, hlay: QHBoxLayout) -> QWidget:
        w = QWidget()
        w.setLayout(hlay)
        return w

    # ── Slots: Engine tab ─────────────────────────────────────────────────

    def _scan_instances(self):
        self._scan_status.setText("Scanning…")
        self._inst_table.setRowCount(0)
        worker = DiscoverWorker(self)
        worker.done.connect(self._on_scan_done)
        worker.error.connect(lambda e: self._scan_status.setText(f"Error: {e}"))
        self._discover_worker = worker
        launch(worker)

    def _on_scan_done(self, instances: list):
        self._instances = instances
        t = self._inst_table
        t.setRowCount(0)
        for inst in instances:
            row = t.rowCount()
            t.insertRow(row)
            status = inst.status
            color = (QColor("#4caf50") if status == "running"
                     else QColor("#f44336") if status == "stopped"
                     else QColor("#9e9e9e"))
            items = [inst.version, status, inst.port, inst.bin_dir, inst.data_dir]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setForeground(color)
                t.setItem(row, col, item)
        self._scan_status.setText(
            f"Found {len(instances)} installation(s)" if instances
            else "No PostgreSQL installations found")

    def _on_inst_selected(self):
        rows = self._inst_table.selectedItems()
        if not rows:
            return
        row = self._inst_table.currentRow()
        if 0 <= row < len(self._instances):
            inst = self._instances[row]
            self._selected_inst = inst
            for b in (self._btn_start, self._btn_stop, self._btn_reload):
                b.setEnabled(True)
            self._conn_port.setText(inst.port)
            # auto-fill pg_hba.conf path
            hba = self._find_hba(inst)
            if hba:
                self._hba_path.setText(hba)

    def _find_hba(self, inst: PGInstance) -> str:
        """Return pg_hba.conf path for this instance."""
        # 1. data_dir/pg_hba.conf (most reliable)
        if inst.data_dir:
            candidate = os.path.join(inst.data_dir, "pg_hba.conf")
            if os.path.isfile(candidate):
                return candidate
        # 2. Ask psql if connected
        if inst.data_dir and os.path.isfile(inst.psql):
            try:
                import subprocess
                out = subprocess.check_output(
                    [inst.psql, "-U", "postgres", "-h", "localhost",
                     "-p", inst.port, "-tAc",
                     "SHOW hba_file;"],
                    timeout=5, stderr=subprocess.DEVNULL, text=True)
                p = out.strip()
                if p and os.path.isfile(p):
                    return p
            except Exception:
                pass
        # 3. Common Linux paths
        if PLATFORM == "Linux":
            import glob
            for pattern in [
                "/etc/postgresql/*/main/pg_hba.conf",
                "/var/lib/postgresql/*/main/pg_hba.conf",
            ]:
                matches = glob.glob(pattern)
                if matches:
                    return sorted(matches)[-1]
        # 4. macOS
        if PLATFORM == "Darwin":
            import glob
            for pattern in [
                os.path.expanduser("~/Library/Application Support/Postgres/var-*/pg_hba.conf"),
                "/opt/homebrew/var/postgresql*/pg_hba.conf",
                "/usr/local/var/postgresql*/pg_hba.conf",
            ]:
                matches = glob.glob(pattern)
                if matches:
                    return sorted(matches)[-1]
        return ""

    # ── pg_hba.conf helpers ───────────────────────────────────────────────

    def _browse_hba(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select pg_hba.conf", "",
            "Config files (pg_hba.conf);;All files (*)")
        if path:
            self._hba_path.setText(path)

    def _open_hba(self):
        path = self._hba_path.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Not found",
                                "Set a valid pg_hba.conf path first.")
            return
        import subprocess
        if PLATFORM == "Windows":
            os.startfile(path)
        elif PLATFORM == "Darwin":
            subprocess.Popen(["open", "-t", path])
        else:
            for editor in ("gedit", "kate", "xed", "mousepad", "nano"):
                import shutil
                if shutil.which(editor):
                    subprocess.Popen([editor, path])
                    return
            subprocess.Popen(["xdg-open", path])

    def _view_hba(self):
        path = self._hba_path.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Not found",
                                "Set a valid pg_hba.conf path first.")
            return
        try:
            content = open(path, encoding="utf-8", errors="replace").read()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        dlg = QMessageBox(self)
        dlg.setWindowTitle(f"pg_hba.conf — {path}")
        dlg.setText("Contents (read-only preview):")
        dlg.setDetailedText(content)
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.exec()

    def _reload_hba_sql(self):
        """Run SELECT pg_reload_conf() via active connection."""
        if not self._conn_params:
            QMessageBox.warning(self, "No connection",
                                "Test a connection first.")
            return
        try:
            conn = psycopg2.connect(**self._conn_params)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SELECT pg_reload_conf();")
            cur.close()
            conn.close()
            QMessageBox.information(self, "Reloaded",
                                    "✓ pg_reload_conf() executed — pg_hba.conf reloaded.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _add_hba_rule(self):
        """Append a new host rule to pg_hba.conf."""
        path = self._hba_path.text().strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Not found",
                                "Set a valid pg_hba.conf path first.")
            return
        # ask for CIDR
        cidr, ok = QInputDialog.getText(
            self, "Add Allow Rule",
            "Enter client IP or CIDR  (e.g. 192.168.0.0/24  or  10.116.12.0/24):",
            text="192.168.0.0/24")
        if not ok or not cidr.strip():
            return
        method, ok2 = QInputDialog.getItem(
            self, "Auth Method", "Authentication method:",
            ["md5", "scram-sha-256", "trust", "reject"], 0, False)
        if not ok2:
            return
        line = f"\nhost\tall\tall\t{cidr.strip()}\t{method}\n"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            QMessageBox.information(
                self, "Rule added",
                f"✓ Added:\n{line.strip()}\n\n"
                "Click 'Reload Config' to apply without restarting PostgreSQL.")
        except PermissionError:
            QMessageBox.critical(
                self, "Permission denied",
                f"Cannot write to {path}.\n\n"
                "Run the application as administrator, or edit the file manually:\n"
                f"  sudo nano {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _svc_start(self):
        if not self._selected_inst:
            return
        ok, msg = start_instance(self._selected_inst)
        self._svc_output.setText(("✓ " if ok else "✗ ") + msg[:120])

    def _svc_stop(self):
        if not self._selected_inst:
            return
        ok, msg = stop_instance(self._selected_inst)
        self._svc_output.setText(("✓ " if ok else "✗ ") + msg[:120])

    def _svc_reload(self):
        if not self._selected_inst:
            return
        ok, msg = reload_instance(self._selected_inst)
        self._svc_output.setText(("✓ " if ok else "✗ ") + msg[:120])

    def _build_conn_params(self) -> dict:
        return {
            "host":     self._conn_host.text().strip() or "localhost",
            "port":     int(self._conn_port.text().strip() or 5432),
            "user":     self._conn_user.text().strip() or "postgres",
            "password": self._conn_pass.text(),
            "dbname":   self._conn_db.text().strip() or "postgres",
        }

    def _test_connection(self):
        try:
            params = self._build_conn_params()
            conn = psycopg2.connect(**params, connect_timeout=5)
            conn.close()
            self._conn_params = params
            self._conn_result.setText("✓ Connected")
            self._conn_result.setStyleSheet("color: #4caf50;")
        except Exception as e:
            self._conn_result.setText(f"✗ {e}")
            self._conn_result.setStyleSheet("color: #f44336;")

    # ── Slots: Create DB tab ──────────────────────────────────────────────

    def _create_db(self):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection",
                                "Test a connection in the Engine tab first.")
            return
        db_name = self._new_db_name.text().strip()
        if not db_name:
            QMessageBox.warning(self, "No name", "Enter a database name.")
            return
        exts = [n for n, cb in self._create_ext_checks.items() if cb.isChecked()]
        owner = self._new_db_owner.text().strip()
        self._create_log.clear()
        worker = CreateDBWorker(self, self._conn_params, db_name, owner, exts)
        worker.log.connect(self._create_log.append)
        worker.finished.connect(
            lambda ok: self._create_log.append(
                "Done ✓" if ok else "Completed with errors ✗"))
        self._create_worker = worker
        launch(worker)

    # ── Slots: Extensions tab ─────────────────────────────────────────────

    def _probe_extensions(self):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection",
                                "Test a connection in the Engine tab first.")
            return
        ext_names = [name for name, _ in EXTENSIONS]
        self._ext_table.setRowCount(0)
        self._ext_log.clear()

        worker = ExtensionWorker(self, self._conn_params, ext_names)
        worker.row_done.connect(self._on_ext_row)
        worker.error.connect(self._ext_log.append)
        self._ext_worker = worker
        launch(worker)

    def _on_ext_row(self, name: str, installed: str, available: str):
        t = self._ext_table
        row = t.rowCount()
        t.insertRow(row)
        # col 0: checkbox
        cb = QTableWidgetItem()
        cb.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        cb.setCheckState(Qt.CheckState.Unchecked if installed
                         else Qt.CheckState.Checked)
        t.setItem(row, 0, cb)
        # name
        name_item = QTableWidgetItem(name)
        desc = next((d for n, d in EXTENSIONS if n == name), "")
        name_item.setToolTip(desc)
        t.setItem(row, 1, name_item)
        # available
        av = QTableWidgetItem(available if available != "N/A" else "—")
        av.setForeground(QColor("#9e9e9e") if available == "N/A" else QColor())
        t.setItem(row, 2, av)
        # installed
        inst_item = QTableWidgetItem(installed if installed else "—")
        inst_item.setForeground(
            QColor("#4caf50") if installed else QColor("#9e9e9e"))
        t.setItem(row, 3, inst_item)

    def _install_extensions(self):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection",
                                "Test a connection in the Engine tab first.")
            return
        t = self._ext_table
        exts = []
        for row in range(t.rowCount()):
            cb = t.item(row, 0)
            if cb and cb.checkState() == Qt.CheckState.Checked:
                name_item = t.item(row, 1)
                if name_item:
                    exts.append(name_item.text())
        if not exts:
            QMessageBox.information(self, "Nothing selected",
                                    "Check extensions to install first.")
            return
        self._ext_log.clear()
        worker = InstallExtWorker(self, self._conn_params, exts)
        worker.log.connect(self._ext_log.append)
        worker.finished.connect(
            lambda ok: self._ext_log.append("Done ✓" if ok else "Done ✗"))
        worker.finished.connect(self._probe_extensions)  # refresh
        self._install_worker = worker
        launch(worker)
