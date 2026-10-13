"""Helpers shared by the database-tool Processing algorithms (backup, restore, CRS audit)."""

import importlib.util
import os
import sys

from qgis.core import (
    QgsApplication,
    QgsAuthMethodConfig,
    QgsProcessingException,
    QgsProcessingParameterAuthConfig,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
)

_pg_tools = None


def pg_tools():
    """Load ``postgis_manager/utils/pg_tools.py`` straight from its file.

    Importing it as ``postgis_manager.utils.pg_tools`` only works once the manager
    window has registered the nested core package; algorithms must work before that
    (and under qgis_process). The module is self-contained, so a file load is enough."""
    global _pg_tools
    if _pg_tools is not None:
        return _pg_tools
    here = os.path.dirname(os.path.abspath(__file__))
    plugin_dir = os.path.dirname(here)
    candidates = (
        # installed ZIP: <plugin>/postgis_manager/utils/pg_tools.py
        os.path.join(plugin_dir, "postgis_manager", "utils", "pg_tools.py"),
        # source tree: <repo>/postgis_manager/utils/pg_tools.py
        os.path.join(os.path.dirname(plugin_dir), "postgis_manager", "utils", "pg_tools.py"),
    )
    for path in candidates:
        if os.path.isfile(path):
            spec = importlib.util.spec_from_file_location("_pgm_pg_tools", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["_pgm_pg_tools"] = mod
            spec.loader.exec_module(mod)
            _pg_tools = mod
            return mod
    raise QgsProcessingException("pg_tools.py not found next to the plugin.")


def add_connection_parameters(alg, tr, dbname_hint=""):
    """HOST / PORT / DBNAME / USER / PASSWORD + AUTHCFG (same ids as the other algorithms)."""
    alg.addParameter(QgsProcessingParameterString(
        "HOST", tr("Host"), defaultValue="localhost"))
    alg.addParameter(QgsProcessingParameterNumber(
        "PORT", tr("Port"), QgsProcessingParameterNumber.Type.Integer,
        defaultValue=5432, minValue=1, maxValue=65535))
    alg.addParameter(QgsProcessingParameterString("DBNAME", tr("Database")))
    alg.addParameter(QgsProcessingParameterString(
        "USER", tr("User"), defaultValue="postgres", optional=True))
    alg.addParameter(QgsProcessingParameterString(
        "PASSWORD", tr("Password (empty = pgpass / PGPASSWORD / saved login)"),
        optional=True))
    alg.addParameter(QgsProcessingParameterAuthConfig(
        "AUTHCFG", tr("Saved login (authentication configuration, overrides user/password)"),
        optional=True))


def read_connection(alg, parameters, context):
    """dict(host, port, dbname, user, password) from the standard parameters."""
    conn = {
        "host": alg.parameterAsString(parameters, "HOST", context).strip() or "localhost",
        "port": alg.parameterAsInt(parameters, "PORT", context),
        "dbname": alg.parameterAsString(parameters, "DBNAME", context).strip(),
        "user": (alg.parameterAsString(parameters, "USER", context) or "").strip(),
        "password": alg.parameterAsString(parameters, "PASSWORD", context) or "",
    }
    authcfg = alg.parameterAsString(parameters, "AUTHCFG", context)
    if authcfg:
        cfg = QgsAuthMethodConfig()
        if not QgsApplication.authManager().loadAuthenticationConfig(authcfg, cfg, True):
            raise QgsProcessingException(
                "Authentication configuration {} could not be loaded.".format(authcfg))
        conn["user"] = cfg.config("username") or conn["user"]
        conn["password"] = cfg.config("password") or conn["password"]
    if not conn["dbname"]:
        raise QgsProcessingException("Database name is empty.")
    return conn


def connect(conn):
    """psycopg2 autocommit connection from read_connection()'s dict."""
    import psycopg2
    kwargs = dict(host=conn["host"], port=int(conn["port"]), dbname=conn["dbname"],
                  connect_timeout=10, options="-c client_encoding=UTF8")
    if conn["user"]:
        kwargs["user"] = conn["user"]
    if conn["password"]:
        kwargs["password"] = conn["password"]
    c = psycopg2.connect(**kwargs)
    c.autocommit = True
    return c


def tool_dirs():
    """Folders that may hold pg_dump & co. (the QGIS bin folder ships them)."""
    prefix = QgsApplication.prefixPath()
    return [QgsApplication.applicationDirPath(),
            os.path.normpath(os.path.join(prefix, "..", "..", "bin")),
            os.path.join(prefix, "bin")]
