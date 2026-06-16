"""Security panel — Role/Grant matrix, Row-Level Security, SSL status."""

from __future__ import annotations

import psycopg2
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QLineEdit,
    QTextEdit, QGroupBox, QFormLayout, QComboBox, QCheckBox,
    QMessageBox, QSplitter, QAbstractItemView,
    QFileDialog, QDialog, QDialogButtonBox,
)

import platform
PLATFORM = platform.system()


# ── SQL helpers ───────────────────────────────────────────────────────────

_ROLES_SQL = """
SELECT
    r.rolname,
    r.rolsuper,
    r.rolinherit,
    r.rolcreaterole,
    r.rolcreatedb,
    r.rolcanlogin,
    r.rolconnlimit,
    COALESCE(ARRAY_AGG(m.rolname ORDER BY m.rolname)
        FILTER (WHERE m.rolname IS NOT NULL), '{}') AS member_of
FROM pg_roles r
LEFT JOIN pg_auth_members am ON am.member = r.oid
LEFT JOIN pg_roles m ON m.oid = am.roleid
GROUP BY r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole,
         r.rolcreatedb, r.rolcanlogin, r.rolconnlimit
ORDER BY r.rolcanlogin DESC, r.rolname;
"""

_TABLE_GRANTS_SQL = """
SELECT
    grantee,
    table_schema,
    table_name,
    STRING_AGG(privilege_type, ', ' ORDER BY privilege_type) AS privileges
FROM information_schema.role_table_grants
WHERE table_schema NOT IN ('pg_catalog','information_schema')
GROUP BY grantee, table_schema, table_name
ORDER BY grantee, table_schema, table_name;
"""

_RLS_POLICIES_SQL = """
SELECT
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd,
    qual,
    with_check
FROM pg_policies
ORDER BY schemaname, tablename, policyname;
"""

_RLS_ENABLED_SQL = """
SELECT schemaname, tablename, rowsecurity
FROM pg_tables
WHERE schemaname NOT IN ('pg_catalog','information_schema')
ORDER BY schemaname, tablename;
"""

_SSL_STATUS_SQL = """
SELECT
    pg_postmaster_start_time()::text AS started,
    version() AS pg_version,
    current_setting('ssl') AS ssl_enabled,
    current_setting('ssl_cert_file') AS ssl_cert_file,
    current_setting('ssl_key_file') AS ssl_key_file,
    current_setting('ssl_ca_file') AS ssl_ca_file;
"""

_ACTIVE_CONNS_SQL = """
SELECT
    pid,
    usename,
    application_name,
    client_addr::text,
    state,
    ssl,
    ssl_version,
    ssl_cipher,
    ROUND(EXTRACT(EPOCH FROM (NOW() - backend_start))::numeric, 0)::int AS age_s
FROM pg_stat_ssl
JOIN pg_stat_activity USING (pid)
WHERE pid <> pg_backend_pid()
ORDER BY ssl DESC, usename;
"""

ALL_TABLE_PRIVS = ["SELECT", "INSERT", "UPDATE", "DELETE",
                   "TRUNCATE", "REFERENCES", "TRIGGER"]


# ── Workers ───────────────────────────────────────────────────────────────

class SecurityLoadWorker(QThread):
    roles_ready   = pyqtSignal(list)
    grants_ready  = pyqtSignal(list)
    rls_ready     = pyqtSignal(list, list)   # policies, rls_tables
    ssl_ready     = pyqtSignal(dict, list)   # server_info, connections
    error         = pyqtSignal(str)
    finished      = pyqtSignal()

    def __init__(self, conn_params: dict):
        super().__init__()
        self.conn_params = conn_params

    def run(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            cur = conn.cursor()

            cur.execute(_ROLES_SQL)
            self.roles_ready.emit([dict(zip(
                ["rolname","rolsuper","rolinherit","rolcreaterole",
                 "rolcreatedb","rolcanlogin","rolconnlimit","member_of"],
                row)) for row in cur.fetchall()])

            cur.execute(_TABLE_GRANTS_SQL)
            self.grants_ready.emit([dict(zip(
                ["grantee","schema","table","privs"],
                row)) for row in cur.fetchall()])

            cur.execute(_RLS_POLICIES_SQL)
            policies = [dict(zip(
                ["schema","table","policy","permissive",
                 "roles","cmd","qual","with_check"],
                row)) for row in cur.fetchall()]

            cur.execute(_RLS_ENABLED_SQL)
            rls_tables = [dict(zip(
                ["schema","table","rowsecurity"], row))
                for row in cur.fetchall()]
            self.rls_ready.emit(policies, rls_tables)

            try:
                cur.execute(_SSL_STATUS_SQL)
                row = cur.fetchone()
                info = dict(zip(
                    ["started","pg_version","ssl_enabled",
                     "ssl_cert_file","ssl_key_file","ssl_ca_file"],
                    row)) if row else {}

                cur.execute(_ACTIVE_CONNS_SQL)
                conns = [dict(zip(
                    ["pid","user","app","client","state",
                     "ssl","ssl_ver","cipher","age_s"],
                    r)) for r in cur.fetchall()]
                self.ssl_ready.emit(info, conns)
            except Exception:
                self.ssl_ready.emit({}, [])

            cur.close()
            conn.close()
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


class GrantWorker(QThread):
    log      = pyqtSignal(str, str)   # msg, level
    finished = pyqtSignal()

    def __init__(self, conn_params: dict, sql: str):
        super().__init__()
        self.conn_params = conn_params
        self.sql = sql

    def run(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute(self.sql)
            cur.close()
            conn.close()
            self.log.emit(f"✓ {self.sql}", "success")
        except Exception as e:
            self.log.emit(f"✗ {e}", "error")
        finally:
            self.finished.emit()


# ── New Role Dialog ───────────────────────────────────────────────────────

class NewRoleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Role / User")
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        fl = QFormLayout()

        self.name_edit = QLineEdit()
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_cb  = QCheckBox("Can login (user)")
        self.super_cb  = QCheckBox("Superuser")
        self.createdb_cb = QCheckBox("Can create databases")
        self.createrole_cb = QCheckBox("Can create roles")
        self.login_cb.setChecked(True)

        fl.addRow("Role name:", self.name_edit)
        fl.addRow("Password:",  self.pass_edit)
        fl.addRow("", self.login_cb)
        fl.addRow("", self.super_cb)
        fl.addRow("", self.createdb_cb)
        fl.addRow("", self.createrole_cb)
        lay.addLayout(fl)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def build_sql(self) -> str:
        name = self.name_edit.text().strip()
        if not name:
            return ""
        opts = []
        if self.login_cb.isChecked():    opts.append("LOGIN")
        else:                             opts.append("NOLOGIN")
        if self.super_cb.isChecked():    opts.append("SUPERUSER")
        if self.createdb_cb.isChecked(): opts.append("CREATEDB")
        if self.createrole_cb.isChecked(): opts.append("CREATEROLE")
        pw = self.pass_edit.text()
        if pw:
            opts.append(f"PASSWORD '{pw}'")
        return f"CREATE ROLE \"{name}\" {' '.join(opts)};"


# ── New RLS Policy Dialog ─────────────────────────────────────────────────

class NewPolicyDialog(QDialog):
    def __init__(self, schemas: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create RLS Policy")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        fl = QFormLayout()

        self.schema_cb = QComboBox(); self.schema_cb.addItems(schemas)
        self.table_edit  = QLineEdit()
        self.policy_edit = QLineEdit()
        self.cmd_cb = QComboBox()
        self.cmd_cb.addItems(["ALL", "SELECT", "INSERT", "UPDATE", "DELETE"])
        self.roles_edit = QLineEdit("PUBLIC")
        self.using_edit = QLineEdit()
        self.using_edit.setPlaceholderText("e.g. user_id = current_user_id()")
        self.check_edit = QLineEdit()
        self.check_edit.setPlaceholderText("WITH CHECK expression (optional)")
        self.permissive_cb = QCheckBox("Permissive (default — OR logic)")
        self.permissive_cb.setChecked(True)

        fl.addRow("Schema:",      self.schema_cb)
        fl.addRow("Table:",       self.table_edit)
        fl.addRow("Policy name:", self.policy_edit)
        fl.addRow("Command:",     self.cmd_cb)
        fl.addRow("Roles:",       self.roles_edit)
        fl.addRow("USING:",       self.using_edit)
        fl.addRow("WITH CHECK:",  self.check_edit)
        fl.addRow("",             self.permissive_cb)
        lay.addLayout(fl)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def build_sql(self) -> str:
        schema = self.schema_cb.currentText()
        table  = self.table_edit.text().strip()
        policy = self.policy_edit.text().strip()
        if not table or not policy:
            return ""
        perm = "PERMISSIVE" if self.permissive_cb.isChecked() else "RESTRICTIVE"
        cmd  = self.cmd_cb.currentText()
        roles = self.roles_edit.text().strip() or "PUBLIC"
        using = self.using_edit.text().strip()
        check = self.check_edit.text().strip()
        sql = (f'CREATE POLICY "{policy}" ON "{schema}"."{table}" '
               f'AS {perm} FOR {cmd} TO {roles}')
        if using:
            sql += f' USING ({using})'
        if check:
            sql += f' WITH CHECK ({check})'
        return sql + ";"


# ── Main Panel ────────────────────────────────────────────────────────────

class SecurityPanel(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._conn_params: dict = {}
        self._roles: list[dict] = []
        self._grants: list[dict] = []
        self._policies: list[dict] = []
        self._rls_tables: list[dict] = []
        self._worker = None
        self._setup_ui()

    def set_connection(self, params: dict):
        """Called from MainWindow when DB connects."""
        self._conn_params = params
        self._reload()

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # top bar
        top = QHBoxLayout()
        top.setContentsMargins(8, 6, 8, 0)
        lbl = QLabel("🔐  Security Manager")
        lbl.setStyleSheet("font-size:15px; font-weight:bold;")
        top.addWidget(lbl)
        top.addStretch()
        self._reload_btn = QPushButton("↺  Refresh")
        self._reload_btn.clicked.connect(self._reload)
        top.addWidget(self._reload_btn)
        self._status_lbl = QLabel("Not connected")
        self._status_lbl.setStyleSheet("color:gray; font-size:11px;")
        top.addWidget(self._status_lbl)
        root.addLayout(top)

        tabs = QTabWidget()
        tabs.addTab(self._build_roles_tab(), "👥  Roles & Grants")
        tabs.addTab(self._build_rls_tab(),   "🛡  Row-Level Security")
        tabs.addTab(self._build_ssl_tab(),   "🔒  SSL & Connections")
        root.addWidget(tabs)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(90)
        self._log.setFont(QFont("Courier New", 10))
        root.addWidget(self._log)

    # ── Tab 1: Roles & Grants ─────────────────────────────────────────────

    def _build_roles_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Roles table ──
        roles_box = QGroupBox("Roles & Users")
        rb_lay = QVBoxLayout(roles_box)

        r_bar = QHBoxLayout()
        self._btn_new_role   = QPushButton("➕ New Role/User")
        self._btn_drop_role  = QPushButton("🗑 Drop Role")
        self._btn_new_role.clicked.connect(self._create_role)
        self._btn_drop_role.clicked.connect(self._drop_role)
        r_bar.addWidget(self._btn_new_role)
        r_bar.addWidget(self._btn_drop_role)
        r_bar.addStretch()
        rb_lay.addLayout(r_bar)

        self._roles_table = QTableWidget(0, 7)
        self._roles_table.setHorizontalHeaderLabels(
            ["Role", "Login", "Superuser", "CreateDB",
             "CreateRole", "Conn Limit", "Member of"])
        self._roles_table.horizontalHeader().setStretchLastSection(True)
        self._roles_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._roles_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        rb_lay.addWidget(self._roles_table)
        splitter.addWidget(roles_box)

        # ── Grant matrix ──
        grants_box = QGroupBox("Table Privileges")
        gb_lay = QVBoxLayout(grants_box)

        g_bar = QHBoxLayout()
        self._grant_role_cb   = QComboBox()
        self._grant_schema_cb = QComboBox()
        self._grant_table_edit = QLineEdit()
        self._grant_table_edit.setPlaceholderText("table or * for all")
        self._grant_table_edit.setText("*")
        g_bar.addWidget(QLabel("Role:"))
        g_bar.addWidget(self._grant_role_cb)
        g_bar.addWidget(QLabel("Schema:"))
        g_bar.addWidget(self._grant_schema_cb)
        g_bar.addWidget(QLabel("Table:"))
        g_bar.addWidget(self._grant_table_edit)
        g_bar.addStretch()
        gb_lay.addLayout(g_bar)

        priv_row = QHBoxLayout()
        self._priv_checks: dict[str, QCheckBox] = {}
        for p in ALL_TABLE_PRIVS:
            cb = QCheckBox(p)
            self._priv_checks[p] = cb
            priv_row.addWidget(cb)
        priv_row.addStretch()
        gb_lay.addLayout(priv_row)

        act_row = QHBoxLayout()
        btn_grant  = QPushButton("✔ GRANT")
        btn_revoke = QPushButton("✖ REVOKE")
        btn_grant.clicked.connect(self._do_grant)
        btn_revoke.clicked.connect(self._do_revoke)
        act_row.addWidget(btn_grant)
        act_row.addWidget(btn_revoke)
        act_row.addStretch()
        gb_lay.addLayout(act_row)

        # Existing grants table
        self._grants_table = QTableWidget(0, 4)
        self._grants_table.setHorizontalHeaderLabels(
            ["Grantee", "Schema", "Table", "Privileges"])
        self._grants_table.horizontalHeader().setStretchLastSection(True)
        self._grants_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        gb_lay.addWidget(self._grants_table)
        splitter.addWidget(grants_box)

        splitter.setSizes([220, 380])
        lay.addWidget(splitter)
        return w

    # ── Tab 2: RLS ────────────────────────────────────────────────────────

    def _build_rls_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: tables with RLS on/off
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel("<b>Tables</b>"))

        self._rls_table_widget = QTableWidget(0, 3)
        self._rls_table_widget.setHorizontalHeaderLabels(
            ["Schema", "Table", "RLS"])
        self._rls_table_widget.horizontalHeader().setStretchLastSection(True)
        self._rls_table_widget.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._rls_table_widget.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._rls_table_widget.itemSelectionChanged.connect(
            self._on_rls_table_selected)
        ll.addWidget(self._rls_table_widget, 1)

        tbl_bar = QHBoxLayout()
        btn_enable  = QPushButton("Enable RLS")
        btn_disable = QPushButton("Disable RLS")
        btn_force   = QPushButton("Force RLS")
        btn_enable.clicked.connect(lambda: self._toggle_rls("ENABLE"))
        btn_disable.clicked.connect(lambda: self._toggle_rls("DISABLE"))
        btn_force.clicked.connect(lambda: self._toggle_rls("FORCE"))
        for b in (btn_enable, btn_disable, btn_force):
            tbl_bar.addWidget(b)
        tbl_bar.addStretch()
        ll.addLayout(tbl_bar)
        splitter.addWidget(left)

        # Right: policies for selected table
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.addWidget(QLabel("<b>Policies</b>"))

        self._policies_table = QTableWidget(0, 6)
        self._policies_table.setHorizontalHeaderLabels(
            ["Policy", "Type", "Roles", "Command", "USING", "WITH CHECK"])
        self._policies_table.horizontalHeader().setStretchLastSection(True)
        self._policies_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        rl.addWidget(self._policies_table, 1)

        pol_bar = QHBoxLayout()
        btn_new_pol  = QPushButton("➕ New Policy")
        btn_drop_pol = QPushButton("🗑 Drop Policy")
        btn_new_pol.clicked.connect(self._create_policy)
        btn_drop_pol.clicked.connect(self._drop_policy)
        pol_bar.addWidget(btn_new_pol)
        pol_bar.addWidget(btn_drop_pol)
        pol_bar.addStretch()
        rl.addLayout(pol_bar)
        splitter.addWidget(right)

        splitter.setSizes([300, 500])
        lay.addWidget(splitter)
        return w

    # ── Tab 3: SSL ────────────────────────────────────────────────────────

    def _build_ssl_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        # Server SSL info
        self._ssl_info_box = QGroupBox("Server SSL Configuration")
        fl = QFormLayout(self._ssl_info_box)
        self._ssl_enabled_lbl  = QLabel("—")
        self._ssl_cert_lbl     = QLabel("—")
        self._ssl_key_lbl      = QLabel("—")
        self._ssl_ca_lbl       = QLabel("—")
        self._pg_started_lbl   = QLabel("—")
        for lbl in (self._ssl_enabled_lbl, self._ssl_cert_lbl,
                    self._ssl_key_lbl, self._ssl_ca_lbl):
            lbl.setWordWrap(True)
        fl.addRow("SSL enabled:",   self._ssl_enabled_lbl)
        fl.addRow("Server cert:",   self._ssl_cert_lbl)
        fl.addRow("Server key:",    self._ssl_key_lbl)
        fl.addRow("CA file:",       self._ssl_ca_lbl)
        fl.addRow("Server started:", self._pg_started_lbl)
        lay.addWidget(self._ssl_info_box)

        # Active connections
        conn_box = QGroupBox("Active Connections")
        cb_lay = QVBoxLayout(conn_box)

        self._conns_table = QTableWidget(0, 8)
        self._conns_table.setHorizontalHeaderLabels(
            ["PID", "User", "Application", "Client IP",
             "State", "SSL", "Version/Cipher", "Age (s)"])
        self._conns_table.horizontalHeader().setStretchLastSection(True)
        self._conns_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._conns_table.setAlternatingRowColors(True)
        cb_lay.addWidget(self._conns_table)

        kill_bar = QHBoxLayout()
        btn_kill = QPushButton("⚡ Terminate Selected Connection")
        btn_kill.clicked.connect(self._terminate_connection)
        kill_bar.addWidget(btn_kill)
        kill_bar.addStretch()
        cb_lay.addLayout(kill_bar)
        lay.addWidget(conn_box, 1)

        # Client cert paths (local)
        client_box = QGroupBox("Client Certificate Paths (local)")
        cl_fl = QFormLayout(client_box)
        self._client_cert = QLineEdit()
        self._client_key  = QLineEdit()
        self._root_ca     = QLineEdit()
        for le, name in [
            (self._client_cert, "client_cert"),
            (self._client_key,  "client_key"),
            (self._root_ca,     "root_ca"),
        ]:
            le.setPlaceholderText(f"Path to {name}...")
            btn = QPushButton("Browse")
            btn.setFixedWidth(70)
            row = QHBoxLayout()
            row.addWidget(le)
            row.addWidget(btn)
            btn.clicked.connect(lambda _, f=le: self._browse_cert(f))
            wrap = QWidget(); wrap.setLayout(row)
            cl_fl.addRow(name + ":", wrap)
        lay.addWidget(client_box)
        return w

    # ── Data loading ──────────────────────────────────────────────────────

    def _reload(self):
        if not self._conn_params:
            self._try_get_conn_from_db()
        if not self._conn_params:
            self._status_lbl.setText("Not connected — open a connection first")
            return
        self._status_lbl.setText("Loading…")
        self._reload_btn.setEnabled(False)
        self._worker = SecurityLoadWorker(self._conn_params)
        self._worker.roles_ready.connect(self._on_roles)
        self._worker.grants_ready.connect(self._on_grants)
        self._worker.rls_ready.connect(self._on_rls)
        self._worker.ssl_ready.connect(self._on_ssl)
        self._worker.error.connect(
            lambda e: (self._status_lbl.setText(f"Error: {e}"),
                       self._log_msg(f"Load error: {e}", "error")))
        self._worker.finished.connect(
            lambda: (self._status_lbl.setText("Loaded"),
                     self._reload_btn.setEnabled(True)))
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _try_get_conn_from_db(self):
        if self._db and hasattr(self._db, "params") and self._db.params:
            self._conn_params = dict(self._db.params)

    # ── Roles ─────────────────────────────────────────────────────────────

    def _on_roles(self, roles: list):
        self._roles = roles
        t = self._roles_table
        t.setRowCount(0)
        names = []
        for r in roles:
            row = t.rowCount()
            t.insertRow(row)
            name = r["rolname"]
            names.append(name)
            vals = [
                name,
                "✔" if r["rolcanlogin"] else "—",
                "✔" if r["rolsuper"] else "—",
                "✔" if r["rolcreatedb"] else "—",
                "✔" if r["rolcreaterole"] else "—",
                str(r["rolconnlimit"]) if r["rolconnlimit"] != -1 else "∞",
                ", ".join(r["member_of"]),
            ]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if col == 1 and v == "✔":
                    item.setForeground(QColor("#3fb950"))
                if col == 2 and v == "✔":
                    item.setForeground(QColor("#ff7b72"))
                t.setItem(row, col, item)

        self._grant_role_cb.clear()
        self._grant_role_cb.addItems(names)

    def _create_role(self):
        dlg = NewRoleDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sql = dlg.build_sql()
        if not sql:
            QMessageBox.warning(self, "Empty name", "Enter a role name.")
            return
        self._run_sql(sql, refresh=True)

    def _drop_role(self):
        row = self._roles_table.currentRow()
        if row < 0:
            return
        name = self._roles_table.item(row, 0).text()
        if QMessageBox.question(
                self, "Drop Role",
                f"Drop role '{name}'? This cannot be undone.",
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._run_sql(f'DROP ROLE "{name}";', refresh=True)

    # ── Grants ────────────────────────────────────────────────────────────

    def _on_grants(self, grants: list):
        self._grants = grants
        t = self._grants_table
        t.setRowCount(0)
        schemas = sorted({g["schema"] for g in grants})
        self._grant_schema_cb.clear()
        self._grant_schema_cb.addItems(schemas or ["public"])
        for g in grants:
            row = t.rowCount()
            t.insertRow(row)
            for col, v in enumerate(
                    [g["grantee"], g["schema"], g["table"], g["privs"]]):
                t.setItem(row, col, QTableWidgetItem(v))

    def _build_grant_sql(self, verb: str) -> str:
        role   = self._grant_role_cb.currentText()
        schema = self._grant_schema_cb.currentText()
        table  = self._grant_table_edit.text().strip()
        privs  = [p for p, cb in self._priv_checks.items() if cb.isChecked()]
        if not role or not privs:
            return ""
        priv_str = ", ".join(privs)
        if table == "*":
            return (f'{verb} {priv_str} ON ALL TABLES IN SCHEMA '
                    f'"{schema}" TO "{role}";')
        return (f'{verb} {priv_str} ON "{schema}"."{table}" TO "{role}";')

    def _do_grant(self):
        sql = self._build_grant_sql("GRANT")
        if sql:
            self._run_sql(sql, refresh=True)

    def _do_revoke(self):
        sql = self._build_grant_sql("REVOKE")
        if sql:
            self._run_sql(sql, refresh=True)

    # ── RLS ───────────────────────────────────────────────────────────────

    def _on_rls(self, policies: list, rls_tables: list):
        self._policies   = policies
        self._rls_tables = rls_tables
        t = self._rls_table_widget
        t.setRowCount(0)
        for r in rls_tables:
            row = t.rowCount()
            t.insertRow(row)
            t.setItem(row, 0, QTableWidgetItem(r["schema"]))
            t.setItem(row, 1, QTableWidgetItem(r["table"]))
            enabled = r["rowsecurity"]
            item = QTableWidgetItem("🟢 ON" if enabled else "⚫ OFF")
            item.setForeground(
                QColor("#3fb950") if enabled else QColor("#888"))
            t.setItem(row, 2, item)

    def _on_rls_table_selected(self):
        row = self._rls_table_widget.currentRow()
        if row < 0:
            return
        schema = self._rls_table_widget.item(row, 0).text()
        table  = self._rls_table_widget.item(row, 1).text()
        relevant = [p for p in self._policies
                    if p["schema"] == schema and p["table"] == table]
        t = self._policies_table
        t.setRowCount(0)
        for p in relevant:
            r = t.rowCount()
            t.insertRow(r)
            vals = [p["policy"],
                    "PERMISSIVE" if p["permissive"] == "PERMISSIVE" else "RESTRICTIVE",
                    str(p["roles"] or "PUBLIC"),
                    p["cmd"] or "ALL",
                    p["qual"] or "—",
                    p["with_check"] or "—"]
            for col, v in enumerate(vals):
                t.setItem(r, col, QTableWidgetItem(v))

    def _toggle_rls(self, action: str):
        row = self._rls_table_widget.currentRow()
        if row < 0:
            QMessageBox.warning(self, "No selection", "Select a table first.")
            return
        schema = self._rls_table_widget.item(row, 0).text()
        table  = self._rls_table_widget.item(row, 1).text()
        sql = f'ALTER TABLE "{schema}"."{table}" {action} ROW LEVEL SECURITY;'
        self._run_sql(sql, refresh=True)

    def _create_policy(self):
        schemas = sorted({r["schema"] for r in self._rls_tables})
        dlg = NewPolicyDialog(schemas or ["public"], self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        sql = dlg.build_sql()
        if not sql:
            QMessageBox.warning(self, "Incomplete", "Fill table and policy name.")
            return
        self._run_sql(sql, refresh=True)

    def _drop_policy(self):
        pol_row = self._policies_table.currentRow()
        tbl_row = self._rls_table_widget.currentRow()
        if pol_row < 0 or tbl_row < 0:
            return
        policy = self._policies_table.item(pol_row, 0).text()
        schema = self._rls_table_widget.item(tbl_row, 0).text()
        table  = self._rls_table_widget.item(tbl_row, 1).text()
        if QMessageBox.question(
                self, "Drop Policy", f"Drop policy '{policy}'?",
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        sql = f'DROP POLICY "{policy}" ON "{schema}"."{table}";'
        self._run_sql(sql, refresh=True)

    # ── SSL ───────────────────────────────────────────────────────────────

    def _on_ssl(self, info: dict, conns: list):
        if info:
            enabled = info.get("ssl_enabled", "off")
            color = "#3fb950" if enabled == "on" else "#ff7b72"
            self._ssl_enabled_lbl.setText(
                f'<span style="color:{color};font-weight:bold;">'
                f'{enabled.upper()}</span>')
            self._ssl_cert_lbl.setText(info.get("ssl_cert_file") or "—")
            self._ssl_key_lbl.setText(info.get("ssl_key_file") or "—")
            self._ssl_ca_lbl.setText(info.get("ssl_ca_file") or "—")
            self._pg_started_lbl.setText(info.get("started") or "—")

        t = self._conns_table
        t.setRowCount(0)
        for c in conns:
            row = t.rowCount()
            t.insertRow(row)
            ssl_on = c.get("ssl") is True
            vals = [
                str(c.get("pid", "")),
                c.get("user", ""),
                c.get("app", ""),
                c.get("client", "") or "local",
                c.get("state", ""),
                "🔒 SSL" if ssl_on else "⚠ plain",
                f'{c.get("ssl_ver","") or ""} {c.get("cipher","") or ""}'.strip() or "—",
                str(c.get("age_s", "")),
            ]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if col == 5:
                    item.setForeground(
                        QColor("#3fb950") if ssl_on else QColor("#d29922"))
                t.setItem(row, col, item)

    def _terminate_connection(self):
        row = self._conns_table.currentRow()
        if row < 0:
            return
        pid = self._conns_table.item(row, 0).text()
        user = self._conns_table.item(row, 1).text()
        if QMessageBox.question(
                self, "Terminate",
                f"Terminate connection PID {pid} (user: {user})?",
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._run_sql(f"SELECT pg_terminate_backend({pid});")

    def _browse_cert(self, field: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select certificate file", "",
            "Cert/Key files (*.crt *.pem *.key *.p12);;All files (*)")
        if path:
            field.setText(path)

    # ── SQL runner ────────────────────────────────────────────────────────

    def _run_sql(self, sql: str, refresh: bool = False):
        if not self._conn_params:
            QMessageBox.warning(self, "No connection",
                                "Open a DB connection first.")
            return
        self._log_msg(f"SQL: {sql}", "sql")
        w = GrantWorker(self, self._conn_params, sql)
        w.log.connect(self._log_msg)
        if refresh:
            w.finished.connect(self._reload)
        w.finished.connect(w.deleteLater)
        w.start()

    def _log_msg(self, msg: str, level: str = "info"):
        colors = {
            "sql": "#d2a8ff", "success": "#3fb950",
            "error": "#ff7b72", "info": "#58a6ff",
        }
        color = colors.get(level, "#aaa")
        self._log.append(
            f'<span style="color:{color};">{msg}</span>')
