# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for PostGIS Manager standalone desktop app.

Build:
    pyinstaller postgis_manager.spec

Output:
    dist/PostGIS Manager/PostGIS Manager.exe   (folder mode)
    dist/PostGIS_Manager_Setup.exe             (after NSIS or Inno Setup)
"""

import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# ── Data files to bundle ───────────────────────────────────────────────────
datas = [
    # i18n JSON files
    (os.path.join("postgis_manager", "i18n", "en.json"),
     os.path.join("postgis_manager", "i18n")),
    (os.path.join("postgis_manager", "i18n", "ka.json"),
     os.path.join("postgis_manager", "i18n")),
    # App icon
    (os.path.join("qgis_plugin", "icon.png"), "."),
]

# Collect geopandas, fiona, pyproj data (projections, drivers, etc.)
datas += collect_data_files("geopandas")
datas += collect_data_files("fiona")
datas += collect_data_files("pyproj")
datas += collect_data_files("shapely")

# ── Hidden imports ─────────────────────────────────────────────────────────
hiddenimports = [
    # psycopg2
    "psycopg2",
    "psycopg2._psycopg",
    # PyQt6 modules used at runtime
    "PyQt6.QtPrintSupport",
    "PyQt6.QtSvg",
    "PyQt6.QtNetwork",
    # geopandas / fiona drivers
    "fiona.ogrext",
    "pyproj.datadir",
    # All UI panels (imported dynamically in main_window)
    "postgis_manager.ui.panels.browser",
    "postgis_manager.ui.panels.sql_editor",
    "postgis_manager.ui.panels.raster_import",
    "postgis_manager.ui.panels.routing",
    "postgis_manager.ui.panels.topology",
    "postgis_manager.ui.panels.chainage",
    "postgis_manager.ui.panels.export_panel",
    "postgis_manager.ui.panels.style_manager",
    "postgis_manager.ui.panels.geoprocessing",
    "postgis_manager.ui.panels.log_panel",
    "postgis_manager.ui.panels.instance_manager",
    "postgis_manager.ui.panels.network_scan_panel",
    "postgis_manager.ui.panels.versioning_panel",
    "postgis_manager.ui.panels.validation_panel",
    "postgis_manager.ui.panels.query_builder",
    "postgis_manager.ui.panels.citydb_panel",
    "postgis_manager.ui.panels.db_dashboard",
    "postgis_manager.ui.panels.table_designer",
    "postgis_manager.ui.panels.function_browser",
    "postgis_manager.ui.panels.backup_panel",
    "postgis_manager.ui.panels.schema_role_manager",
    "postgis_manager.ui.panels.matview_panel",
    "postgis_manager.ui.panels.raster_tools",
    "postgis_manager.ui.sidebar_nav",
    "postgis_manager.ui.dialogs.connection_dialog",
    "postgis_manager.ui.dialogs.settings_dialog",
    "postgis_manager.ui.dialogs.about_dialog",
    "postgis_manager.ui.dialogs.credits_dialog",
]

hiddenimports += collect_submodules("postgis_manager")

# ── Analysis ───────────────────────────────────────────────────────────────
a = Analysis(
    ["standalone/app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "qgis", "osgeo", "tkinter", "matplotlib",
        "notebook", "IPython", "sphinx",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE (onedir mode — faster startup, easier debugging) ──────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PostGIS Manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no black terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="postgis_manager_icon.ico" if os.path.exists("postgis_manager_icon.ico") else None,
    version_file=None,
)

# ── COLLECT (folder dist/) ────────────────────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PostGIS Manager",
)
