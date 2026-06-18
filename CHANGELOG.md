# Changelog

All notable changes to PostGIS Manager are documented here.

## [1.1.0] — 2026-06-18

### Strategic refocus — unique GIS tools, not a generic DB clone

PostGIS Manager doubled down on workflows QGIS and pgAdmin both lack. Generic
database-admin panels that overlapped pgAdmin were removed; a suite of
spatial-database-as-a-GIS-workspace tools was added.

#### New unique GIS panels
- **Map Viewer + Geometry Editor** — render PostGIS layers on a built-in canvas;
  draw point/line/polygon and INSERT geometry directly into the table via
  `ST_GeomFromText` + `ST_Transform`
- **CRS Browser & Reproject** — search `spatial_ref_sys`, inspect WKT/Proj4,
  reproject a whole layer in place
- **CRS Audit** — scan every geometry column, compare declared vs. actual SRID,
  flag mismatches, one-click `UpdateGeometrySRID` fix
- **Spatial Join GUI** — 7 predicates + KNN nearest, no SQL required, SQL preview
- **Spatial Data Quality Dashboard** — 7 checks, 0–100 quality score, auto-fix
  (`ST_MakeValid`, dedup)
- **pgRouting Network Wizard** — inspect → install pgRouting → build topology →
  generate drive-time isochrones, step by step
- **WFS Connector** — OGC WFS 2.0 GetCapabilities/GetFeature with CQL filters,
  streamed onto the map
- **GPX / KML Importer** — field GPS tracks, waypoints, routes → PostGIS with Z
  and timestamps
- **Thematic Style Generator** — Natural Breaks (Jenks), Quantile, Equal Interval
  classification with color ramps
- **Layer Snapshot + Change Diff** — snapshot a layer to a timestamped table and
  spatially diff two snapshots (added / removed / geometry-changed)

#### Removed
- Generic DB-admin panels that duplicated pgAdmin (ERD, table diff, data
  sampling, notifications, RLS manager, pg_cron scheduler, size analyzer,
  column lineage, connection profiles dialog)

#### Fixes
- Map Viewer pan now uses Qt `ScrollHandDrag` with an explicit large sceneRect
- Guarded `KeyError 'schema'` when WFS/SQL-result layers are selected
- Instance Manager layout reworked (QScrollArea + QFormLayout) to stop
  overlapping widgets on narrow viewports

## [1.0.0] — 2026-06-15

### First stable release

#### Core
- Hybrid architecture: same codebase runs as QGIS 4.x plugin and standalone desktop app
- Full PyQt6 / Qt6 support (`supportsQt6=True`, `qgisMinimumVersion=3.34`)
- Runtime language switching: English ↔ Georgian without restart
- Light / Dark theme with full QSS token system
- Persistent config: connections, SQL history, window geometry, font size

#### Navigation
- Replaced 21-tab flat layout with sidebar navigation + QStackedWidget
- 7 logical groups: Data, Import/Export, Spatial Analysis, Database Design, Advanced, Connectivity, Monitor
- Theme-aware sidebar via `#NavItem` / `#NavSidebar` QSS selectors

#### Panels (21 total)
- **SQL Editor** — syntax highlighting, persistent history (50 entries), templates, CSV export
- **Query Builder** — visual filter builder with SQL preview
- **Raster Import** — raster2pgsql wrapper with tile/SRID/overview options
- **Export** — GeoPackage, Shapefile, GeoJSON, CSV with target SRID
- **Backup / Restore** — pg_dump / pg_restore / psql GUI with live progress
- **pgRouting** — Dijkstra, Driving Distance, Isochrone
- **Topology** — create, validate, browse errors
- **Chainage** — linear referencing, M-value assignment
- **Geoprocessing** — Buffer, Simplify, Convex Hull, Centroid, Union
- **Validation** — invalid, null, duplicate, self-intersecting, wrong SRID
- **Table Designer** — create/alter tables, indexes, maintenance (VACUUM/ANALYZE)
- **Schema / Role Manager** — schemas, roles, GRANT/REVOKE GUI
- **Function Browser** — browse, view source, execute functions
- **Materialized Views** — create, REFRESH CONCURRENTLY, drop, preview
- **Raster Tools** — statistics, histogram, map algebra (NDVI/threshold/custom), reproject
- **Styles** — save/load QML to `layer_styles`, default style flag
- **Versioning** — pgVersion row-level commit/checkout/diff/branch
- **3DCityDB** — auto-detect schema, feature counts, 6 SQL templates
- **Instances** — Docker/Podman PostGIS container launcher
- **Network Scan** — LAN subnet scanner for PostgreSQL discovery
- **Dashboard** — live pg_stat_* metrics: connections, cache, queries, table sizes

#### QGIS Processing Provider
- 6 algorithms: Import Vector, Export Layer, Validation, Geoprocess, Fix Invalid Geometry, Network Scan

#### Infrastructure
- pytest test suite: 38 tests across config, i18n, DB, theme, UI panels, MainWindow
- GitHub Actions CI: lint (ruff), syntax (compileall), pytest (headless PyQt6), plugin zip build
- Website: `pg.qgis.ge` — bilingual EN/KA, light/dark, GitHub Pages

#### Website
- `docs/index.html` — full bilingual website at [pg.qgis.ge](https://pg.qgis.ge)
