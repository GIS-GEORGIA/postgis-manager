"""PostGIS Manager Processing Provider — registers algorithms in QGIS Toolbox."""

from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
import os


class PostGISManagerProvider(QgsProcessingProvider):
    ID = "postgis_manager"

    def __init__(self):
        super().__init__()

    def id(self) -> str:
        return self.ID

    def name(self) -> str:
        return "PostGIS Manager"

    def longName(self) -> str:
        return "PostGIS Manager (GIS GEORGIA | Giorgi Kapanadze)"

    def icon(self) -> QIcon:
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        return QIcon(icon_path) if os.path.exists(icon_path) else super().icon()

    def loadAlgorithms(self):
        from .algorithms.import_vector     import ImportVectorAlgorithm
        from .algorithms.export_layer      import ExportLayerAlgorithm
        from .algorithms.run_validation    import ValidationAlgorithm
        from .algorithms.geoprocess        import GeoprocessAlgorithm
        from .algorithms.network_scan      import NetworkScanAlgorithm
        from .algorithms.fix_invalid_geom  import FixInvalidGeomAlgorithm

        for alg in [
            ImportVectorAlgorithm(),
            ExportLayerAlgorithm(),
            ValidationAlgorithm(),
            GeoprocessAlgorithm(),
            NetworkScanAlgorithm(),
            FixInvalidGeomAlgorithm(),
        ]:
            self.addAlgorithm(alg)

    def supportsNonFileBasedOutput(self) -> bool:
        return True
