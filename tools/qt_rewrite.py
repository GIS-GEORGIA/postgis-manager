#!/usr/bin/env python3
"""Rewrite direct PyQt5/PyQt6 imports to go through postgis_manager.qtcompat.

Inside the postgis_manager package the import has to be relative — the QGIS plugin
zip nests the package as postgis_manager/postgis_manager/, so an absolute
"postgis_manager.qtcompat" would resolve to the outer plugin package instead.
Everywhere else (standalone/, tests/) the absolute form is correct.

    python tools/qt_rewrite.py           # report
    python tools/qt_rewrite.py --fix     # rewrite
"""

from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "postgis_manager"
QTCOMPAT = "qtcompat"

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "build", "dist"}

IMPORT_RE = re.compile(r"^(\s*)from PyQt[56]\.(Qt[A-Za-z]+) import ")


def iter_py_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py") and name != os.path.basename(__file__):
                yield os.path.join(dirpath, name)


def import_prefix(path: str) -> str:
    """The 'from X' spelling of qtcompat as seen from this file."""
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    if not rel.startswith(PACKAGE + "/"):
        return f"{PACKAGE}.{QTCOMPAT}"
    # depth of the file's own package below postgis_manager/
    inner = rel[len(PACKAGE) + 1 :]
    depth = inner.count("/")  # 0 for postgis_manager/foo.py, 2 for .../ui/panels/foo.py
    return "." * (depth + 1) + QTCOMPAT


def rewrite(path: str, fix: bool) -> list[str]:
    with open(path, encoding="utf-8", newline="") as fh:
        original = fh.read()

    prefix = import_prefix(path)
    changes = []
    out = []
    for lineno, line in enumerate(original.splitlines(keepends=True), 1):
        m = IMPORT_RE.match(line)
        if m:
            indent, module = m.group(1), m.group(2)
            new = IMPORT_RE.sub(f"{indent}from {prefix}.{module} import ", line, count=1)
            changes.append(f"{os.path.relpath(path, ROOT)}:{lineno}: {line.strip()}")
            out.append(new)
        else:
            out.append(line)

    if fix and changes:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(out))
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()

    total = 0
    files = 0
    for path in iter_py_files():
        changes = rewrite(path, args.fix)
        if changes:
            files += 1
            total += len(changes)
            for c in changes:
                print(("fixed " if args.fix else "") + c)

    print(f"\n{total} import(s) in {files} file(s)")
    leftovers = []
    for path in iter_py_files():
        if os.sep + QTCOMPAT + os.sep in path:
            continue  # the shim is the one place allowed to name a binding
        with open(path, encoding="utf-8", newline="") as fh:
            for lineno, line in enumerate(fh, 1):
                if re.search(r"\bPyQt[56]\b", line.split("#")[0]) and QTCOMPAT not in line:
                    leftovers.append(f"{os.path.relpath(path, ROOT)}:{lineno}: {line.strip()}")
    if leftovers:
        print("\nstill referencing a binding directly:")
        for line in leftovers:
            print("  " + line)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
