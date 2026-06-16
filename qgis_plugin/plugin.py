"""PostGIS Manager QGIS Plugin — targets QGIS 4.x / Qt 6."""

import os
import sys
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt


def _ensure_postgis_manager_on_path():
    """Add the directory that contains the postgis_manager package to sys.path.

    Search order:
    1. plugin_dir itself  — ZIP install: qgis_plugin folder IS the plugin root
       and postgis_manager/ is nested inside it.
    2. parent of plugin_dir — dev install where both qgis_plugin/ and
       postgis_manager/ sit side-by-side inside the QGIS plugins folder.
    3. grandparent        — repo checkout: .../repo/qgis_plugin/ with
       .../repo/postgis_manager/ next to it.
    """
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        plugin_dir,
        os.path.dirname(plugin_dir),
        os.path.dirname(os.path.dirname(plugin_dir)),
    ]
    for candidate in candidates:
        if os.path.isdir(os.path.join(candidate, "postgis_manager")):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return True
    return False


class PostGISManagerPlugin:
    """QGIS Plugin wrapper — opens PostGIS Manager as a dockable window."""

    def __init__(self, iface):
        self.iface = iface
        self._window = None
        self._action = None
        self._provider = None
        self.initProcessing()

    def initProcessing(self):
        from .processing_provider import PostGISManagerProvider
        self._provider = PostGISManagerProvider()
        from qgis.core import QgsApplication
        QgsApplication.processingRegistry().addProvider(self._provider)

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self._action = QAction(
            QIcon(icon_path) if os.path.exists(icon_path) else QIcon(),
            "PostGIS Manager",
            self.iface.mainWindow(),
        )
        self._action.setToolTip("Open PostGIS Manager (GIS GEORGIA | Giorgi Kapanadze)")
        self._action.setCheckable(True)
        self._action.triggered.connect(self._toggle_manager)

        self.iface.addToolBarIcon(self._action)
        self.iface.addPluginToMenu("&PostGIS Manager", self._action)

    def unload(self):
        self.iface.removePluginMenu("&PostGIS Manager", self._action)
        self.iface.removeToolBarIcon(self._action)
        if self._window:
            self._window.close()
        if self._provider:
            from qgis.core import QgsApplication
            QgsApplication.processingRegistry().removeProvider(self._provider)
        del self._action

    def _toggle_manager(self, checked: bool):
        if checked:
            self._open_manager()
        else:
            if self._window:
                self._window.hide()

    def _open_manager(self):
        if self._window is None:
            if not _ensure_postgis_manager_on_path():
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    "PostGIS Manager — Import Error",
                    "Cannot find the <b>postgis_manager</b> package.<br><br>"
                    "Please install the plugin from the ZIP file built by "
                    "<code>python make_plugin_zip.py</code>, which bundles the "
                    "core package inside the plugin folder.",
                )
                self._action.setChecked(False)
                return

            from postgis_manager.ui.main_window import MainWindow
            self._window = MainWindow(
                parent=self.iface.mainWindow(),
                embedded=True,
                iface=self.iface,
            )
            self._window.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.WindowMinimizeButtonHint
                | Qt.WindowType.WindowMaximizeButtonHint
                | Qt.WindowType.WindowCloseButtonHint
            )
            self._window.destroyed.connect(self._on_window_destroyed)

        self._window.show()
        self._window.raise_()
        self._window.activateWindow()
        self._action.setChecked(True)

    def _on_window_destroyed(self):
        self._window = None
        self._action.setChecked(False)
