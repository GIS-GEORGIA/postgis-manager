"""Instance Manager panel — manage standalone + Docker/Podman PostgreSQL instances."""

from __future__ import annotations
import subprocess
import shutil
import textwrap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QMessageBox, QAbstractItemView, QGroupBox, QLineEdit,
    QTextEdit, QDialog, QTabWidget, QSpinBox, QCheckBox,
    QScrollArea, QApplication, QFileDialog,
)
from PyQt6.QtCore import QThread, pyqtSignal, QTimer, Qt
from PyQt6.QtGui import QColor, QFont

from ...utils import i18n, config
from ...utils.docker_utils import (
    available_runtimes, list_postgres_containers,
    start_container, stop_container, restart_container,
    suggest_connection_from_container,
)


# ── PostGIS image catalogue ───────────────────────────────────────────────

POSTGIS_IMAGES = [
    ("postgis/postgis:16-3.4",   "PostgreSQL 16 + PostGIS 3.4  (latest)"),
    ("postgis/postgis:15-3.4",   "PostgreSQL 15 + PostGIS 3.4"),
    ("postgis/postgis:14-3.4",   "PostgreSQL 14 + PostGIS 3.4"),
    ("postgis/postgis:13-3.4",   "PostgreSQL 13 + PostGIS 3.4"),
    ("imresamu/postgis:16-3.4-alpine", "PostgreSQL 16 + PostGIS 3.4 (Alpine, small)"),
    ("pgrouting/pgrouting:16-3.4-3.6.2", "PostgreSQL 16 + PostGIS 3.4 + pgRouting"),
]

_BTN = ("QPushButton{background-color:palette(button);color:palette(button-text);"
        "border:1px solid palette(mid);border-radius:3px;padding:2px 8px;}"
        "QPushButton:hover{background-color:palette(light);}"
        "QPushButton:pressed{background-color:palette(midlight);}")
_BTN_GREEN = ("QPushButton{background-color:#2e7d32;color:#fff;border:none;"
              "border-radius:3px;padding:2px 8px;font-weight:bold;}"
              "QPushButton:hover{background-color:#388e3c;}"
              "QPushButton:disabled{background-color:#555;color:#888;}")


# ── Workers ───────────────────────────────────────────────────────────────

class CreateContainerWorker(QThread):
    log      = pyqtSignal(str)
    finished = pyqtSignal(bool, str)   # ok, container_name

    def __init__(self, runtime: str, image: str, name: str,
                 port: int, password: str, volume: str):
        super().__init__()
        self.runtime  = runtime
        self.image    = image
        self.name     = name
        self.port     = port
        self.password = password
        self.volume   = volume

    def run(self):
        d = self.runtime
        def _run(cmd):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                return r.returncode == 0, (r.stdout + r.stderr).strip()
            except Exception as e:
                return False, str(e)

        # Pull image
        self.log.emit(f"Pulling {self.image}…")
        ok, out = _run([d, "pull", self.image])
        if not ok:
            self.log.emit(f"Pull warning: {out}")

        # Remove old container with same name
        _run([d, "rm", "-f", self.name])

        # Create volume if named
        if self.volume:
            self.log.emit(f"Creating volume: {self.volume}")
            _run([d, "volume", "create", self.volume])

        # Run container
        self.log.emit(f"Starting container '{self.name}' on port {self.port}…")
        vol_arg = f"{self.volume}:/var/lib/postgresql/data" if self.volume else ""
        cmd = [d, "run", "-d",
               "--name", self.name,
               "--restart", "unless-stopped",
               "-p", f"{self.port}:5432",
               "-e", f"POSTGRES_PASSWORD={self.password}",
               "-e", "POSTGRES_USER=postgres",
               "-e", "POSTGRES_DB=postgres",
               ]
        if vol_arg:
            cmd += ["-v", vol_arg]
        cmd.append(self.image)

        ok, out = _run(cmd)
        if ok:
            self.log.emit(f"✓ Container '{self.name}' started on localhost:{self.port}")
            self.log.emit("  User: postgres  |  DB: postgres")
            self.finished.emit(True, self.name)
        else:
            self.log.emit(f"✗ {out}")
            self.finished.emit(False, "")


class LogStreamer(QThread):
    """Tails docker logs and emits lines."""
    line     = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, runtime: str, name: str, tail: int = 100):
        super().__init__()
        self.runtime = runtime
        self.name    = name
        self.tail    = tail
        self._stop   = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            proc = subprocess.Popen(
                [self.runtime, "logs", "-f", f"--tail={self.tail}", self.name],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for raw in proc.stdout:
                if self._stop:
                    break
                self.line.emit(raw.rstrip())
            proc.terminate()
        except Exception as e:
            self.line.emit(f"Error: {e}")
        self.finished.emit()


class StatsWorker(QThread):
    """One-shot docker stats snapshot."""
    done  = pyqtSignal(str)

    def __init__(self, runtime: str, name: str):
        super().__init__()
        self.runtime = runtime
        self.name    = name

    def run(self):
        try:
            r = subprocess.run(
                [self.runtime, "stats", "--no-stream", "--format",
                 "CPU: {{.CPUPerc}}  RAM: {{.MemUsage}}  Net: {{.NetIO}}  "
                 "Block: {{.BlockIO}}",
                 self.name],
                capture_output=True, text=True, timeout=10)
            self.done.emit(r.stdout.strip() or r.stderr.strip())
        except Exception as e:
            self.done.emit(str(e))


# ── Log viewer dialog ─────────────────────────────────────────────────────

class LogViewerDialog(QDialog):
    def __init__(self, runtime: str, name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Logs — {name}")
        self.resize(800, 500)
        lay = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel(f"🐳 {runtime}  /  {name}"))
        bar.addStretch()
        self._stats_lbl = QLabel("Stats: —")
        self._stats_lbl.setStyleSheet("font-family:monospace;color:gray;font-size:11px;")
        bar.addWidget(self._stats_lbl)
        btn_stats = QPushButton("↻ Stats")
        btn_stats.setStyleSheet(_BTN)
        btn_stats.clicked.connect(self._refresh_stats)
        bar.addWidget(btn_stats)
        btn_clear = QPushButton("Clear")
        btn_clear.setStyleSheet(_BTN)
        btn_clear.clicked.connect(lambda: self._log.clear())
        bar.addWidget(btn_clear)
        lay.addLayout(bar)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        f = QFont("Courier New", 10)
        self._log.setFont(f)
        lay.addWidget(self._log)

        self._streamer = LogStreamer(runtime, name)
        self._streamer.line.connect(self._append)
        self._streamer.start()

        # auto-stats every 5 s
        self._stats_w = None
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(5000)
        self._refresh_stats()
        self._runtime = runtime
        self._name = name

    def _append(self, line: str):
        self._log.append(line)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _refresh_stats(self):
        w = StatsWorker(self._runtime, self._name)
        w.done.connect(self._stats_lbl.setText)
        self._stats_w = w
        w.start()

    def closeEvent(self, e):
        self._stats_timer.stop()
        self._streamer.stop()
        super().closeEvent(e)


class ContainerScanWorker(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, runtime: str):
        super().__init__()
        self.runtime = runtime

    def run(self):
        try:
            containers = list_postgres_containers(self.runtime)
            self.done.emit(containers)
        except Exception as e:
            self.error.emit(str(e))


class ContainerActionWorker(QThread):
    done  = pyqtSignal(bool, str)

    def __init__(self, action: str, runtime: str, name: str):
        super().__init__()
        self.action = action; self.runtime = runtime; self.name = name

    def run(self):
        if self.action == "start":
            ok, msg = start_container(self.runtime, self.name)
        elif self.action == "stop":
            ok, msg = stop_container(self.runtime, self.name)
        else:
            ok, msg = restart_container(self.runtime, self.name)
        self.done.emit(ok, msg)


class InstanceManagerPanel(QWidget):
    """Tab for managing DB instances: standalone + Docker/Podman containers."""

    # Signal emitted when user wants to add a container-based connection
    add_connection = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._containers: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"🐳  {i18n.t('inst_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        # ── Runtime selector ──────────────────────────────────────────────────
        rt_box = QGroupBox(i18n.t("inst_runtime"))
        rt_l = QHBoxLayout(rt_box)
        rt_l.addWidget(QLabel(i18n.t("inst_runtime") + ":"))
        self._rt_combo = QComboBox()
        self._rt_combo.addItem("docker", "docker")
        self._rt_combo.addItem("podman", "podman")
        rt_l.addWidget(self._rt_combo)
        self._scan_btn = QPushButton(f"🔍  {i18n.t('inst_scan')}")
        self._scan_btn.clicked.connect(self._scan)
        rt_l.addWidget(self._scan_btn)
        self._rt_status = QLabel("")
        rt_l.addWidget(self._rt_status)
        rt_l.addStretch()
        layout.addWidget(rt_box)

        # ── Container table ───────────────────────────────────────────────────
        ct_box = QGroupBox(i18n.t("inst_containers"))
        ct_l = QVBoxLayout(ct_box)
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            i18n.t("inst_col_name"),
            i18n.t("inst_col_image"),
            i18n.t("inst_col_status"),
            i18n.t("inst_col_ports"),
            "ID",
        ])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(180)
        ct_l.addWidget(self._table)

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("▶  Start")
        self._start_btn.setStyleSheet(_BTN)
        self._start_btn.clicked.connect(lambda: self._container_action("start"))
        btn_row.addWidget(self._start_btn)
        self._stop_btn = QPushButton("■  Stop")
        self._stop_btn.setStyleSheet(_BTN)
        self._stop_btn.clicked.connect(lambda: self._container_action("stop"))
        btn_row.addWidget(self._stop_btn)
        self._restart_btn = QPushButton("↺  Restart")
        self._restart_btn.setStyleSheet(_BTN)
        self._restart_btn.clicked.connect(lambda: self._container_action("restart"))
        btn_row.addWidget(self._restart_btn)
        self._logs_btn = QPushButton("📋  Logs")
        self._logs_btn.setStyleSheet(_BTN)
        self._logs_btn.setToolTip("View live container logs + resource stats")
        self._logs_btn.clicked.connect(self._open_logs)
        btn_row.addWidget(self._logs_btn)
        btn_row.addStretch()
        self._use_btn = QPushButton(f"➕  {i18n.t('inst_add_connection')}")
        self._use_btn.setStyleSheet(_BTN_GREEN)
        self._use_btn.clicked.connect(self._use_as_connection)
        btn_row.addWidget(self._use_btn)
        ct_l.addLayout(btn_row)
        layout.addWidget(ct_box)

        # ── Create new PostGIS container ──────────────────────────────────
        create_box = QGroupBox("🐳  Create New PostGIS Container")
        cr_l = QVBoxLayout(create_box)

        form1 = QHBoxLayout()
        form1.addWidget(QLabel("Image:"))
        self._image_combo = QComboBox()
        for img, desc in POSTGIS_IMAGES:
            self._image_combo.addItem(desc, img)
        form1.addWidget(self._image_combo, 1)
        cr_l.addLayout(form1)

        form2 = QHBoxLayout()
        form2.addWidget(QLabel("Name:"))
        self._new_name = QLineEdit("postgis-local")
        form2.addWidget(self._new_name)
        form2.addWidget(QLabel("Port:"))
        self._new_port = QSpinBox()
        self._new_port.setRange(1024, 65535)
        self._new_port.setValue(5432)
        form2.addWidget(self._new_port)
        form2.addWidget(QLabel("Password:"))
        self._new_pass = QLineEdit("postgres")
        self._new_pass.setEchoMode(QLineEdit.EchoMode.Password)
        form2.addWidget(self._new_pass)
        form2.addWidget(QLabel("Volume:"))
        self._new_vol = QLineEdit("pgdata")
        self._new_vol.setToolTip("Docker named volume for data persistence (leave empty = no volume)")
        form2.addWidget(self._new_vol)
        cr_l.addLayout(form2)

        cr_btn_row = QHBoxLayout()
        btn_create = QPushButton("▶  Create & Start")
        btn_create.setStyleSheet(_BTN_GREEN)
        btn_create.clicked.connect(self._create_container)
        cr_btn_row.addWidget(btn_create)
        cr_btn_row.addStretch()
        cr_l.addLayout(cr_btn_row)

        self._create_log = QTextEdit()
        self._create_log.setReadOnly(True)
        self._create_log.setMaximumHeight(90)
        cr_l.addWidget(self._create_log)
        layout.addWidget(create_box)

        # ── Docker Compose stack generator ────────────────────────────────
        compose_box = QGroupBox("📦  Docker Compose Stack")
        cp_l = QVBoxLayout(compose_box)
        cp_l.addWidget(QLabel(
            "Generate a docker-compose.yml with PostGIS + publishing services:"))

        svc_row = QHBoxLayout()
        self._chk_featureserv = QCheckBox("pg_featureserv  (OGC API / WFS)")
        self._chk_featureserv.setChecked(True)
        self._chk_tileserv = QCheckBox("pg_tileserv  (Vector Tiles)")
        self._chk_tileserv.setChecked(True)
        self._chk_pgadmin = QCheckBox("pgAdmin 4")
        svc_row.addWidget(self._chk_featureserv)
        svc_row.addWidget(self._chk_tileserv)
        svc_row.addWidget(self._chk_pgadmin)
        svc_row.addStretch()
        cp_l.addLayout(svc_row)

        compose_btn_row = QHBoxLayout()
        btn_preview = QPushButton("👁  Preview")
        btn_preview.setStyleSheet(_BTN)
        btn_preview.clicked.connect(self._preview_compose)
        btn_save = QPushButton("💾  Save docker-compose.yml")
        btn_save.setStyleSheet(_BTN)
        btn_save.clicked.connect(self._save_compose)
        btn_up = QPushButton("▶  docker compose up -d")
        btn_up.setStyleSheet(_BTN_GREEN)
        btn_up.clicked.connect(self._compose_up)
        compose_btn_row.addWidget(btn_preview)
        compose_btn_row.addWidget(btn_save)
        compose_btn_row.addWidget(btn_up)
        compose_btn_row.addStretch()
        cp_l.addLayout(compose_btn_row)

        self._compose_preview = QTextEdit()
        self._compose_preview.setReadOnly(True)
        self._compose_preview.setMaximumHeight(150)
        f = QFont("Courier New", 10)
        self._compose_preview.setFont(f)
        cp_l.addWidget(self._compose_preview)
        layout.addWidget(compose_box)

        # ── Saved instances overview ──────────────────────────────────────────
        saved_box = QGroupBox(i18n.t("inst_saved"))
        sv_l = QVBoxLayout(saved_box)
        self._saved_table = QTableWidget()
        self._saved_table.setColumnCount(4)
        self._saved_table.setHorizontalHeaderLabels([
            i18n.t("conn_name"),
            i18n.t("inst_type"),
            i18n.t("conn_host"),
            i18n.t("conn_database"),
        ])
        for c in range(4):
            self._saved_table.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.Stretch)
        self._saved_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._saved_table.setAlternatingRowColors(True)
        self._saved_table.setMinimumHeight(120)
        sv_l.addWidget(self._saved_table)
        ref2 = QPushButton("↻ Refresh saved connections")
        ref2.clicked.connect(self._load_saved)
        sv_l.addWidget(ref2)
        layout.addWidget(saved_box)

        layout.addStretch()
        self._load_saved()
        self._check_runtimes()

    # ── Runtime detection ─────────────────────────────────────────────────────

    def _check_runtimes(self):
        runtimes = available_runtimes()
        if not runtimes:
            self._rt_status.setText("⚠  docker/podman not found in PATH")
            self._rt_status.setStyleSheet("color: #E65100;")
        else:
            self._rt_status.setText(f"✔  Available: {', '.join(runtimes)}")
            self._rt_status.setStyleSheet("color: #2E7D32;")
            idx = self._rt_combo.findData(runtimes[0])
            if idx >= 0:
                self._rt_combo.setCurrentIndex(idx)

    # ── Container scanning ────────────────────────────────────────────────────

    def _scan(self):
        runtime = self._rt_combo.currentData()
        self._scan_btn.setEnabled(False)
        self._rt_status.setText(f"Scanning {runtime}...")
        worker = ContainerScanWorker(runtime)
        worker.done.connect(self._on_scan_done)
        worker.error.connect(self._on_scan_error)
        worker.finished.connect(lambda: self._scan_btn.setEnabled(True))
        worker.start()
        self._scan_worker = worker  # keep reference

    def _on_scan_done(self, containers: list):
        self._containers = containers
        self._table.setRowCount(len(containers))
        for i, c in enumerate(containers):
            running = c.get("running", False)
            status_icon = "🟢" if running else "🔴"
            items = [
                c.get("name", ""),
                c.get("image", ""),
                f"{status_icon}  {c.get('status', '')}",
                c.get("ports", ""),
                c.get("id", ""),
            ]
            for j, text in enumerate(items):
                item = QTableWidgetItem(text)
                if not running:
                    item.setForeground(QColor("#888888"))
                self._table.setItem(i, j, item)

        count = len(containers)
        self._rt_status.setText(
            f"✔  Found {count} PostgreSQL container{'s' if count != 1 else ''}")
        self._rt_status.setStyleSheet("color: #2E7D32;" if count else "color: #888;")

    def _on_scan_error(self, err: str):
        self._rt_status.setText(f"✖  {err}")
        self._rt_status.setStyleSheet("color: #C62828;")

    # ── Container actions ─────────────────────────────────────────────────────

    def _selected_container(self) -> dict | None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._containers):
            QMessageBox.warning(self, "Select container",
                                "Please select a container from the list.")
            return None
        return self._containers[row]

    def _container_action(self, action: str):
        c = self._selected_container()
        if not c:
            return
        runtime = c.get("runtime", self._rt_combo.currentData())
        worker = ContainerActionWorker(action, runtime, c["name"])
        worker.done.connect(lambda ok, msg: self._on_action_done(ok, msg, action))
        worker.start()
        self._action_worker = worker

    def _on_action_done(self, ok: bool, msg: str, action: str):
        if ok:
            self._scan()  # refresh table
        else:
            QMessageBox.critical(self, "Error", f"Container {action} failed:\n{msg}")

    # ── Use container as connection ───────────────────────────────────────────

    def _use_as_connection(self):
        c = self._selected_container()
        if not c:
            return
        if not c.get("running"):
            reply = QMessageBox.question(
                self, "Container stopped",
                "Container is not running. Start it first?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self._container_action("start")
            return
        runtime = c.get("runtime", self._rt_combo.currentData())
        profile = suggest_connection_from_container(runtime, c["name"])
        self.add_connection.emit(profile)

    # ── Logs ─────────────────────────────────────────────────────────────────

    def _open_logs(self):
        c = self._selected_container()
        if not c:
            return
        runtime = c.get("runtime", self._rt_combo.currentData())
        dlg = LogViewerDialog(runtime, c["name"], self)
        dlg.show()

    # ── Create container ──────────────────────────────────────────────────────

    def _create_container(self):
        runtime  = self._rt_combo.currentData()
        image    = self._image_combo.currentData()
        name     = self._new_name.text().strip() or "postgis-local"
        port     = self._new_port.value()
        password = self._new_pass.text() or "postgres"
        volume   = self._new_vol.text().strip()

        self._create_log.clear()
        w = CreateContainerWorker(runtime, image, name, port, password, volume)
        w.log.connect(self._create_log.append)
        w.finished.connect(self._on_create_done)
        self._create_worker = w
        w.start()

    def _on_create_done(self, ok: bool, name: str):
        if ok:
            self._scan()   # refresh container list
            reply = QMessageBox.question(
                self, "Container ready",
                f"Container '{name}' is running.\nAdd it as a connection?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                port     = self._new_port.value()
                password = self._new_pass.text() or "postgres"
                profile = {
                    "name":     name,
                    "host":     "localhost",
                    "port":     port,
                    "dbname":   "postgres",
                    "user":     "postgres",
                    "password": password,
                    "instance_type": "docker",
                }
                self.add_connection.emit(profile)

    # ── Docker Compose ────────────────────────────────────────────────────────

    def _build_compose(self) -> str:
        image    = self._image_combo.currentData()
        name     = self._new_name.text().strip() or "postgis"
        port     = self._new_port.value()
        password = self._new_pass.text() or "postgres"
        volume   = self._new_vol.text().strip() or "pgdata"

        db_url = (f"postgresql://postgres:{password}"
                  f"@{name}:5432/postgres")

        services = textwrap.dedent(f"""\
            version: "3.9"

            services:
              {name}:
                image: {image}
                container_name: {name}
                restart: unless-stopped
                environment:
                  POSTGRES_PASSWORD: "{password}"
                  POSTGRES_USER: postgres
                  POSTGRES_DB: postgres
                ports:
                  - "{port}:5432"
                volumes:
                  - {volume}:/var/lib/postgresql/data
                healthcheck:
                  test: ["CMD-SHELL", "pg_isready -U postgres"]
                  interval: 10s
                  timeout: 5s
                  retries: 5
            """)

        if self._chk_featureserv.isChecked():
            services += textwrap.dedent(f"""
              pg_featureserv:
                image: pramsey/pg_featureserv:latest
                container_name: {name}_featureserv
                restart: unless-stopped
                environment:
                  DATABASE_URL: "{db_url}"
                ports:
                  - "9000:9000"
                depends_on:
                  {name}:
                    condition: service_healthy
            """)

        if self._chk_tileserv.isChecked():
            services += textwrap.dedent(f"""
              pg_tileserv:
                image: pramsey/pg_tileserv:latest
                container_name: {name}_tileserv
                restart: unless-stopped
                environment:
                  DATABASE_URL: "{db_url}"
                ports:
                  - "7800:7800"
                depends_on:
                  {name}:
                    condition: service_healthy
            """)

        if self._chk_pgadmin.isChecked():
            services += textwrap.dedent(f"""
              pgadmin:
                image: dpage/pgadmin4:latest
                container_name: {name}_pgadmin
                restart: unless-stopped
                environment:
                  PGADMIN_DEFAULT_EMAIL: admin@admin.com
                  PGADMIN_DEFAULT_PASSWORD: admin
                ports:
                  - "5050:80"
                depends_on:
                  - {name}
            """)

        volumes_section = textwrap.dedent(f"""
            volumes:
              {volume}:
            """)

        return services + volumes_section

    def _preview_compose(self):
        self._compose_preview.setPlainText(self._build_compose())

    def _save_compose(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save docker-compose.yml", "docker-compose.yml",
            "YAML files (*.yml *.yaml);;All files (*)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._build_compose())
            QMessageBox.information(self, "Saved", f"✓ Saved to:\n{path}")

    def _compose_up(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save & run docker-compose.yml", "docker-compose.yml",
            "YAML files (*.yml *.yaml);;All files (*)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self._build_compose())
        try:
            runtime = self._rt_combo.currentData()
            compose_cmd = ("docker" if runtime == "docker" else "podman")
            subprocess.Popen(
                [compose_cmd, "compose", "-f", path, "up", "-d"],
                creationflags=subprocess.CREATE_NEW_CONSOLE
                if hasattr(subprocess, "CREATE_NEW_CONSOLE") else 0,
            )
            QMessageBox.information(
                self, "Started",
                f"✓ docker compose up -d running in background.\n\n"
                f"After ~10 seconds the services will be ready.\n"
                f"Then use 'Scan' to find the new container.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ── Saved connections ─────────────────────────────────────────────────────

    def _load_saved(self):
        conns = config.get_connections()
        self._saved_table.setRowCount(len(conns))
        for i, conn in enumerate(conns):
            inst_type = conn.get("instance_type", "standalone")
            icon = {"docker": "🐳", "podman": "🦭"}.get(inst_type, "🖥")
            for j, text in enumerate([
                conn.get("name", ""),
                f"{icon}  {inst_type}",
                f"{conn.get('host','')}:{conn.get('port','')}",
                conn.get("dbname", ""),
            ]):
                self._saved_table.setItem(i, j, QTableWidgetItem(text))
