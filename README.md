# PostGIS Manager

**PostGIS Manager** is a powerful, open-source hybrid **QGIS plugin** and **standalone desktop application** for managing PostGIS spatial databases. Available in **English** and **Georgian (ქართული)** with **Light** and **Dark** themes.

> Developed by [GIS GEORGIA](https://github.com/GIS-GEORGIA) | Giorgi Kapanadze  
> License: GNU GPL v2+

---

## Features

### 🔌 Connection Management
- Multiple named connection profiles (host, port, DB, user, SSL mode)
- Auto-save credentials, test connection with version info
- PostGIS, pgRouting, Topology, Raster extension detection

### 📋 Layer Browser
- Tree view: schemas → vector layers / rasters / views
- Color-coded geometry types (Point, Line, Polygon…)
- Right-click context menu: attribute table, import, QGIS load, delete
- Full schema bulk-load into QGIS

### 📊 Attribute Table
- Pagination (100–2500 rows per page), inline cell editing with DB commit
- Multi-row delete, SQL Filter Builder, CSV export

### 🖊 SQL Editor
- Syntax highlighting, query history & saved queries
- Template toolbar (SELECT *, COUNT, Extent, Validate…)
- Results table with CSV export, F5 shortcut to run

### 📥 Import / Export
- **Vector:** Shapefile, GeoJSON, GeoPackage, KML, GML — auto CRS detection, append/replace/create modes
- **Raster:** `raster2pgsql` wrapper with tile size, SRID, overview and constraint options
- **Export:** GeoPackage, Shapefile, GeoJSON, CSV with target SRID reprojection

### 🛣 pgRouting
- Dijkstra shortest path, Driving Distance / Isochrone
- Result table with load-to-QGIS button

### 🔗 Topology Editor
- Create topologies, validate and inspect topology errors

### 📏 Linear Referencing / Chainage
- Point features along lines at fixed intervals, M-value assignment

### 🎨 Style Manager
- Save/load QML styles to `public.layer_styles` — compatible with QGIS native style storage

### ⚙ Geoprocessing & Validation
- Server-side Buffer, Simplify, Convex Hull, Centroid, Union
- Geometry validation: invalid, null, duplicate, self-intersecting, wrong SRID

### 🛠 Database Design
- **Table Designer** — create/alter tables, manage columns, indexes, maintenance
- **Schema & Role Manager** — schemas, roles, GRANT/REVOKE with GUI
- **Function Browser** — browse, view source, and execute functions
- **Materialized Views** — create, refresh (including CONCURRENTLY), drop, preview

### 🗺 Raster Tools
- Statistics, histogram, map algebra (NDVI, threshold, custom), reproject

### 📊 DB Dashboard
- Live server metrics: connections, cache hit ratio, transaction rate, active queries, table sizes, unused indexes

### 💾 Backup / Restore
- `pg_dump` / `pg_restore` / `psql` wrappers with format selection and live progress

### 🕑 Versioning
- Row-level commit/checkout/diff via pgVersion, branch support

### 🖥 Instance & Network Management
- Docker/Podman instance launcher, LAN network scanner for PostgreSQL discovery

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
  algorithms/           ← 6 processing algorithms
  metadata.txt          ← supportsQt6=True, qgisMinimumVersion=3.34

standalone/             ← Standalone launcher
  app.py                ← QApplication + MainWindow
```

The same `MainWindow` runs both inside QGIS (floating window, with `iface` for layer loading) and standalone (its own `QApplication`). The UI uses a **sidebar navigation + stacked panel** layout — pgAdmin/DBeaver style — instead of flat tabs.

---

## Installation

### Standalone — Windows (one-time setup)

```bat
git clone https://github.com/GIS-GEORGIA/postgis-manager.git
cd postgis-manager
install.bat        ← creates .venv and installs dependencies
run.bat            ← launch the app (every time)
```

### Standalone — Linux / macOS (one-time setup)

```bash
git clone https://github.com/GIS-GEORGIA/postgis-manager.git
cd postgis-manager
chmod +x install.sh run.sh
./install.sh       # creates .venv and installs dependencies
./run.sh           # launch the app (every time)
```

### QGIS Plugin (QGIS 3.34+ / QGIS 4.x)

```bash
python make_plugin_zip.py          # builds postgis_manager_plugin.zip
```

Then in QGIS: **Plugins → Manage and Install Plugins → Install from ZIP** → select the file.

Or manually: copy the `postgis_manager/` folder into your QGIS plugins directory and enable via Plugin Manager.

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

For raster import: **`raster2pgsql`** must be in PATH (part of PostGIS client tools).

---

## Languages

| Code | Language | Status |
|------|----------|--------|
| `en` | English | ✔ Complete |
| `ka` | ქართული (Georgian) | ✔ Complete |

Switch at runtime via the **Lang** dropdown in the toolbar or the **Settings** dialog.

---

## Third-Party Acknowledgements

This project was built with inspiration and reference code from a number of open-source QGIS plugins and PostGIS tools — some of which were used as starting points or structural references during development.

We are grateful to every author of those projects. **If any rights-holder finds an issue with how their code is represented here, please open an issue or contact us — we will promptly address it, up to and including removing the relevant code.**

See [CREDITS.md](CREDITS.md) for the full list of referenced projects with repository links, authors, and licenses.

---

## Contributing

We would be very happy to have collaborators on this project. Whether it is a bug fix, a new feature, a translation, or documentation — every contribution is welcome.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes
4. Open a Pull Request

Bug reports and feature requests: [GitHub Issues](https://github.com/GIS-GEORGIA/postgis-manager/issues)

---

## License

GNU General Public License v2 or later — see [LICENSE](LICENSE)

© 2026 GIS GEORGIA | Giorgi Kapanadze
