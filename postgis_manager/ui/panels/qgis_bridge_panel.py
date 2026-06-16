"""QGIS Bridge Panel — read QGIS connections, credentials, and project layers."""

from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QLabel, QAbstractItemView, QGroupBox, QLineEdit, QFormLayout,
    QMessageBox, QToolBar, QComboBox, QFrame, QProgressBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QAction, QFont

from ...db.connection import DBManager
from ...utils import i18n, config
from ...utils.qgis_integration import (
    get_qgis_pg_connections,
    get_qgis_auth_configs,
    get_project_postgis_layers,
    save_credentials_to_qgis,
)


# ── Worker ────────────────────────────────────────────────────────────────
class BridgeLoadWorker(QThread):
    connections_loaded  = pyqtSignal(list)
    auth_loaded         = pyqtSignal(list)
    layers_loaded       = pyqtSignal(list)

    def __init__(self, iface=None):
        super().__init__()
        self.iface = iface

    def run(self):
        self.connections_loaded.emit(get_qgis_pg_connections())
        self.auth_loaded.emit(get_qgis_auth_configs())
        self.layers_loaded.emit(get_project_postgis_layers(self.iface))


# ── Main panel ────────────────────────────────────────────────────────────
class QGISBridgePanel(QWidget):
    # Signal emitted when user wants to connect to a DB found in QGIS
    connect_to = pyqtSignal(dict)   # profile dict

    def __init__(self, db: DBManager, iface=None, parent=None):
        super().__init__(parent)
        self.db    = db
        self.iface = iface
        self._worker: Optional[BridgeLoadWorker] = None
        self._layer_data: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Toolbar ───────────────────────────────────────────────────────
        tb = QToolBar()
        tb.setMovable(False)
        self._act_refresh = QAction("↻  " + i18n.t("action_refresh"), self)
        self._act_refresh.triggered.connect(self.refresh)
        tb.addAction(self._act_refresh)
        layout.addWidget(tb)

        # ── Tabs ──────────────────────────────────────────────────────────
        tabs = QTabWidget()
        tabs.addTab(self._build_connections_tab(), "🔌  " + i18n.t("qb_connections"))
        tabs.addTab(self._build_layers_tab(),      "🗺  " + i18n.t("qb_project_layers"))
        tabs.addTab(self._build_credentials_tab(), "🔐  " + i18n.t("qb_credentials"))
        layout.addWidget(tabs)

        # ── Status ────────────────────────────────────────────────────────
        self._status = QLabel(i18n.t("qb_not_loaded"))
        self._status.setStyleSheet("color: #888; padding: 2px 4px; font-size: 12px;")
        layout.addWidget(self._status)

    # ── Connections tab ───────────────────────────────────────────────────
    def _build_connections_tab(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(4, 4, 4, 4)

        info = QLabel(i18n.t("qb_connections_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; font-size: 12px; padding: 2px;")
        vl.addWidget(info)

        self._conn_table = QTableWidget()
        self._conn_table.setColumnCount(6)
        self._conn_table.setHorizontalHeaderLabels([
            i18n.t("conn_name"), i18n.t("conn_host"), i18n.t("conn_port"),
            i18n.t("conn_database"), i18n.t("conn_user"), "Source",
        ])
        self._conn_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._conn_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._conn_table.horizontalHeader().setStretchLastSection(True)
        self._conn_table.verticalHeader().setDefaultSectionSize(24)
        self._conn_table.setAlternatingRowColors(True)
        vl.addWidget(self._conn_table)

        btn_row = QHBoxLayout()
        self._btn_import_conn = QPushButton("⬇  " + i18n.t("qb_import_selected"))
        self._btn_import_conn.clicked.connect(self._import_selected_connection)
        self._btn_import_all  = QPushButton("⬇⬇  " + i18n.t("qb_import_all"))
        self._btn_import_all.clicked.connect(self._import_all_connections)
        self._btn_connect_now = QPushButton("⚡  " + i18n.t("qb_connect_now"))
        self._btn_connect_now.clicked.connect(self._connect_selected)
        btn_row.addWidget(self._btn_import_conn)
        btn_row.addWidget(self._btn_import_all)
        btn_row.addWidget(self._btn_connect_now)
        btn_row.addStretch()
        vl.addLayout(btn_row)
        return w

    # ── Project layers tab ────────────────────────────────────────────────
    def _build_layers_tab(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(4, 4, 4, 4)

        info = QLabel(i18n.t("qb_layers_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; font-size: 12px; padding: 2px;")
        vl.addWidget(info)

        self._layer_table = QTableWidget()
        self._layer_table.setColumnCount(7)
        self._layer_table.setHorizontalHeaderLabels([
            i18n.t("tab_browser"), "Schema", "Table",
            "Geom col", "SRID", "Host", "DB",
        ])
        self._layer_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._layer_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._layer_table.horizontalHeader().setStretchLastSection(True)
        self._layer_table.verticalHeader().setDefaultSectionSize(24)
        self._layer_table.setAlternatingRowColors(True)
        self._layer_table.doubleClicked.connect(self._open_layer_ops)
        vl.addWidget(self._layer_table)

        # Operations on selected layer
        ops_box = QGroupBox(i18n.t("qb_layer_ops"))
        ops_vl  = QVBoxLayout(ops_box)
        ops_row = QHBoxLayout()

        self._btn_attr_table  = QPushButton("📋  " + i18n.t("browser_open_attr_table"))
        self._btn_attr_table.clicked.connect(self._open_attr_table)
        self._btn_connect_db  = QPushButton("🔌  " + i18n.t("qb_connect_db"))
        self._btn_connect_db.clicked.connect(self._connect_layer_db)
        self._btn_open_sql    = QPushButton("⌨  SQL")
        self._btn_open_sql.clicked.connect(self._open_sql_for_layer)
        self._btn_validate    = QPushButton("✅  " + i18n.t("tab_validation"))
        self._btn_validate.clicked.connect(self._validate_layer)

        for btn in (self._btn_attr_table, self._btn_connect_db,
                    self._btn_open_sql, self._btn_validate):
            ops_row.addWidget(btn)
        ops_row.addStretch()
        ops_vl.addLayout(ops_row)
        vl.addWidget(ops_box)
        return w

    # ── Credentials tab ───────────────────────────────────────────────────
    def _build_credentials_tab(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(4, 4, 4, 4)

        info = QLabel(i18n.t("qb_credentials_info"))
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; font-size: 12px; padding: 2px;")
        vl.addWidget(info)

        # Existing QGIS auth configs
        vl.addWidget(QLabel(i18n.t("qb_existing_auth")))
        self._auth_table = QTableWidget()
        self._auth_table.setColumnCount(3)
        self._auth_table.setHorizontalHeaderLabels(["ID", i18n.t("conn_name"), i18n.t("conn_user")])
        self._auth_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._auth_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._auth_table.horizontalHeader().setStretchLastSection(True)
        self._auth_table.verticalHeader().setDefaultSectionSize(24)
        self._auth_table.setAlternatingRowColors(True)
        self._auth_table.setMaximumHeight(160)
        vl.addWidget(self._auth_table)

        # Add new credential
        new_box = QGroupBox(i18n.t("qb_save_credential"))
        form = QFormLayout(new_box)
        self._cred_name = QLineEdit()
        self._cred_name.setPlaceholderText(i18n.t("conn_name"))
        self._cred_user = QLineEdit()
        self._cred_user.setPlaceholderText(i18n.t("conn_user"))
        self._cred_pass = QLineEdit()
        self._cred_pass.setPlaceholderText(i18n.t("conn_password"))
        self._cred_pass.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(i18n.t("conn_name"),     self._cred_name)
        form.addRow(i18n.t("conn_user"),     self._cred_user)
        form.addRow(i18n.t("conn_password"), self._cred_pass)

        save_btn = QPushButton("💾  " + i18n.t("qb_save_to_qgis"))
        save_btn.clicked.connect(self._save_credential)
        form.addRow("", save_btn)
        vl.addWidget(new_box)

        # Export / Import
        exp_row = QHBoxLayout()
        self._btn_export_cfg = QPushButton("📤  " + i18n.t("cfg_export_title"))
        self._btn_export_cfg.clicked.connect(self._export_connections)
        self._btn_import_cfg = QPushButton("📥  " + i18n.t("cfg_import_title"))
        self._btn_import_cfg.clicked.connect(self._import_connections)
        exp_row.addWidget(self._btn_export_cfg)
        exp_row.addWidget(self._btn_import_cfg)
        exp_row.addStretch()
        vl.addLayout(exp_row)
        vl.addStretch()
        return w

    # ── Load data ─────────────────────────────────────────────────────────
    def showEvent(self, e):
        super().showEvent(e)
        if self._worker is None or not self._worker.isRunning():
            self.refresh()

    def refresh(self):
        self._status.setText(i18n.t("status_loading"))
        self._worker = BridgeLoadWorker(self.iface)
        self._worker.connections_loaded.connect(self._on_connections)
        self._worker.auth_loaded.connect(self._on_auth)
        self._worker.layers_loaded.connect(self._on_layers)
        self._worker.start()

    def _on_connections(self, conns: list):
        self._qgis_conns = conns
        t = self._conn_table
        t.setRowCount(len(conns))
        for r, c in enumerate(conns):
            t.setItem(r, 0, QTableWidgetItem(c.get("name", "")))
            t.setItem(r, 1, QTableWidgetItem(c.get("host", "")))
            t.setItem(r, 2, QTableWidgetItem(str(c.get("port", ""))))
            t.setItem(r, 3, QTableWidgetItem(c.get("dbname", "")))
            t.setItem(r, 4, QTableWidgetItem(c.get("user", "")))
            src = QTableWidgetItem(c.get("source", ""))
            src.setForeground(QColor("#888"))
            t.setItem(r, 5, src)
        self._status.setText(i18n.t("qb_found_conns", n=len(conns)))

    def _on_auth(self, configs: list):
        self._auth_cfgs = configs
        t = self._auth_table
        t.setRowCount(len(configs))
        for r, c in enumerate(configs):
            t.setItem(r, 0, QTableWidgetItem(c.get("id", "")))
            t.setItem(r, 1, QTableWidgetItem(c.get("name", "")))
            t.setItem(r, 2, QTableWidgetItem(c.get("username", "")))

    def _on_layers(self, layers: list):
        self._layer_data = layers
        t = self._layer_table
        t.setRowCount(len(layers))
        for r, l in enumerate(layers):
            t.setItem(r, 0, QTableWidgetItem(l.get("name", "")))
            t.setItem(r, 1, QTableWidgetItem(l.get("schema", "")))
            t.setItem(r, 2, QTableWidgetItem(l.get("table", "")))
            t.setItem(r, 3, QTableWidgetItem(l.get("geom_col", "")))
            t.setItem(r, 4, QTableWidgetItem(str(l.get("srid", ""))))
            t.setItem(r, 5, QTableWidgetItem(l.get("host", "")))
            t.setItem(r, 6, QTableWidgetItem(l.get("dbname", "")))

    # ── Connection import ─────────────────────────────────────────────────
    def _selected_conn(self) -> Optional[dict]:
        rows = self._conn_table.selectedItems()
        if not rows:
            return None
        return self._qgis_conns[self._conn_table.currentRow()]

    def _import_selected_connection(self):
        conn = self._selected_conn()
        if not conn:
            return
        self._do_import([conn])

    def _import_all_connections(self):
        conns = getattr(self, "_qgis_conns", [])
        if not conns:
            return
        self._do_import(conns)

    def _do_import(self, conns: list):
        imported = 0
        for c in conns:
            profile = {
                "name":     c["name"],
                "host":     c["host"],
                "port":     str(c.get("port", "5432")),
                "dbname":   c["dbname"],
                "user":     c["user"],
                "password": c.get("password", ""),
                "ssl_mode": c.get("ssl_mode", "prefer"),
                "timeout":  10,
            }
            config.save_connection(profile)
            imported += 1
        QMessageBox.information(
            self, "PostGIS Manager",
            i18n.t("qb_imported_n", n=imported))

    def _connect_selected(self):
        conn = self._selected_conn()
        if not conn:
            return
        profile = {
            "name":     conn["name"],
            "host":     conn["host"],
            "port":     str(conn.get("port", "5432")),
            "dbname":   conn["dbname"],
            "user":     conn["user"],
            "password": conn.get("password", ""),
            "ssl_mode": conn.get("ssl_mode", "prefer"),
            "timeout":  10,
        }
        self.connect_to.emit(profile)

    # ── Layer operations ──────────────────────────────────────────────────
    def _selected_layer(self) -> Optional[dict]:
        rows = self._layer_table.selectedItems()
        if not rows:
            QMessageBox.information(self, "PostGIS Manager",
                                    i18n.t("err_no_layer_selected"))
            return None
        return self._layer_data[self._layer_table.currentRow()]

    def _open_layer_ops(self):
        self._open_attr_table()

    def _connect_layer_db(self):
        """Connect the app's DBManager to the selected layer's database."""
        layer = self._selected_layer()
        if not layer:
            return
        profile = {
            "name":     layer["dbname"],
            "host":     layer["host"],
            "port":     layer["port"],
            "dbname":   layer["dbname"],
            "user":     layer["user"],
            "password": layer.get("password", ""),
            "ssl_mode": "prefer",
            "timeout":  10,
        }
        self.connect_to.emit(profile)

    def _open_attr_table(self):
        """Open the attribute table panel for the selected layer."""
        layer = self._selected_layer()
        if not layer:
            return
        if not self.db.is_connected():
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("err_not_connected"))
            return
        # Find the attribute table panel via parent MainWindow
        mw = self.window()
        if hasattr(mw, "attr_table_panel"):
            mw.attr_table_panel.set_active_layer(
                layer["schema"], layer["table"])
        self._status.setText(
            i18n.t("qb_opened_layer", name=layer["name"]))

    def _open_sql_for_layer(self):
        layer = self._selected_layer()
        if not layer:
            return
        mw = self.window()
        if hasattr(mw, "sql_editor"):
            mw.sql_editor.set_active_layer(layer["schema"], layer["table"])
        self._status.setText(i18n.t("qb_opened_sql", name=layer["name"]))

    def _validate_layer(self):
        layer = self._selected_layer()
        if not layer:
            return
        if not self.db.is_connected():
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("err_not_connected"))
            return
        mw = self.window()
        if hasattr(mw, "validation_panel"):
            mw.validation_panel.set_active_layer(
                layer["schema"], layer["table"],
                layer.get("geom_col", "geom"),
                int(layer.get("srid", 4326) or 4326))
        self._status.setText(
            i18n.t("qb_opened_validation", name=layer["name"]))

    # ── Credentials ───────────────────────────────────────────────────────
    def _save_credential(self):
        name = self._cred_name.text().strip()
        user = self._cred_user.text().strip()
        pwd  = self._cred_pass.text()
        if not name or not user:
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("qb_fill_cred_fields"))
            return
        ok = save_credentials_to_qgis(name, user, pwd)
        if ok:
            QMessageBox.information(self, "PostGIS Manager",
                                    i18n.t("qb_cred_saved"))
            self._cred_name.clear()
            self._cred_user.clear()
            self._cred_pass.clear()
            self.refresh()
        else:
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("qb_cred_save_failed"))

    # ── Export / Import connections JSON ──────────────────────────────────
    def _export_connections(self):
        from PyQt6.QtWidgets import QFileDialog
        import json
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.t("cfg_export_title"),
            "qgis_postgis_connections.json", "JSON (*.json)")
        if not path:
            return
        data = getattr(self, "_qgis_conns", [])
        export = [{k: v for k, v in c.items() if k != "password"}
                  for c in data]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)
        QMessageBox.information(self, "PostGIS Manager",
                                i18n.t("cfg_export_done", path=path))

    def _import_connections(self):
        from PyQt6.QtWidgets import QFileDialog
        import json
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("cfg_import_title"), "", "JSON (*.json)")
        if not path:
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for c in data:
            if "name" in c and "host" in c:
                config.save_connection(c)
                count += 1
        QMessageBox.information(self, "PostGIS Manager",
                                i18n.t("cfg_import_done", n=count))
