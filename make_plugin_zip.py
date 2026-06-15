"""
Build a QGIS-installable plugin zip.

Structure inside the zip:
    postgis_manager/
        __init__.py       (from qgis_plugin/)
        metadata.txt      (from qgis_plugin/)
        icon.png          (from qgis_plugin/)
        plugin.py         (from qgis_plugin/)
        postgis_manager/  (the core package — db/, ui/, utils/, i18n/)
            ...
"""

import os, sys, zipfile, shutil

ROOT    = os.path.dirname(os.path.abspath(__file__))
PLUGIN  = os.path.join(ROOT, "qgis_plugin")
PACKAGE = os.path.join(ROOT, "postgis_manager")
OUT_ZIP = os.path.join(ROOT, "postgis_manager_plugin.zip")

SKIP_DIRS  = {"__pycache__", ".git", ".venv", "node_modules"}
SKIP_EXTS  = {".pyc", ".pyo", ".pyd"}


def should_skip(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return any(p in SKIP_DIRS for p in parts)


def add_tree(zf: zipfile.ZipFile, src_dir: str, zip_prefix: str):
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if os.path.splitext(fname)[1] in SKIP_EXTS:
                continue
            abs_path = os.path.join(dirpath, fname)
            rel      = os.path.relpath(abs_path, src_dir)
            arc_name = os.path.join(zip_prefix, rel).replace("\\", "/")
            zf.write(abs_path, arc_name)


if os.path.exists(OUT_ZIP):
    os.remove(OUT_ZIP)

with zipfile.ZipFile(OUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    # qgis_plugin/ files go into root postgis_manager/
    for fname in os.listdir(PLUGIN):
        src = os.path.join(PLUGIN, fname)
        if os.path.isfile(src) and os.path.splitext(fname)[1] not in SKIP_EXTS:
            zf.write(src, f"postgis_manager/{fname}")

    # core package goes into postgis_manager/postgis_manager/
    add_tree(zf, PACKAGE, "postgis_manager/postgis_manager")

print(f"Created: {OUT_ZIP}")
print(f"Size:    {os.path.getsize(OUT_ZIP):,} bytes")
print()
print("Top-level entries in zip:")
with zipfile.ZipFile(OUT_ZIP) as zf:
    tops = sorted({n.split("/")[0] for n in zf.namelist()})
    for t in tops:
        print(f"  {t}/")
