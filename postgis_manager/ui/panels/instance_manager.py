"""Instance Manager panel — manage standalone + Docker/Podman PostgreSQL instances."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QMessageBox, QAbstractItemView, QGroupBox,
)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QColor

from ...utils import i18n, config
from ...utils.docker_utils import (
    available_runtimes, list_postgres_containers,
    start_container, stop_container, restart_container,
    suggest_connection_from_container,
)


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
        self._start_btn.clicked.connect(lambda: self._container_action("start"))
        btn_row.addWidget(self._start_btn)
        self._stop_btn = QPushButton("■  Stop")
        self._stop_btn.clicked.connect(lambda: self._container_action("stop"))
        btn_row.addWidget(self._stop_btn)
        self._restart_btn = QPushButton("↺  Restart")
        self._restart_btn.clicked.connect(lambda: self._container_action("restart"))
        btn_row.addWidget(self._restart_btn)
        btn_row.addStretch()
        self._use_btn = QPushButton(f"➕  {i18n.t('inst_add_connection')}")
        self._use_btn.clicked.connect(self._use_as_connection)
        btn_row.addWidget(self._use_btn)
        ct_l.addLayout(btn_row)
        layout.addWidget(ct_box)

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
