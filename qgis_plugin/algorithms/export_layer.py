"""Export PostGIS layer — Processing algorithm."""

from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingParameterString,
    QgsProcessingParameterNumber, QgsProcessingParameterEnum,
    QgsProcessingParameterFileDestination, QgsProcessingOutputString,
    QgsProcessingContext, QgsProcessingFeedback,
)
from qgis.PyQt.QtCore import QCoreApplication

_FORMATS = ["GPKG", "ESRI Shapefile", "GeoJSON", "CSV"]
_EXTS    = [".gpkg", ".shp", ".geojson", ".csv"]


class ExportLayerAlgorithm(QgsProcessingAlgorithm):
    HOST = "HOST"; PORT = "PORT"; DBNAME = "DBNAME"
    USER = "USER"; PASSWORD = "PASSWORD"
    SCHEMA = "SCHEMA"; TABLE = "TABLE"
    FORMAT = "FORMAT"; OUTPUT = "OUTPUT"; RESULT = "RESULT"

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(
            self.HOST, self.tr("Host"), defaultValue="localhost"))
        self.addParameter(QgsProcessingParameterNumber(
            self.PORT, self.tr("Port"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=5432, minValue=1, maxValue=65535))
        self.addParameter(QgsProcessingParameterString(
            self.DBNAME, self.tr("Database")))
        self.addParameter(QgsProcessingParameterString(
            self.USER, self.tr("User"), defaultValue="postgres"))
        self.addParameter(QgsProcessingParameterString(
            self.PASSWORD, self.tr("Password"), optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.SCHEMA, self.tr("Schema"), defaultValue="public"))
        self.addParameter(QgsProcessingParameterString(
            self.TABLE, self.tr("Table")))
        self.addParameter(QgsProcessingParameterEnum(
            self.FORMAT, self.tr("Output format"),
            options=_FORMATS, defaultValue=0))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, self.tr("Output file"),
            fileFilter="GeoPackage (*.gpkg);;Shapefile (*.shp);;"
                        "GeoJSON (*.geojson);;CSV (*.csv)"))
        self.addOutput(QgsProcessingOutputString(
            self.RESULT, self.tr("Result")))

    def processAlgorithm(self, parameters, context, feedback):
        host     = self.parameterAsString(parameters, self.HOST, context)
        port     = self.parameterAsInt(parameters, self.PORT, context)
        dbname   = self.parameterAsString(parameters, self.DBNAME, context)
        user     = self.parameterAsString(parameters, self.USER, context)
        password = self.parameterAsString(parameters, self.PASSWORD, context)
        schema   = self.parameterAsString(parameters, self.SCHEMA, context)
        table    = self.parameterAsString(parameters, self.TABLE, context)
        fmt_idx  = self.parameterAsEnum(parameters, self.FORMAT, context)
        out_path = self.parameterAsFileOutput(parameters, self.OUTPUT, context)

        fmt  = _FORMATS[fmt_idx]
        ext  = _EXTS[fmt_idx]
        if not out_path.endswith(ext):
            out_path += ext

        try:
            import sys, os
            plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            from postgis_manager.db.connection import DBManager
            db = DBManager()
            db.connect(host=host, port=port, dbname=dbname,
                       user=user, password=password)
            db.export_layer(schema, table, out_path, fmt)
            db.disconnect()
            msg = f"Exported {schema}.{table} → {out_path}"
            feedback.pushInfo(msg)
            return {self.OUTPUT: out_path, self.RESULT: msg}
        except Exception as e:
            raise Exception(f"Export failed: {e}") from e

    def name(self): return "exportlayer"
    def displayName(self): return self.tr("Export PostGIS layer")
    def group(self): return self.tr("Import / Export")
    def groupId(self): return "importexport"
    def createInstance(self): return ExportLayerAlgorithm()
    def tr(self, s): return QCoreApplication.translate("ExportLayerAlgorithm", s)
