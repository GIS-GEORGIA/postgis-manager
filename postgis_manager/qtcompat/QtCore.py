"""qgis.PyQt/PyQt6/PyQt5 QtCore, whichever this environment has. See __init__.py."""

from . import load, reexport

reexport(globals(), load("QtCore"))
