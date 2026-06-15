"""Import Vector Layer to PostGIS — Processing algorithm."""

from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingParameterVectorLayer,
    QgsProcessingParameterString, QgsProcessingParameterNumber,
    QgsProcessingParameterEnum, QgsProcessingOutputString,
    QgsProcessingContext, QgsProcessingFeedback,
    QgsVectorFileWriter, QgsCoordinateTransformContext,
)
from qgis.PyQt.QtCore import QCoreApplication


class ImportVectorAlgorithm(QgsProcessingAlgorithm):
    INPUT   = "INPUT"
    HOST    = "HOST"
    PORT    = "PORT"
    DBNAME  = "DBNAME"
    USER    = "USER"
    PASSWORD = "PASSWORD"
    SCHEMA  = "SCHEMA"
    TABLE   = "TABLE"
    SRID    = "SRID"
    MODE    = "MODE"
    OUTPUT  = "OUTPUT"

    _MODES = ["create", "append", "replace"]

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT, self.tr("Input layer")))
        self.addParameter(QgsProcessingParameterString(
            self.HOST, self.tr("PostgreSQL host"), defaultValue="localhost"))
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
            self.SCHEMA, self.tr("Target schema"), defaultValue="public"))
        self.addParameter(QgsProcessingParameterString(
            self.TABLE, self.tr("Target table")))
        self.addParameter(QgsProcessingParameterNumber(
            self.SRID, self.tr("Target SRID"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=4326, minValue=1))
        self.addParameter(QgsProcessingParameterEnum(
            self.MODE, self.tr("Import mode"),
            options=["Create new table", "Append to existing", "Replace existing"],
            defaultValue=0))
        self.addOutput(QgsProcessingOutputString(
            self.OUTPUT, self.tr("Result message")))

    def processAlgorithm(self, parameters, context, feedback):
        layer    = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        host     = self.parameterAsString(parameters, self.HOST, context)
        port     = self.parameterAsInt(parameters, self.PORT, context)
        dbname   = self.parameterAsString(parameters, self.DBNAME, context)
        user     = self.parameterAsString(parameters, self.USER, context)
        password = self.parameterAsString(parameters, self.PASSWORD, context)
        schema   = self.parameterAsString(parameters, self.SCHEMA, context)
        table    = self.parameterAsString(parameters, self.TABLE, context)
        srid     = self.parameterAsInt(parameters, self.SRID, context)
        mode_idx = self.parameterAsEnum(parameters, self.MODE, context)
        mode     = self._MODES[mode_idx]

        feedback.setProgressText(f"Importing {layer.name()} → {schema}.{table}")

        try:
            import sys, os
            plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            from postgis_manager.db.connection import DBManager
            db = DBManager()
            db.connect(host=host, port=port, dbname=dbname,
                       user=user, password=password)

            total = layer.featureCount()
            feedback.setProgress(0)

            import geopandas as gpd
            from shapely import wkb as shapely_wkb

            features = list(layer.getFeatures())
            rows = []
            for f in features:
                geom = f.geometry()
                attrs = {field.name(): f[field.name()]
                         for field in layer.fields()}
                attrs["geom"] = geom.asWkb().data() if not geom.isNull() else None
                rows.append(attrs)

            if not rows:
                return {self.OUTPUT: "No features to import."}

            import psycopg2.extras
            with db.conn.cursor() as cur:
                if mode == "replace":
                    cur.execute(f'DROP TABLE IF EXISTS "{schema}"."{table}"')
                if mode in ("create", "replace"):
                    # Build CREATE TABLE from layer fields
                    pg_type = {
                        "Integer": "integer", "LongLong": "bigint",
                        "Double": "double precision", "String": "text",
                        "Date": "date", "DateTime": "timestamp",
                        "Boolean": "boolean",
                    }
                    cols = ", ".join(
                        f'"{f.name()}" {pg_type.get(f.typeName(), "text")}'
                        for f in layer.fields())
                    cur.execute(
                        f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" '
                        f'(id serial PRIMARY KEY, {cols}, '
                        f'geom geometry({layer.geometryType()},{srid}))')
                for i, row in enumerate(rows):
                    geom_wkb = row.pop("geom")
                    col_names = list(row.keys()) + ["geom"]
                    values = list(row.values()) + [
                        psycopg2.Binary(geom_wkb) if geom_wkb else None]
                    placeholders = ",".join(["%s"] * len(values))
                    cols_sql = ",".join(f'"{c}"' for c in col_names)
                    cur.execute(
                        f'INSERT INTO "{schema}"."{table}" ({cols_sql}) '
                        f'VALUES ({placeholders})', values)
                    if i % 100 == 0:
                        feedback.setProgress(int(100 * i / total))
                        if feedback.isCanceled():
                            db.conn.rollback()
                            return {self.OUTPUT: "Cancelled."}
            db.conn.commit()
            db.disconnect()
            msg = f"Imported {total} features into {schema}.{table}"
            feedback.pushInfo(msg)
            return {self.OUTPUT: msg}

        except Exception as e:
            raise Exception(f"Import failed: {e}") from e

    def name(self) -> str:
        return "importvector"

    def displayName(self) -> str:
        return self.tr("Import vector layer to PostGIS")

    def group(self) -> str:
        return self.tr("Import / Export")

    def groupId(self) -> str:
        return "importexport"

    def shortHelpString(self) -> str:
        return self.tr(
            "Import a QGIS vector layer into a PostGIS table. "
            "Supports create, append and replace modes.")

    def createInstance(self):
        return ImportVectorAlgorithm()

    def tr(self, string: str) -> str:
        return QCoreApplication.translate("ImportVectorAlgorithm", string)
