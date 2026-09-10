"""QtWidgets, plus the classes Qt 6 moved to QtGui (QAction, QShortcut, ...)."""

from . import MOVED_TO_QTGUI, load, reexport

reexport(globals(), load("QtWidgets"), fill_from=load("QtGui"), fill_names=MOVED_TO_QTGUI)
