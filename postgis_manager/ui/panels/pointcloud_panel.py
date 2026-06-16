"""Point Cloud Panel — LAS/LAZ/E57 import to PostGIS pointcloud / pgPointCloud."""

from __future__ import annotations
import os
import subprocess
import shutil
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QLineEdit, QComboBox, QGroupBox, QFormLayout,
    QSpinBox, QDoubleSpinBox, QCheckBox, QFileDialog, QMessageBox, QTabWidget, QProgressBar,
)


# ── Tool discovery ────────────────────────────────────────────────────────

def _find_tool(name: str) -> str | None:
    """Find pdal, las2pg, or lastools executable."""
    found = shutil.which(name)
    if found:
        return found
    # Common install locations
    candidates = []
    if name == "pdal":
        candidates = [
            r"C:\OSGeo4W\bin\pdal.exe",
            r"C:\Program Files\PDAL\bin\pdal.exe",
            "/usr/bin/pdal", "/usr/local/bin/pdal",
            "/opt/conda/bin/pdal",
        ]
    elif name == "las2pg":
        candidates = [
            r"C:\OSGeo4W\bin\las2pg.exe",
            "/usr/bin/las2pg", "/usr/local/bin/las2pg",
        ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


# ── Worker ────────────────────────────────────────────────────────────────

class PCWorker(QThread):
    log      = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished = pyqtSignal(bool)

    def __init__(self, cmd: list[str], env: dict | None = None):
        super().__init__()
        self.cmd = cmd
        self.env = env

    def run(self):
        ok = True
        try:
            proc = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=self.env,
            )
            for line in proc.stdout:
                self.log.emit(line.rstrip())
            proc.wait()
            if proc.returncode != 0:
                self.log.emit(f"Process exited with code {proc.returncode}")
                ok = False
            else:
                self.log.emit("✓ Completed successfully.")
        except FileNotFoundError:
            self.log.emit(f"✗ Tool not found: {self.cmd[0]}")
            ok = False
        except Exception as e:
            self.log.emit(f"✗ {e}")
            ok = False
        finally:
            self.finished.emit(ok)


# ── Panel ─────────────────────────────────────────────────────────────────

class PointCloudPanel(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._conn_params: dict = {}
        self._worker: PCWorker | None = None
        self._setup_ui()
        self._check_tools()

    def set_connection(self, params: dict):
        self._conn_params = params

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.addTab(self._build_import_tab(),  "📥  Import LAS/LAZ")
        tabs.addTab(self._build_pdal_tab(),    "⚙  PDAL Pipeline")
        tabs.addTab(self._build_info_tab(),    "ℹ  File Info / Stats")
        tabs.addTab(self._build_install_tab(), "🛠  Install Guide")
        root.addWidget(tabs, 1)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 10))
        self._log.setMaximumHeight(130)
        root.addWidget(self._log)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setMaximumHeight(4)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

    # ── Import tab ────────────────────────────────────────────────────────

    def _build_import_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        # Tool status
        self._pdal_status = QLabel("Checking PDAL…")
        lay.addWidget(self._pdal_status)

        # File selector
        file_box = QGroupBox("Input File(s)")
        ffl = QFormLayout(file_box)
        self._files = QTextEdit()
        self._files.setMaximumHeight(60)
        self._files.setPlaceholderText(
            "Drop .las / .laz / .e57 / .ply files here, or use Browse…")
        self._files.setAcceptDrops(True)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_files)
        ffl.addRow("Files:", self._files)
        ffl.addRow("", btn_browse)
        lay.addWidget(file_box)

        # Target
        tgt_box = QGroupBox("PostgreSQL Target")
        tfl = QFormLayout(tgt_box)
        self._pc_schema = QLineEdit("public")
        self._pc_table  = QLineEdit("pointcloud")
        self._pc_srid   = QSpinBox()
        self._pc_srid.setRange(0, 999999)
        self._pc_srid.setValue(4326)
        self._pc_mode   = QComboBox()
        self._pc_mode.addItems([
            "overwrite (DROP + CREATE)",
            "append",
            "create new",
        ])
        tfl.addRow("Schema:", self._pc_schema)
        tfl.addRow("Table:", self._pc_table)
        tfl.addRow("SRID:", self._pc_srid)
        tfl.addRow("Mode:", self._pc_mode)
        lay.addWidget(tgt_box)

        # Options
        opt_box = QGroupBox("Options")
        ofl = QFormLayout(opt_box)
        self._pc_patch_size = QSpinBox()
        self._pc_patch_size.setRange(100, 50000)
        self._pc_patch_size.setValue(400)
        self._pc_patch_size.setToolTip("Points per patch (pgPointCloud)")
        self._pc_thin = QDoubleSpinBox()
        self._pc_thin.setRange(0, 1)
        self._pc_thin.setValue(1.0)
        self._pc_thin.setSingleStep(0.1)
        self._pc_thin.setToolTip("1.0 = keep all; 0.1 = keep 10% (random)")
        self._pc_stats = QCheckBox("Compute statistics after import")
        ofl.addRow("Patch size:", self._pc_patch_size)
        ofl.addRow("Thinning ratio:", self._pc_thin)
        ofl.addRow("", self._pc_stats)
        lay.addWidget(opt_box)

        btn_row = QHBoxLayout()
        btn_run = QPushButton("▶  Import Point Cloud")
        btn_run.setStyleSheet("font-weight:bold;")
        btn_run.clicked.connect(self._run_import)
        btn_preview = QPushButton("Preview PDAL command")
        btn_preview.clicked.connect(self._preview_cmd)
        btn_row.addWidget(btn_run)
        btn_row.addWidget(btn_preview)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        lay.addStretch()
        return w

    def _browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select point cloud files", "",
            "Point Cloud (*.las *.laz *.e57 *.ply *.xyz);;All (*)")
        if paths:
            self._files.setPlainText("\n".join(paths))

    def _build_pdal_cmd(self, input_file: str) -> list[str]:
        pdal = _find_tool("pdal") or "pdal"
        params = self._conn_params
        pg_conn = (f"postgresql://{params.get('user', 'postgres')}:"
                   f"{params.get('password', '')}@"
                   f"{params.get('host', 'localhost')}:"
                   f"{params.get('port', 5432)}/"
                   f"{params.get('dbname', 'postgis')}")
        schema = self._pc_schema.text().strip()
        table  = self._pc_table.text().strip()
        srid   = self._pc_srid.value()
        mode   = ["overwrite", "append", "create"][self._pc_mode.currentIndex()]
        patch  = self._pc_patch_size.value()
        thin   = self._pc_thin.value()

        pipeline = {
            "pipeline": [
                {"type": "readers.las", "filename": input_file},
                *([] if thin >= 1.0 else
                  [{"type": "filters.sample", "radius": 0.5}]),
                {"type": "filters.chipper", "capacity": patch},
                {
                    "type": "writers.pgpointcloud",
                    "connection": pg_conn,
                    "schema": schema,
                    "table": table,
                    "srid": srid,
                    "overwrite": mode == "overwrite",
                    "append": mode == "append",
                    "compression": "dimensional",
                },
            ]
        }
        # Write temp pipeline JSON
        import json
        import tempfile
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False, encoding="utf-8")
        json.dump(pipeline, tmp)
        tmp.close()
        return [pdal, "pipeline", tmp.name], tmp.name

    def _preview_cmd(self):
        files = [f.strip() for f in
                 self._files.toPlainText().splitlines() if f.strip()]
        if not files:
            return
        cmd, _ = self._build_pdal_cmd(files[0])
        self._log.setPlainText(" ".join(cmd))

    def _run_import(self):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection", "Connect to DB first.")
            return
        files = [f.strip() for f in
                 self._files.toPlainText().splitlines() if f.strip()]
        if not files:
            QMessageBox.warning(self, "No files", "Select at least one file.")
            return
        # Run first file (batch: loop is simple but we do one at a time here)
        cmd, _ = self._build_pdal_cmd(files[0])
        self._progress.setVisible(True)
        w = PCWorker(cmd)
        w.log.connect(self._log.append)
        w.finished.connect(self._on_finished)
        self._worker = w
        w.start()

    def _on_finished(self, ok: bool):
        self._progress.setVisible(False)
        if ok and self._pc_stats.isChecked():
            self._run_stats_query()

    def _run_stats_query(self):
        schema = self._pc_schema.text().strip()
        table  = self._pc_table.text().strip()
        try:
            import psycopg2
            conn = psycopg2.connect(**self._conn_params)
            cur  = conn.cursor()
            cur.execute(
                f'SELECT PC_Summary(pa) FROM "{schema}"."{table}" LIMIT 1')
            row = cur.fetchone()
            if row:
                self._log.append(f"PC_Summary: {row[0]}")
            cur.close(); conn.close()
        except Exception as e:
            self._log.append(f"Stats error: {e}")

    # ── PDAL Pipeline tab ─────────────────────────────────────────────────

    def _build_pdal_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        info = QLabel(
            "Write a custom PDAL pipeline JSON and run it directly. "
            "Full PDAL docs: pdal.io/stages/")
        info.setWordWrap(True)
        lay.addWidget(info)

        self._pipeline_edit = QTextEdit()
        self._pipeline_edit.setFont(QFont("Courier New", 10))
        self._pipeline_edit.setPlainText(json_pipeline_template())
        lay.addWidget(self._pipeline_edit, 1)

        btn_row = QHBoxLayout()
        btn_run = QPushButton("▶ Run Pipeline")
        btn_run.setStyleSheet("font-weight:bold;")
        btn_run.clicked.connect(self._run_pipeline)
        btn_save = QPushButton("💾 Save pipeline to file")
        btn_save.clicked.connect(self._save_pipeline)
        btn_load = QPushButton("📂 Load pipeline")
        btn_load.clicked.connect(self._load_pipeline)
        btn_row.addWidget(btn_run)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_load)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return w

    def _run_pipeline(self):
        import json
        import tempfile
        text = self._pipeline_edit.toPlainText().strip()
        try:
            json.loads(text)  # validate
        except Exception as e:
            QMessageBox.critical(self, "Invalid JSON", str(e))
            return
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False, encoding="utf-8")
        tmp.write(text); tmp.close()
        pdal = _find_tool("pdal") or "pdal"
        self._progress.setVisible(True)
        w = PCWorker([pdal, "pipeline", tmp.name])
        w.log.connect(self._log.append)
        w.finished.connect(lambda ok: self._progress.setVisible(False))
        self._worker = w; w.start()

    def _save_pipeline(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Pipeline", "pipeline.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._pipeline_edit.toPlainText())

    def _load_pipeline(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Pipeline", "", "JSON (*.json)")
        if path:
            with open(path, encoding="utf-8") as f:
                self._pipeline_edit.setPlainText(f.read())

    # ── Info tab ──────────────────────────────────────────────────────────

    def _build_info_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        file_row = QHBoxLayout()
        self._info_file = QLineEdit()
        self._info_file.setPlaceholderText("path to .las / .laz file")
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse_info_file)
        btn_info = QPushButton("▶ Get Info")
        btn_info.clicked.connect(self._run_info)
        file_row.addWidget(self._info_file, 1)
        file_row.addWidget(btn_browse)
        file_row.addWidget(btn_info)
        lay.addLayout(file_row)
        lay.addStretch()
        return w

    def _browse_info_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "Select point cloud", "",
            "Point Cloud (*.las *.laz *.e57 *.ply);;All (*)")
        if p:
            self._info_file.setText(p)

    def _run_info(self):
        f = self._info_file.text().strip()
        if not f:
            return
        pdal = _find_tool("pdal") or "pdal"
        self._progress.setVisible(True)
        w = PCWorker([pdal, "info", f, "--summary"])
        w.log.connect(self._log.append)
        w.finished.connect(lambda ok: self._progress.setVisible(False))
        self._worker = w; w.start()

    # ── Install tab ───────────────────────────────────────────────────────

    def _build_install_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        self._install_status = QTextEdit()
        self._install_status.setReadOnly(True)
        self._install_status.setFont(QFont("Courier New", 10))
        lay.addWidget(self._install_status)

        guide = QTextEdit()
        guide.setReadOnly(True)
        guide.setHtml("""
<h3>How to install PDAL</h3>
<b>Windows (OSGeo4W):</b><br>
<code>osgeo4w-setup.exe → Advanced → pdal + pdal-pgpointcloud</code><br><br>
<b>Linux (conda):</b><br>
<code>conda install -c conda-forge pdal python-pdal pdal-plugin-pgpointcloud</code><br><br>
<b>Linux (apt):</b><br>
<code>sudo apt install pdal libpdal-plugin-pgpointcloud</code><br><br>
<b>macOS (Homebrew):</b><br>
<code>brew install pdal</code><br><br>
<h3>PostgreSQL pointcloud extension</h3>
<code>sudo apt install postgresql-16-pointcloud</code><br>
Then in psql: <code>CREATE EXTENSION pointcloud;</code><br>
And: <code>CREATE EXTENSION pointcloud_postgis;</code>
""")
        lay.addWidget(guide)

        btn_check = QPushButton("↺ Re-check tools")
        btn_check.clicked.connect(self._check_tools)
        lay.addWidget(btn_check)
        return w

    def _check_tools(self):
        lines = []
        for tool in ["pdal", "las2pg", "laszip"]:
            path = _find_tool(tool)
            status = f"✓ {path}" if path else "✗ not found"
            lines.append(f"{tool}: {status}")
        status_text = "\n".join(lines)
        self._pdal_status.setText(
            "✓ PDAL found" if _find_tool("pdal") else
            "✗ PDAL not found — see Install Guide tab")
        self._pdal_status.setStyleSheet(
            "color:#3fb950; font-weight:bold;" if _find_tool("pdal")
            else "color:#ff7b72; font-weight:bold;")
        if hasattr(self, "_install_status"):
            self._install_status.setPlainText(status_text)


def json_pipeline_template() -> str:
    return """{
  "pipeline": [
    {
      "type": "readers.las",
      "filename": "/path/to/input.laz"
    },
    {
      "type": "filters.reprojection",
      "in_srs": "EPSG:4326",
      "out_srs": "EPSG:32638"
    },
    {
      "type": "filters.chipper",
      "capacity": 400
    },
    {
      "type": "writers.pgpointcloud",
      "connection": "host=localhost dbname=postgis user=postgres",
      "schema": "public",
      "table": "pointcloud",
      "srid": "32638",
      "overwrite": true,
      "compression": "dimensional"
    }
  ]
}"""
