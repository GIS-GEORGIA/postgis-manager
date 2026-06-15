"""PostGIS Manager QGIS Plugin."""

import os
from PyQt5.QtWidgets import QAction, QDialog, QVBoxLayout
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt


class PostGISManagerPlugin:
    """QGIS Plugin wrapper — opens PostGIS Manager as a floating window."""

    def __init__(self, iface):
        self.iface = iface
        self._window = None
        self._action = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self._action = QAction(
            QIcon(icon_path) if os.path.exists(icon_path) else QIcon(),
            "PostGIS Manager",
            self.iface.mainWindow()
        )
        self._action.setToolTip("Open PostGIS Manager")
        self._action.triggered.connect(self._open_manager)

        self.iface.addToolBarIcon(self._action)
        self.iface.addPluginToMenu("&PostGIS Manager", self._action)

    def unload(self):
        self.iface.removePluginMenu("&PostGIS Manager", self._action)
        self.iface.removeToolBarIcon(self._action)
        if self._window:
            self._window.close()
        del self._action

    def _open_manager(self):
        if self._window is None or not self._window.isVisible():
            import sys
            import os
            # Add parent directory to path so postgis_manager package is importable
            plugin_dir = os.path.dirname(os.path.dirname(__file__))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            from postgis_manager.ui.main_window import MainWindow
            self._window = MainWindow(
                parent=self.iface.mainWindow(),
                embedded=True,
                iface=self.iface,
            )
            self._window.setWindowFlags(
                Qt.Window |
                Qt.WindowMinimizeButtonHint |
                Qt.WindowMaximizeButtonHint |
                Qt.WindowCloseButtonHint
            )

        self._window.show()
        self._window.raise_()
        self._window.activateWindow()
