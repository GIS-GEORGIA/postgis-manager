"""QGIS plugin entry point — required by QGIS plugin loader."""


def classFactory(iface):
    from .plugin import PostGISManagerPlugin
    return PostGISManagerPlugin(iface)
