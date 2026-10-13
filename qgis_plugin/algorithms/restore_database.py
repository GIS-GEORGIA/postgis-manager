"""Restore database (pg_restore / psql) — Processing algorithm."""

import os

from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingOutputNumber, QgsProcessingParameterBoolean,
    QgsProcessingParameterFile, QgsProcessingParameterNumber,
)
from qgis.PyQt.QtCore import QCoreApplication

from . import _shared


class RestoreDatabaseAlgorithm(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    CREATE = "CREATE"
    CLEAN = "CLEAN"
    NO_OWNER = "NO_OWNER"
    SCHEMA_ONLY = "SCHEMA_ONLY"
    DATA_ONLY = "DATA_ONLY"
    JOBS = "JOBS"
    CONFIRM = "CONFIRM"

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.INPUT, self.tr("Backup file or directory (.sql = psql, others = pg_restore)"),
            behavior=QgsProcessingParameterFile.Behavior.File,
            fileFilter="Backups (*.backup *.dump *.sql *.tar);;All files (*.*)"))
        _shared.add_connection_parameters(self, self.tr)
        self.addParameter(QgsProcessingParameterBoolean(
            self.CREATE, self.tr("Create the database first (-C; connect to an existing one)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.CLEAN, self.tr("Drop objects before recreating them (-c)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.NO_OWNER, self.tr("Do not restore object ownership"), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SCHEMA_ONLY, self.tr("Schema only"), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.DATA_ONLY, self.tr("Data only"), defaultValue=False))
        self.addParameter(QgsProcessingParameterNumber(
            self.JOBS, self.tr("Parallel jobs (pg_restore)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=1, minValue=1, maxValue=32))
        self.addParameter(QgsProcessingParameterBoolean(
            self.CONFIRM,
            self.tr("I understand this writes into (and with 'drop' deletes from) the target database"),
            defaultValue=False))
        self.addOutput(QgsProcessingOutputNumber("EXITCODE", self.tr("Exit code")))

    def processAlgorithm(self, parameters, context, feedback):
        if not self.parameterAsBoolean(parameters, self.CONFIRM, context):
            raise QgsProcessingException(
                "Restore not confirmed: tick the confirmation box to run it.")
        tools = _shared.pg_tools()
        conn = _shared.read_connection(self, parameters, context)
        infile = self.parameterAsFile(parameters, self.INPUT, context)
        if not os.path.exists(infile):
            raise QgsProcessingException("Backup not found: {}".format(infile))

        try:
            cmd = tools.build_restore_cmd(
                infile,
                restore_tool=tools.find_tool("pg_restore", _shared.tool_dirs()),
                psql_tool=tools.find_tool("psql", _shared.tool_dirs()),
                host=conn["host"], port=conn["port"], user=conn["user"],
                dbname=conn["dbname"],
                create=self.parameterAsBoolean(parameters, self.CREATE, context),
                clean=self.parameterAsBoolean(parameters, self.CLEAN, context),
                no_owner=self.parameterAsBoolean(parameters, self.NO_OWNER, context),
                schema_only=self.parameterAsBoolean(parameters, self.SCHEMA_ONLY, context),
                data_only=self.parameterAsBoolean(parameters, self.DATA_ONLY, context),
                jobs=self.parameterAsInt(parameters, self.JOBS, context),
                stop_on_error=True)
        except FileNotFoundError as exc:
            raise QgsProcessingException(
                "{} was not found. Install the PostgreSQL client tools or add them "
                "to PATH.".format(exc))

        feedback.pushInfo(" ".join(cmd))
        code = tools.run_command(cmd, tools.db_env(conn["password"]),
                                 on_line=feedback.pushInfo,
                                 is_canceled=feedback.isCanceled)
        if code == -1:
            raise QgsProcessingException("Restore cancelled.")
        if code != 0:
            raise QgsProcessingException("Restore failed (exit code {}).".format(code))
        feedback.pushInfo("Restore finished.")
        return {"EXITCODE": code}

    def name(self): return "restoredatabase"
    def displayName(self): return self.tr("Restore database (pg_restore / psql)")
    def group(self): return self.tr("Database")
    def groupId(self): return "database"
    def createInstance(self): return RestoreDatabaseAlgorithm()
    def tr(self, s): return QCoreApplication.translate("RestoreDatabaseAlgorithm", s)

    def shortHelpString(self):
        return self.tr(
            "Restores a backup made by 'Backup database' (or pg_dump) with pg_restore, "
            "or runs a plain .sql file with psql (stops at the first error).\n\n"
            "DANGEROUS: it writes into the target database, and 'drop objects first' "
            "deletes existing tables. The confirmation box must be ticked. Test on a "
            "scratch database first. The password goes through PGPASSWORD, not the "
            "command line.")
