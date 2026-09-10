"""qgis.PyQt/PyQt6/PyQt5 QtNetwork, whichever this environment has."""

from . import load, reexport

reexport(globals(), load("QtNetwork"))
