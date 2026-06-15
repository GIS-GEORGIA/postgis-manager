"""PostGIS Manager QGIS Plugin — targets QGIS 4.x / Qt 6."""

import os
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt


class PostGISManagerPlugin:
    """QGIS Plugin wrapper — opens PostGIS Manager as a dockable window."""

    def __init__(self, iface):
        self.iface = iface
        self._window = None
        self._action = None

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
        del self._action

    def _toggle_manager(self, checked: bool):
        if checked:
            self._open_manager()
        else:
            if self._window:
                self._window.hide()

    def _open_manager(self):
        if self._window is None:
            # Make the core package importable from inside QGIS
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            import sys
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

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
