"""qgis.PyQt/PyQt6/PyQt5 QtPrintSupport, whichever this environment has."""

from . import load, reexport

reexport(globals(), load("QtPrintSupport"))
