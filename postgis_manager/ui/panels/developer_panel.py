"""Developer Panel — function editor, ERD viewer, extensions, pg_notify, migrations."""

from __future__ import annotations
import psycopg2
import psycopg2.extras
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QBrush
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QLineEdit,
    QComboBox, QTextEdit, QGroupBox, QFormLayout, QSpinBox,
    QMessageBox, QCheckBox, QHeaderView, QAbstractItemView,
    QTreeWidget, QTreeWidgetItem, QSplitter, QFileDialog,
    QDialog, QDialogButtonBox,
)


# ── Worker ────────────────────────────────────────────────────────────────

class DevWorker(QThread):
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
                n = cur.rowcount if cur.rowcount >= 0 else "?"
                self.log.emit(f"✓ {n}", "success")
            cur.close(); conn.close()
        except Exception as e:
            self.log.emit(f"✗ {e}", "error")
            ok = False
        finally:
            self.finished.emit(ok)


# ── pg_notify listener ────────────────────────────────────────────────────

class NotifyListener(QThread):
    received = pyqtSignal(str, str, str)  # channel, pid, payload
    stopped  = pyqtSignal()

    def __init__(self, conn_params: dict, channels: list[str]):
        super().__init__()
        self.conn_params = conn_params
        self.channels = channels
        self._running = False

    def run(self):
        import select
        self._running = True
        try:
            conn = psycopg2.connect(**self.conn_params)
            conn.autocommit = True
            cur = conn.cursor()
            for ch in self.channels:
                cur.execute(f"LISTEN {ch};")
            cur.close()
            while self._running:
                if select.select([conn], [], [], 1.0)[0]:
                    conn.poll()
                    while conn.notifies:
                        n = conn.notifies.pop(0)
                        self.received.emit(n.channel, str(n.pid), n.payload)
            conn.close()
        except Exception:
            pass
        finally:
            self.stopped.emit()

    def stop(self):
        self._running = False


# ── Main panel ────────────────────────────────────────────────────────────

class DeveloperPanel(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._conn_params: dict = {}
        self._worker: DevWorker | None = None
        self._notify_listener: NotifyListener | None = None
        self._setup_ui()

    def set_connection(self, params: dict):
        self._conn_params = params
        self._load_functions()
        self._load_extensions()
        self._load_erd()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.addTab(self._build_func_tab(),   "ƒ  Functions / Procedures")
        tabs.addTab(self._build_erd_tab(),    "🗺  ERD Viewer")
        tabs.addTab(self._build_ext_tab(),    "📦  Extensions")
        tabs.addTab(self._build_notify_tab(), "📡  pg_notify")
        tabs.addTab(self._build_migrate_tab(),"🚀  Migrations")
        root.addWidget(tabs, 1)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 10))
        self._log.setMaximumHeight(80)
        root.addWidget(self._log)

    # ── Function / Procedure editor ───────────────────────────────────────

    def _build_func_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: function list
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        search_row = QHBoxLayout()
        self._fn_search = QLineEdit()
        self._fn_search.setPlaceholderText("Search function…")
        self._fn_search.textChanged.connect(self._filter_functions)
        btn_reload = QPushButton("↺")
        btn_reload.setFixedWidth(28)
        btn_reload.clicked.connect(self._load_functions)
        search_row.addWidget(self._fn_search)
        search_row.addWidget(btn_reload)
        ll.addLayout(search_row)

        self._fn_table = QTableWidget(0, 4)
        self._fn_table.setHorizontalHeaderLabels(
            ["Schema", "Name", "Language", "Kind"])
        self._fn_table.setAlternatingRowColors(True)
        self._fn_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._fn_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._fn_table.doubleClicked.connect(self._load_fn_source)
        ll.addWidget(self._fn_table, 1)
        splitter.addWidget(left)

        # Right: editor
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        new_row = QHBoxLayout()
        btn_new_fn   = QPushButton("＋ New Function")
        btn_new_proc = QPushButton("＋ New Procedure")
        btn_new_fn.clicked.connect(lambda: self._editor.setPlainText(
            self._fn_template("FUNCTION")))
        btn_new_proc.clicked.connect(lambda: self._editor.setPlainText(
            self._fn_template("PROCEDURE")))
        new_row.addWidget(btn_new_fn)
        new_row.addWidget(btn_new_proc)
        new_row.addStretch()
        rl.addLayout(new_row)

        self._editor = QTextEdit()
        self._editor.setFont(QFont("Courier New", 11))
        self._editor.setPlaceholderText(
            "-- Double-click a function to load its source\n"
            "-- or click + New Function above")
        rl.addWidget(self._editor, 1)

        fn_btn_row = QHBoxLayout()
        btn_save = QPushButton("▶ Run (CREATE OR REPLACE)")
        btn_save.setStyleSheet("font-weight:bold;")
        btn_save.clicked.connect(self._save_function)
        btn_drop = QPushButton("🗑 Drop selected")
        btn_drop.clicked.connect(self._drop_function)
        btn_copy = QPushButton("📋 Copy source")
        btn_copy.clicked.connect(lambda: self._copy_text(
            self._editor.toPlainText()))
        fn_btn_row.addWidget(btn_save)
        fn_btn_row.addWidget(btn_drop)
        fn_btn_row.addWidget(btn_copy)
        fn_btn_row.addStretch()
        rl.addLayout(fn_btn_row)
        splitter.addWidget(right)
        splitter.setSizes([320, 680])
        lay.addWidget(splitter, 1)
        return w

    def _fn_template(self, kind: str) -> str:
        if kind == "PROCEDURE":
            return ("CREATE OR REPLACE PROCEDURE public.my_procedure()\n"
                    "LANGUAGE plpgsql AS $$\nBEGIN\n  -- your code here\nEND;\n$$;")
        return ("CREATE OR REPLACE FUNCTION public.my_function()\n"
                "RETURNS void LANGUAGE plpgsql AS $$\n"
                "BEGIN\n  -- your code here\nEND;\n$$;")

    def _load_functions(self):
        self._exec_sql(
            """SELECT n.nspname AS schema,
                      p.proname AS name,
                      l.lanname AS language,
                      CASE p.prokind
                        WHEN 'f' THEN 'FUNCTION'
                        WHEN 'p' THEN 'PROCEDURE'
                        WHEN 'a' THEN 'AGGREGATE'
                        WHEN 'w' THEN 'WINDOW'
                        ELSE 'OTHER' END AS kind,
                      p.oid
               FROM pg_proc p
               JOIN pg_namespace n ON n.oid = p.pronamespace
               JOIN pg_language l  ON l.oid = p.prolang
               WHERE n.nspname NOT IN ('pg_catalog','information_schema')
               ORDER BY n.nspname, p.proname""",
            fetch=True, _callback=self._populate_fn)

    def _populate_fn(self, rows: list, cols: list):
        self._fn_all_rows = rows
        self._fn_table.setRowCount(0)
        search = self._fn_search.text().lower()
        for row in rows:
            if search and search not in row[1].lower():
                continue
            r = self._fn_table.rowCount()
            self._fn_table.insertRow(r)
            for c, val in enumerate(row[:4]):
                self._fn_table.setItem(
                    r, c, QTableWidgetItem(str(val) if val is not None else ""))
            self._fn_table.item(r, 0).setData(
                Qt.ItemDataRole.UserRole, row[4])  # store OID

    def _filter_functions(self, _):
        rows = getattr(self, "_fn_all_rows", [])
        self._populate_fn(rows, [])

    def _load_fn_source(self):
        row = self._fn_table.currentRow()
        if row < 0: return
        oid = self._fn_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not oid: return
        self._exec_sql(
            f"SELECT pg_get_functiondef({oid})",
            fetch=True,
            _callback=lambda rows, cols: self._editor.setPlainText(
                rows[0][0] if rows else ""))

    def _save_function(self):
        sql = self._editor.toPlainText().strip()
        if not sql: return
        self._exec_sql(sql, _callback=lambda r, c: self._load_functions())

    def _drop_function(self):
        row = self._fn_table.currentRow()
        if row < 0: return
        schema = self._fn_table.item(row, 0).text()
        name   = self._fn_table.item(row, 1).text()
        kind   = self._fn_table.item(row, 3).text()
        if QMessageBox.question(self, "Drop",
                f'DROP {kind} "{schema}"."{name}"?'
                ) == QMessageBox.StandardButton.Yes:
            self._exec_sql(
                f'DROP {kind} IF EXISTS "{schema}"."{name}" CASCADE;',
                _callback=lambda r, c: self._load_functions())

    # ── ERD tab ───────────────────────────────────────────────────────────

    def _build_erd_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        top_row = QHBoxLayout()
        self._erd_schema = QComboBox()
        self._erd_schema.setEditable(True)
        self._erd_schema.addItem("public")
        btn_load = QPushButton("Load ERD")
        btn_load.clicked.connect(self._load_erd)
        top_row.addWidget(QLabel("Schema:"))
        top_row.addWidget(self._erd_schema)
        top_row.addWidget(btn_load)
        top_row.addStretch()
        lay.addLayout(top_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._erd_tree = QTreeWidget()
        self._erd_tree.setHeaderLabels(["Table / Column", "Type", "Nullable", "FK →"])
        self._erd_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._erd_tree.setAlternatingRowColors(True)
        splitter.addWidget(self._erd_tree)

        # FK list
        fk_w = QWidget()
        fk_l = QVBoxLayout(fk_w)
        fk_l.addWidget(QLabel("Foreign Keys"))
        self._fk_table = QTableWidget(0, 4)
        self._fk_table.setHorizontalHeaderLabels(
            ["From Table", "From Column", "To Table", "To Column"])
        self._fk_table.horizontalHeader().setStretchLastSection(True)
        self._fk_table.setAlternatingRowColors(True)
        self._fk_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        fk_l.addWidget(self._fk_table, 1)
        splitter.addWidget(fk_w)
        splitter.setSizes([500, 300])
        lay.addWidget(splitter, 1)
        return w

    def _load_erd(self):
        schema = self._erd_schema.currentText().strip() or "public"
        self._exec_sql(
            f"""SELECT c.table_name,
                       c.column_name,
                       c.data_type,
                       c.is_nullable,
                       kcu.constraint_name IS NOT NULL AS is_pk
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage kcu
                  ON kcu.table_schema = c.table_schema
                 AND kcu.table_name   = c.table_name
                 AND kcu.column_name  = c.column_name
                LEFT JOIN information_schema.table_constraints tc
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.constraint_type = 'PRIMARY KEY'
                WHERE c.table_schema = '{schema}'
                ORDER BY c.table_name, c.ordinal_position""",
            fetch=True, _callback=self._populate_erd)
        self._exec_sql(
            f"""SELECT
                  tc.table_name       AS from_table,
                  kcu.column_name     AS from_col,
                  ccu.table_name      AS to_table,
                  ccu.column_name     AS to_col
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name = tc.constraint_name
                 AND kcu.table_schema    = tc.table_schema
                JOIN information_schema.referential_constraints rc
                  ON rc.constraint_name = tc.constraint_name
                 AND rc.constraint_schema = tc.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = rc.unique_constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema    = '{schema}'""",
            fetch=True, _callback=self._populate_fk)

    def _populate_erd(self, rows: list, cols: list):
        self._erd_tree.clear()
        tables: dict[str, QTreeWidgetItem] = {}
        for table, col, dtype, nullable, is_pk in rows:
            if table not in tables:
                tbl_item = QTreeWidgetItem([f"📋 {table}", "", "", ""])
                f = tbl_item.font(0); f.setBold(True); tbl_item.setFont(0, f)
                self._erd_tree.addTopLevelItem(tbl_item)
                tables[table] = tbl_item
            icon = "🔑" if is_pk else "  "
            col_item = QTreeWidgetItem([
                f"  {icon} {col}", dtype or "", nullable or "", ""])
            tables[table].addChild(col_item)
        self._erd_tree.expandAll()

    def _populate_fk(self, rows: list, cols: list):
        t = self._fk_table; t.setRowCount(0)
        for row in rows:
            r = t.rowCount(); t.insertRow(r)
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(str(val) or ""))
            # also update ERD tree FK column
            # (optional enhancement: draw arrows)

    # ── Extensions tab ────────────────────────────────────────────────────

    def _build_ext_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        inst_w = QWidget()
        inst_l = QVBoxLayout(inst_w)
        inst_l.addWidget(QLabel("Installed Extensions"))
        self._ext_installed = QTableWidget(0, 3)
        self._ext_installed.setHorizontalHeaderLabels(
            ["Name", "Version", "Schema"])
        self._ext_installed.setAlternatingRowColors(True)
        self._ext_installed.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._ext_installed.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        inst_l.addWidget(self._ext_installed, 1)
        btn_row = QHBoxLayout()
        btn_reload = QPushButton("↺")
        btn_reload.setFixedWidth(28)
        btn_reload.clicked.connect(self._load_extensions)
        btn_drop = QPushButton("🗑 Drop selected")
        btn_drop.clicked.connect(self._drop_extension)
        btn_row.addWidget(btn_reload)
        btn_row.addWidget(btn_drop)
        btn_row.addStretch()
        inst_l.addLayout(btn_row)
        splitter.addWidget(inst_w)

        avail_w = QWidget()
        avail_l = QVBoxLayout(avail_w)
        avail_l.addWidget(QLabel("Available Extensions (pg_available_extensions)"))
        self._ext_search = QLineEdit()
        self._ext_search.setPlaceholderText("Filter…")
        self._ext_search.textChanged.connect(self._filter_available)
        avail_l.addWidget(self._ext_search)
        self._ext_available = QTableWidget(0, 3)
        self._ext_available.setHorizontalHeaderLabels(
            ["Name", "Default version", "Comment"])
        self._ext_available.horizontalHeader().setStretchLastSection(True)
        self._ext_available.setAlternatingRowColors(True)
        self._ext_available.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._ext_available.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        avail_l.addWidget(self._ext_available, 1)
        btn_install = QPushButton("＋ CREATE EXTENSION")
        btn_install.clicked.connect(self._install_extension)
        avail_l.addWidget(btn_install)
        splitter.addWidget(avail_w)
        splitter.setSizes([300, 500])
        lay.addWidget(splitter, 1)
        return w

    def _load_extensions(self):
        self._exec_sql(
            "SELECT name, installed_version, extnamespace::regnamespace::text "
            "FROM pg_extension e "
            "RIGHT JOIN pg_available_extensions ON name=extname "
            "WHERE installed_version IS NOT NULL "
            "ORDER BY name",
            fetch=True, _callback=self._populate_installed)
        self._exec_sql(
            "SELECT name, default_version, comment "
            "FROM pg_available_extensions ORDER BY name",
            fetch=True, _callback=self._populate_available)

    def _populate_installed(self, rows: list, cols: list):
        t = self._ext_installed; t.setRowCount(0)
        for row in rows:
            r = t.rowCount(); t.insertRow(r)
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(str(val) or ""))

    def _populate_available(self, rows: list, cols: list):
        self._ext_all = rows
        self._render_available(rows)

    def _render_available(self, rows):
        t = self._ext_available; t.setRowCount(0)
        for row in rows:
            r = t.rowCount(); t.insertRow(r)
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(str(val) or ""))

    def _filter_available(self, text: str):
        rows = getattr(self, "_ext_all", [])
        filtered = [r for r in rows if text.lower() in r[0].lower()]
        self._render_available(filtered)

    def _install_extension(self):
        row = self._ext_available.currentRow()
        if row < 0: return
        name = self._ext_available.item(row, 0).text()
        if QMessageBox.question(self, "Install",
                f"CREATE EXTENSION {name}?") == QMessageBox.StandardButton.Yes:
            self._exec_sql(
                f"CREATE EXTENSION IF NOT EXISTS {name};",
                _callback=lambda r, c: self._load_extensions())

    def _drop_extension(self):
        row = self._ext_installed.currentRow()
        if row < 0: return
        name = self._ext_installed.item(row, 0).text()
        if QMessageBox.question(self, "Drop extension",
                f"DROP EXTENSION {name} CASCADE?") == QMessageBox.StandardButton.Yes:
            self._exec_sql(
                f"DROP EXTENSION IF EXISTS {name} CASCADE;",
                _callback=lambda r, c: self._load_extensions())

    # ── pg_notify tab ─────────────────────────────────────────────────────

    def _build_notify_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        listen_box = QGroupBox("LISTEN — receive notifications")
        lfl = QFormLayout(listen_box)
        self._notify_channels = QLineEdit("my_channel")
        self._notify_channels.setPlaceholderText("channel1, channel2, …")
        self._notify_btn = QPushButton("▶ Start Listening")
        self._notify_btn.setCheckable(True)
        self._notify_btn.toggled.connect(self._toggle_listen)
        lfl.addRow("Channels:", self._notify_channels)
        lfl.addRow("", self._notify_btn)
        lay.addWidget(listen_box)

        self._notify_log = QTextEdit()
        self._notify_log.setReadOnly(True)
        self._notify_log.setFont(QFont("Courier New", 10))
        self._notify_log.setPlaceholderText(
            "Received notifications will appear here…")
        lay.addWidget(self._notify_log, 1)

        send_box = QGroupBox("NOTIFY — send notification")
        sfl = QFormLayout(send_box)
        self._send_channel = QLineEdit("my_channel")
        self._send_payload = QLineEdit()
        self._send_payload.setPlaceholderText("optional payload (text)")
        btn_send = QPushButton("📤 Send NOTIFY")
        btn_send.clicked.connect(self._send_notify)
        sfl.addRow("Channel:", self._send_channel)
        sfl.addRow("Payload:", self._send_payload)
        sfl.addRow("", btn_send)
        lay.addWidget(send_box)
        return w

    def _toggle_listen(self, on: bool):
        if on:
            chs = [c.strip() for c in
                   self._notify_channels.text().split(",") if c.strip()]
            if not chs or not self._conn_params:
                self._notify_btn.setChecked(False)
                return
            self._notify_listener = NotifyListener(self._conn_params, chs)
            self._notify_listener.received.connect(self._on_notify)
            self._notify_listener.stopped.connect(
                lambda: self._notify_btn.setChecked(False))
            self._notify_listener.start()
            self._notify_btn.setText("⏹ Stop Listening")
        else:
            if self._notify_listener:
                self._notify_listener.stop()
                self._notify_listener = None
            self._notify_btn.setText("▶ Start Listening")

    def _on_notify(self, channel: str, pid: str, payload: str):
        from PyQt6.QtCore import QDateTime
        ts = QDateTime.currentDateTime().toString("HH:mm:ss.zzz")
        self._notify_log.append(
            f'<span style="color:#3fb950;">[{ts}]</span> '
            f'<b>{channel}</b> (pid {pid}): {payload}')

    def _send_notify(self):
        ch  = self._send_channel.text().strip()
        pay = self._send_payload.text().strip()
        if not ch: return
        sql = (f"NOTIFY {ch}, $${pay}$$" if pay else f"NOTIFY {ch}")
        self._exec_sql(sql)

    # ── Migrations tab ────────────────────────────────────────────────────

    def _build_migrate_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        info = QLabel(
            "Load and run SQL migration scripts. "
            "Each script is executed in a transaction; "
            "failures roll back automatically.")
        info.setWordWrap(True)
        lay.addWidget(info)

        queue_box = QGroupBox("Migration Queue")
        qfl = QVBoxLayout(queue_box)
        self._mig_list = QTableWidget(0, 3)
        self._mig_list.setHorizontalHeaderLabels(
            ["#", "File", "Status"])
        self._mig_list.horizontalHeader().setStretchLastSection(True)
        self._mig_list.setAlternatingRowColors(True)
        self._mig_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        qfl.addWidget(self._mig_list, 1)

        btn_row = QHBoxLayout()
        btn_add   = QPushButton("＋ Add SQL files")
        btn_add.clicked.connect(self._add_migration_files)
        btn_clear = QPushButton("🗑 Clear queue")
        btn_clear.clicked.connect(lambda: self._mig_list.setRowCount(0))
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()
        qfl.addLayout(btn_row)
        lay.addWidget(queue_box)

        self._mig_log = QTextEdit()
        self._mig_log.setReadOnly(True)
        self._mig_log.setFont(QFont("Courier New", 10))
        self._mig_log.setMaximumHeight(120)
        lay.addWidget(self._mig_log)

        run_row = QHBoxLayout()
        btn_run = QPushButton("▶  Run All Migrations")
        btn_run.setStyleSheet("font-weight:bold;")
        btn_run.clicked.connect(self._run_migrations)
        self._mig_dry = QCheckBox("Dry run (ROLLBACK after each)")
        run_row.addWidget(btn_run)
        run_row.addWidget(self._mig_dry)
        run_row.addStretch()
        lay.addLayout(run_row)
        return w

    def _add_migration_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select SQL migration files",
            "", "SQL files (*.sql);;All (*)")
        for path in sorted(paths):
            r = self._mig_list.rowCount()
            self._mig_list.insertRow(r)
            self._mig_list.setItem(r, 0, QTableWidgetItem(str(r + 1)))
            self._mig_list.setItem(r, 1, QTableWidgetItem(path))
            self._mig_list.setItem(r, 2, QTableWidgetItem("pending"))

    def _run_migrations(self):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection", "Connect to DB first.")
            return
        dry = self._mig_dry.isChecked()
        import threading
        threading.Thread(target=self._exec_migrations, args=(dry,),
                         daemon=True).start()

    def _exec_migrations(self, dry: bool):
        try:
            conn = psycopg2.connect(**self._conn_params)
            n = self._mig_list.rowCount()
            for i in range(n):
                path_item = self._mig_list.item(i, 1)
                stat_item = self._mig_list.item(i, 2)
                if not path_item: continue
                path = path_item.text()
                try:
                    sql = open(path, encoding="utf-8").read()
                    with conn:
                        cur = conn.cursor()
                        cur.execute(sql)
                        if dry:
                            conn.rollback()
                            stat = "dry-run OK"
                        else:
                            conn.commit()
                            stat = "✓ applied"
                        cur.close()
                except Exception as e:
                    conn.rollback()
                    stat = f"✗ {e}"
                finally:
                    stat_item.setText(stat)
                    self._mig_log.append(f"{path}: {stat}")
            conn.close()
        except Exception as e:
            self._mig_log.append(f"Connection error: {e}")

    # ── Shared exec ───────────────────────────────────────────────────────

    def _exec_sql(self, sql: str, fetch: bool = False, _callback=None):
        if not self._conn_params:
            return
        w = DevWorker(self._conn_params, sql, fetch=fetch)
        w.log.connect(self._log_msg)
        if _callback:
            w.rows.connect(_callback)
        w.finished.connect(w.deleteLater)
        self._worker = w
        w.start()

    def _log_msg(self, msg: str, level: str = "info"):
        colors = {
            "success": "#3fb950", "error": "#ff7b72",
            "warn": "#d29922",    "info": "#aaa",
        }
        self._log.append(
            f'<span style="color:{colors.get(level,"#aaa")};">{msg}</span>')

    def _copy_text(self, text: str):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
