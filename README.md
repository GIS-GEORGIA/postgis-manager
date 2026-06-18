# PostGIS Manager

**PostGIS Manager** is an open-source hybrid **QGIS plugin** and **standalone desktop application** that fills the gap QGIS leaves in **spatial database management and exploitation**. It is **not** a QGIS clone, and **not** a pgAdmin clone — it focuses on the unique workflows GIS people need when PostGIS *is* the working environment, not just a storage backend.

Available in **English** and **Georgian (ქართული)** with **Light** and **Dark** themes.

> Developed by [GIS GEORGIA](https://github.com/GIS-GEORGIA) | Giorgi Kapanadze  
> Website: [pg.qgis.ge](https://pg.qgis.ge) · License: GNU GPL v2+

---

## Why PostGIS Manager?

QGIS is great at *displaying* PostGIS data; pgAdmin is great at *administering* a Postgres server. Neither is good at the **spatial-database-as-a-GIS-workspace** middle ground — editing geometry straight into the DB, auditing CRS consistency across hundreds of tables, building a routing network without writing SQL, scoring the spatial quality of a dataset, or pulling field GPS tracks straight into PostGIS.

That middle ground is what PostGIS Manager owns.

### ⭐ Unique GIS tools (what sets us apart)

| Tool | What it does | Why it's unique |
|------|--------------|-----------------|
| 🗺 **Map Viewer + Geometry Editor** | Renders PostGIS layers on a built-in canvas; draw point/line/polygon and write geometry **directly into the table** via `ST_GeomFromText` + `ST_Transform` | Edit DB geometry without round-tripping through QGIS layers |
| 🌐 **CRS Browser & Reproject** | Search `spatial_ref_sys`, inspect WKT/Proj4, reproject a whole layer in place | CRS work as a first-class GUI task |
| 📐 **CRS Audit** | Scans every geometry column, compares declared vs. actual SRID, flags mismatches, one-click `UpdateGeometrySRID` fix | Catches the #1 silent PostGIS data bug across an entire database |
| ⚡ **Spatial Join GUI** | 7 predicates (Intersects, Contains, Within, Overlaps, Touches, Crosses, DWithin) + KNN nearest — no SQL required | Visual spatial joins, with SQL preview to learn from |
| 🩺 **Spatial Data Quality Dashboard** | 7 checks (null/invalid/empty geom, wrong SRID, duplicates, outliers, self-intersections), a 0–100 quality score, and auto-fix (`ST_MakeValid`, dedup) | A "data health report" no other PostGIS GUI offers |
| 🕸 **pgRouting Network Wizard** | Inspects a table, installs pgRouting, builds topology (`pgr_createTopology`), and generates drive-time isochrones — step by step | Build a routable network with zero SQL |
| 🌊 **WFS Connector** | OGC WFS 2.0 GetCapabilities/GetFeature with CQL filters, streamed straight onto the map | Pull external OGC services next to your DB layers |
| 🧭 **GPX / KML Importer** | Field GPS tracks, waypoints and routes → PostGIS with Z + timestamps and metadata | Direct field-to-database pipeline |
| 🎨 **Thematic Style Generator** | Natural Breaks (Jenks), Quantile, Equal Interval classification with color ramps, applied to the map | Cartographic classification on live DB data |
| 📸 **Layer Snapshot + Change Diff** | Snapshot a layer to a timestamped table; spatially diff two snapshots (added / removed / geometry-changed) | Track how spatial data evolves over time |

These sit alongside a full set of **production database tools** below.

---

## Full feature set

### 🔌 Connection & Data
- Multiple named connection profiles (host, port, DB, user, SSL, timeout) with auto-save
- PostGIS / pgRouting / Topology / Raster extension detection on connect
- **Layer Browser** — schemas → vector / raster / views, color-coded geometry types
- **Attribute Table** — pagination, inline editing with DB commit, multi-row delete, filter builder, CSV export
- **SQL Editor** — syntax highlighting, query history, templates, **Show on Map** for any geometry result
- **Query Builder** — visual filter builder with SQL preview
- **DB Health** — GIST index coverage, geometry validity, per-table VACUUM/ANALYZE

### 📥 Import / Export
- **Vector:** Shapefile, GeoJSON, GeoPackage, KML, GML — auto CRS detection, append/replace/create
- **Raster:** `raster2pgsql` wrapper (tile size, SRID, overview, constraints)
- **GPX/KML:** GPS field data with Z values and metadata
- **Export:** GeoPackage, Shapefile, GeoJSON, CSV with target-SRID reprojection
- **Backup / Restore:** `pg_dump` / `pg_restore` / `psql` GUI with live progress

### 🔲 Spatial Analysis
- Server-side Buffer, Simplify, Convex Hull, Centroid, Union
- pgRouting (Dijkstra, Driving Distance, Isochrone), Topology editor
- Linear referencing / Chainage (point-along-line, M-values)
- Geometry validation (invalid, null, duplicate, self-intersecting, wrong SRID)

### 🛠 Database Design
- **Table Designer** — create/alter tables, columns, indexes, maintenance
- **Schema & Role Manager** — schemas, roles, GRANT/REVOKE with GUI
- **Function Browser** — browse, view source, execute functions
- **Materialized Views** — create, refresh (incl. CONCURRENTLY), drop, preview

### 🗺 Advanced
- **Raster Tools** — statistics, histogram, map algebra (NDVI, threshold, custom), reproject
- **Style Manager** — save/load QML to `layer_styles` (QGIS-compatible)
- **Versioning** — row-level commit/checkout/diff via pgVersion
- **3DCityDB** — auto-detect schema, feature counts, SQL templates

### 📡 Connectivity & Ops
- **Publishing** — pg_featureserv / pg_tileserv config & launch
- **QGIS Bridge** — push DB connection straight into QGIS
- **DB Setup** — guided PostGIS database provisioning
- **Security / Audit** — role review, audit logging
- **Instances** — Docker/Podman PostGIS container launcher + Docker Compose stack generator
- **Network Scan** — LAN discovery of PostgreSQL servers

### 📊 Monitor & More
- **DB Dashboard / Monitor** — live `pg_stat_*` metrics
- **Automation**, **Developer tools**, **AI Assistant** (SQL generation), **API Tester**, **Point Cloud**

---

## Architecture — Hybrid QGIS Plugin + Standalone

```
postgis_manager/        ← Core package (no QGIS dependency)
  db/                   ← DBManager, queries, PostGIS helpers
  ui/                   ← PyQt6 panels, dialogs, sidebar navigation
  i18n/                 ← en.json, ka.json (English, Georgian)
  utils/                ← theme, config, i18n engine

qgis_plugin/            ← Thin QGIS 4.x / Qt6 plugin wrapper
  plugin.py             ← classFactory, initGui, unload
  processing_provider.py ← QGIS Processing algorithms
  algorithms/           ← processing algorithms
  metadata.txt          ← supportsQt6=True, qgisMinimumVersion=3.34

standalone/             ← Standalone launcher
  app.py                ← QApplication + MainWindow
```

The same `MainWindow` runs both inside QGIS (floating window, with `iface` for layer loading) and standalone (its own `QApplication`). The UI uses a **sidebar navigation + stacked panel** layout — pgAdmin/DBeaver style — instead of flat tabs.

---

## Installation

### QGIS Plugin — via GIS GEORGIA repository (recommended)

1. QGIS → **Plugins → Manage and Install Plugins → Settings → Add**
2. Name: `GIS GEORGIA`, URL: `https://plugins.qgis.ge/plugins.xml`
3. **OK** → find **PostGIS Manager** in the plugin list → Install

### QGIS Plugin — from ZIP

```bash
python make_plugin_zip.py          # builds postgis_manager_plugin.zip
```
Then in QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.

### Standalone — Windows

```bat
git clone https://github.com/GIS-GEORGIA/postgis-manager.git
cd postgis-manager
install.bat        ← creates .venv and installs dependencies
run.bat            ← launch the app
```

### Standalone — Linux / macOS

```bash
git clone https://github.com/GIS-GEORGIA/postgis-manager.git
cd postgis-manager
chmod +x install.sh run.sh
./install.sh
./run.sh
```

---

## Requirements

| Package | Version | Purpose |
|---------|---------|---------|
| PyQt6 | ≥6.4 | GUI (provided by QGIS 4.x in plugin mode) |
| psycopg2-binary | ≥2.9 | PostgreSQL adapter |
| geopandas | ≥0.13 | Geospatial data I/O |
| shapely | ≥2.0 | Geometry operations |
| pyproj | ≥3.5 | CRS transformations |
| fiona | ≥1.9 | Vector file I/O |

For raster import: **`raster2pgsql`** must be in PATH (PostGIS client tools).

---

## Languages

| Code | Language | Status |
|------|----------|--------|
| `en` | English | ✔ Complete |
| `ka` | ქართული (Georgian) | ✔ Complete |

Switch at runtime via the **Lang** dropdown in the toolbar or the **Settings** dialog.

---

## Third-Party Acknowledgements

This project was built with inspiration and reference code from a number of open-source QGIS plugins and PostGIS tools.

We are grateful to every author of those projects. **If any rights-holder finds an issue with how their code is represented here, please open an issue or contact us — we will promptly address it, up to and including removing the relevant code.**

See [CREDITS.md](CREDITS.md) for the full list with repository links, authors, and licenses.

---

## Contributing

We would be very happy to have collaborators. Whether it is a bug fix, a new feature, a translation, or documentation — every contribution is welcome.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes
4. Open a Pull Request

Bug reports and feature requests: [GitHub Issues](https://github.com/GIS-GEORGIA/postgis-manager/issues)

---

## License

GNU General Public License v2 or later — see [LICENSE](LICENSE)

© 2026 GIS GEORGIA | Giorgi Kapanadze
