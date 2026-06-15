"""Fix Invalid Geometries (ST_MakeValid) — Processing algorithm."""

from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingParameterString,
    QgsProcessingParameterNumber, QgsProcessingOutputNumber,
    QgsProcessingOutputString, QgsProcessingContext, QgsProcessingFeedback,
)
from qgis.PyQt.QtCore import QCoreApplication


class FixInvalidGeomAlgorithm(QgsProcessingAlgorithm):
    HOST = "HOST"; PORT = "PORT"; DBNAME = "DBNAME"
    USER = "USER"; PASSWORD = "PASSWORD"
    SCHEMA = "SCHEMA"; TABLE = "TABLE"; GEOM_COL = "GEOM_COL"
    FIXED = "FIXED"; OUTPUT = "OUTPUT"

    def initAlgorithm(self, config=None):
        for pid, label, default in [
            (self.HOST, "Host", "localhost"),
            (self.DBNAME, "Database", ""),
            (self.USER, "User", "postgres"),
            (self.PASSWORD, "Password", ""),
            (self.SCHEMA, "Schema", "public"),
            (self.TABLE, "Table", ""),
            (self.GEOM_COL, "Geometry column", "geom"),
        ]:
            self.addParameter(QgsProcessingParameterString(
                pid, self.tr(label), defaultValue=default,
                optional=(pid == self.PASSWORD)))
        self.addParameter(QgsProcessingParameterNumber(
            self.PORT, self.tr("Port"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=5432, minValue=1, maxValue=65535))
        self.addOutput(QgsProcessingOutputNumber(
            self.FIXED, self.tr("Number of geometries fixed")))
        self.addOutput(QgsProcessingOutputString(
            self.OUTPUT, self.tr("Result message")))

    def processAlgorithm(self, parameters, context, feedback):
        host     = self.parameterAsString(parameters, self.HOST, context)
        port     = self.parameterAsInt(parameters, self.PORT, context)
        dbname   = self.parameterAsString(parameters, self.DBNAME, context)
        user     = self.parameterAsString(parameters, self.USER, context)
        password = self.parameterAsString(parameters, self.PASSWORD, context)
        schema   = self.parameterAsString(parameters, self.SCHEMA, context)
        table    = self.parameterAsString(parameters, self.TABLE, context)
        geom_col = self.parameterAsString(parameters, self.GEOM_COL, context)

        try:
            import sys, os
            plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            from postgis_manager.db.connection import DBManager
            db = DBManager()
            db.connect(host=host, port=port, dbname=dbname,
                       user=user, password=password)
            n_fixed = db.fix_invalid_geometries(schema, table, geom_col)
            db.disconnect()
            msg = f"Fixed {n_fixed} invalid geometry/geometries in {schema}.{table}"
            feedback.pushInfo(msg)
            return {self.FIXED: n_fixed, self.OUTPUT: msg}
        except Exception as e:
            raise Exception(f"Fix failed: {e}") from e

    def name(self): return "fixinvalidgeom"
    def displayName(self): return self.tr("Fix invalid geometries (ST_MakeValid)")
    def group(self): return self.tr("Geometry")
    def groupId(self): return "geometry"
    def createInstance(self): return FixInvalidGeomAlgorithm()
    def tr(self, s): return QCoreApplication.translate("FixInvalidGeomAlgorithm", s)
