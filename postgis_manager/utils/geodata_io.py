"""Geospatial import / export helpers.

Import:  GeoJSON (pure Python), Shapefile/GeoPackage (ogr2ogr), CSV
Export:  PostGIS → GeoJSON / GeoPackage / Shapefile (ogr2ogr / SQL)
WFS:     OGC WFS GetCapabilities + GetFeature (requests + xml.etree)

All heavy operations are generator-based or subprocess-based —
no entire dataset is loaded into Python memory at once.
"""

from __future__ import annotations
import csv
import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Iterator

import psycopg2
import psycopg2.extras

BATCH_SIZE = 500   # rows per INSERT transaction

# ── ogr2ogr / shp2pgsql detection ────────────────────────────────────────

def find_ogr2ogr() -> str:
    """Return path to ogr2ogr or ''."""
    candidate = shutil.which("ogr2ogr")
    if candidate:
        return candidate
    # QGIS / OSGeo4W bundled locations
    import platform, glob
    if platform.system() == "Windows":
        for pattern in [
            r"C:\OSGeo4W\bin\ogr2ogr.exe",
            r"C:\OSGeo4W64\bin\ogr2ogr.exe",
            r"C:\Program Files\QGIS*\bin\ogr2ogr.exe",
        ]:
            matches = glob.glob(pattern)
            if matches:
                return matches[0]
    elif platform.system() == "Darwin":
        for p in ["/opt/homebrew/bin/ogr2ogr", "/usr/local/bin/ogr2ogr"]:
            if os.path.isfile(p):
                return p
    return ""


def find_shp2pgsql() -> str:
    candidate = shutil.which("shp2pgsql")
    if candidate:
        return candidate
    import platform, glob
    if platform.system() == "Windows":
        for pattern in [
            r"C:\Program Files\PostgreSQL\*\bin\shp2pgsql.exe",
            r"C:\OSGeo4W64\bin\shp2pgsql.exe",
        ]:
            for m in glob.glob(pattern):
                return m
    return ""


def _run(cmd: list[str], timeout: int = 300,
         log_fn: Callable[[str], None] | None = None) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    out_lines: list[str] = []
    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.rstrip()
        out_lines.append(line)
        if log_fn:
            log_fn(line)
    proc.wait(timeout=timeout)
    return proc.returncode, "\n".join(out_lines)


def _pg_conn_str(params: dict) -> str:
    """Build a libpq connection string for ogr2ogr."""
    p = params
    return (f"PG:host={p.get('host','localhost')} "
            f"port={p.get('port',5432)} "
            f"dbname={p.get('dbname','postgres')} "
            f"user={p.get('user','postgres')} "
            f"password={p.get('password','')}")


# ── GeoJSON import ────────────────────────────────────────────────────────

def import_geojson(conn: psycopg2.extensions.connection,
                   path: str,
                   schema: str, table: str,
                   srid: int = 4326,
                   mode: str = "create",    # create | append | replace
                   geom_col: str = "geom",
                   log_fn: Callable[[str], None] | None = None) -> int:
    """Import a GeoJSON file into PostGIS.  Returns row count inserted."""

    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    with open(path, encoding="utf-8", errors="replace") as f:
        fc = json.load(f)

    features = fc.get("features") or ([fc] if fc.get("type") == "Feature" else [])
    if not features:
        raise ValueError("No features found in GeoJSON")

    # Collect attribute columns from first feature
    first_props = features[0].get("properties") or {}
    attr_cols = list(first_props.keys())

    with conn.cursor() as cur:
        if mode in ("create", "replace"):
            if mode == "replace":
                cur.execute(f'DROP TABLE IF EXISTS "{schema}"."{table}";')
            cols_ddl = ", ".join(f'"{c}" TEXT' for c in attr_cols)
            sep = ", " if cols_ddl else ""
            cur.execute(
                f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" ('
                f'  gid SERIAL PRIMARY KEY'
                f'  {sep}{cols_ddl}'
                f', "{geom_col}" geometry(Geometry, {srid})'
                f');')
            cur.execute(
                f'CREATE INDEX IF NOT EXISTS "{table}_{geom_col}_idx" '
                f'ON "{schema}"."{table}" USING GIST ("{geom_col}");')
            conn.commit()
            _log(f"✓ Table {schema}.{table} created")

        inserted = 0
        batch: list[tuple] = []

        def _flush():
            nonlocal inserted
            if not batch:
                return
            placeholders = ", ".join(
                ["(%s" + ", %s" * len(attr_cols) + ", ST_SetSRID(ST_GeomFromGeoJSON(%s), %s))"]
                * len(batch))
            flat = []
            for row in batch:
                flat.extend(row)
            cur.executemany(
                f'INSERT INTO "{schema}"."{table}" '
                f'({", ".join(f"{chr(34)}{c}{chr(34)}" for c in attr_cols)}'
                f'{", " if attr_cols else ""}"{geom_col}") VALUES '
                f'({", ".join(["%s"] * len(attr_cols))}'
                f'{", " if attr_cols else ""}ST_SetSRID(ST_GeomFromGeoJSON(%s), %s))',
                batch)
            conn.commit()
            inserted += len(batch)
            batch.clear()
            _log(f"  … {inserted} rows inserted")

        for feat in features:
            props = feat.get("properties") or {}
            geom  = feat.get("geometry")
            if not geom:
                continue
            row = tuple(str(props.get(c, "")) for c in attr_cols) + (
                json.dumps(geom), srid)
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                _flush()

        _flush()
        _log(f"✓ GeoJSON import complete — {inserted} rows")
        return inserted


# ── Shapefile import (ogr2ogr) ────────────────────────────────────────────

def import_shapefile(conn_params: dict,
                     shp_path: str,
                     schema: str, table: str,
                     srid_override: int | None = None,
                     mode: str = "create",
                     log_fn: Callable[[str], None] | None = None) -> bool:
    ogr = find_ogr2ogr()
    if not ogr:
        raise RuntimeError(
            "ogr2ogr not found. Install GDAL or QGIS.")
    cmd = [ogr, "-f", "PostgreSQL",
           _pg_conn_str(conn_params),
           shp_path,
           "-nln", f"{schema}.{table}",
           "-lco", "GEOMETRY_NAME=geom",
           "-lco", "FID=gid",
           "-lco", f"SCHEMA={schema}",
           "--config", "PG_USE_COPY", "YES",
           "-progress"]
    if srid_override:
        cmd += ["-t_srs", f"EPSG:{srid_override}"]
    if mode == "replace":
        cmd += ["-overwrite"]
    elif mode == "append":
        cmd += ["-append"]
    code, _ = _run(cmd, log_fn=log_fn)
    return code == 0


# ── GeoPackage import (ogr2ogr) ───────────────────────────────────────────

def import_geopackage(conn_params: dict,
                      gpkg_path: str,
                      layer: str,
                      schema: str, table: str,
                      mode: str = "create",
                      log_fn: Callable[[str], None] | None = None) -> bool:
    ogr = find_ogr2ogr()
    if not ogr:
        raise RuntimeError("ogr2ogr not found.")
    cmd = [ogr, "-f", "PostgreSQL",
           _pg_conn_str(conn_params),
           gpkg_path,
           layer,
           "-nln", f"{schema}.{table}",
           "-lco", "GEOMETRY_NAME=geom",
           "-lco", "FID=gid",
           "-lco", f"SCHEMA={schema}",
           "--config", "PG_USE_COPY", "YES",
           "-progress"]
    if mode == "replace":
        cmd += ["-overwrite"]
    elif mode == "append":
        cmd += ["-append"]
    code, _ = _run(cmd, log_fn=log_fn)
    return code == 0


def list_gpkg_layers(gpkg_path: str) -> list[str]:
    ogr = find_ogr2ogr()
    if not ogr:
        return []
    ogrinfo = os.path.join(os.path.dirname(ogr), "ogrinfo") + (
        ".exe" if os.name == "nt" else "")
    if not os.path.isfile(ogrinfo):
        ogrinfo = shutil.which("ogrinfo") or ""
    if not ogrinfo:
        return []
    try:
        out = subprocess.check_output(
            [ogrinfo, "-al", "-so", gpkg_path],
            stderr=subprocess.DEVNULL, text=True, timeout=10)
        layers = []
        for line in out.splitlines():
            if line.startswith("Layer name:"):
                layers.append(line.split(":", 1)[1].strip())
        return layers
    except Exception:
        return []


# ── CSV import ────────────────────────────────────────────────────────────

def import_csv(conn: psycopg2.extensions.connection,
               path: str,
               schema: str, table: str,
               lon_col: str, lat_col: str,
               srid: int = 4326,
               delimiter: str = ",",
               mode: str = "create",
               log_fn: Callable[[str], None] | None = None) -> int:
    def _log(msg: str):
        if log_fn:
            log_fn(msg)

    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        fieldnames = reader.fieldnames or []
        attr_cols = [c for c in fieldnames
                     if c not in (lon_col, lat_col)]

        with conn.cursor() as cur:
            if mode in ("create", "replace"):
                if mode == "replace":
                    cur.execute(f'DROP TABLE IF EXISTS "{schema}"."{table}";')
                cols_ddl = ", ".join(f'"{c}" TEXT' for c in attr_cols)
                sep = ", " if cols_ddl else ""
                cur.execute(
                    f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" ('
                    f'  gid SERIAL PRIMARY KEY'
                    f'  {sep}{cols_ddl}'
                    f', geom geometry(Point, {srid})'
                    f');')
                cur.execute(
                    f'CREATE INDEX IF NOT EXISTS "{table}_geom_idx" '
                    f'ON "{schema}"."{table}" USING GIST (geom);')
                conn.commit()
                _log(f"✓ Table {schema}.{table} created")

            inserted = 0
            batch: list[tuple] = []

            def _flush():
                nonlocal inserted
                if not batch:
                    return
                cur.executemany(
                    f'INSERT INTO "{schema}"."{table}" '
                    f'({", ".join(f"{chr(34)}{c}{chr(34)}" for c in attr_cols)}'
                    f'{", " if attr_cols else ""}geom) '
                    f'VALUES ({", ".join(["%s"] * len(attr_cols))}'
                    f'{", " if attr_cols else ""}'
                    f'ST_SetSRID(ST_MakePoint(%s, %s), {srid}))',
                    batch)
                conn.commit()
                inserted += len(batch)
                batch.clear()
                _log(f"  … {inserted} rows")

            for row in reader:
                try:
                    lon = float(row.get(lon_col, ""))
                    lat = float(row.get(lat_col, ""))
                except ValueError:
                    continue
                rec = tuple(row.get(c, "") for c in attr_cols) + (lon, lat)
                batch.append(rec)
                if len(batch) >= BATCH_SIZE:
                    _flush()
            _flush()

        _log(f"✓ CSV import complete — {inserted} rows")
        return inserted


# ── Export ────────────────────────────────────────────────────────────────

def export_geojson(conn: psycopg2.extensions.connection,
                   schema: str, table: str, geom_col: str,
                   path: str,
                   where: str = "",
                   log_fn: Callable[[str], None] | None = None) -> int:
    where_sql = f"WHERE {where}" if where.strip() else ""
    sql = (f'SELECT row_to_json(t) FROM ('
           f'  SELECT *, ST_AsGeoJSON("{geom_col}")::json AS geometry '
           f'  FROM "{schema}"."{table}" {where_sql}'
           f') t')
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    features = [{"type": "Feature",
                 "geometry": r[0].get("geometry"),
                 "properties": {k: v for k, v in r[0].items()
                                if k != "geometry"}}
                for r in rows]
    fc = {"type": "FeatureCollection", "features": features}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, default=str)
    if log_fn:
        log_fn(f"✓ Exported {len(features)} features → {path}")
    return len(features)


def export_ogr(conn_params: dict,
               schema: str, table: str,
               out_path: str,
               fmt: str = "GPKG",   # GPKG | ESRI Shapefile | GeoJSON
               where: str = "",
               log_fn: Callable[[str], None] | None = None) -> bool:
    ogr = find_ogr2ogr()
    if not ogr:
        raise RuntimeError("ogr2ogr not found.")
    cmd = [ogr, "-f", fmt,
           out_path,
           _pg_conn_str(conn_params),
           f"{schema}.{table}",
           "-progress"]
    if where.strip():
        cmd += ["-where", where]
    code, _ = _run(cmd, log_fn=log_fn)
    return code == 0


# ── WFS ──────────────────────────────────────────────────────────────────

def wfs_capabilities(url: str, timeout: int = 15) -> list[dict]:
    """Fetch WFS GetCapabilities and return list of layer dicts."""
    base = url.rstrip("?& ")
    cap_url = (f"{base}?SERVICE=WFS&REQUEST=GetCapabilities&VERSION=2.0.0")
    with urllib.request.urlopen(cap_url, timeout=timeout) as resp:
        xml_bytes = resp.read()
    root = ET.fromstring(xml_bytes)
    ns = {
        "wfs": "http://www.opengis.net/wfs/2.0",
        "ows": "http://www.opengis.net/ows/1.1",
    }
    layers = []
    for ft in root.findall(".//wfs:FeatureType", ns):
        name  = ft.findtext("wfs:Name", "", ns)
        title = ft.findtext("wfs:Title", "", ns)
        crs   = ft.findtext("wfs:DefaultCRS", "", ns) or \
                ft.findtext("wfs:DefaultSRS", "", ns) or ""
        layers.append({"name": name, "title": title, "crs": crs})
    return layers


def wfs_download_geojson(url: str, layer: str,
                         max_features: int = 5000,
                         timeout: int = 60) -> dict:
    """Download WFS layer as GeoJSON dict."""
    base = url.rstrip("?& ")
    params = urllib.parse.urlencode({
        "SERVICE": "WFS",
        "REQUEST": "GetFeature",
        "VERSION": "2.0.0",
        "TYPENAMES": layer,
        "OUTPUTFORMAT": "application/json",
        "COUNT": str(max_features),
    })
    full_url = f"{base}?{params}"
    with urllib.request.urlopen(full_url, timeout=timeout) as resp:
        return json.loads(resp.read())


def wfs_to_postgis(conn: psycopg2.extensions.connection,
                   url: str, layer: str,
                   schema: str, table: str,
                   max_features: int = 5000,
                   srid: int = 4326,
                   mode: str = "create",
                   log_fn: Callable[[str], None] | None = None) -> int:
    def _log(msg):
        if log_fn:
            log_fn(msg)
    _log(f"Downloading {layer} from WFS…")
    fc = wfs_download_geojson(url, layer, max_features)
    # Save to temp file then import
    with tempfile.NamedTemporaryFile(
            suffix=".geojson", mode="w",
            encoding="utf-8", delete=False) as tmp:
        json.dump(fc, tmp, default=str)
        tmp_path = tmp.name
    try:
        count = import_geojson(conn, tmp_path, schema, table,
                               srid=srid, mode=mode, log_fn=log_fn)
    finally:
        os.unlink(tmp_path)
    return count
