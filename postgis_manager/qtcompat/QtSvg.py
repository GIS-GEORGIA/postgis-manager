"""qgis.PyQt/PyQt6/PyQt5 QtSvg, whichever this environment has."""

from . import load, reexport

reexport(globals(), load("QtSvg"))
