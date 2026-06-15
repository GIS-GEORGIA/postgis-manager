"""Geoprocessing (Buffer/Simplify/Hull/Centroid) — Processing algorithm."""

from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingParameterString,
    QgsProcessingParameterNumber, QgsProcessingParameterEnum,
    QgsProcessingOutputString, QgsProcessingContext, QgsProcessingFeedback,
)
from qgis.PyQt.QtCore import QCoreApplication

_OPS = ["buffer", "simplify", "convex_hull", "centroid"]


class GeoprocessAlgorithm(QgsProcessingAlgorithm):
    HOST = "HOST"; PORT = "PORT"; DBNAME = "DBNAME"
    USER = "USER"; PASSWORD = "PASSWORD"
    SCHEMA = "SCHEMA"; TABLE = "TABLE"; GEOM_COL = "GEOM_COL"
    OPERATION = "OPERATION"; DISTANCE = "DISTANCE"; TOLERANCE = "TOLERANCE"
    OUT_SCHEMA = "OUT_SCHEMA"; OUT_TABLE = "OUT_TABLE"; OUTPUT = "OUTPUT"

    def initAlgorithm(self, config=None):
        for pid, label, default in [
            (self.HOST, "Host", "localhost"),
            (self.DBNAME, "Database", ""),
            (self.USER, "User", "postgres"),
            (self.PASSWORD, "Password", ""),
            (self.SCHEMA, "Schema", "public"),
            (self.TABLE, "Table", ""),
            (self.GEOM_COL, "Geometry column", "geom"),
            (self.OUT_SCHEMA, "Output schema", "public"),
            (self.OUT_TABLE, "Output table", ""),
        ]:
            self.addParameter(QgsProcessingParameterString(
                pid, self.tr(label), defaultValue=default,
                optional=(pid == self.PASSWORD)))
        self.addParameter(QgsProcessingParameterNumber(
            self.PORT, self.tr("Port"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=5432, minValue=1, maxValue=65535))
        self.addParameter(QgsProcessingParameterEnum(
            self.OPERATION, self.tr("Operation"),
            options=["Buffer", "Simplify", "Convex Hull", "Centroid"],
            defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DISTANCE, self.tr("Buffer distance (m)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=100.0, minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.TOLERANCE, self.tr("Simplify tolerance"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=1.0, minValue=0.0))
        self.addOutput(QgsProcessingOutputString(
            self.OUTPUT, self.tr("Result")))

    def processAlgorithm(self, parameters, context, feedback):
        host      = self.parameterAsString(parameters, self.HOST, context)
        port      = self.parameterAsInt(parameters, self.PORT, context)
        dbname    = self.parameterAsString(parameters, self.DBNAME, context)
        user      = self.parameterAsString(parameters, self.USER, context)
        password  = self.parameterAsString(parameters, self.PASSWORD, context)
        schema    = self.parameterAsString(parameters, self.SCHEMA, context)
        table     = self.parameterAsString(parameters, self.TABLE, context)
        geom_col  = self.parameterAsString(parameters, self.GEOM_COL, context)
        op_idx    = self.parameterAsEnum(parameters, self.OPERATION, context)
        distance  = self.parameterAsDouble(parameters, self.DISTANCE, context)
        tolerance = self.parameterAsDouble(parameters, self.TOLERANCE, context)
        out_schema = self.parameterAsString(parameters, self.OUT_SCHEMA, context)
        out_table  = self.parameterAsString(parameters, self.OUT_TABLE, context)

        operation = _OPS[op_idx]

        try:
            import sys, os
            plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            from postgis_manager.db.connection import DBManager
            db = DBManager()
            db.connect(host=host, port=port, dbname=dbname,
                       user=user, password=password)
            msg = db.geoprocess(schema, table, geom_col, operation,
                                out_schema, out_table,
                                distance=distance, tolerance=tolerance)
            db.disconnect()
            feedback.pushInfo(msg)
            return {self.OUTPUT: msg}
        except Exception as e:
            raise Exception(f"Geoprocess failed: {e}") from e

    def name(self): return "geoprocess"
    def displayName(self): return self.tr("PostGIS Geoprocessing")
    def group(self): return self.tr("Geometry")
    def groupId(self): return "geometry"
    def createInstance(self): return GeoprocessAlgorithm()
    def tr(self, s): return QCoreApplication.translate("GeoprocessAlgorithm", s)
