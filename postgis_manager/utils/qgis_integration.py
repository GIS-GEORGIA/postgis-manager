"""Utilities for reading QGIS connections, auth configs, and project layers.

Works in three modes:
  - Full QGIS plugin  (iface available, QgsProject/QgsSettings accessible)
  - Standalone + QGIS installed (reads QSettings / Windows registry directly)
  - Standalone, no QGIS (returns empty lists gracefully)
"""

from __future__ import annotations
import sys


# ── helpers ──────────────────────────────────────────────────────────────

def _qgis_available() -> bool:
    try:
        import qgis.core  # noqa: F401
        return True
    except ImportError:
        return False


def _qsettings_pg_connections() -> list[dict]:
    """Read PostGIS connections from QGIS QSettings (works in plugin mode
    and standalone if QGIS libs are on the path)."""
    connections = []
    try:
        if _qgis_available():
            from qgis.core import QgsSettings
            s = QgsSettings()
        else:
            from PyQt6.QtCore import QSettings
            # Try QGIS3 then QGIS4 key
            s = None
            for app in ("QGIS3", "QGIS4", "QGIS"):
                s = QSettings("QGIS", app)
                s.beginGroup("PostgreSQL/connections")
                names = s.childGroups()
                s.endGroup()
                if names:
                    break
            if s is None:
                return []

        s.beginGroup("PostgreSQL/connections")
        names = s.childGroups()
        for name in names:
            s.beginGroup(name)
            conn = {
                "name":     name,
                "host":     s.value("host",     "localhost"),
                "port":     s.value("port",     "5432"),
                "dbname":   s.value("database", ""),
                "user":     s.value("username", ""),
                "password": s.value("password", ""),
                "ssl_mode": s.value("sslmode",  "prefer"),
                "source":   "qgis_settings",
            }
            s.endGroup()
            connections.append(conn)
        s.endGroup()
    except Exception:
        pass
    return connections


def _registry_pg_connections() -> list[dict]:
    """Fallback: read directly from Windows registry (no QGIS libs needed)."""
    connections = []
    if sys.platform != "win32":
        return connections
    try:
        import winreg
        roots = [
            r"SOFTWARE\QGIS\QGIS3\Profiles\default\PostgreSQL\connections",
            r"SOFTWARE\QGIS\QGIS4\Profiles\default\PostgreSQL\connections",
        ]
        for root_key in roots:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_key)
            except FileNotFoundError:
                continue
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                    sub = winreg.OpenKey(key, name)

                    def _val(k, field, default=""):
                        try:
                            return winreg.QueryValueEx(k, field)[0]
                        except FileNotFoundError:
                            return default

                    conn = {
                        "name":     name,
                        "host":     _val(sub, "host",     "localhost"),
                        "port":     _val(sub, "port",     "5432"),
                        "dbname":   _val(sub, "database", ""),
                        "user":     _val(sub, "username", ""),
                        "password": _val(sub, "password", ""),
                        "ssl_mode": _val(sub, "sslmode",  "prefer"),
                        "source":   "registry",
                    }
                    connections.append(conn)
                    winreg.CloseKey(sub)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
            if connections:
                break
    except Exception:
        pass
    return connections


# ── Public API ────────────────────────────────────────────────────────────

def get_qgis_pg_connections() -> list[dict]:
    """Return all PostGIS connections defined in QGIS.

    Each dict has: name, host, port, dbname, user, password, ssl_mode, source.
    """
    conns = _qsettings_pg_connections()
    if not conns:
        conns = _registry_pg_connections()
    # Deduplicate by name
    seen = set()
    result = []
    for c in conns:
        if c["name"] not in seen:
            seen.add(c["name"])
            result.append(c)
    return result


def get_qgis_auth_configs() -> list[dict]:
    """Return QGIS auth configurations (basic username/password type only).
    Requires QGIS libs. Returns [] in standalone mode."""
    configs = []
    if not _qgis_available():
        return configs
    try:
        from qgis.core import QgsApplication, QgsAuthMethodConfig
        am = QgsApplication.authManager()
        if am.isDisabled():
            return configs
        cfg_map = {}
        am.availableAuthMethodConfigs(cfg_map)
        for cfg_id, cfg in cfg_map.items():
            if cfg.method() in ("Basic", "basic"):
                full = QgsAuthMethodConfig()
                am.loadAuthenticationConfig(cfg_id, full, True)
                configs.append({
                    "id":       cfg_id,
                    "name":     full.name(),
                    "method":   full.method(),
                    "username": full.configMap().get("username", ""),
                    "password": full.configMap().get("password", ""),
                })
    except Exception:
        pass
    return configs


def get_project_postgis_layers(iface=None) -> list[dict]:
    """Return PostGIS layers from the current QGIS project.

    Each dict: id, name, schema, table, geom_col, srid, host, port,
               dbname, user, password, uri_string.
    Only available in plugin mode (iface or QgsProject accessible).
    """
    layers = []
    if not _qgis_available():
        return layers
    try:
        from qgis.core import QgsProject, QgsVectorLayer, QgsDataSourceUri
        project = QgsProject.instance()
        for layer_id, layer in project.mapLayers().items():
            if not isinstance(layer, QgsVectorLayer):
                continue
            if layer.providerType() not in ("postgres", "postgresraster"):
                continue
            uri = QgsDataSourceUri(layer.dataProvider().dataSourceUri())
            layers.append({
                "id":         layer_id,
                "name":       layer.name(),
                "schema":     uri.schema(),
                "table":      uri.table(),
                "geom_col":   uri.geometryColumn(),
                "srid":       uri.srid(),
                "host":       uri.host(),
                "port":       uri.port() or "5432",
                "dbname":     uri.database(),
                "user":       uri.username(),
                "password":   uri.password(),
                "auth_cfg":   uri.authConfigId(),
                "uri_string": layer.dataProvider().dataSourceUri(),
                "layer_obj":  layer,  # keep reference for QGIS ops
            })
    except Exception:
        pass
    return layers


def save_credentials_to_qgis(name: str, username: str, password: str) -> bool:
    """Save a Basic auth config in QGIS auth manager. Returns True on success."""
    if not _qgis_available():
        return False
    try:
        from qgis.core import QgsApplication, QgsAuthMethodConfig
        am = QgsApplication.authManager()
        if am.isDisabled():
            return False
        cfg = QgsAuthMethodConfig("Basic")
        cfg.setName(name)
        cfg.setConfigMap({"username": username, "password": password})
        return am.storeAuthenticationConfig(cfg)
    except Exception:
        return False
