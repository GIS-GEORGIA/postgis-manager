"""Backup database (pg_dump) — Processing algorithm."""

from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingOutputString, QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum, QgsProcessingParameterFileDestination,
    QgsProcessingParameterNumber, QgsProcessingParameterString,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import _shared

_FORMATS = ["Custom (.backup, compressed)", "Directory (parallel)", "Plain SQL (.sql)",
            "Tar (.tar)"]
_LETTERS = ["c", "d", "p", "t"]


class BackupDatabaseAlgorithm(QgsProcessingAlgorithm):
    FORMAT = "FORMAT"
    SCHEMAS = "SCHEMAS"
    SCHEMA_ONLY = "SCHEMA_ONLY"
    DATA_ONLY = "DATA_ONLY"
    NO_OWNER = "NO_OWNER"
    COMPRESS = "COMPRESS"
    JOBS = "JOBS"
    OUTPUT = "OUTPUT"

    def initAlgorithm(self, config=None):
        _shared.add_connection_parameters(self, self.tr)
        self.addParameter(QgsProcessingParameterEnum(
            self.FORMAT, self.tr("Format"), options=_FORMATS, defaultValue=0))
        self.addParameter(QgsProcessingParameterString(
            self.SCHEMAS, self.tr("Only these schemas (comma separated, empty = all)"),
            optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SCHEMA_ONLY, self.tr("Schema only (no data)"), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DATA_ONLY, self.tr("Data only (no schema)"), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.NO_OWNER, self.tr("Do not dump object ownership (--no-owner)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.COMPRESS, self.tr("Compress (custom / directory / tar)"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.JOBS, self.tr("Parallel jobs (directory format only)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=1, minValue=1, maxValue=32))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT, self.tr("Backup file (for the directory format: the folder)"),
            fileFilter="Backup (*.backup *.dump *.sql *.tar);;All files (*.*)"))
        self.addOutput(QgsProcessingOutputString(
            "RESULT", self.tr("Backup path")))

    def processAlgorithm(self, parameters, context, feedback):
        tools = _shared.pg_tools()
        conn = _shared.read_connection(self, parameters, context)
        fmt = _LETTERS[self.parameterAsEnum(parameters, self.FORMAT, context)]
        outfile = self.parameterAsFileOutput(parameters, self.OUTPUT, context)
        schemas = [s.strip() for s in
                   (self.parameterAsString(parameters, self.SCHEMAS, context) or "").split(",")
                   if s.strip()]

        tool = tools.find_tool("pg_dump", _shared.tool_dirs())
        if not tool:
            raise QgsProcessingException(
                "pg_dump was not found. Install the PostgreSQL client tools or add "
                "them to PATH.")
        try:
            cmd = tools.build_dump_cmd(
                tool, host=conn["host"], port=conn["port"], user=conn["user"],
                dbname=conn["dbname"], outfile=outfile, fmt=fmt,
                compress=self.parameterAsBoolean(parameters, self.COMPRESS, context),
                jobs=self.parameterAsInt(parameters, self.JOBS, context),
                schema_only=self.parameterAsBoolean(parameters, self.SCHEMA_ONLY, context),
                data_only=self.parameterAsBoolean(parameters, self.DATA_ONLY, context),
                no_owner=self.parameterAsBoolean(parameters, self.NO_OWNER, context),
                schemas=schemas)
        except ValueError as exc:
            raise QgsProcessingException(str(exc))

        feedback.pushInfo(" ".join(cmd))
        code = tools.run_command(cmd, tools.db_env(conn["password"]),
                                 on_line=feedback.pushInfo,
                                 is_canceled=feedback.isCanceled)
        if code == -1:
            raise QgsProcessingException("Backup cancelled.")
        if code != 0:
            raise QgsProcessingException("pg_dump failed (exit code {}).".format(code))
        feedback.pushInfo("Backup written to {}".format(outfile))
        return {self.OUTPUT: outfile, "RESULT": outfile}

    def name(self): return "backupdatabase"
    def displayName(self): return self.tr("Backup database (pg_dump)")
    def group(self): return self.tr("Database")
    def groupId(self): return "database"
    def createInstance(self): return BackupDatabaseAlgorithm()
    def tr(self, s): return QCoreApplication.translate("BackupDatabaseAlgorithm", s)

    def shortHelpString(self):
        return self.tr(
            "Dumps a PostgreSQL/PostGIS database with pg_dump (the client tools shipped "
            "with QGIS are used when none is on PATH).\n\n"
            "The password is handed to pg_dump through the PGPASSWORD environment "
            "variable, never on the command line. Prefer a saved login "
            "(authentication configuration) or pgpass over typing it here: parameters "
            "typed into an algorithm can end up in the Processing history and in models.")
