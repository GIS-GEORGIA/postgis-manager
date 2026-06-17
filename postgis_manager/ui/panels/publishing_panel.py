"""Publishing panel — pg_featureserv / pg_tileserv management.

Lets the user start/stop OGC API Feature and tile services backed directly
by PostGIS, then copy ready-made connection URLs for ArcGIS Pro or any
WFS/WMTS client.
"""

from __future__ import annotations
import json
import subprocess
import shutil
import urllib.request
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QLineEdit,
    QTextEdit, QGroupBox, QFormLayout, QCheckBox, QMessageBox,
    QApplication, QHeaderView, QSplitter, QScrollArea,
)

from ...utils.workers import launch


# ── Docker helpers ────────────────────────────────────────────────────────

FEATURESERV_IMAGE = "pramsey/pg_featureserv:latest"
TILESERV_IMAGE    = "pramsey/pg_tileserv:latest"

FEATURESERV_CONTAINER = "postgis_mgr_featureserv"
TILESERV_CONTAINER    = "postgis_mgr_tileserv"

FEATURESERV_PORT = 9000
TILESERV_PORT    = 7800


def _docker() -> Optional[str]:
    return shutil.which("docker")


def _run(cmd: list[str], timeout: int = 15) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)


def _container_status(name: str) -> str:
    """Return 'running', 'stopped', 'missing', or 'no-docker'."""
    if not _docker():
        return "no-docker"
    ok, out = _run([_docker(), "inspect", "--format", "{{.State.Status}}", name])
    if not ok or not out:
        return "missing"
    return out.strip()


# ── Workers ───────────────────────────────────────────────────────────────

class ServiceWorker(QThread):
    log    = pyqtSignal(str)
    done   = pyqtSignal(bool, str)   # success, status

    def __init__(self, action: str, name: str, image: str,
                 port: int, database_url: str):
        super().__init__()
        self.action = action          # "start" | "stop" | "pull" | "status"
        self.name = name
        self.image = image
        self.port = port
        self.database_url = database_url

    def run(self):
        d = _docker()
        if not d:
            self.done.emit(False, "no-docker")
            return

        if self.action == "status":
            status = _container_status(self.name)
            self.done.emit(True, status)
            return

        if self.action == "pull":
            self.log.emit(f"Pulling {self.image}…")
            ok, out = _run([d, "pull", self.image], timeout=120)
            self.log.emit(out)
            self.done.emit(ok, "pulled" if ok else "error")
            return

        if self.action == "stop":
            self.log.emit(f"Stopping {self.name}…")
            _run([d, "stop", self.name])
            _run([d, "rm", self.name])
            self.done.emit(True, "missing")
            return

        if self.action == "start":
            # Remove old container if present
            _run([d, "rm", "-f", self.name])
            self.log.emit(f"Starting {self.name} on port {self.port}…")
            ok, out = _run([
                d, "run", "-d",
                "--name", self.name,
                "--restart", "unless-stopped",
                "-p", f"{self.port}:{self.port}",
                "-e", f"DATABASE_URL={self.database_url}",
                self.image,
            ], timeout=30)
            self.log.emit(out)
            if ok:
                self.log.emit(f"✓ Service started → http://localhost:{self.port}")
                self.done.emit(True, "running")
            else:
                self.done.emit(False, "error")


class LayerFetchWorker(QThread):
    """Fetch available collections from a running featureserv instance."""
    done  = pyqtSignal(list)   # list of {id, title, description}
    error = pyqtSignal(str)

    def __init__(self, port: int):
        super().__init__()
        self.port = port

    def run(self):
        try:
            url = f"http://localhost:{self.port}/collections?f=json"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.load(r)
            cols = data.get("collections", [])
            self.done.emit(cols)
        except Exception as e:
            self.error.emit(str(e))


# ── Shared style ──────────────────────────────────────────────────────────

_BTN = ("QPushButton{background-color:palette(button);color:palette(button-text);"
        "border:1px solid palette(mid);border-radius:3px;padding:3px 10px;}"
        "QPushButton:hover{background-color:palette(light);}"
        "QPushButton:pressed{background-color:palette(midlight);}")

_BTN_GREEN = ("QPushButton{background-color:#2e7d32;color:#fff;"
              "border:none;border-radius:3px;padding:3px 10px;font-weight:bold;}"
              "QPushButton:hover{background-color:#388e3c;}"
              "QPushButton:disabled{background-color:#555;color:#999;}")

_BTN_RED   = ("QPushButton{background-color:#c62828;color:#fff;"
              "border:none;border-radius:3px;padding:3px 10px;font-weight:bold;}"
              "QPushButton:hover{background-color:#d32f2f;}"
              "QPushButton:disabled{background-color:#555;color:#999;}")


# ── Service widget (reusable for featureserv / tileserv) ──────────────────

class _ServiceWidget(QWidget):
    """Generic start/stop UI for one Docker-based OGC service."""

    def __init__(self, title: str, image: str, container: str,
                 port: int, docs_url: str, parent=None):
        super().__init__(parent)
        self._image     = image
        self._container = container
        self._port      = port
        self._docs_url  = docs_url
        self._db_url    = ""        # set by parent when connection is known
        self._worker: Optional[ServiceWorker] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        # ── Status row ────────────────────────────────────────────────────
        status_row = QHBoxLayout()
        self._status_dot = QLabel("⬤")
        self._status_dot.setFixedWidth(20)
        self._status_lbl = QLabel("Unknown")
        f = QFont()
        f.setBold(True)
        self._status_lbl.setFont(f)
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()

        self._btn_start = QPushButton("▶  Start")
        self._btn_stop  = QPushButton("■  Stop")
        self._btn_pull  = QPushButton("⬇  Pull / Update image")
        self._btn_start.setStyleSheet(_BTN_GREEN)
        self._btn_stop.setStyleSheet(_BTN_RED)
        self._btn_pull.setStyleSheet(_BTN)
        self._btn_start.clicked.connect(self._start)
        self._btn_stop.clicked.connect(self._stop)
        self._btn_pull.clicked.connect(self._pull)
        status_row.addWidget(self._btn_start)
        status_row.addWidget(self._btn_stop)
        status_row.addWidget(self._btn_pull)
        lay.addLayout(status_row)

        # ── URL box ───────────────────────────────────────────────────────
        url_box = QGroupBox("Connection URLs")
        url_lay = QFormLayout(url_box)

        self._url_base = QLineEdit(f"http://localhost:{port}")
        self._url_base.setReadOnly(True)
        self._url_wfs  = QLineEdit(f"http://localhost:{port}/collections")
        self._url_wfs.setReadOnly(True)
        self._url_arcgis = QLineEdit(
            f"http://localhost:{port}/collections/{{schema}}.{{table}}/items")
        self._url_arcgis.setReadOnly(True)
        self._url_arcgis.setPlaceholderText("Replace {schema}.{table} with actual layer")

        for label, le, tip in [
            ("Base URL:", self._url_base,
             "Open in browser to see all collections"),
            ("WFS / OGC API:", self._url_wfs,
             "Paste in ArcGIS Pro → Add Data → From Path"),
            ("Single layer:", self._url_arcgis,
             "Direct layer URL for ArcGIS Pro"),
        ]:
            row_lay = QHBoxLayout()
            row_lay.addWidget(le, 1)
            btn_copy = QPushButton("Copy")
            btn_copy.setStyleSheet(_BTN)
            btn_copy.setFixedWidth(55)
            btn_copy.setToolTip(tip)
            btn_copy.clicked.connect(lambda _, w=le: (
                QApplication.clipboard().setText(w.text())))
            row_lay.addWidget(btn_copy)
            url_lay.addRow(label, self._wrap(row_lay))

        lay.addWidget(url_box)

        # ── ArcGIS Pro instructions ───────────────────────────────────────
        how_box = QGroupBox("ArcGIS Pro — How to connect")
        how_lay = QVBoxLayout(how_box)
        steps = QLabel(
            "<ol>"
            "<li>Start the service (▶ Start above)</li>"
            "<li>In ArcGIS Pro: <b>Insert → Connections → New WFS Server</b></li>"
            "<li>Paste the <b>WFS / OGC API</b> URL above</li>"
            "<li>Version: <b>OGC API Features</b></li>"
            "<li>Click OK — all PostGIS layers appear in the Catalog pane</li>"
            "</ol>"
            "<p style='color:gray;font-size:11px;'>"
            "⚠  ArcGIS Pro 2.7+ supports OGC API Features directly.<br>"
            "For older versions use WFS 2.0: add <code>?SERVICE=WFS&VERSION=2.0.0</code></p>"
        )
        steps.setWordWrap(True)
        steps.setOpenExternalLinks(True)
        how_lay.addWidget(steps)
        lay.addWidget(how_box)

        # ── Layers table (populated when service is running) ──────────────
        self._layers_box = QGroupBox("Published Collections (fetched from live service)")
        layers_lay = QVBoxLayout(self._layers_box)
        refresh_row = QHBoxLayout()
        btn_refresh_layers = QPushButton("↻ Refresh list")
        btn_refresh_layers.setStyleSheet(_BTN)
        btn_refresh_layers.clicked.connect(self._fetch_layers)
        refresh_row.addWidget(btn_refresh_layers)
        refresh_row.addStretch()
        layers_lay.addLayout(refresh_row)

        self._layers_tbl = QTableWidget(0, 3)
        self._layers_tbl.setHorizontalHeaderLabels(["Collection ID", "Title", "Copy URL"])
        self._layers_tbl.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._layers_tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._layers_tbl.setMaximumHeight(200)
        layers_lay.addWidget(self._layers_tbl)
        lay.addWidget(self._layers_box)

        # ── Log ───────────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(130)
        lay.addWidget(QLabel("Output:"))
        lay.addWidget(self._log)
        lay.addStretch()

        # Initial status check
        self._check_status()

    # ── helpers ───────────────────────────────────────────────────────────

    def _wrap(self, hlay: QHBoxLayout) -> QWidget:
        w = QWidget()
        w.setLayout(hlay)
        return w

    def set_db_url(self, db_url: str) -> None:
        self._db_url = db_url

    def _check_status(self):
        if not _docker():
            self._set_status("no-docker")
            return
        w = ServiceWorker("status", self._container, self._image,
                          self._port, self._db_url)
        w.done.connect(lambda ok, s: self._set_status(s))
        self._worker = w
        launch(w)

    def _set_status(self, status: str):
        colors = {
            "running":   ("#4caf50", "Running"),
            "stopped":   ("#f44336", "Stopped"),
            "missing":   ("#9e9e9e", "Not created"),
            "no-docker": ("#ff9800", "Docker not found"),
            "error":     ("#f44336", "Error"),
        }
        color, label = colors.get(status, ("#9e9e9e", status))
        self._status_dot.setStyleSheet(f"color:{color};font-size:16px;")
        self._status_lbl.setText(label)
        self._status_lbl.setStyleSheet(f"color:{color};")
        running = (status == "running")
        self._btn_start.setEnabled(not running)
        self._btn_stop.setEnabled(running)
        if running:
            self._fetch_layers()

    def _start(self):
        if not self._db_url:
            QMessageBox.warning(self, "No connection",
                                "Connect to a PostGIS database first.")
            return
        self._log.clear()
        self._btn_start.setEnabled(False)
        w = ServiceWorker("start", self._container, self._image,
                          self._port, self._db_url)
        w.log.connect(self._log.append)
        w.done.connect(lambda ok, s: self._set_status(s))
        self._worker = w
        launch(w)

    def _stop(self):
        self._btn_stop.setEnabled(False)
        w = ServiceWorker("stop", self._container, self._image,
                          self._port, self._db_url)
        w.log.connect(self._log.append)
        w.done.connect(lambda ok, s: self._set_status(s))
        self._worker = w
        launch(w)

    def _pull(self):
        self._log.clear()
        w = ServiceWorker("pull", self._container, self._image,
                          self._port, self._db_url)
        w.log.connect(self._log.append)
        w.done.connect(lambda ok, s: self._log.append("✓ Image up to date" if ok else "✗ Pull failed"))
        self._worker = w
        launch(w)

    def _fetch_layers(self):
        w = LayerFetchWorker(self._port)
        w.done.connect(self._on_layers)
        w.error.connect(lambda e: self._log.append(f"Cannot fetch layers: {e}"))
        self._layer_worker = w
        launch(w)

    def _on_layers(self, collections: list):
        t = self._layers_tbl
        t.setRowCount(0)
        for col in collections:
            cid   = col.get("id", "")
            title = col.get("title", cid)
            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, QTableWidgetItem(cid))
            t.setItem(row, 1, QTableWidgetItem(title))
            url = f"http://localhost:{self._port}/collections/{cid}/items"
            btn = QPushButton("Copy URL")
            btn.setStyleSheet(_BTN)
            btn.clicked.connect(lambda _, u=url: QApplication.clipboard().setText(u))
            t.setCellWidget(row, 2, btn)


# ── Main panel ────────────────────────────────────────────────────────────

class PublishingPanel(QWidget):
    """Publish PostGIS layers as OGC API Features (WFS) or MVT tiles."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Header ────────────────────────────────────────────────────────
        header = QWidget()
        header.setMaximumHeight(60)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(12, 8, 12, 4)
        icon = QLabel("📡")
        icon.setStyleSheet("font-size:28px;")
        title = QLabel(
            "<b>PostGIS Publishing</b><br>"
            "<span style='color:gray;font-size:11px;'>"
            "Expose PostGIS layers as OGC API / WFS — connect ArcGIS Pro in one click</span>")
        h_lay.addWidget(icon)
        h_lay.addWidget(title, 1)

        # Connection status indicator
        self._conn_lbl = QLabel("⬤ Not connected")
        self._conn_lbl.setStyleSheet("color:#f44336;font-weight:bold;padding-right:8px;")
        h_lay.addWidget(self._conn_lbl)
        root.addWidget(header)

        # ── Tabs ──────────────────────────────────────────────────────────
        tabs = QTabWidget()

        sa1 = QScrollArea()
        sa1.setWidgetResizable(True)
        self._featureserv = _ServiceWidget(
            "pg_featureserv", FEATURESERV_IMAGE, FEATURESERV_CONTAINER,
            FEATURESERV_PORT,
            "https://access.crunchydata.com/documentation/pg_featureserv/")
        sa1.setWidget(self._featureserv)
        tabs.addTab(sa1, "🌐  OGC API Features (WFS)")

        sa2 = QScrollArea()
        sa2.setWidgetResizable(True)
        self._tileserv = _ServiceWidget(
            "pg_tileserv", TILESERV_IMAGE, TILESERV_CONTAINER,
            TILESERV_PORT,
            "https://access.crunchydata.com/documentation/pg_tileserv/")
        # Adjust tile URLs
        self._tileserv._url_base.setText(f"http://localhost:{TILESERV_PORT}")
        self._tileserv._url_wfs.setText(f"http://localhost:{TILESERV_PORT}/index.json")
        self._tileserv._url_arcgis.setText(
            f"http://localhost:{TILESERV_PORT}/public.{{table}}/{{z}}/{{x}}/{{y}}.pbf")
        sa2.setWidget(self._tileserv)
        tabs.addTab(sa2, "🗺  Vector Tiles (MVT)")

        root.addWidget(tabs)

    # ── Called from main window when connection changes ───────────────────

    def set_connection(self, params: dict) -> None:
        """Receive active DB connection params and build DATABASE_URL."""
        host     = params.get("host", "localhost")
        port     = params.get("port", 5432)
        dbname   = params.get("dbname", "")
        user     = params.get("user", "postgres")
        password = params.get("password", "")

        # pg_featureserv / pg_tileserv need the host.docker.internal trick on Windows/Mac
        docker_host = "host.docker.internal"
        db_url = f"postgresql://{user}:{password}@{docker_host}:{port}/{dbname}"

        self._featureserv.set_db_url(db_url)
        self._tileserv.set_db_url(db_url)

        self._conn_lbl.setText(f"⬤ {user}@{host}/{dbname}")
        self._conn_lbl.setStyleSheet(
            "color:#4caf50;font-weight:bold;padding-right:8px;")
