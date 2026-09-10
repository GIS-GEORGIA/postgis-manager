"""Qt binding shim — one codebase for QGIS 3, QGIS 4 and the standalone app.

PostGIS Manager runs in three places that do not agree on which Qt is available:

    QGIS 4          PyQt6, reachable as qgis.PyQt
    QGIS 3.40+      PyQt5, reachable as qgis.PyQt
    standalone      whatever is installed — PyQt6 normally, PyQt5 as a fallback

So nothing in this package imports ``PyQt5``/``PyQt6`` directly. Import the Qt
modules from here instead::

    from ..qtcompat.QtWidgets import QDialog, QVBoxLayout
    from ..qtcompat.QtCore import Qt, pyqtSignal

Inside QGIS we go through ``qgis.PyQt`` rather than the binding directly, so we
always get exactly the Qt that QGIS itself was built against.

Two rules make the shared code work on both Qt generations:

* **Scoped enums only** — ``Qt.AlignmentFlag.AlignLeft``, never ``Qt.AlignLeft``.
  PyQt6 dropped the unscoped names; PyQt5 accepts the scoped ones.
* **``exec()``, not ``exec_()``** — ``exec_()`` is gone in PyQt6.

The one thing this module papers over is the handful of classes Qt 6 moved from
QtWidgets to QtGui: :mod:`.QtGui` and :mod:`.QtWidgets` both export them, whichever
binding is in play.
"""

import importlib

#: Classes that live in QtWidgets on Qt 5 and in QtGui on Qt 6. Re-exported from
#: both of our modules so an import never depends on the binding.
MOVED_TO_QTGUI = (
    "QAction",
    "QActionGroup",
    "QShortcut",
    "QFileSystemModel",
    "QUndoCommand",
    "QUndoGroup",
    "QUndoStack",
)

_CANDIDATES = ("qgis.PyQt", "PyQt6", "PyQt5")


def _resolve_binding():
    for package in _CANDIDATES:
        try:
            importlib.import_module(package + ".QtCore")
        except ImportError:
            continue
        return package
    raise ImportError(
        "No Qt binding found. Run inside QGIS 3.40+/4.x, or install PyQt6 "
        "(pip install PyQt6) for the standalone app."
    )


#: Which package the Qt modules are actually coming from.
BINDING = _resolve_binding()


def load(module_name):
    """Import a Qt submodule (``"QtWidgets"``) from the resolved binding."""
    return importlib.import_module(f"{BINDING}.{module_name}")


def reexport(namespace, module, fill_from=None, fill_names=()):
    """Copy a Qt module's public names into ``namespace``.

    ``fill_from``/``fill_names`` add just the named classes from a second module, and
    only when this Qt version does not already provide them here — that is how the
    classes Qt 6 moved between QtWidgets and QtGui stay importable from both.
    """
    for name, value in vars(module).items():
        if not name.startswith("_"):
            namespace[name] = value
    if fill_from is not None:
        for name in fill_names:
            if name not in namespace and hasattr(fill_from, name):
                namespace[name] = getattr(fill_from, name)


def qt_major():
    """5 or 6 — occasionally needed for behaviour that cannot be papered over."""
    from .QtCore import QT_VERSION_STR

    return int(QT_VERSION_STR.split(".")[0])
