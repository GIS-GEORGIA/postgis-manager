"""Main application window — works as standalone QMainWindow and as QGIS panel."""

from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QStatusBar, QToolBar, QMenuBar, QMenu, QApplication,
    QLabel, QComboBox, QSizePolicy, QMessageBox, QDialog,
    QStackedWidget, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QIcon, QAction

from ..db.connection import DBManager
from ..utils import i18n, theme, config
from .panels.browser import LayerBrowserPanel
from .panels.sql_editor import SQLEditorPanel
from .panels.raster_import import RasterImportPanel
from .panels.routing import pgRoutingPanel
from .panels.topology import TopologyPanel
from .panels.chainage import ChainagePanel
from .panels.export_panel import ExportPanel
from .panels.style_manager import StyleManagerPanel
from .panels.geoprocessing import GeoprocessingPanel
from .panels.log_panel import LogPanel
from .panels.instance_manager import InstanceManagerPanel
from .panels.network_scan_panel import NetworkScanPanel
from .panels.versioning_panel import VersioningPanel
from .panels.validation_panel import ValidationPanel
from .panels.query_builder import QueryBuilderPanel
from .panels.citydb_panel import CityDBPanel
from .panels.db_dashboard import DBDashboardPanel
from .panels.table_designer import TableDesignerPanel
from .panels.function_browser import FunctionBrowserPanel
from .panels.backup_panel import BackupRestorePanel
from .panels.schema_role_manager import SchemaRolePanel
from .panels.matview_panel import MatViewPanel
from .panels.raster_tools import RasterToolsPanel
from .panels.map_viewer import MapViewerPanel
from .panels.qgis_bridge_panel import QGISBridgePanel
from .panels.db_setup_panel import DBSetupPanel
from .panels.security_panel import SecurityPanel
from .panels.audit_panel import AuditPanel
from .panels.import_export_panel import ImportExportPanel
from .panels.spatial_analysis_panel import SpatialAnalysisPanel
from .sidebar_nav import NavSidebar
from .dialogs.connection_dialog import ConnectionDialog
from .dialogs.settings_dialog import SettingsDialog
from .dialogs.about_dialog import AboutDialog

APP_VERSION = "1.0.0"


class ConnectWorker(QThread):
    success = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, profile: dict):
        super().__init__()
        self.db = db
        self.profile = profile

    def run(self):
        try:
            self.db.connect(
                host=self.profile["host"],
                port=self.profile["port"],
                dbname=self.profile["dbname"],
                user=self.profile["user"],
                password=self.profile.get("password", ""),
                ssl_mode=self.profile.get("ssl_mode", "prefer"),
                timeout=self.profile.get("timeout", 10),
            )
            info = self.db.server_info()
            self.success.emit(info)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self, parent=None, embedded: bool = False, iface=None):
        super().__init__(parent)
        self.embedded = embedded
        self.iface = iface
        self.db = DBManager()
        self._connect_worker: Optional[ConnectWorker] = None

        self._apply_config()
        self._setup_ui()
        self._apply_theme()
        self._retranslate()
        i18n.on_language_change(self._on_lang_change)
        theme.on_theme_change(self._on_theme_change)

    def _apply_config(self):
        i18n.load(config.get("language", "en"))
        theme.set_theme(config.get("theme", "light"))
        size = config.get("font_size", 13)
        family = config.get("font_family", "Segoe UI")
        if not self.embedded:
            # In standalone mode only — don't touch QGIS's QApplication font
            app = QApplication.instance()
            if app:
                app.setFont(QFont(family, size))
        else:
            self.setFont(QFont(family, size))

    def _setup_ui(self):
        self.setWindowTitle("PostGIS Manager")
        self.setMinimumSize(1100, 700)
        geo = config.get("window_geometry")
        if geo:
            try:
                self.restoreGeometry(bytes.fromhex(geo))
            except Exception:
                self.resize(1400, 850)
        else:
            self.resize(1400, 850)
        self._build_menubar()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

    def _build_menubar(self):
        mb = self.menuBar()
        self._menu_file = mb.addMenu("")
        self._action_quit = QAction(self)
        self._action_quit.triggered.connect(self.close)
        self._menu_file.addAction(self._action_quit)

        self._menu_conn = mb.addMenu("")
        self._action_new_conn = QAction(self)
        self._action_new_conn.triggered.connect(self._new_connection)
        self._action_connect = QAction(self)
        self._action_connect.triggered.connect(self._connect_selected)
        self._action_disconnect = QAction(self)
        self._action_disconnect.triggered.connect(self._disconnect)
        self._action_refresh = QAction(self)
        self._action_refresh.triggered.connect(self._refresh)
        for a in [self._action_new_conn, self._action_connect,
                  self._action_disconnect, self._action_refresh]:
            self._menu_conn.addAction(a)

        self._menu_tools = mb.addMenu("")
        self._action_export_cfg = QAction(self)
        self._action_export_cfg.triggered.connect(self._export_config)
        self._action_import_cfg = QAction(self)
        self._action_import_cfg.triggered.connect(self._import_config)
        self._menu_tools.addAction(self._action_export_cfg)
        self._menu_tools.addAction(self._action_import_cfg)

        self._menu_view = mb.addMenu("")
        self._action_settings = QAction(self)
        self._action_settings.triggered.connect(self._open_settings)
        self._menu_view.addAction(self._action_settings)

        self._menu_help = mb.addMenu("")
        self._action_about = QAction(self)
        self._action_about.triggered.connect(self._open_about)
        self._action_credits = QAction(self)
        self._action_credits.triggered.connect(self._open_credits)
        self._menu_help.addAction(self._action_about)
        self._menu_help.addAction(self._action_credits)

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)

        self._conn_combo = QComboBox()
        self._conn_combo.setMinimumWidth(200)
        self._refresh_conn_combo()
        tb.addWidget(self._conn_combo)

        self._tb_connect = QAction("⚡ Connect", self)
        self._tb_connect.triggered.connect(self._connect_selected)
        tb.addAction(self._tb_connect)

        self._tb_disconnect = QAction("✂ Disconnect", self)
        self._tb_disconnect.triggered.connect(self._disconnect)
        tb.addAction(self._tb_disconnect)

        tb.addSeparator()

        self._tb_new_conn = QAction("＋ New Connection", self)
        self._tb_new_conn.triggered.connect(self._new_connection)
        tb.addAction(self._tb_new_conn)

        self._tb_refresh = QAction("↻ Refresh", self)
        self._tb_refresh.triggered.connect(self._refresh)
        tb.addAction(self._tb_refresh)

        tb.addSeparator()

        tb.addWidget(QLabel(" Lang: "))
        self._lang_combo = QComboBox()
        for code, name in i18n.available_languages().items():
            self._lang_combo.addItem(name, code)
        idx = self._lang_combo.findData(i18n.current_lang())
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
        self._lang_combo.currentIndexChanged.connect(self._switch_language)
        tb.addWidget(self._lang_combo)

        tb.addWidget(QLabel("  Theme: "))
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("☀ Light", "light")
        self._theme_combo.addItem("🌙 Dark", "dark")
        idx = self._theme_combo.findData(theme.current())
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        self._theme_combo.currentIndexChanged.connect(self._switch_theme)
        tb.addWidget(self._theme_combo)

        tb.addWidget(QLabel("  Font: "))
        self._font_spin = QComboBox()
        for sz in [10, 11, 12, 13, 14, 15, 16, 17, 18]:
            self._font_spin.addItem(str(sz), sz)
        idx = self._font_spin.findData(config.get("font_size", 13))
        if idx >= 0:
            self._font_spin.setCurrentIndex(idx)
        self._font_spin.currentIndexChanged.connect(self._switch_font_size)
        tb.addWidget(self._font_spin)

        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        self._conn_indicator = QLabel("⬤ Disconnected")
        self._conn_indicator.setStyleSheet(
            "color: #C62828; font-weight: bold; padding-right: 8px;")
        tb.addWidget(self._conn_indicator)

    def _build_central(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(self._main_splitter)

        # ── Left: layer browser ───────────────────────────────────────────────
        self.browser = LayerBrowserPanel(self.db, self)
        self.browser.layer_selected.connect(self._on_layer_selected)
        self.browser.load_in_qgis.connect(self._load_layer_in_qgis)
        self._main_splitter.addWidget(self.browser)

        # ── Right: sidebar + stacked content + log ────────────────────────────
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        v_splitter = QSplitter(Qt.Orientation.Vertical)
        right_layout.addWidget(v_splitter)

        # Content area = sidebar nav + stacked panels side by side
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self._nav = NavSidebar()
        self._nav.page_requested.connect(self._show_page)
        # thin separator line between sidebar and content
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        content_layout.addWidget(self._nav)
        content_layout.addWidget(sep)

        self._stack = QStackedWidget()
        content_layout.addWidget(self._stack, 1)

        v_splitter.addWidget(content_widget)

        # ── Build panels and register in sidebar ──────────────────────────────
        self._nav_labels: list[tuple] = []  # (btn, i18n_key)

        def _add(panel, icon, key):
            idx = self._stack.addWidget(panel)
            btn = self._nav.add_item(icon, i18n.t(key), idx)
            self._nav_labels.append((btn, key))

        # ── Data ──────────────────────────────────────────────────────────────
        self._nav.add_group(i18n.t("nav_grp_data"))

        self.map_viewer = MapViewerPanel(self.db, self)
        _add(self.map_viewer, "🗺", "tab_map_viewer")

        self.sql_editor = SQLEditorPanel(self.db, self)
        _add(self.sql_editor, "⌨", "tab_sql")

        self.query_builder = QueryBuilderPanel(self.db, self)
        _add(self.query_builder, "🔍", "tab_query_builder")

        # ── Import / Export ───────────────────────────────────────────────────
        self._nav.add_group(i18n.t("nav_grp_import"))

        self.raster_panel = RasterImportPanel(self.db, self)
        _add(self.raster_panel, "📥", "tab_raster")

        self.geodata_panel = ImportExportPanel(db=self.db, parent=self)
        _add(self.geodata_panel, "🌍", "tab_geodata_io")

        self.export_panel = ExportPanel(self.db, self)
        _add(self.export_panel, "📤", "tab_export")

        self.backup_panel = BackupRestorePanel(self.db, self)
        _add(self.backup_panel, "💾", "tab_backup")

        # ── Spatial Analysis ──────────────────────────────────────────────────
        self._nav.add_group(i18n.t("nav_grp_analysis"))

        self.spatial_analysis_panel = SpatialAnalysisPanel(parent=self)
        _add(self.spatial_analysis_panel, "🔲", "tab_spatial_analysis")

        self.routing_panel = pgRoutingPanel(self.db, self)
        _add(self.routing_panel, "🛣", "tab_routing")

        self.topology_panel = TopologyPanel(self.db, self)
        _add(self.topology_panel, "🔗", "tab_topology")

        self.chainage_panel = ChainagePanel(self.db, self)
        _add(self.chainage_panel, "📏", "tab_chainage")

        self.geo_panel = GeoprocessingPanel(self.db, self)
        _add(self.geo_panel, "⚙", "tab_geoprocessing")

        self.validation_panel = ValidationPanel(self.db, self)
        _add(self.validation_panel, "✅", "tab_validation")

        # ── Database Design ───────────────────────────────────────────────────
        self._nav.add_group(i18n.t("nav_grp_design"))

        self.table_designer = TableDesignerPanel(self.db, self)
        self.table_designer.layer_changed.connect(self._refresh)
        _add(self.table_designer, "🛠", "tab_table_designer")

        self.schema_role_panel = SchemaRolePanel(self.db, self)
        _add(self.schema_role_panel, "🔐", "tab_schema_roles")

        self.func_browser = FunctionBrowserPanel(self.db, self)
        _add(self.func_browser, "ƒ", "tab_function_browser")

        self.matview_panel = MatViewPanel(self.db, self)
        _add(self.matview_panel, "📋", "tab_matviews")

        # ── Raster / Advanced ─────────────────────────────────────────────────
        self._nav.add_group(i18n.t("nav_grp_advanced"))

        self.raster_tools = RasterToolsPanel(self.db, self)
        _add(self.raster_tools, "🗺", "tab_raster_tools")

        self.style_panel = StyleManagerPanel(self.db, self)
        _add(self.style_panel, "🎨", "tab_styles")

        self.versioning_panel = VersioningPanel(self.db, self)
        _add(self.versioning_panel, "🕑", "tab_versioning")

        self.citydb_panel = CityDBPanel(self.db, self)
        _add(self.citydb_panel, "🏙", "tab_3dcitydb")

        # ── Connectivity ──────────────────────────────────────────────────────
        self._nav.add_group(i18n.t("nav_grp_connect"))

        self.qgis_bridge = QGISBridgePanel(self.db, iface=self.iface, parent=self)
        self.qgis_bridge.connect_to.connect(self._connect_from_bridge)
        _add(self.qgis_bridge, "🔗", "tab_qgis_bridge")

        self.db_setup = DBSetupPanel(parent=self)
        self.db_setup.connect_to.connect(self._connect_from_bridge)
        _add(self.db_setup, "⚙", "tab_db_setup")

        self.security_panel = SecurityPanel(db=self.db, parent=self)
        _add(self.security_panel, "🔐", "tab_security")

        self.audit_panel = AuditPanel(db=self.db, parent=self)
        _add(self.audit_panel, "📋", "tab_audit")

        self.instance_panel = InstanceManagerPanel(self)
        self.instance_panel.add_connection.connect(self._add_container_connection)
        _add(self.instance_panel, "🖥", "tab_instances")

        self.scan_panel = NetworkScanPanel(self)
        self.scan_panel.add_connection.connect(self._add_container_connection)
        _add(self.scan_panel, "🌐", "tab_network_scan")

        # ── Monitor ───────────────────────────────────────────────────────────
        self._nav.add_group(i18n.t("nav_grp_monitor"))

        self.dashboard_panel = DBDashboardPanel(self.db, self)
        _add(self.dashboard_panel, "📊", "tab_dashboard")

        # ── Log at bottom ─────────────────────────────────────────────────────
        self.log_panel = LogPanel(self)
        v_splitter.addWidget(self.log_panel)

        v_splitter.setSizes([620, 180])
        v_splitter.setCollapsible(0, False)
        v_splitter.setCollapsible(1, False)
        v_splitter.setHandleWidth(6)
        self.log_panel.setMinimumHeight(60)
        self._v_splitter = v_splitter
        self._main_splitter.addWidget(right_widget)
        self._main_splitter.setSizes([260, 1140])

        # Activate first page
        self._nav.select_page(0)

    def _show_page(self, idx: int):
        self._stack.setCurrentIndex(idx)

    def _build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._status_label = QLabel(i18n.t("status_ready"))
        sb.addWidget(self._status_label)
        self._db_info_label = QLabel("")
        sb.addPermanentWidget(self._db_info_label)

    def _refresh_conn_combo(self):
        self._conn_combo.clear()
        for conn in config.get_connections():
            self._conn_combo.addItem(conn.get("name", "?"), conn)
        if self._conn_combo.count() == 0:
            self._conn_combo.addItem("(no connections)", None)

    def _new_connection(self):
        dlg = ConnectionDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            profile = dlg.get_profile()
            config.save_connection(profile)
            self._refresh_conn_combo()
            idx = self._conn_combo.findText(profile["name"])
            if idx >= 0:
                self._conn_combo.setCurrentIndex(idx)

    def _connect_selected(self):
        profile = self._conn_combo.currentData()
        if not profile:
            self._new_connection()
            return
        self._set_conn_status("connecting")
        self.log(f"Connecting to {profile.get('dbname')} @ {profile.get('host')}...", "info")
        self._connect_worker = ConnectWorker(self.db, profile)
        self._connect_worker.success.connect(self._on_connect_success)
        self._connect_worker.error.connect(self._on_connect_error)
        self._connect_worker.start()

    def _disconnect(self):
        self.db.disconnect()
        self._set_conn_status("disconnected")
        self.browser.clear()
        self.log(i18n.t("log_disconnected"), "warn")

    def _on_connect_success(self, info: dict):
        self._set_conn_status("connected")
        pg_ver = info.get("pg_version", "?")
        postgis_ver = info.get("postgis_version", "N/A")
        self._db_info_label.setText(f"PostgreSQL  |  PostGIS {postgis_ver}")
        self.log(i18n.t("log_connected",
                         dbname=self.db.params.get("dbname", ""),
                         host=self.db.params.get("host", ""),
                         port=self.db.params.get("port", "")), "success")
        if info.get("pgrouting_version"):
            self.log(f"pgRouting {info['pgrouting_version']} available", "info")
        if info.get("topology"):
            self.log("PostGIS Topology extension available", "info")
        self.browser.refresh()
        self.security_panel.set_connection(dict(self.db.params))
        self.audit_panel.set_connection(dict(self.db.params))
        self.geodata_panel.set_connection(dict(self.db.params))
        self.spatial_analysis_panel.set_connection(dict(self.db.params))
        self.sql_editor.refresh_completions()

    def _on_connect_error(self, error: str):
        self._set_conn_status("disconnected")
        self.log(f"{i18n.t('conn_error')}: {error}", "error")
        QMessageBox.critical(self, i18n.t("conn_error"), error)

    def _set_conn_status(self, state: str):
        if state == "connected":
            self._conn_indicator.setText("⬤ Connected")
            self._conn_indicator.setStyleSheet(
                "color: #2E7D32; font-weight: bold; padding-right: 8px;")
            self._status_label.setText(i18n.t("status_ready"))
        elif state == "connecting":
            self._conn_indicator.setText("⬤ Connecting...")
            self._conn_indicator.setStyleSheet(
                "color: #E65100; font-weight: bold; padding-right: 8px;")
            self._status_label.setText(i18n.t("status_connecting"))
        else:
            self._conn_indicator.setText("⬤ Disconnected")
            self._conn_indicator.setStyleSheet(
                "color: #C62828; font-weight: bold; padding-right: 8px;")
            self._status_label.setText(i18n.t("status_ready"))
            self._db_info_label.setText("")

    def _refresh(self):
        if self.db.is_connected():
            self.browser.refresh()

    def _on_layer_selected(self, schema, table, geom_col, srid, geom_type):
        self.sql_editor.set_active_layer(schema, table)
        self.style_panel.set_active_layer(schema, table, geom_col)
        self.export_panel.set_active_schema(schema)
        self.routing_panel.set_active_layer(schema, table)
        self.chainage_panel.set_active_layer(schema, table, geom_col, srid)
        self.geo_panel.set_active_layer(schema, table, geom_col, srid)
        self.versioning_panel.set_active_layer(schema, table)
        self.validation_panel.set_active_layer(schema, table, geom_col, srid)
        self.map_viewer.set_active_layer(schema, table, geom_col)
        self.query_builder.set_active_layer(schema, table)

    def _load_layer_in_qgis(self, schema, table, geom_col, srid):
        if not self.iface:
            self.log("QGIS interface not available (standalone mode).", "warn")
            return
        try:
            uri = (f"dbname='{self.db.params['dbname']}' "
                   f"host={self.db.params['host']} "
                   f"port={self.db.params['port']} "
                   f"user='{self.db.params['user']}' "
                   f"key='ctid' srid={srid} "
                   f"table=\"{schema}\".\"{table}\" ({geom_col}) sql=")
            from qgis.core import QgsVectorLayer, QgsProject
            layer = QgsVectorLayer(uri, f"{schema}.{table}", "postgres")
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
                self.log(f"Layer loaded in QGIS: {schema}.{table}", "success")
            else:
                self.log(f"Failed to load layer: {schema}.{table}", "error")
        except Exception as e:
            self.log(str(e), "error")

    def log(self, message: str, level: str = "info"):
        self.log_panel.append(message, level)
        self._status_label.setText(message[:80])

    def _switch_language(self, _):
        code = self._lang_combo.currentData()
        i18n.load(code)
        config.set("language", code)

    def _switch_theme(self, _):
        name = self._theme_combo.currentData()
        theme.set_theme(name)
        config.set("theme", name)
        self._apply_theme()

    def _switch_font_size(self, _):
        size = self._font_spin.currentData()
        config.set("font_size", size)
        if not self.embedded:
            app = QApplication.instance()
            if app:
                f = app.font()
                f.setPointSize(size)
                app.setFont(f)
        else:
            f = self.font()
            f.setPointSize(size)
            self.setFont(f)

    def _apply_theme(self):
        qss = theme.build_qss()
        if not self.embedded:
            # Standalone: style the whole app
            app = QApplication.instance()
            if app:
                app.setStyleSheet(qss)
        else:
            # Plugin: style only our window, never touch QGIS UI
            self.setStyleSheet(qss)

    def _on_lang_change(self, lang):
        self._retranslate()

    def _on_theme_change(self, name):
        self._apply_theme()

    def _export_config(self):
        from PyQt6.QtWidgets import QFileDialog, QCheckBox, QDialog, QVBoxLayout, QDialogButtonBox
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.t("cfg_export_title"), "postgis_manager_config.json",
            "JSON (*.json)")
        if not path:
            return
        from PyQt6.QtWidgets import QInputDialog
        inc_pw, ok = QInputDialog.getItem(
            self, i18n.t("cfg_export_title"),
            i18n.t("cfg_include_passwords"),
            [i18n.t("cfg_no_passwords"), i18n.t("cfg_with_passwords")], 0, False)
        if not ok:
            return
        include_pw = inc_pw == i18n.t("cfg_with_passwords")
        try:
            config.export_config(path, include_passwords=include_pw)
            QMessageBox.information(self, "OK", i18n.t("cfg_export_done", path=path))
            self.log(i18n.t("cfg_export_done", path=path), "success")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _import_config(self):
        from PyQt6.QtWidgets import QFileDialog, QInputDialog
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("cfg_import_title"), "", "JSON (*.json)")
        if not path:
            return
        mode, ok = QInputDialog.getItem(
            self, i18n.t("cfg_import_title"),
            i18n.t("cfg_import_mode"),
            [i18n.t("cfg_merge"), i18n.t("cfg_replace")], 0, False)
        if not ok:
            return
        merge = mode == i18n.t("cfg_merge")
        try:
            imported = config.import_config(path, merge=merge)
            n_conns = len(imported.get("connections", []))
            self._refresh_conn_combo()
            if hasattr(self, "instance_panel"):
                self.instance_panel._load_saved()
            QMessageBox.information(
                self, "OK", i18n.t("cfg_import_done", n=n_conns))
            self.log(i18n.t("cfg_import_done", n=n_conns), "success")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _add_container_connection(self, profile: dict):
        """Called from InstanceManagerPanel when user clicks 'Add as connection'."""
        config.save_connection(profile)
        self._refresh_conn_combo()
        if hasattr(self, "instance_panel"):
            self.instance_panel._load_saved()
        idx = self._conn_combo.findText(profile["name"])
        if idx >= 0:
            self._conn_combo.setCurrentIndex(idx)
        self.log(f"Connection added: {profile['name']}", "success")

    def _connect_from_bridge(self, profile: dict):
        """Triggered by QGISBridgePanel — save + immediately connect."""
        config.save_connection(profile)
        self._refresh_conn_combo()
        idx = self._conn_combo.findText(profile["name"])
        if idx >= 0:
            self._conn_combo.setCurrentIndex(idx)
        self._connect_selected()

    def _retranslate(self):
        self.setWindowTitle(i18n.t("app_title"))
        self._menu_file.setTitle(i18n.t("menu_file"))
        self._menu_conn.setTitle(i18n.t("menu_connection"))
        self._menu_tools.setTitle(i18n.t("menu_tools"))
        self._menu_view.setTitle(i18n.t("menu_view"))
        self._menu_help.setTitle(i18n.t("menu_help"))
        self._action_quit.setText(i18n.t("action_quit"))
        self._action_new_conn.setText(i18n.t("action_new_connection"))
        self._action_connect.setText(i18n.t("action_connect"))
        self._action_disconnect.setText(i18n.t("action_disconnect"))
        self._action_refresh.setText(i18n.t("action_refresh"))
        self._action_export_cfg.setText(i18n.t("cfg_export_title"))
        self._action_import_cfg.setText(i18n.t("cfg_import_title"))
        self._action_settings.setText(i18n.t("action_settings"))
        self._action_about.setText(i18n.t("about_title"))
        self._action_credits.setText(i18n.t("action_credits"))
        # Update sidebar nav button labels
        for btn, key in self._nav_labels:
            icon_text = btn.text().split("  ", 1)
            icon = icon_text[1].split("  ", 1)[0] if "  " in icon_text[1] else ""
            btn.setText(f"  {icon}  {i18n.t(key)}")

    def _open_settings(self):
        SettingsDialog(self).exec()

    def _open_about(self):
        AboutDialog(self, APP_VERSION).exec()

    def _open_credits(self):
        import os
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "CREDITS.md")
        from .dialogs.credits_dialog import CreditsDialog
        CreditsDialog(self, path).exec()

    def closeEvent(self, event):
        config.set("window_geometry", self.saveGeometry().toHex().data().decode())
        self.db.disconnect()
        super().closeEvent(event)
