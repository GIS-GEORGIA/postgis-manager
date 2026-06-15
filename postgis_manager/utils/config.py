"""Persistent config — connections, UI preferences, saved queries."""

import json
import os
from typing import Any

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".postgis_manager", "config.json")


def _defaults() -> dict:
    return {
        "language": "en",
        "theme": "light",
        "font_family": "Segoe UI",
        "font_size": 13,
        "max_rows": 1000,
        "confirm_delete": True,
        "auto_connect": None,
        "log_level": "info",
        "window_geometry": None,
        "splitter_sizes": {},
        "connections": [],
        "saved_queries": [],
        "recent_files": [],
    }


_data: dict = {}


def load() -> None:
    global _data
    _data = _defaults()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                _data.update(json.load(f))
        except Exception:
            pass


def save() -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False, indent=2)


def get(key: str, default: Any = None) -> Any:
    return _data.get(key, default)


def set(key: str, value: Any) -> None:
    _data[key] = value
    save()


# ── Connection profiles ──────────────────────────────────────────────────────

def get_connections() -> list[dict]:
    return _data.get("connections", [])


def save_connection(profile: dict) -> None:
    conns = get_connections()
    existing = next((i for i, c in enumerate(conns) if c.get("name") == profile.get("name")), None)
    if existing is not None:
        conns[existing] = profile
    else:
        conns.append(profile)
    _data["connections"] = conns
    save()


def delete_connection(name: str) -> None:
    _data["connections"] = [c for c in get_connections() if c.get("name") != name]
    save()


# ── Saved queries ────────────────────────────────────────────────────────────

def get_saved_queries() -> list[dict]:
    return _data.get("saved_queries", [])


def save_query(name: str, sql: str) -> None:
    queries = get_saved_queries()
    existing = next((i for i, q in enumerate(queries) if q.get("name") == name), None)
    entry = {"name": name, "sql": sql}
    if existing is not None:
        queries[existing] = entry
    else:
        queries.append(entry)
    _data["saved_queries"] = queries
    save()


def delete_query(name: str) -> None:
    _data["saved_queries"] = [q for q in get_saved_queries() if q.get("name") != name]
    save()


# ── Recent files ─────────────────────────────────────────────────────────────

def add_recent_file(path: str) -> None:
    recent = _data.get("recent_files", [])
    if path in recent:
        recent.remove(path)
    recent.insert(0, path)
    _data["recent_files"] = recent[:20]
    save()


# ── Config import / export ───────────────────────────────────────────────────

def export_config(dest_path: str, include_passwords: bool = False) -> None:
    """Export the full config to a JSON file (optionally strip passwords)."""
    export_data = dict(_data)
    if not include_passwords:
        conns = []
        for c in export_data.get("connections", []):
            c2 = dict(c)
            c2["password"] = ""
            conns.append(c2)
        export_data["connections"] = conns
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    with open(dest_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)


def import_config(src_path: str, merge: bool = True) -> dict:
    """
    Import config from a JSON file.
    merge=True  → overlay onto current config (connections are merged by name).
    merge=False → replace everything (except window_geometry).
    Returns the imported data dict.
    """
    with open(src_path, encoding="utf-8") as f:
        imported: dict = json.load(f)

    if merge:
        # Merge connections by name
        existing_names = {c["name"] for c in get_connections()}
        for c in imported.get("connections", []):
            if c.get("name") not in existing_names:
                _data.setdefault("connections", []).append(c)
        # Merge saved queries by name
        existing_qnames = {q["name"] for q in get_saved_queries()}
        for q in imported.get("saved_queries", []):
            if q.get("name") not in existing_qnames:
                _data.setdefault("saved_queries", []).append(q)
        # Merge top-level prefs (skip connection lists already handled)
        skip = {"connections", "saved_queries", "window_geometry"}
        for k, v in imported.items():
            if k not in skip:
                _data[k] = v
    else:
        geo = _data.get("window_geometry")
        _data.clear()
        _data.update(_defaults())
        _data.update(imported)
        if geo:
            _data["window_geometry"] = geo

    save()
    return imported


load()
