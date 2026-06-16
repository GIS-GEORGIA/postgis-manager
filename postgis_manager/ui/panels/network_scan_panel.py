"""Network Scanner panel — find PostgreSQL/PostGIS instances on the network."""

from __future__ import annotations
import ipaddress
import socket
import threading
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QSpinBox, QDoubleSpinBox,
    QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QProgressBar, QMessageBox, QComboBox,
    QSplitter, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QMutex
from PyQt6.QtGui import QColor, QFont

from ...utils import i18n, config
from ...utils.network_scanner import scan_network, mdns_discover, parse_targets


def _local_subnet_guess() -> str:
    """Guess the local /24 subnet from the primary interface."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        return str(net)
    except Exception:
        return "192.168.1.0/24"


# ── Worker threads ────────────────────────────────────────────────────────────

class mDNSWorker(QThread):
    found    = pyqtSignal(dict)
    finished = pyqtSignal(int)

    def run(self):
        results = mdns_discover(timeout=3.0)
        for r in results:
            self.found.emit(r)
        self.finished.emit(len(results))


class ScanWorker(QThread):
    found    = pyqtSignal(dict)
    progress = pyqtSignal(int, int)   # done, total
    finished = pyqtSignal(int)        # total found

    def __init__(self, cidr: str, extra_ports: list[int],
                 tcp_timeout: float, workers: int,
                 deep: bool, user: str, password: str, dbname: str):
        super().__init__()
        self._cidr        = cidr
        self._extra_ports = extra_ports
        self._tcp_timeout = tcp_timeout
        self._workers     = workers
        self._deep        = deep
        self._user        = user
        self._password    = password
        self._dbname      = dbname
        self._stop        = False

    def stop(self):
        self._stop = True

    def run(self):
        results = scan_network(
            self._cidr,
            extra_ports   = self._extra_ports,
            tcp_timeout   = self._tcp_timeout,
            deep_check    = self._deep,
            deep_user     = self._user,
            deep_password = self._password,
            deep_dbname   = self._dbname,
            workers       = self._workers,
            on_found      = lambda r: self.found.emit(r),
            on_progress   = lambda d, t: self.progress.emit(d, t),
            stop_flag     = lambda: self._stop,
        )
        self.finished.emit(len(results))


# ── Panel ─────────────────────────────────────────────────────────────────────

class NetworkScanPanel(QWidget):
    """Tab: scan the network for live PostgreSQL/PostGIS instances."""

    add_connection = pyqtSignal(dict)   # emitted when user adds a result

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: ScanWorker | None = None
        self._mdns_worker: mDNSWorker | None = None
        self._found: list[dict] = []
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(splitter)

        # ── TOP: scrollable config area ───────────────────────────────────────
        top_scroll = QScrollArea()
        top_scroll.setWidgetResizable(True)
        top_scroll.setFrameShape(top_scroll.Shape.NoFrame)
        top_widget = QWidget()
        layout = QVBoxLayout(top_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"🔎  {i18n.t('scan_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        # ── Scan parameters ───────────────────────────────────────────────────
        params_box = QGroupBox(i18n.t("scan_params"))
        pform = QFormLayout(params_box)

        range_row = QHBoxLayout()
        self._range_edit = QLineEdit(_local_subnet_guess())
        self._range_edit.setPlaceholderText("192.168.1.0/24  or  10.0.0.1-10.0.0.50")
        range_row.addWidget(self._range_edit)
        auto_btn = QPushButton("⟳")
        auto_btn.setFixedWidth(32)
        auto_btn.setToolTip(i18n.t("scan_auto_detect"))
        auto_btn.clicked.connect(
            lambda: self._range_edit.setText(_local_subnet_guess()))
        range_row.addWidget(auto_btn)
        pform.addRow(i18n.t("scan_range"), range_row)

        self._ports_edit = QLineEdit("5432")
        self._ports_edit.setPlaceholderText("5432, 5433, 5434")
        pform.addRow(i18n.t("scan_ports"), self._ports_edit)

        self._timeout_spin = QDoubleSpinBox()
        self._timeout_spin.setRange(0.1, 5.0)
        self._timeout_spin.setValue(0.4)
        self._timeout_spin.setSingleStep(0.1)
        self._timeout_spin.setSuffix(" s")
        pform.addRow(i18n.t("scan_timeout"), self._timeout_spin)

        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(8, 512)
        self._workers_spin.setValue(128)
        self._workers_spin.setSingleStep(16)
        pform.addRow(i18n.t("scan_workers"), self._workers_spin)

        layout.addWidget(params_box)

        # ── Deep check (optional credentials) ────────────────────────────────
        deep_box = QGroupBox(i18n.t("scan_deep_check"))
        deep_box.setCheckable(True)
        deep_box.setChecked(False)
        self._deep_box = deep_box
        dform = QFormLayout(deep_box)
        self._deep_user  = QLineEdit("postgres")
        dform.addRow(i18n.t("conn_user"), self._deep_user)
        self._deep_pass  = QLineEdit()
        self._deep_pass.setEchoMode(QLineEdit.EchoMode.Password)
        dform.addRow(i18n.t("conn_password"), self._deep_pass)
        self._deep_db    = QLineEdit("postgres")
        dform.addRow(i18n.t("conn_database"), self._deep_db)
        layout.addWidget(deep_box)

        # ── Action row ────────────────────────────────────────────────────────
        action_row = QHBoxLayout()
        self._scan_btn = QPushButton(f"▶  {i18n.t('scan_start')}")
        self._scan_btn.setStyleSheet("font-weight: bold;")
        self._scan_btn.clicked.connect(self._start_scan)
        action_row.addWidget(self._scan_btn)

        self._stop_btn = QPushButton(f"■  {i18n.t('scan_stop')}")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_scan)
        action_row.addWidget(self._stop_btn)

        self._mdns_btn = QPushButton(f"📡  {i18n.t('scan_mdns')}")
        self._mdns_btn.setToolTip(i18n.t("scan_mdns_tip"))
        self._mdns_btn.clicked.connect(self._start_mdns)
        action_row.addWidget(self._mdns_btn)

        action_row.addStretch()
        self._clear_btn = QPushButton(i18n.t("scan_clear"))
        self._clear_btn.clicked.connect(self._clear)
        action_row.addWidget(self._clear_btn)
        layout.addLayout(action_row)

        # ── Progress ──────────────────────────────────────────────────────────
        prog_row = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setValue(0)
        prog_row.addWidget(self._progress)
        self._status_lbl = QLabel(i18n.t("scan_status_idle"))
        self._status_lbl.setMinimumWidth(180)
        prog_row.addWidget(self._status_lbl)
        layout.addLayout(prog_row)

        layout.addStretch()
        top_scroll.setWidget(top_widget)
        splitter.addWidget(top_scroll)

        # ── BOTTOM: results table (gets all remaining space) ──────────────────
        bottom = QWidget()
        res_layout = QVBoxLayout(bottom)
        res_layout.setContentsMargins(8, 4, 8, 8)
        res_layout.setSpacing(4)

        res_label = QLabel(f"<b>{i18n.t('scan_results')}</b>")
        res_layout.addWidget(res_label)

        self._table = QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels([
            i18n.t("scan_col_host"),
            i18n.t("scan_col_port"),
            i18n.t("scan_col_latency"),
            i18n.t("scan_col_pg_version"),
            i18n.t("scan_col_postgis"),
            i18n.t("scan_col_source"),
            i18n.t("scan_col_info"),
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        res_layout.addWidget(self._table, 1)

        add_row = QHBoxLayout()
        self._add_btn = QPushButton(f"➕  {i18n.t('scan_add_connection')}")
        self._add_btn.clicked.connect(self._add_selected)
        add_row.addWidget(self._add_btn)
        self._add_all_btn = QPushButton(f"➕  {i18n.t('scan_add_all')}")
        self._add_all_btn.clicked.connect(self._add_all)
        add_row.addWidget(self._add_all_btn)
        add_row.addStretch()
        res_layout.addLayout(add_row)

        splitter.addWidget(bottom)
        splitter.setSizes([280, 400])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    # ── Scan control ──────────────────────────────────────────────────────────

    def _parse_ports(self) -> list[int]:
        ports = []
        for p in self._ports_edit.text().split(","):
            p = p.strip()
            if p.isdigit():
                ports.append(int(p))
        return ports or [5432]

    def _start_scan(self):
        cidr = self._range_edit.text().strip()
        if not cidr:
            QMessageBox.warning(self, "Error", i18n.t("scan_err_no_range"))
            return

        # Estimate target count for progress bar
        try:
            targets = parse_targets(cidr, self._parse_ports()[1:])
            self._progress.setRange(0, len(targets))
            self._progress.setValue(0)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            return

        self._scan_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._status_lbl.setText(i18n.t("scan_status_running", n=len(targets)))

        ports = self._parse_ports()
        extra = ports[1:] if len(ports) > 1 else []

        self._worker = ScanWorker(
            cidr         = cidr,
            extra_ports  = extra,
            tcp_timeout  = self._timeout_spin.value(),
            workers      = self._workers_spin.value(),
            deep         = self._deep_box.isChecked(),
            user         = self._deep_user.text().strip(),
            password     = self._deep_pass.text(),
            dbname       = self._deep_db.text().strip(),
        )
        self._worker.found.connect(self._on_found)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _stop_scan(self):
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)
        self._status_lbl.setText(i18n.t("scan_status_stopped"))

    def _start_mdns(self):
        self._mdns_btn.setEnabled(False)
        self._status_lbl.setText("📡 mDNS discovery (3s)...")
        self._mdns_worker = mDNSWorker()
        self._mdns_worker.found.connect(self._on_found)
        self._mdns_worker.finished.connect(
            lambda n: (
                self._mdns_btn.setEnabled(True),
                self._status_lbl.setText(
                    f"mDNS: {n} service{'s' if n != 1 else ''} found")
            )
        )
        self._mdns_worker.start()

    def _clear(self):
        self._found.clear()
        self._table.setRowCount(0)
        self._progress.setValue(0)
        self._status_lbl.setText(i18n.t("scan_status_idle"))

    # ── Signals ───────────────────────────────────────────────────────────────

    def _on_found(self, r: dict):
        self._found.append(r)
        row = self._table.rowCount()
        self._table.insertRow(row)

        postgis_ver = r.get("postgis_version") or ""
        pg_ver      = r.get("pg_version") or r.get("banner") or ""
        source      = r.get("source", "scan")
        source_icon = {"scan": "🔎", "mDNS": "📡"}.get(source, "")
        error       = r.get("error") or ""
        latency     = f"{r['latency_ms']} ms" if r.get("latency_ms") is not None else "—"

        values = [
            r.get("host", ""),
            str(r.get("port", 5432)),
            latency,
            pg_ver,
            f"✔  {postgis_ver}" if postgis_ver else ("—" if not error else ""),
            f"{source_icon} {source}",
            error[:80] if error else "",
        ]

        for col, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, r)
            if postgis_ver:
                item.setForeground(QColor("#1B5E20"))
            elif error:
                item.setForeground(QColor("#C62828"))
            self._table.setItem(row, col, item)

    def _on_progress(self, done: int, total: int):
        self._progress.setValue(done)

    def _on_finished(self, n_found: int):
        self._scan_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._progress.setValue(self._progress.maximum())
        self._status_lbl.setText(
            i18n.t("scan_status_done",
                   n=n_found,
                   total=self._progress.maximum()))

    # ── Add connections ───────────────────────────────────────────────────────

    def _result_to_profile(self, r: dict) -> dict:
        host = r.get("host", "")
        port = str(r.get("port", 5432))
        postgis = r.get("postgis_version")
        label = f"{host}:{port}"
        if postgis:
            label += " [PostGIS]"
        return {
            "name":          label,
            "instance_type": "standalone",
            "container_name": "",
            "host":          host,
            "port":          port,
            "dbname":        self._deep_db.text().strip() if self._deep_box.isChecked() else "postgres",
            "user":          self._deep_user.text().strip() if self._deep_box.isChecked() else "postgres",
            "password":      "",
            "ssl_mode":      "prefer",
            "timeout":       10,
        }

    def _add_selected(self):
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if not rows:
            QMessageBox.warning(self, "Error", i18n.t("scan_err_no_selection"))
            return
        for row in rows:
            item = self._table.item(row, 0)
            if item:
                r = item.data(Qt.ItemDataRole.UserRole)
                if r:
                    profile = self._result_to_profile(r)
                    config.save_connection(profile)
                    self.add_connection.emit(profile)
        QMessageBox.information(self, "OK",
                                i18n.t("scan_added", n=len(rows)))

    def _add_all(self):
        if not self._found:
            return
        for r in self._found:
            profile = self._result_to_profile(r)
            config.save_connection(profile)
            self.add_connection.emit(profile)
        QMessageBox.information(self, "OK",
                                i18n.t("scan_added", n=len(self._found)))
