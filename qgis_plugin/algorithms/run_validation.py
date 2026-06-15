"""Geometry Validation — Processing algorithm."""

from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingParameterString,
    QgsProcessingParameterNumber, QgsProcessingOutputString,
    QgsProcessingContext, QgsProcessingFeedback,
)
from qgis.PyQt.QtCore import QCoreApplication


class ValidationAlgorithm(QgsProcessingAlgorithm):
    HOST = "HOST"; PORT = "PORT"; DBNAME = "DBNAME"
    USER = "USER"; PASSWORD = "PASSWORD"
    SCHEMA = "SCHEMA"; TABLE = "TABLE"; GEOM_COL = "GEOM_COL"
    SRID = "SRID"; OUTPUT = "OUTPUT"

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
        self.addParameter(QgsProcessingParameterNumber(
            self.SRID, self.tr("Expected SRID"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=4326, minValue=1))
        self.addOutput(QgsProcessingOutputString(
            self.OUTPUT, self.tr("Validation report")))

    def processAlgorithm(self, parameters, context, feedback):
        host     = self.parameterAsString(parameters, self.HOST, context)
        port     = self.parameterAsInt(parameters, self.PORT, context)
        dbname   = self.parameterAsString(parameters, self.DBNAME, context)
        user     = self.parameterAsString(parameters, self.USER, context)
        password = self.parameterAsString(parameters, self.PASSWORD, context)
        schema   = self.parameterAsString(parameters, self.SCHEMA, context)
        table    = self.parameterAsString(parameters, self.TABLE, context)
        geom_col = self.parameterAsString(parameters, self.GEOM_COL, context)
        srid     = self.parameterAsInt(parameters, self.SRID, context)

        try:
            import sys, os
            plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            from postgis_manager.db.connection import DBManager
            db = DBManager()
            db.connect(host=host, port=port, dbname=dbname,
                       user=user, password=password)
            results = db.run_validation(schema, table, geom_col, srid)
            db.disconnect()

            lines = [f"Validation results for {schema}.{table}:"]
            for check, result in results.items():
                status = "✔ OK" if result == 0 else f"✖ {result} issue(s)"
                lines.append(f"  {check}: {status}")
                if result:
                    feedback.reportError(f"{check}: {result} issue(s)")
                else:
                    feedback.pushInfo(f"{check}: OK")

            report = "\n".join(lines)
            feedback.pushInfo(report)
            return {self.OUTPUT: report}
        except Exception as e:
            raise Exception(f"Validation failed: {e}") from e

    def name(self): return "validategeometry"
    def displayName(self): return self.tr("Validate PostGIS geometry")
    def group(self): return self.tr("Geometry")
    def groupId(self): return "geometry"
    def createInstance(self): return ValidationAlgorithm()
    def tr(self, s): return QCoreApplication.translate("ValidationAlgorithm", s)
