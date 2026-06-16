"""Automation Panel — pg_cron jobs, auto-backup, matview refresh, event triggers."""

from __future__ import annotations
import psycopg2
import psycopg2.extras
from PyQt6.QtCore import QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QLineEdit,
    QComboBox, QTextEdit, QGroupBox, QFormLayout, QSpinBox,
    QMessageBox, QCheckBox, QAbstractItemView,
    QDialog, QDialogButtonBox, QFileDialog,
)

from ...utils.workers import launch


# ── Worker ────────────────────────────────────────────────────────────────

class AutoWorker(QThread):
    log      = pyqtSignal(str, str)
    rows     = pyqtSignal(list, list)
    finished = pyqtSignal(bool)

    def __init__(self, conn_params: dict, sql: str, fetch: bool = False):
        super().__init__()
        self.conn_params = conn_params
        self.sql = sql
        self.fetch = fetch

    def run(self):
        ok = True
        try:
            conn = psycopg2.connect(**self.conn_params)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(self.sql)
            if self.fetch and cur.description:
                cols = [d[0] for d in cur.description]
                data = [list(r) for r in cur.fetchall()]
                self.rows.emit(data, cols)
            else:
                self.log.emit(f"✓ {cur.rowcount if cur.rowcount >= 0 else 'OK'}", "success")
            cur.close(); conn.close()
        except Exception as e:
            self.log.emit(f"✗ {e}", "error")
            ok = False
        finally:
            self.finished.emit(ok)


# ── New Job Dialog ────────────────────────────────────────────────────────

class NewJobDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New pg_cron Job")
        self.resize(520, 300)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self._name   = QLineEdit()
        self._name.setPlaceholderText("e.g. nightly_vacuum")
        self._sched  = QComboBox()
        self._sched.setEditable(True)
        self._sched.addItems([
            "0 2 * * *",        # every day 02:00
            "*/10 * * * *",     # every 10 min
            "0 * * * *",        # hourly
            "0 0 * * 0",        # weekly Sunday midnight
            "0 0 1 * *",        # monthly 1st
        ])
        self._cmd = QTextEdit()
        self._cmd.setMaximumHeight(80)
        self._cmd.setPlaceholderText("SELECT cron.schedule(...) or any SQL")
        self._db = QLineEdit()
        self._db.setPlaceholderText("database name (leave blank = current)")
        form.addRow("Job name:", self._name)
        form.addRow("Schedule (cron):", self._sched)
        form.addRow("Command (SQL):", self._cmd)
        form.addRow("Database:", self._db)
        lay.addLayout(form)

        # quick templates
        tpl_row = QHBoxLayout()
        for label, cmd in [
            ("VACUUM",   "VACUUM ANALYZE;"),
            ("Refresh MV", "REFRESH MATERIALIZED VIEW CONCURRENTLY public.my_view;"),
            ("pg_dump",  "SELECT cron.schedule('backup','0 2 * * *',$$COPY (SELECT 1) TO '/dev/null'$$);"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(24)
            b.clicked.connect(lambda _, c=cmd: self._cmd.setPlainText(c))
            tpl_row.addWidget(b)
        tpl_row.addStretch()
        lay.addLayout(tpl_row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def get_sql(self) -> str:
        name  = self._name.text().strip()
        sched = self._sched.currentText().strip()
        cmd   = self._cmd.toPlainText().strip()
        db    = self._db.text().strip()
        if not name or not sched or not cmd:
            return ""
        if db:
            return (f"SELECT cron.schedule_in_database("
                    f"'{name}', '{sched}', $cmd${cmd}$cmd$, '{db}');")
        return f"SELECT cron.schedule('{name}', '{sched}', $cmd${cmd}$cmd$);"


# ── Main panel ────────────────────────────────────────────────────────────

class AutomationPanel(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._conn_params: dict = {}
        self._worker: AutoWorker | None = None
        self._setup_ui()

    def set_connection(self, params: dict):
        self._conn_params = params
        self._check_pgcron()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.addTab(self._build_cron_tab(),    "⏰  pg_cron Jobs")
        tabs.addTab(self._build_history_tab(), "📋  Job History")
        tabs.addTab(self._build_backup_tab(),  "💾  Auto-Backup")
        tabs.addTab(self._build_event_tab(),   "🔔  Event Triggers")
        root.addWidget(tabs, 1)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 10))
        self._log.setMaximumHeight(90)
        root.addWidget(self._log)

    # ── pg_cron tab ───────────────────────────────────────────────────────

    def _build_cron_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        self._pgcron_status = QLabel("⚠  pg_cron status unknown — connect to DB first")
        self._pgcron_status.setStyleSheet("font-weight:bold; color:#d29922;")
        lay.addWidget(self._pgcron_status)

        install_row = QHBoxLayout()
        self._install_btn = QPushButton("CREATE EXTENSION pg_cron")
        self._install_btn.clicked.connect(self._install_pgcron)
        install_row.addWidget(self._install_btn)
        install_row.addStretch()
        lay.addLayout(install_row)

        self._jobs_table = QTableWidget(0, 5)
        self._jobs_table.setHorizontalHeaderLabels(
            ["ID", "Name", "Schedule", "Command", "Active"])
        self._jobs_table.horizontalHeader().setStretchLastSection(True)
        self._jobs_table.setAlternatingRowColors(True)
        self._jobs_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._jobs_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self._jobs_table, 1)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("↺ Refresh")
        btn_refresh.clicked.connect(self._load_jobs)
        btn_new = QPushButton("＋ New Job")
        btn_new.clicked.connect(self._new_job)
        btn_toggle = QPushButton("⏸ Toggle Active")
        btn_toggle.clicked.connect(self._toggle_job)
        btn_drop = QPushButton("🗑 Drop Job")
        btn_drop.clicked.connect(self._drop_job)
        btn_run_now = QPushButton("▶ Run Now")
        btn_run_now.clicked.connect(self._run_job_now)
        for b in (btn_refresh, btn_new, btn_toggle, btn_drop, btn_run_now):
            btn_row.addWidget(b)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return w

    def _check_pgcron(self):
        if not self._conn_params:
            return
        self._exec_sql(
            "SELECT extname FROM pg_extension WHERE extname='pg_cron'",
            fetch=True,
            _callback=self._on_pgcron_check)

    def _on_pgcron_check(self, rows: list, cols: list):
        if rows:
            self._pgcron_status.setText("✓ pg_cron installed")
            self._pgcron_status.setStyleSheet("font-weight:bold; color:#3fb950;")
            self._install_btn.setVisible(False)
            self._load_jobs()
        else:
            self._pgcron_status.setText(
                "✗ pg_cron NOT installed — click button to install")
            self._pgcron_status.setStyleSheet("font-weight:bold; color:#ff7b72;")
            self._install_btn.setVisible(True)

    def _install_pgcron(self):
        self._exec_sql("CREATE EXTENSION IF NOT EXISTS pg_cron;")
        QTimer.singleShot(800, self._check_pgcron)

    def _load_jobs(self):
        self._exec_sql(
            "SELECT jobid, jobname, schedule, command, active "
            "FROM cron.job ORDER BY jobid",
            fetch=True,
            _callback=self._populate_jobs)

    def _populate_jobs(self, rows: list, cols: list):
        t = self._jobs_table
        t.setRowCount(0)
        for row in rows:
            r = t.rowCount(); t.insertRow(r)
            for c, val in enumerate(row):
                item = QTableWidgetItem(str(val) if val is not None else "")
                if c == 4:  # active
                    item.setForeground(
                        QColor("#3fb950") if val else QColor("#ff7b72"))
                t.setItem(r, c, item)

    def _new_job(self):
        dlg = NewJobDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            sql = dlg.get_sql()
            if sql:
                self._exec_sql(sql, _callback=lambda r, c: self._load_jobs())
            else:
                QMessageBox.warning(self, "Empty", "Fill in all fields.")

    def _selected_jobid(self) -> str | None:
        rows = self._jobs_table.selectedItems()
        if not rows:
            return None
        return self._jobs_table.item(
            self._jobs_table.currentRow(), 0).text()

    def _toggle_job(self):
        jid = self._selected_jobid()
        if not jid:
            return
        # Check current state
        row = self._jobs_table.currentRow()
        active_item = self._jobs_table.item(row, 4)
        currently_active = active_item and active_item.text() == "True"
        new_val = "false" if currently_active else "true"
        self._exec_sql(
            f"UPDATE cron.job SET active={new_val} WHERE jobid={jid};",
            _callback=lambda r, c: self._load_jobs())

    def _drop_job(self):
        jid = self._selected_jobid()
        if not jid:
            return
        if QMessageBox.question(self, "Drop job",
                f"Drop cron job id={jid}?") != QMessageBox.StandardButton.Yes:
            return
        self._exec_sql(
            f"SELECT cron.unschedule({jid});",
            _callback=lambda r, c: self._load_jobs())

    def _run_job_now(self):
        jid = self._selected_jobid()
        if not jid:
            return
        row = self._jobs_table.currentRow()
        cmd = self._jobs_table.item(row, 3)
        if not cmd:
            return
        self._exec_sql(cmd.text())

    # ── History tab ───────────────────────────────────────────────────────

    def _build_history_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        self._hist_table = QTableWidget(0, 6)
        self._hist_table.setHorizontalHeaderLabels(
            ["Run ID", "Job ID", "Job Name", "Start Time", "Status", "Return Msg"])
        self._hist_table.horizontalHeader().setStretchLastSection(True)
        self._hist_table.setAlternatingRowColors(True)
        self._hist_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self._hist_table, 1)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("↺ Refresh")
        btn_refresh.clicked.connect(self._load_history)
        self._hist_limit = QSpinBox()
        self._hist_limit.setRange(10, 500)
        self._hist_limit.setValue(50)
        btn_row.addWidget(btn_refresh)
        btn_row.addWidget(QLabel("Show last:"))
        btn_row.addWidget(self._hist_limit)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return w

    def _load_history(self):
        lim = self._hist_limit.value()
        self._exec_sql(
            f"SELECT runid, jobid, jobname, start_time, status, return_message "
            f"FROM cron.job_run_details ORDER BY start_time DESC LIMIT {lim}",
            fetch=True, _callback=self._populate_history)

    def _populate_history(self, rows: list, cols: list):
        t = self._hist_table
        t.setRowCount(0)
        for row in rows:
            r = t.rowCount(); t.insertRow(r)
            for c, val in enumerate(row):
                item = QTableWidgetItem(str(val) if val is not None else "")
                if c == 4:  # status
                    color = ("#3fb950" if str(val) == "succeeded"
                             else "#ff7b72")
                    item.setForeground(QColor(color))
                t.setItem(r, c, item)

    # ── Auto-backup tab ───────────────────────────────────────────────────

    def _build_backup_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        info = QLabel(
            "Schedule automatic pg_dump backups via pg_cron. "
            "Requires pg_cron to be installed.")
        info.setWordWrap(True)
        info.setStyleSheet("color: var(--color-text-secondary, gray);")
        lay.addWidget(info)

        bk_box = QGroupBox("Backup Schedule")
        bfl = QFormLayout(bk_box)
        self._bk_db    = QLineEdit()
        self._bk_db.setPlaceholderText("database name")
        self._bk_path  = QLineEdit("/var/backups/postgis")
        btn_browse = QPushButton("…")
        btn_browse.setFixedWidth(28)
        btn_browse.clicked.connect(self._browse_backup_dir)
        path_row = QHBoxLayout()
        path_row.addWidget(self._bk_path)
        path_row.addWidget(btn_browse)
        path_wgt = QWidget(); path_wgt.setLayout(path_row)
        self._bk_sched = QComboBox()
        self._bk_sched.setEditable(True)
        self._bk_sched.addItems([
            "0 2 * * *",    # daily 2 AM
            "0 2 * * 0",    # weekly
            "0 2 1 * *",    # monthly
        ])
        self._bk_fmt = QComboBox()
        self._bk_fmt.addItems(["custom (-Fc)", "plain SQL (-Fp)", "directory (-Fd)"])
        self._bk_compress = QCheckBox("Compress (-Z9)")
        self._bk_compress.setChecked(True)
        bfl.addRow("Database:", self._bk_db)
        bfl.addRow("Output dir:", path_wgt)
        bfl.addRow("Schedule:", self._bk_sched)
        bfl.addRow("Format:", self._bk_fmt)
        bfl.addRow("", self._bk_compress)
        lay.addWidget(bk_box)

        btn_row = QHBoxLayout()
        btn_create = QPushButton("▶  Create Backup Schedule")
        btn_create.setStyleSheet("font-weight:bold;")
        btn_create.clicked.connect(self._create_backup_schedule)
        btn_preview = QPushButton("Preview SQL")
        btn_preview.clicked.connect(self._preview_backup_sql)
        btn_row.addWidget(btn_create)
        btn_row.addWidget(btn_preview)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        lay.addStretch()
        return w

    def _browse_backup_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Backup directory")
        if d:
            self._bk_path.setText(d)

    def _build_backup_sql(self) -> str:
        db      = self._bk_db.text().strip() or "mydb"
        path    = self._bk_path.text().strip()
        sched   = self._bk_sched.currentText().strip()
        fmt_map = {0: "-Fc", 1: "-Fp", 2: "-Fd"}
        fmt     = fmt_map.get(self._bk_fmt.currentIndex(), "-Fc")
        comp    = " -Z9" if self._bk_compress.isChecked() else ""
        cmd     = (f"pg_dump -h $PGHOST -p $PGPORT -U $PGUSER "
                   f"{fmt}{comp} -f {path}/{db}_$(date +%Y%m%d_%H%M%S).dump {db}")
        return (f"SELECT cron.schedule("
                f"'auto_backup_{db}', '{sched}', "
                f"$$\\! {cmd}$$);")

    def _preview_backup_sql(self):
        self._log.setPlainText(self._build_backup_sql())

    def _create_backup_schedule(self):
        self._exec_sql(self._build_backup_sql(),
                       _callback=lambda r, c: self._load_jobs())

    # ── Event Triggers tab ────────────────────────────────────────────────

    def _build_event_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        info = QLabel(
            "Event triggers fire on DDL events (CREATE, ALTER, DROP). "
            "Useful for DDL auditing, preventing accidental drops, etc.")
        info.setWordWrap(True)
        lay.addWidget(info)

        self._ev_table = QTableWidget(0, 4)
        self._ev_table.setHorizontalHeaderLabels(
            ["Name", "Event", "Function", "Enabled"])
        self._ev_table.horizontalHeader().setStretchLastSection(True)
        self._ev_table.setAlternatingRowColors(True)
        self._ev_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self._ev_table, 1)

        create_box = QGroupBox("Create Event Trigger")
        cfl = QFormLayout(create_box)
        self._ev_name   = QLineEdit()
        self._ev_event  = QComboBox()
        self._ev_event.addItems([
            "ddl_command_start", "ddl_command_end",
            "sql_drop", "table_rewrite"
        ])
        self._ev_tags   = QLineEdit()
        self._ev_tags.setPlaceholderText("DROP TABLE, ALTER TABLE (comma-separated, optional)")
        self._ev_func   = QLineEdit()
        self._ev_func.setPlaceholderText("schema.function_name() — must return event_trigger")
        cfl.addRow("Trigger name:", self._ev_name)
        cfl.addRow("Event:", self._ev_event)
        cfl.addRow("Tags filter:", self._ev_tags)
        cfl.addRow("Function:", self._ev_func)
        lay.addWidget(create_box)

        btn_row = QHBoxLayout()
        btn_refresh = QPushButton("↺ Refresh")
        btn_refresh.clicked.connect(self._load_event_triggers)
        btn_create = QPushButton("＋ Create")
        btn_create.clicked.connect(self._create_event_trigger)
        btn_drop = QPushButton("🗑 Drop")
        btn_drop.clicked.connect(self._drop_event_trigger)
        btn_insert_fn = QPushButton("📋 Insert sample function")
        btn_insert_fn.clicked.connect(self._insert_sample_fn)
        for b in (btn_refresh, btn_create, btn_drop, btn_insert_fn):
            btn_row.addWidget(b)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return w

    def _load_event_triggers(self):
        self._exec_sql(
            "SELECT evtname, evtevent, evtfoid::regproc, evtenabled "
            "FROM pg_event_trigger ORDER BY evtname",
            fetch=True, _callback=self._populate_ev)

    def _populate_ev(self, rows: list, cols: list):
        t = self._ev_table; t.setRowCount(0)
        for row in rows:
            r = t.rowCount(); t.insertRow(r)
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(str(val) if val is not None else ""))

    def _create_event_trigger(self):
        name  = self._ev_name.text().strip()
        event = self._ev_event.currentText()
        func  = self._ev_func.text().strip()
        tags  = self._ev_tags.text().strip()
        if not name or not func:
            QMessageBox.warning(self, "Missing", "Enter trigger name and function.")
            return
        when = ""
        if tags:
            tag_list = ", ".join(f"'{t.strip()}'" for t in tags.split(","))
            when = f" WHEN TAG IN ({tag_list})"
        sql = (f"CREATE EVENT TRIGGER {name} "
               f"ON {event}{when} EXECUTE FUNCTION {func};")
        self._exec_sql(sql, _callback=lambda r, c: self._load_event_triggers())

    def _drop_event_trigger(self):
        row = self._ev_table.currentRow()
        if row < 0:
            return
        name = self._ev_table.item(row, 0).text()
        if QMessageBox.question(self, "Drop",
                f"Drop event trigger '{name}'?") == QMessageBox.StandardButton.Yes:
            self._exec_sql(f"DROP EVENT TRIGGER IF EXISTS {name};",
                           _callback=lambda r, c: self._load_event_triggers())

    def _insert_sample_fn(self):
        sql = """-- DDL audit function (run this first, then create the trigger)
CREATE OR REPLACE FUNCTION public.log_ddl_commands()
RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    INSERT INTO public.ddl_audit_log(event_time, command_tag, object_type, schema_name, object_identity)
    VALUES (now(), r.command_tag, r.object_type, r.schema_name, r.object_identity);
  END LOOP;
END;
$$;

-- Create the audit log table if it doesn't exist
CREATE TABLE IF NOT EXISTS public.ddl_audit_log (
  id          BIGSERIAL PRIMARY KEY,
  event_time  TIMESTAMPTZ DEFAULT now(),
  command_tag TEXT,
  object_type TEXT,
  schema_name TEXT,
  object_identity TEXT
);"""
        self._log.setPlainText(sql)

    # ── Shared exec ───────────────────────────────────────────────────────

    def _exec_sql(self, sql: str, fetch: bool = False, _callback=None):
        if not self._conn_params:
            self._log_msg("No DB connection.", "error")
            return
        w = AutoWorker(self._conn_params, sql, fetch=fetch)
        w.log.connect(self._log_msg)
        if _callback:
            w.rows.connect(_callback)
        self._worker = w
        launch(w)

    def _log_msg(self, msg: str, level: str = "info"):
        colors = {
            "success": "#3fb950", "error": "#ff7b72",
            "warn": "#d29922",    "info": "#aaa",
        }
        self._log.append(
            f'<span style="color:{colors.get(level, "#aaa")};">{msg}</span>')
