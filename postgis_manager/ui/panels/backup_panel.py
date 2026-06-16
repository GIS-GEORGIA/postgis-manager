"""Backup & Restore panel — pg_dump / pg_restore / psql wrappers."""

from __future__ import annotations
import os
import shutil
import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QComboBox, QLineEdit, QCheckBox, QTextEdit,
    QFileDialog, QMessageBox, QProgressBar, QFormLayout, QSplitter,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from ...db.connection import DBManager
from ...utils import i18n, config


def _find_tool(name: str) -> str | None:
    """Find pg_dump / psql etc. either on PATH or near known pg installs."""
    found = shutil.which(name)
    if found:
        return found
    # Common Windows install paths
    for base in [
        r"C:\Program Files\PostgreSQL",
        r"C:\Program Files (x86)\PostgreSQL",
        r"C:\Program Files\pgAdmin 4\runtime",
    ]:
        if os.path.isdir(base):
            for ver in sorted(os.listdir(base), reverse=True):
                candidate = os.path.join(base, ver, "bin", name + ".exe")
                if os.path.isfile(candidate):
                    return candidate
    return None


class BackupWorker(QThread):
    log_line = pyqtSignal(str)
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, cmd: list[str], env: dict | None = None):
        super().__init__()
        self.cmd = cmd
        self.env = env

    def run(self):
        try:
            import subprocess
            proc = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=self.env,
            )
            for line in proc.stdout:
                self.log_line.emit(line.rstrip())
            proc.wait()
            if proc.returncode == 0:
                self.finished.emit(True, i18n.t("bak_done"))
            else:
                self.finished.emit(False, i18n.t("bak_failed",
                                                  code=proc.returncode))
        except Exception as e:
            self.finished.emit(False, str(e))


class BackupRestorePanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._worker: BackupWorker | None = None
        self._build_ui()
        self._detect_tools()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"💾  {i18n.t('bak_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        self._tool_lbl = QLabel("")
        self._tool_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._tool_lbl)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── Backup side ───────────────────────────────────────────────────────
        bak_w = QWidget(); bl = QVBoxLayout(bak_w)
        bak_box = QGroupBox(i18n.t("bak_backup"))
        bb = QFormLayout(bak_box)

        self._bak_dbname = QLineEdit()
        self._bak_host   = QLineEdit("localhost")
        self._bak_port   = QLineEdit("5432")
        self._bak_user   = QLineEdit()
        self._bak_pass   = QLineEdit(); self._bak_pass.setEchoMode(
            QLineEdit.EchoMode.Password)
        self._bak_file   = QLineEdit()
        self._bak_format = QComboBox()
        self._bak_format.addItems([
            "custom (-Fc)", "directory (-Fd)", "plain SQL (-Fp)", "tar (-Ft)"])
        self._bak_jobs   = QLineEdit("4")
        self._bak_jobs.setMaximumWidth(50)
        self._bak_compress = QCheckBox(i18n.t("bak_compress"))
        self._bak_compress.setChecked(True)
        self._bak_schema_only = QCheckBox(i18n.t("bak_schema_only"))
        self._bak_data_only   = QCheckBox(i18n.t("bak_data_only"))
        self._bak_no_owner    = QCheckBox(i18n.t("bak_no_owner"))
        self._bak_no_owner.setChecked(True)

        bb.addRow("Database:", self._bak_dbname)
        bb.addRow("Host:",     self._bak_host)
        bb.addRow("Port:",     self._bak_port)
        bb.addRow("User:",     self._bak_user)
        bb.addRow("Password:", self._bak_pass)

        file_row = QHBoxLayout()
        file_row.addWidget(self._bak_file)
        browse_btn = QPushButton("…")
        browse_btn.setMaximumWidth(30)
        browse_btn.clicked.connect(self._browse_backup_file)
        file_row.addWidget(browse_btn)
        bb.addRow(i18n.t("bak_file"),   file_row)
        bb.addRow(i18n.t("bak_format"), self._bak_format)
        jobs_row = QHBoxLayout()
        jobs_row.addWidget(self._bak_jobs)
        jobs_row.addWidget(QLabel(i18n.t("bak_jobs")))
        jobs_row.addStretch()
        bb.addRow("", jobs_row)
        bb.addRow("", self._bak_compress)
        bb.addRow("", self._bak_schema_only)
        bb.addRow("", self._bak_data_only)
        bb.addRow("", self._bak_no_owner)
        bl.addWidget(bak_box)

        self._bak_btn = QPushButton(f"💾 {i18n.t('bak_run_backup')}")
        self._bak_btn.clicked.connect(self._run_backup)
        bl.addWidget(self._bak_btn)
        bl.addStretch()
        splitter.addWidget(bak_w)

        # ── Restore side ──────────────────────────────────────────────────────
        rst_w = QWidget(); rl = QVBoxLayout(rst_w)
        rst_box = QGroupBox(i18n.t("bak_restore"))
        rb = QFormLayout(rst_box)

        self._rst_dbname = QLineEdit()
        self._rst_host   = QLineEdit("localhost")
        self._rst_port   = QLineEdit("5432")
        self._rst_user   = QLineEdit()
        self._rst_pass   = QLineEdit(); self._rst_pass.setEchoMode(
            QLineEdit.EchoMode.Password)
        self._rst_file   = QLineEdit()
        self._rst_create = QCheckBox(i18n.t("bak_create_db"))
        self._rst_clean  = QCheckBox(i18n.t("bak_clean"))
        self._rst_no_owner = QCheckBox(i18n.t("bak_no_owner"))
        self._rst_no_owner.setChecked(True)
        self._rst_schema_only = QCheckBox(i18n.t("bak_schema_only"))
        self._rst_data_only   = QCheckBox(i18n.t("bak_data_only"))
        self._rst_jobs        = QLineEdit("4"); self._rst_jobs.setMaximumWidth(50)

        rb.addRow("Database:", self._rst_dbname)
        rb.addRow("Host:",     self._rst_host)
        rb.addRow("Port:",     self._rst_port)
        rb.addRow("User:",     self._rst_user)
        rb.addRow("Password:", self._rst_pass)

        rfile_row = QHBoxLayout()
        rfile_row.addWidget(self._rst_file)
        rbrowse = QPushButton("…"); rbrowse.setMaximumWidth(30)
        rbrowse.clicked.connect(self._browse_restore_file)
        rfile_row.addWidget(rbrowse)
        rb.addRow(i18n.t("bak_file"), rfile_row)
        rjobs_row = QHBoxLayout()
        rjobs_row.addWidget(self._rst_jobs)
        rjobs_row.addWidget(QLabel(i18n.t("bak_jobs")))
        rjobs_row.addStretch()
        rb.addRow("", rjobs_row)
        rb.addRow("", self._rst_create)
        rb.addRow("", self._rst_clean)
        rb.addRow("", self._rst_no_owner)
        rb.addRow("", self._rst_schema_only)
        rb.addRow("", self._rst_data_only)
        rl.addWidget(rst_box)

        self._rst_btn = QPushButton(f"📥 {i18n.t('bak_run_restore')}")
        self._rst_btn.clicked.connect(self._run_restore)
        rl.addWidget(self._rst_btn)
        rl.addStretch()
        splitter.addWidget(rst_w)
        splitter.setSizes([500, 500])

        # Log
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setMaximumHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        layout.addWidget(QLabel(f"<b>{i18n.t('bak_log')}</b>"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(__import__("PyQt6.QtGui", fromlist=["QFont"]).QFont(
            "Courier New", 10))
        self._log.setMaximumHeight(160)
        layout.addWidget(self._log)

        stop_btn = QPushButton(f"⏹ {i18n.t('bak_stop')}")
        stop_btn.clicked.connect(self._stop)
        layout.addWidget(stop_btn)

    # ── Tool detection ────────────────────────────────────────────────────────

    def _detect_tools(self):
        pg_dump = _find_tool("pg_dump")
        pg_restore = _find_tool("pg_restore")
        psql = _find_tool("psql")
        parts = []
        if pg_dump:    parts.append(f"pg_dump: {pg_dump}")
        if pg_restore: parts.append(f"pg_restore: {pg_restore}")
        if psql:       parts.append(f"psql: {psql}")
        if parts:
            self._tool_lbl.setText("  |  ".join(parts))
            self._tool_lbl.setStyleSheet("color: #2E7D32; font-size: 11px;")
        else:
            self._tool_lbl.setText(i18n.t("bak_no_tools"))
            self._tool_lbl.setStyleSheet("color: #C62828; font-size: 11px;")

    def _fill_from_connection(self):
        if self.db.is_connected():
            p = self.db.params
            for widget, key in [(self._bak_dbname, "dbname"),
                                 (self._bak_host, "host"),
                                 (self._bak_port, "port"),
                                 (self._bak_user, "user"),
                                 (self._rst_dbname, "dbname"),
                                 (self._rst_host, "host"),
                                 (self._rst_port, "port"),
                                 (self._rst_user, "user")]:
                val = p.get(key, "")
                if val:
                    widget.setText(str(val))

    # ── Browse files ──────────────────────────────────────────────────────────

    def _browse_backup_file(self):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        db = self._bak_dbname.text().strip() or "db"
        default = os.path.join(
            config.get("last_export_dir", os.path.expanduser("~")),
            f"{db}_{ts}.backup")
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.t("bak_choose_file"), default,
            "Backup files (*.backup *.dump *.sql *.tar);;All (*)")
        if path:
            self._bak_file.setText(path)
            config.set("last_export_dir", os.path.dirname(path))

    def _browse_restore_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, i18n.t("bak_choose_file"),
            config.get("last_export_dir", os.path.expanduser("~")),
            "Backup files (*.backup *.dump *.sql *.tar);;All (*)")
        if path:
            self._rst_file.setText(path)
            config.set("last_export_dir", os.path.dirname(path))

    # ── Run backup ────────────────────────────────────────────────────────────

    def _run_backup(self):
        tool = _find_tool("pg_dump")
        if not tool:
            QMessageBox.critical(self, "PostGIS Manager", i18n.t("bak_no_pg_dump"))
            return
        dbname = self._bak_dbname.text().strip()
        outfile = self._bak_file.text().strip()
        if not dbname or not outfile:
            QMessageBox.warning(self, "PostGIS Manager", i18n.t("bak_fill_required"))
            return

        fmt_map = {0: "c", 1: "d", 2: "p", 3: "t"}
        fmt = fmt_map.get(self._bak_format.currentIndex(), "c")

        cmd = [tool,
               "-h", self._bak_host.text().strip() or "localhost",
               "-p", self._bak_port.text().strip() or "5432",
               "-U", self._bak_user.text().strip() or "postgres",
               "-F", fmt,
               "-f", outfile]
        if fmt in ("c", "d", "t") and self._bak_compress.isChecked():
            cmd += ["-Z", "6"]
        try:
            jobs = int(self._bak_jobs.text())
            if jobs > 1 and fmt == "d":
                cmd += ["-j", str(jobs)]
        except ValueError:
            pass
        if self._bak_schema_only.isChecked(): cmd.append("--schema-only")
        if self._bak_data_only.isChecked():   cmd.append("--data-only")
        if self._bak_no_owner.isChecked():    cmd.append("--no-owner")
        cmd.append(dbname)

        env = os.environ.copy()
        pw = self._bak_pass.text()
        if pw:
            env["PGPASSWORD"] = pw

        self._start_worker(cmd, env)

    # ── Run restore ───────────────────────────────────────────────────────────

    def _run_restore(self):
        infile = self._rst_file.text().strip()
        if not infile or not os.path.exists(infile):
            QMessageBox.warning(self, "PostGIS Manager", i18n.t("bak_file_not_found"))
            return
        dbname = self._rst_dbname.text().strip()
        if not dbname:
            QMessageBox.warning(self, "PostGIS Manager", i18n.t("bak_fill_required"))
            return

        # Use psql for plain SQL, pg_restore for binary
        is_sql = infile.endswith(".sql")
        if is_sql:
            tool = _find_tool("psql")
            if not tool:
                QMessageBox.critical(self, "PostGIS Manager",
                                     i18n.t("bak_no_psql"))
                return
            cmd = [tool,
                   "-h", self._rst_host.text().strip() or "localhost",
                   "-p", self._rst_port.text().strip() or "5432",
                   "-U", self._rst_user.text().strip() or "postgres",
                   "-d", dbname, "-f", infile]
        else:
            tool = _find_tool("pg_restore")
            if not tool:
                QMessageBox.critical(self, "PostGIS Manager",
                                     i18n.t("bak_no_pg_restore"))
                return
            cmd = [tool,
                   "-h", self._rst_host.text().strip() or "localhost",
                   "-p", self._rst_port.text().strip() or "5432",
                   "-U", self._rst_user.text().strip() or "postgres",
                   "-d", dbname]
            if self._rst_create.isChecked():   cmd.append("-C")
            if self._rst_clean.isChecked():    cmd.append("-c")
            if self._rst_no_owner.isChecked(): cmd.append("--no-owner")
            if self._rst_schema_only.isChecked(): cmd.append("--schema-only")
            if self._rst_data_only.isChecked():   cmd.append("--data-only")
            try:
                jobs = int(self._rst_jobs.text())
                if jobs > 1:
                    cmd += ["-j", str(jobs)]
            except ValueError:
                pass
            cmd.append(infile)

        if QMessageBox.question(
                self, i18n.t("bak_confirm_restore_title"),
                i18n.t("bak_confirm_restore", db=dbname)) != \
                QMessageBox.StandardButton.Yes:
            return

        env = os.environ.copy()
        pw = self._rst_pass.text()
        if pw:
            env["PGPASSWORD"] = pw

        self._start_worker(cmd, env)

    # ── Worker management ─────────────────────────────────────────────────────

    def _start_worker(self, cmd: list, env: dict):
        self._log.clear()
        self._log.append(f"▶ {' '.join(cmd)}\n")
        self._progress.show()
        self._bak_btn.setEnabled(False)
        self._rst_btn.setEnabled(False)

        w = BackupWorker(cmd, env)
        w.log_line.connect(self._log.append)
        w.finished.connect(self._on_finished)
        w.start(); self._worker = w

    def _on_finished(self, success: bool, msg: str):
        self._progress.hide()
        self._bak_btn.setEnabled(True)
        self._rst_btn.setEnabled(True)
        if success:
            self._log.append(f"\n✔ {msg}")
        else:
            self._log.append(f"\n✖ {msg}")

    def _stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._log.append("\n⏹ Stopped by user.")

    def showEvent(self, event):
        super().showEvent(event)
        self._fill_from_connection()
