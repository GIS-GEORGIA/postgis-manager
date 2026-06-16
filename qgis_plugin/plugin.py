"""PostGIS Manager QGIS Plugin — targets QGIS 4.x / Qt 6."""

import os
import sys
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt


def _register_core_package():
    """Register the nested core package under its canonical name.

    When installed from ZIP the layout is:
        plugins/postgis_manager/              ← QGIS loads this as 'postgis_manager'
            plugin.py  (this file)
            postgis_manager/                  ← core package lives HERE
                ui/ db/ utils/ i18n/ ...

    QGIS has already bound 'postgis_manager' in sys.modules to the outer
    wrapper.  We inject the nested core sub-package into sys.modules under
    the same top-level name so that 'from postgis_manager.ui...' resolves
    to the nested core package instead of the wrapper.

    For standalone / dev installs (core package already on sys.path) this
    function is a no-op.
    """
    import importlib
    import types

    # Already properly importable — nothing to do.
    try:
        import postgis_manager.ui  # noqa: F401
        return
    except ImportError:
        pass

    # Locate the nested core package directory.
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    core_dir = os.path.join(plugin_dir, "postgis_manager")

    if not os.path.isdir(core_dir):
        raise ImportError(
            "Cannot find the nested postgis_manager core package. "
            "Please reinstall using the ZIP built by make_plugin_zip.py."
        )

    # Add plugin_dir to sys.path so sub-packages resolve correctly.
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    # Force-reload 'postgis_manager' from the nested core directory
    # so that postgis_manager.ui / .db / .utils all resolve correctly.
    spec = importlib.util.spec_from_file_location(
        "postgis_manager",
        os.path.join(core_dir, "__init__.py"),
        submodule_search_locations=[core_dir],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [core_dir]
    mod.__package__ = "postgis_manager"
    sys.modules["postgis_manager"] = mod
    spec.loader.exec_module(mod)


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
            try:
                _register_core_package()
            except ImportError as e:
                from qgis.PyQt.QtWidgets import QMessageBox
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    "PostGIS Manager — Import Error",
                    str(e),
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
