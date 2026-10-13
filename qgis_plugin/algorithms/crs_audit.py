"""CRS audit — Processing algorithm (declared vs. actual SRID of every geometry column)."""

from qgis.core import (
    QgsFeature, QgsField, QgsFields, QgsProcessingAlgorithm,
    QgsProcessingException, QgsProcessingOutputNumber,
    QgsProcessingParameterBoolean, QgsProcessingParameterFeatureSink,
    QgsProcessingParameterString, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication, QMetaType

from . import _shared


class CrsAuditAlgorithm(QgsProcessingAlgorithm):
    SCHEMA = "SCHEMA"
    FIX = "FIX"
    OUTPUT = "OUTPUT"

    def initAlgorithm(self, config=None):
        _shared.add_connection_parameters(self, self.tr)
        self.addParameter(QgsProcessingParameterString(
            self.SCHEMA, self.tr("Schema (empty = all schemas)"), optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FIX,
            self.tr("Fix mismatches: set the declared SRID to the one found in the data "
                    "(only columns with a single SRID; coordinates are NOT transformed)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("CRS audit")))
        self.addOutput(QgsProcessingOutputNumber("ERRORS", self.tr("Columns with errors")))
        self.addOutput(QgsProcessingOutputNumber("WARNINGS", self.tr("Columns with warnings")))
        self.addOutput(QgsProcessingOutputNumber("FIXED", self.tr("Columns fixed")))

    def processAlgorithm(self, parameters, context, feedback):
        tools = _shared.pg_tools()
        conn_info = _shared.read_connection(self, parameters, context)
        schema = (self.parameterAsString(parameters, self.SCHEMA, context) or "").strip()
        do_fix = self.parameterAsBoolean(parameters, self.FIX, context)

        fields = QgsFields()
        for name, typ in (("schema", QMetaType.Type.QString), ("table", QMetaType.Type.QString),
                          ("geom_column", QMetaType.Type.QString), ("geom_type", QMetaType.Type.QString),
                          ("declared_srid", QMetaType.Type.Int), ("actual_srid", QMetaType.Type.Int),
                          ("distinct_srids", QMetaType.Type.Int), ("status", QMetaType.Type.QString),
                          ("issue", QMetaType.Type.QString), ("fixed", QMetaType.Type.Bool)):
            fields.append(QgsField(name, typ))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.NoGeometry)
        if sink is None:
            raise QgsProcessingException(self.invalidSinkError(parameters, self.OUTPUT))

        try:
            conn = _shared.connect(conn_info)
        except Exception as exc:  # noqa: BLE001
            raise QgsProcessingException("Cannot connect: {}".format(exc))
        try:
            rows = tools.audit_crs(
                conn, schema or None,
                progress=lambda p, msg: (feedback.setProgress(p), feedback.setProgressText(msg)))

            fixed_keys = set()
            if do_fix:
                todo = [r for r in rows if tools.fixable(r)]

                def on_error(r, exc):
                    feedback.reportError("{}.{}.{}: {}".format(
                        r["schema"], r["table"], r["col"], str(exc).strip()))

                n_ok = tools.fix_crs(conn, todo, on_error=on_error)
                # mark the rows that now audit clean
                if n_ok:
                    recheck = tools.audit_crs(conn, schema or None)
                    ok = {(r["schema"], r["table"], r["col"]) for r in recheck
                          if r["status"] in ("OK", "EMPTY")}
                    fixed_keys = {(r["schema"], r["table"], r["col"]) for r in todo
                                  if (r["schema"], r["table"], r["col"]) in ok}
        finally:
            conn.close()

        errors = warnings = 0
        for r in rows:
            key = (r["schema"], r["table"], r["col"])
            if r["status"] == "ERROR":
                errors += 1
            elif r["status"] == "WARN":
                warnings += 1
            if r["status"] in ("ERROR", "WARN"):
                (feedback.reportError if r["status"] == "ERROR" else feedback.pushWarning)(
                    "{}.{}.{}: {}".format(r["schema"], r["table"], r["col"], r["issue"]))
            f = QgsFeature(fields)
            f.setAttributes([r["schema"], r["table"], r["col"], r["type"], r["declared"],
                             r["actual"], r["unique_srids"], r["status"], r["issue"],
                             key in fixed_keys])
            sink.addFeature(f)
        feedback.pushInfo("{} geometry column(s): {} error(s), {} warning(s), {} fixed.".format(
            len(rows), errors, warnings, len(fixed_keys)))
        return {self.OUTPUT: dest, "ERRORS": errors, "WARNINGS": warnings,
                "FIXED": len(fixed_keys)}

    def name(self): return "crsaudit"
    def displayName(self): return self.tr("CRS audit (declared vs. actual SRID)")
    def group(self): return self.tr("Geometry")
    def groupId(self): return "geometry"
    def createInstance(self): return CrsAuditAlgorithm()
    def tr(self, s): return QCoreApplication.translate("CrsAuditAlgorithm", s)

    def shortHelpString(self):
        return self.tr(
            "Compares, for every geometry column listed in geometry_columns, the SRID "
            "declared in the table definition with the SRIDs actually stored in the "
            "data. Status: OK, EMPTY (no geometries), WARN (declared 0, or several SRIDs "
            "mixed), ERROR (declared differs from the data).\n\n"
            "'Fix' relabels columns whose data has exactly one SRID using "
            "UpdateGeometrySRID. That only changes the metadata: use it when the "
            "declaration is wrong, not to reproject (use ST_Transform for that). Views "
            "cannot be fixed and are reported as errors.")
