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
- Full schema bulk-load into QGIS (schema importer pattern)

### 📊 Attribute Table
- Pagination (100–2500 rows per page)
- Inline cell editing with DB commit
- Multi-row delete
- **SQL Filter Builder** — templates for text/number/date/geometry/logic
- CSV export

### 🖊 SQL Editor
- Syntax highlighting (SQL keywords + PostGIS functions)
- Query history & saved queries
- Template toolbar (SELECT *, COUNT, Extent, Validate…)
- Results table with CSV export
- F5 shortcut to run

### 📥 Vector Import
- **Shapefile**, GeoJSON, GeoPackage, KML, GML
- Auto CRS detection & reprojection
- Append / Replace / Create table modes
- Row-level savepoints (bad rows skipped, rest continue)

### 🖼 Raster Import
- `raster2pgsql` wrapper with tile size, SRID, overview options
- Insert / Append / Delete+Insert modes
- Constraints and band index creation

### 🛣 pgRouting Panel
- **Dijkstra** shortest path
- **Driving Distance** / Isochrone
- Result table with load-to-QGIS button

### 🕸 Topology Editor
- List existing topologies
- Create topology with SRID and precision
- **Validate topology** — error table with ID pairs

### 📏 Linear Referencing / Chainage
- Generate point features along lines at fixed intervals
- M-value assignment
- Configurable start offset

### 📦 Export (DB → Files)
- Multi-layer export: **GeoPackage, Shapefile, GeoJSON, CSV**
- Target SRID reprojection
- Batch schema export

### 🎨 DB Style Manager
- Save/load QML styles to `public.layer_styles` table
- Compatible with QGIS native style storage
- Default style flag

### ⚙ Geoprocessing
- Server-side PostGIS operations: **Buffer, Simplify, Convex Hull, Centroid, Union**
- **Geometry Validation** suite — invalid, null, duplicate, self-intersecting, wrong SRID

### 📜 Version Control (pgVersion)
- Row-level commit/checkout/diff
- Branch support
- Full history log

---

## Architecture — Hybrid QGIS Plugin + Standalone

```
postgis_manager/        ← Core package (no QGIS dependency)
  db/                   ← DBManager, queries, PostGIS helpers
  ui/                   ← PyQt5 UI panels and dialogs
  i18n/                 ← en.json, ka.json (English, Georgian)
  utils/                ← theme, config, i18n engine

qgis_plugin/            ← Thin QGIS plugin wrapper
  plugin.py             ← classFactory, initGui, unload
  metadata.txt          ← QGIS plugin registry metadata

standalone/             ← Standalone launcher
  app.py                ← QApplication + MainWindow
```

The same `MainWindow` runs both in QGIS (floating window, with `iface` for layer loading) and standalone (its own `QApplication`).

---

## Installation

### Standalone (Windows / Linux / macOS)

```bash
git clone https://github.com/GIS-GEORGIA/postgis-manager.git
cd postgis-manager
pip install -r requirements.txt
python run.py
```

### QGIS Plugin

1. Clone or download this repository
2. Copy the entire folder into your QGIS plugins directory:
   - **Windows:** `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - **Linux:** `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
3. In QGIS: **Plugins → Manage and Install Plugins → Installed** → enable **PostGIS Manager**
4. A toolbar button and menu entry appear: **Plugins → PostGIS Manager**

---

## Requirements

| Package | Version | Purpose |
|---------|---------|---------|
| PyQt5 | ≥5.15 | GUI (provided by QGIS in plugin mode) |
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

Switch language in: **Toolbar → Lang** dropdown or **Settings dialog**.

---

## Credits & Attributions

This project integrates ideas and patterns from **21 open-source QGIS plugins and PostGIS tools**.  
See [CREDITS.md](CREDITS.md) for the full list with repository links, authors, and licenses.

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes
4. Push and open a Pull Request

Issues and feature requests: [GitHub Issues](https://github.com/GIS-GEORGIA/postgis-manager/issues)

---

## License

GNU General Public License v2 or later — see [LICENSE](LICENSE)

© 2024 GIS GEORGIA | Giorgi Kapanadze
