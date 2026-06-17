"""PostGIS Manager project files (.pgmgr).

A project stores:
  - connection profile name
  - map viewer layer stack (order, visibility, color)
  - active basemap
  - canvas viewport extent
"""

from __future__ import annotations
import json
import os
from typing import Any

PROJECT_EXT = ".pgmgr"
PROJECT_FILTER = "PostGIS Manager Project (*.pgmgr);;All files (*)"
FORMAT_VERSION = 1

RECENT_KEY = "recent_projects"


def save(path: str, data: dict) -> None:
    """Write project dict to *path* (creates parent dirs)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = {"version": FORMAT_VERSION, **data}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load(path: str) -> dict:
    """Read and return project dict from *path*."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data


def add_recent(path: str) -> None:
    from . import config
    recent: list[str] = config.get(RECENT_KEY, [])
    path = os.path.abspath(path)
    if path in recent:
        recent.remove(path)
    recent.insert(0, path)
    config.set(RECENT_KEY, recent[:10])


def get_recent() -> list[str]:
    from . import config
    return [p for p in config.get(RECENT_KEY, []) if os.path.isfile(p)]
