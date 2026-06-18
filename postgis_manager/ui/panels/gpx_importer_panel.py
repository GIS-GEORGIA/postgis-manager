"""GPX / KML Track Importer — field GPS data → PostGIS."""

from __future__ import annotations
import os
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QFileDialog, QComboBox, QCheckBox, QFormLayout,
    QProgressBar, QTextEdit, QGroupBox, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QTabWidget,
)


class _ParseWorker(QThread):
    done  = pyqtSignal(dict)   # {tracks, waypoints, routes}
    error = pyqtSignal(str)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        try:
            ext = os.path.splitext(self._path)[1].lower()
            if ext == ".gpx":
                self.done.emit(self._parse_gpx())
            elif ext in (".kml", ".kmz"):
                self.done.emit(self._parse_kml())
            else:
                self.error.emit(f"Unsupported format: {ext}")
        except Exception as e:
            self.error.emit(str(e))

    def _parse_gpx(self) -> dict:
        from xml.etree import ElementTree as ET
        NS = "http://www.topografix.com/GPX/1/1"
        tree = ET.parse(self._path)
        root = tree.getroot()

        def tag(name): return f"{{{NS}}}{name}"

        tracks = []
        for trk in root.findall(tag("trk")):
            name = trk.findtext(tag("name")) or "Track"
            pts  = []
            for trkpt in trk.findall(f".//{tag('trkpt')}"):
                lat = float(trkpt.get("lat", 0))
                lon = float(trkpt.get("lon", 0))
                ele = trkpt.findtext(tag("ele"))
                tim = trkpt.findtext(tag("time"))
                pts.append((lon, lat, float(ele) if ele else None, tim))
            if pts:
                tracks.append({"name": name, "points": pts})

        waypoints = []
        for wpt in root.findall(tag("wpt")):
            lat = float(wpt.get("lat", 0))
            lon = float(wpt.get("lon", 0))
            name = wpt.findtext(tag("name")) or ""
            sym  = wpt.findtext(tag("sym"))  or ""
            ele  = wpt.findtext(tag("ele"))
            tim  = wpt.findtext(tag("time"))
            waypoints.append({
                "name": name, "sym": sym,
                "lon": lon, "lat": lat,
                "ele": float(ele) if ele else None,
                "time": tim,
            })

        routes = []
        for rte in root.findall(tag("rte")):
            name = rte.findtext(tag("name")) or "Route"
            pts  = []
            for rtept in rte.findall(tag("rtept")):
                lat = float(rtept.get("lat", 0))
                lon = float(rtept.get("lon", 0))
                pts.append((lon, lat))
            if pts:
                routes.append({"name": name, "points": pts})

        return {"tracks": tracks, "waypoints": waypoints, "routes": routes}

    def _parse_kml(self) -> dict:
        from xml.etree import ElementTree as ET
        import zipfile, io
        content = self._path
        if self._path.endswith(".kmz"):
            with zipfile.ZipFile(self._path) as z:
                for n in z.namelist():
                    if n.endswith(".kml"):
                        content = io.BytesIO(z.read(n))
                        break
        NS = "http://www.opengis.net/kml/2.2"
        tree = ET.parse(content)
        root = tree.getroot()

        def coords_to_pts(text: str):
            pts = []
            for token in text.strip().split():
                parts = token.split(",")
                if len(parts) >= 2:
                    pts.append((float(parts[0]), float(parts[1])))
            return pts

        tracks = []
        waypoints = []
        for pm in root.findall(f".//{{{NS}}}Placemark"):
            name = pm.findtext(f"{{{NS}}}name") or ""
            ls = pm.find(f".//{{{NS}}}LineString")
            pt = pm.find(f".//{{{NS}}}Point")
            if ls is not None:
                coord_text = ls.findtext(f"{{{NS}}}coordinates") or ""
                pts = coords_to_pts(coord_text)
                if pts:
                    tracks.append({"name": name,
                                   "points": [(x, y, None, None) for x, y in pts]})
            elif pt is not None:
                coord_text = pt.findtext(f"{{{NS}}}coordinates") or ""
                pts = coords_to_pts(coord_text)
                if pts:
                    x, y = pts[0]
                    waypoints.append({"name": name, "sym": "",
                                      "lon": x, "lat": y, "ele": None, "time": None})
        return {"tracks": tracks, "waypoints": waypoints, "routes": []}


class _ImportWorker(QThread):
    done     = pyqtSignal(int, int, int)   # tracks, waypoints, routes imported
    error    = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, db, schema: str, parsed: dict, config: dict):
        super().__init__()
        self._db = db; self._schema = schema
        self._parsed = parsed; self._config = config

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            s = self._schema
            c = self._config
            n_tracks = n_wpts = n_routes = 0

            if c.get("import_tracks") and self._parsed.get("tracks"):
                t = c.get("tracks_table", "gps_tracks")
                self.progress.emit(10, f"Creating tracks table {s}.{t}…")
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS "{s}"."{t}" (
                        id SERIAL PRIMARY KEY,
                        name TEXT,
                        source_file TEXT,
                        n_points INTEGER,
                        start_time TEXT,
                        end_time TEXT,
                        total_length_m FLOAT,
                        geom geometry(LineStringZ, 4326)
                    )
                """)
                conn.commit()
                for i, trk in enumerate(self._parsed["tracks"]):
                    pts = trk["points"]
                    if len(pts) < 2: continue
                    coords = ", ".join(
                        f"{x} {y} {z if z is not None else 0}"
                        for x, y, z, _ in pts)
                    wkt = f"LINESTRING Z ({coords})"
                    times = [p[3] for p in pts if p[3]]
                    cur.execute(f"""
                        INSERT INTO "{s}"."{t}"
                            (name, source_file, n_points, start_time, end_time,
                             total_length_m, geom)
                        VALUES (%s, %s, %s, %s, %s,
                            ST_Length(ST_GeomFromText(%s,4326)::geography),
                            ST_GeomFromText(%s,4326))
                    """, (trk["name"],
                          os.path.basename(c.get("file","")),
                          len(pts),
                          times[0] if times else None,
                          times[-1] if times else None,
                          wkt, wkt))
                    n_tracks += 1
                conn.commit()

            if c.get("import_waypoints") and self._parsed.get("waypoints"):
                t = c.get("wpts_table", "gps_waypoints")
                self.progress.emit(50, f"Creating waypoints table {s}.{t}…")
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS "{s}"."{t}" (
                        id SERIAL PRIMARY KEY,
                        name TEXT,
                        sym TEXT,
                        elevation FLOAT,
                        time TEXT,
                        source_file TEXT,
                        geom geometry(PointZ, 4326)
                    )
                """)
                conn.commit()
                for wpt in self._parsed["waypoints"]:
                    z = wpt.get("ele") or 0
                    wkt = f"POINT Z ({wpt['lon']} {wpt['lat']} {z})"
                    cur.execute(f"""
                        INSERT INTO "{s}"."{t}"
                            (name, sym, elevation, time, source_file, geom)
                        VALUES (%s, %s, %s, %s, %s, ST_GeomFromText(%s,4326))
                    """, (wpt["name"], wpt["sym"], wpt.get("ele"),
                          wpt.get("time"),
                          os.path.basename(c.get("file","")), wkt))
                    n_wpts += 1
                conn.commit()

            if c.get("import_routes") and self._parsed.get("routes"):
                t = c.get("routes_table", "gps_routes")
                self.progress.emit(80, f"Creating routes table {s}.{t}…")
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS "{s}"."{t}" (
                        id SERIAL PRIMARY KEY,
                        name TEXT,
                        source_file TEXT,
                        geom geometry(LineString, 4326)
                    )
                """)
                conn.commit()
                for rte in self._parsed["routes"]:
                    if len(rte["points"]) < 2: continue
                    coords = ", ".join(f"{x} {y}" for x,y in rte["points"])
                    wkt = f"LINESTRING ({coords})"
                    cur.execute(f"""
                        INSERT INTO "{s}"."{t}" (name, source_file, geom)
                        VALUES (%s, %s, ST_GeomFromText(%s,4326))
                    """, (rte["name"], os.path.basename(c.get("file","")), wkt))
                    n_routes += 1
                conn.commit()

            conn.close()
            self.progress.emit(100, "Done")
            self.done.emit(n_tracks, n_wpts, n_routes)
        except Exception as e:
            self.error.emit(str(e))


# ── panel ─────────────────────────────────────────────────────────────────────

class GPXImporterPanel(QWidget):
    layer_imported = pyqtSignal(str, str)   # schema, table

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._parsed: dict = {}
        self._workers = []
        self._build_ui()

    def set_db(self, db):
        self._db = db

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # file picker
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("File:"))
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("Select GPX or KML/KMZ file…")
        self._file_edit.setReadOnly(True)
        file_row.addWidget(self._file_edit)
        browse = QPushButton("📂 Browse")
        browse.clicked.connect(self._browse)
        file_row.addWidget(browse)
        self._parse_btn = QPushButton("🔍 Parse")
        self._parse_btn.clicked.connect(self._parse)
        self._parse_btn.setEnabled(False)
        file_row.addWidget(self._parse_btn)
        root.addLayout(file_row)

        # preview tabs
        self._tabs = QTabWidget()
        for label in ("Tracks", "Waypoints", "Routes"):
            tw = QTableWidget(0, 3)
            tw.setHorizontalHeaderLabels(["Name", "Points", "Start time"])
            tw.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tw.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            self._tabs.addTab(tw, label)
        self._trk_table = self._tabs.widget(0)
        self._wpt_table = self._tabs.widget(1)
        self._rte_table = self._tabs.widget(2)
        root.addWidget(self._tabs)

        # import config
        cfg_box = QGroupBox("Import settings")
        cf = QFormLayout(cfg_box)
        self._schema_combo = QComboBox(); self._schema_combo.setMinimumWidth(120)
        cf.addRow("Target schema:", self._schema_combo)
        self._import_tracks = QCheckBox("Import tracks"); self._import_tracks.setChecked(True)
        self._import_wpts   = QCheckBox("Import waypoints"); self._import_wpts.setChecked(True)
        self._import_routes = QCheckBox("Import routes"); self._import_routes.setChecked(True)
        self._tracks_tbl = QLineEdit("gps_tracks")
        self._wpts_tbl   = QLineEdit("gps_waypoints")
        self._routes_tbl = QLineEdit("gps_routes")
        cf.addRow(self._import_tracks, self._tracks_tbl)
        cf.addRow(self._import_wpts,   self._wpts_tbl)
        cf.addRow(self._import_routes, self._routes_tbl)
        root.addWidget(cfg_box)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setMaximumHeight(6)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        btn_row = QHBoxLayout()
        self._import_btn = QPushButton("⬇ Import to PostGIS")
        self._import_btn.setEnabled(False)
        self._import_btn.setFixedHeight(32)
        self._import_btn.clicked.connect(self._import)
        btn_row.addWidget(self._import_btn)
        self._status = QLabel("")
        btn_row.addWidget(self._status)
        btn_row.addStretch()
        root.addLayout(btn_row)

    def refresh_schemas(self):
        if not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') "
                "ORDER BY schema_name")
            schemas = [r[0] for r in cur.fetchall()]
            conn.close()
            self._schema_combo.clear()
            for s in schemas: self._schema_combo.addItem(s)
        except Exception:
            pass

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open GPS file", "",
            "GPS Files (*.gpx *.kml *.kmz);;All (*)")
        if path:
            self._file_edit.setText(path)
            self._parse_btn.setEnabled(True)

    def _parse(self):
        path = self._file_edit.text()
        if not path: return
        self._parse_btn.setEnabled(False)
        self._status.setText("Parsing…")
        w = _ParseWorker(path)
        w.done.connect(self._on_parsed)
        w.error.connect(lambda e: (self._status.setText(f"Error: {e}"),
                                   self._parse_btn.setEnabled(True)))
        w.start(); self._workers.append(w)

    def _on_parsed(self, data: dict):
        self._parsed = data
        self._parse_btn.setEnabled(True)
        self._import_btn.setEnabled(True)

        def _fill(tw: QTableWidget, items, cols_fn):
            tw.setRowCount(len(items))
            for ri, item in enumerate(items):
                for ci, val in enumerate(cols_fn(item)):
                    tw.setItem(ri, ci, QTableWidgetItem(str(val or "")))

        _fill(self._trk_table, data.get("tracks", []),
              lambda t: [t["name"], len(t["points"]),
                         t["points"][0][3] if t["points"] else ""])
        _fill(self._wpt_table, data.get("waypoints", []),
              lambda w: [w["name"], 1, w.get("time","")])
        _fill(self._rte_table, data.get("routes", []),
              lambda r: [r["name"], len(r["points"]), ""])

        self._status.setText(
            f"Parsed: {len(data.get('tracks',[]))} tracks, "
            f"{len(data.get('waypoints',[]))} waypoints, "
            f"{len(data.get('routes',[]))} routes")

    def _import(self):
        if not self._parsed:
            return
        config = {
            "file":            self._file_edit.text(),
            "import_tracks":   self._import_tracks.isChecked(),
            "import_waypoints":self._import_wpts.isChecked(),
            "import_routes":   self._import_routes.isChecked(),
            "tracks_table":    self._tracks_tbl.text().strip() or "gps_tracks",
            "wpts_table":      self._wpts_tbl.text().strip()   or "gps_waypoints",
            "routes_table":    self._routes_tbl.text().strip() or "gps_routes",
        }
        schema = self._schema_combo.currentText()
        self._import_btn.setEnabled(False)
        self._progress.setVisible(True)
        w = _ImportWorker(self._db, schema, self._parsed, config)
        w.done.connect(self._on_imported)
        w.error.connect(lambda e: (
            QMessageBox.critical(self, "Import error", e),
            self._import_btn.setEnabled(True),
            self._progress.setVisible(False),
        ))
        w.progress.connect(lambda v, msg: (
            self._progress.setValue(v), self._status.setText(msg)))
        w.start(); self._workers.append(w)

    def _on_imported(self, n_trk, n_wpt, n_rte):
        self._import_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._status.setText(
            f"✔ Imported: {n_trk} tracks, {n_wpt} waypoints, {n_rte} routes")
        schema = self._schema_combo.currentText()
        for t in ("gps_tracks", "gps_waypoints", "gps_routes"):
            self.layer_imported.emit(schema, t)
