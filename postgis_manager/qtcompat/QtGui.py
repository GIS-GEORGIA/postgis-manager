"""QtGui, plus the classes Qt 5 keeps in QtWidgets (QAction, QShortcut, ...)."""

from . import MOVED_TO_QTGUI, load, reexport

reexport(globals(), load("QtGui"), fill_from=load("QtWidgets"), fill_names=MOVED_TO_QTGUI)
