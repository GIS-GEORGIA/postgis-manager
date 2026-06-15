"""Smoke test — every module must import without errors."""


def test_db_manager():
    from postgis_manager.db.connection import DBManager
    assert DBManager is not None


def test_utils_i18n():
    from postgis_manager.utils import i18n
    assert callable(i18n.t)


def test_utils_config():
    from postgis_manager.utils import config
    assert callable(config.get)


def test_utils_theme():
    from postgis_manager.utils import theme
    assert callable(theme.build_qss)


def test_all_panels(qapp):
    """All UI panels must import (no syntax / import errors)."""
    from postgis_manager.ui.panels import (
        browser, sql_editor, raster_import, routing,
        topology, chainage, export_panel, style_manager,
        geoprocessing, log_panel, instance_manager,
        network_scan_panel, versioning_panel, validation_panel,
        query_builder, citydb_panel, db_dashboard,
        table_designer, function_browser, backup_panel,
        schema_role_manager, matview_panel, raster_tools,
    )


def test_sidebar_nav(qapp):
    from postgis_manager.ui.sidebar_nav import NavSidebar
    nav = NavSidebar()
    nav.add_group("Test Group")
    btn = nav.add_item("★", "Test Item", 0)
    assert btn is not None


def test_main_window_import(qapp):
    from postgis_manager.ui.main_window import MainWindow
    assert MainWindow is not None
