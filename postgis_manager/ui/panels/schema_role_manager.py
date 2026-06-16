"""Schema & Role Manager — manage schemas, users, roles and privileges."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QComboBox, QLineEdit, QCheckBox, QMessageBox,
    QTabWidget, QFormLayout,
)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QColor

from ...db.connection import DBManager
from ...utils import i18n


# ── Workers ───────────────────────────────────────────────────────────────────

class DDLWorker(QThread):
    done  = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, sql: str):
        super().__init__()
        self.db = db
        self.sql = sql

    def run(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute(self.sql)
            self.db.conn.commit()
            self.done.emit("OK")
        except Exception as e:
            try:
                self.db.conn.rollback()
            except Exception:
                pass
            self.error.emit(str(e))


class LoadWorker(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, sql: str, params=None):
        super().__init__()
        self.db = db
        self.sql = sql
        self.params = params

    def run(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute(self.sql, self.params)
                self.done.emit(cur.fetchall())
        except Exception as e:
            self.error.emit(str(e))


# ── Main Panel ────────────────────────────────────────────────────────────────

class SchemaRolePanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"🔐  {i18n.t('srm_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_schema_tab(), i18n.t("srm_tab_schemas"))
        tabs.addTab(self._build_role_tab(),   i18n.t("srm_tab_roles"))
        tabs.addTab(self._build_grants_tab(), i18n.t("srm_tab_grants"))
        layout.addWidget(tabs)

        self._status = QLabel("")
        layout.addWidget(self._status)

    # ── Schemas ───────────────────────────────────────────────────────────────

    def _build_schema_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(f"<b>{i18n.t('srm_schemas')}</b>"))
        hdr.addStretch()
        ref_btn = QPushButton(f"↻ {i18n.t('action_refresh')}")
        ref_btn.clicked.connect(self._load_schemas)
        hdr.addWidget(ref_btn)
        l.addLayout(hdr)

        self._schema_table = _make_table(
            ["Schema", i18n.t("srm_owner"), i18n.t("srm_comment")])
        self._schema_table.itemSelectionChanged.connect(self._on_schema_selected)
        l.addWidget(self._schema_table)

        ops = QGroupBox(i18n.t("srm_operations"))
        op_l = QFormLayout(ops)
        self._sch_name    = QLineEdit(); self._sch_name.setPlaceholderText("schema_name")
        self._sch_owner   = QComboBox(); self._sch_owner.setEditable(True)
        self._sch_comment = QLineEdit()
        op_l.addRow(i18n.t("srm_schema_name"), self._sch_name)
        op_l.addRow(i18n.t("srm_owner"),        self._sch_owner)
        op_l.addRow(i18n.t("srm_comment"),      self._sch_comment)
        l.addWidget(ops)

        btns = QHBoxLayout()
        for label, fn in [
            (f"＋ {i18n.t('srm_create_schema')}", self._create_schema),
            (f"✎ {i18n.t('srm_alter_owner')}",    self._alter_schema_owner),
            (f"🗑 {i18n.t('srm_drop_schema')}",   self._drop_schema),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(fn)
            btns.addWidget(btn)
        l.addLayout(btns)
        return w

    def _load_schemas(self):
        if not self.db.is_connected():
            return
        w = LoadWorker(self.db, """
            SELECT n.nspname,
                   r.rolname,
                   obj_description(n.oid, 'pg_namespace')
            FROM pg_namespace n
            JOIN pg_roles r ON r.oid = n.nspowner
            WHERE n.nspname NOT IN ('pg_toast','pg_catalog','information_schema')
              AND n.nspname NOT LIKE 'pg_temp_%'
              AND n.nspname NOT LIKE 'pg_toast_temp_%'
            ORDER BY n.nspname
        """)
        w.done.connect(lambda rows: _fill(self._schema_table, rows))
        w.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        w.start()

    def _on_schema_selected(self):
        row = self._schema_table.currentRow()
        if row >= 0:
            name = _cell(self._schema_table, row, 0)
            owner = _cell(self._schema_table, row, 1)
            comment = _cell(self._schema_table, row, 2)
            self._sch_name.setText(name)
            self._sch_owner.setCurrentText(owner)
            self._sch_comment.setText(comment)

    def _create_schema(self):
        name  = self._sch_name.text().strip()
        owner = self._sch_owner.currentText().strip()
        if not name:
            return
        sql = f'CREATE SCHEMA "{name}"'
        if owner:
            sql += f' AUTHORIZATION "{owner}"'
        sql += ";"
        comment = self._sch_comment.text().strip()
        if comment:
            sql += f"\nCOMMENT ON SCHEMA \"{name}\" IS '{comment}';"
        self._run(sql, i18n.t("srm_schema_created"), self._load_schemas)

    def _alter_schema_owner(self):
        name  = self._sch_name.text().strip()
        owner = self._sch_owner.currentText().strip()
        if not name or not owner:
            return
        self._run(f'ALTER SCHEMA "{name}" OWNER TO "{owner}";',
                  i18n.t("srm_owner_changed"), self._load_schemas)

    def _drop_schema(self):
        name = self._sch_name.text().strip()
        if not name:
            return
        if QMessageBox.question(
                self, "PostGIS Manager",
                i18n.t("srm_confirm_drop_schema", schema=name)) \
                != QMessageBox.StandardButton.Yes:
            return
        self._run(f'DROP SCHEMA "{name}" CASCADE;',
                  i18n.t("srm_schema_dropped"), self._load_schemas)

    # ── Roles / Users ─────────────────────────────────────────────────────────

    def _build_role_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(f"<b>{i18n.t('srm_roles')}</b>"))
        hdr.addStretch()
        ref_btn = QPushButton(f"↻ {i18n.t('action_refresh')}")
        ref_btn.clicked.connect(self._load_roles)
        hdr.addWidget(ref_btn)
        l.addLayout(hdr)

        self._role_table = _make_table([
            "Role", i18n.t("srm_superuser"), i18n.t("srm_createdb"),
            i18n.t("srm_createrole"), i18n.t("srm_login"),
            i18n.t("srm_conn_limit"), "Valid Until"])
        self._role_table.itemSelectionChanged.connect(self._on_role_selected)
        l.addWidget(self._role_table)

        ops = QGroupBox(i18n.t("srm_operations"))
        op_l = QFormLayout(ops)
        self._role_name  = QLineEdit(); self._role_name.setPlaceholderText("role_name")
        self._role_pw    = QLineEdit(); self._role_pw.setEchoMode(
            QLineEdit.EchoMode.Password)
        self._role_pw.setPlaceholderText(i18n.t("srm_leave_empty"))
        self._role_login = QCheckBox(i18n.t("srm_login"))
        self._role_login.setChecked(True)
        self._role_super = QCheckBox(i18n.t("srm_superuser"))
        self._role_cdb   = QCheckBox(i18n.t("srm_createdb"))
        self._role_cr    = QCheckBox(i18n.t("srm_createrole"))
        op_l.addRow(i18n.t("srm_role_name"), self._role_name)
        op_l.addRow(i18n.t("conn_password"), self._role_pw)
        op_l.addRow("", self._role_login)
        op_l.addRow("", self._role_super)
        op_l.addRow("", self._role_cdb)
        op_l.addRow("", self._role_cr)
        l.addWidget(ops)

        btns = QHBoxLayout()
        for label, fn in [
            (f"＋ {i18n.t('srm_create_role')}", self._create_role),
            (f"✎ {i18n.t('srm_alter_role')}",   self._alter_role),
            (f"🗑 {i18n.t('srm_drop_role')}",   self._drop_role),
            (f"🔑 {i18n.t('srm_reset_pw')}",    self._reset_password),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(fn)
            btns.addWidget(btn)
        l.addLayout(btns)
        return w

    def _load_roles(self):
        if not self.db.is_connected():
            return
        w = LoadWorker(self.db, """
            SELECT rolname,
                   rolsuper, rolcreatedb, rolcreaterole, rolcanlogin,
                   CASE WHEN rolconnlimit = -1 THEN '∞'
                        ELSE rolconnlimit::text END,
                   COALESCE(rolvaliduntil::text, '—')
            FROM pg_roles
            WHERE rolname NOT LIKE 'pg_%'
            ORDER BY rolname
        """)
        w.done.connect(lambda rows: _fill(self._role_table, rows,
                                          bool_cols={1, 2, 3, 4}))
        w.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        w.start()
        # Also fill owner combo in schema tab
        LoadWorker(self.db, "SELECT rolname FROM pg_roles ORDER BY rolname") \
            .start()

    def _on_role_selected(self):
        row = self._role_table.currentRow()
        if row >= 0:
            self._role_name.setText(_cell(self._role_table, row, 0))
            self._role_super.setChecked(_cell(self._role_table, row, 1) == "✔")
            self._role_cdb.setChecked  (_cell(self._role_table, row, 2) == "✔")
            self._role_cr.setChecked   (_cell(self._role_table, row, 3) == "✔")
            self._role_login.setChecked(_cell(self._role_table, row, 4) == "✔")

    def _create_role(self):
        name = self._role_name.text().strip()
        if not name:
            return
        opts = []
        opts.append("LOGIN" if self._role_login.isChecked() else "NOLOGIN")
        opts.append("SUPERUSER" if self._role_super.isChecked() else "NOSUPERUSER")
        opts.append("CREATEDB" if self._role_cdb.isChecked() else "NOCREATEDB")
        opts.append("CREATEROLE" if self._role_cr.isChecked() else "NOCREATEROLE")
        pw = self._role_pw.text()
        if pw:
            opts.append(f"PASSWORD '{pw}'")
        sql = f'CREATE ROLE "{name}" {" ".join(opts)};'
        self._run(sql, i18n.t("srm_role_created"), self._load_roles)

    def _alter_role(self):
        name = self._role_name.text().strip()
        if not name:
            return
        opts = []
        opts.append("LOGIN" if self._role_login.isChecked() else "NOLOGIN")
        opts.append("SUPERUSER" if self._role_super.isChecked() else "NOSUPERUSER")
        opts.append("CREATEDB" if self._role_cdb.isChecked() else "NOCREATEDB")
        opts.append("CREATEROLE" if self._role_cr.isChecked() else "NOCREATEROLE")
        self._run(f'ALTER ROLE "{name}" {" ".join(opts)};',
                  i18n.t("srm_role_altered"), self._load_roles)

    def _drop_role(self):
        name = self._role_name.text().strip()
        if not name:
            return
        if QMessageBox.question(
                self, "PostGIS Manager",
                i18n.t("srm_confirm_drop_role", role=name)) \
                != QMessageBox.StandardButton.Yes:
            return
        self._run(f'DROP ROLE "{name}";',
                  i18n.t("srm_role_dropped"), self._load_roles)

    def _reset_password(self):
        name = self._role_name.text().strip()
        pw   = self._role_pw.text()
        if not name or not pw:
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("srm_enter_pw"))
            return
        self._run(f"ALTER ROLE \"{name}\" PASSWORD '{pw}';",
                  i18n.t("srm_pw_reset"), None)

    # ── Grants ────────────────────────────────────────────────────────────────

    def _build_grants_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)

        l.addWidget(QLabel(f"<b>{i18n.t('srm_grant_revoke')}</b>"))

        top = QHBoxLayout()

        # Left: role selector
        rl = QVBoxLayout()
        rl.addWidget(QLabel(i18n.t("srm_role") + ":"))
        self._gr_role = QComboBox(); self._gr_role.setEditable(True)
        self._gr_role.setMinimumWidth(160)
        rl.addWidget(self._gr_role)
        rl.addStretch()
        top.addLayout(rl)

        # Middle: object selector
        ml = QFormLayout()
        self._gr_type = QComboBox()
        self._gr_type.addItems(["TABLE", "SCHEMA", "DATABASE",
                                 "SEQUENCE", "FUNCTION", "ALL TABLES IN SCHEMA"])
        self._gr_type.currentTextChanged.connect(self._on_gr_type_changed)
        self._gr_schema = QComboBox(); self._gr_schema.setEditable(True)
        self._gr_schema.currentTextChanged.connect(self._load_gr_objects)
        self._gr_object = QComboBox(); self._gr_object.setEditable(True)
        ml.addRow(i18n.t("srm_object_type"), self._gr_type)
        ml.addRow(i18n.t("srm_schema") + ":", self._gr_schema)
        ml.addRow(i18n.t("srm_object") + ":", self._gr_object)
        top.addLayout(ml)

        # Right: privileges
        priv_box = QGroupBox(i18n.t("srm_privileges"))
        priv_l = QVBoxLayout(priv_box)
        self._priv_checks: dict[str, QCheckBox] = {}
        for priv in ["SELECT", "INSERT", "UPDATE", "DELETE",
                     "TRUNCATE", "REFERENCES", "TRIGGER",
                     "USAGE", "CREATE", "CONNECT", "TEMPORARY", "EXECUTE"]:
            cb = QCheckBox(priv)
            self._priv_checks[priv] = cb
            priv_l.addWidget(cb)
        all_btn = QPushButton(i18n.t("srm_check_all"))
        all_btn.clicked.connect(lambda: [c.setChecked(True)
                                          for c in self._priv_checks.values()])
        none_btn = QPushButton(i18n.t("srm_check_none"))
        none_btn.clicked.connect(lambda: [c.setChecked(False)
                                           for c in self._priv_checks.values()])
        priv_l.addWidget(all_btn); priv_l.addWidget(none_btn)
        top.addWidget(priv_box)
        l.addLayout(top)

        btn_row = QHBoxLayout()
        grant_btn  = QPushButton(f"✔ {i18n.t('srm_grant')}")
        grant_btn.clicked.connect(self._do_grant)
        revoke_btn = QPushButton(f"✖ {i18n.t('srm_revoke')}")
        revoke_btn.clicked.connect(self._do_revoke)
        btn_row.addStretch()
        btn_row.addWidget(grant_btn); btn_row.addWidget(revoke_btn)
        l.addLayout(btn_row)

        # Current grants table
        l.addWidget(QLabel(f"<b>{i18n.t('srm_current_grants')}</b>"))
        self._grants_table = _make_table([
            i18n.t("srm_grantee"), i18n.t("srm_object_type"),
            "Object", i18n.t("srm_privilege"), "Grantable"])
        l.addWidget(self._grants_table)

        ref_btn = QPushButton(f"↻ {i18n.t('srm_show_grants')}")
        ref_btn.clicked.connect(self._load_grants)
        l.addWidget(ref_btn)
        return w

    def _on_gr_type_changed(self, obj_type: str):
        needs_object = obj_type in ("TABLE", "SEQUENCE", "FUNCTION")
        needs_schema = obj_type not in ("DATABASE",)
        self._gr_schema.setEnabled(needs_schema)
        self._gr_object.setEnabled(needs_object)
        if not needs_object:
            self._gr_object.clear()

    def _load_gr_objects(self, schema: str):
        if not self.db.is_connected() or not schema.strip():
            return
        obj_type = self._gr_type.currentText()
        if obj_type == "TABLE":
            sql = """SELECT table_name FROM information_schema.tables
                     WHERE table_schema = %s ORDER BY table_name"""
        elif obj_type == "SEQUENCE":
            sql = """SELECT sequence_name FROM information_schema.sequences
                     WHERE sequence_schema = %s ORDER BY sequence_name"""
        elif obj_type == "FUNCTION":
            sql = """SELECT p.proname FROM pg_proc p
                     JOIN pg_namespace n ON n.oid = p.pronamespace
                     WHERE n.nspname = %s ORDER BY p.proname"""
        else:
            return
        w = LoadWorker(self.db, sql, (schema.strip(),))
        w.done.connect(lambda rows: (
            self._gr_object.clear(),
            self._gr_object.addItems([r[0] for r in rows])))
        w.start()

    def _get_selected_privs(self) -> list[str]:
        return [p for p, cb in self._priv_checks.items() if cb.isChecked()]

    def _build_grant_sql(self, action: str) -> str | None:
        role    = self._gr_role.currentText().strip()
        privs   = ", ".join(self._get_selected_privs())
        obj_type = self._gr_type.currentText()
        schema  = self._gr_schema.currentText().strip()
        obj     = self._gr_object.currentText().strip()
        if not role or not privs:
            return None
        if obj_type == "DATABASE":
            return f'{action} {privs} ON DATABASE CURRENT_DATABASE() TO "{role}";'
        elif obj_type in ("SCHEMA", "ALL TABLES IN SCHEMA"):
            if not schema:
                return None
            if obj_type == "SCHEMA":
                return f'{action} {privs} ON SCHEMA "{schema}" TO "{role}";'
            else:
                return (f'{action} {privs} ON ALL TABLES '
                        f'IN SCHEMA "{schema}" TO "{role}";')
        else:
            if not schema or not obj:
                return None
            return (f'{action} {privs} ON {obj_type} "{schema}"."{obj}" '
                    f'TO "{role}";')

    def _do_grant(self):
        sql = self._build_grant_sql("GRANT")
        if not sql:
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("srm_fill_required"))
            return
        self._run(sql, i18n.t("srm_granted"), self._load_grants)

    def _do_revoke(self):
        sql = self._build_grant_sql("REVOKE")
        if not sql:
            return
        sql = sql.replace(" TO ", " FROM ")
        self._run(sql, i18n.t("srm_revoked"), self._load_grants)

    def _load_grants(self):
        if not self.db.is_connected():
            return
        w = LoadWorker(self.db, """
            SELECT grantee, object_type, object_name,
                   privilege_type, is_grantable
            FROM information_schema.role_table_grants
            WHERE grantor != grantee
            UNION ALL
            SELECT grantee, 'SCHEMA', table_schema,
                   privilege_type, is_grantable
            FROM information_schema.role_usage_grants
            ORDER BY 1, 2, 3
            LIMIT 200
        """)
        w.done.connect(lambda rows: _fill(self._grants_table, rows))
        w.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        w.start()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _run(self, sql: str, msg: str, callback):
        self._status.setText(i18n.t("status_running"))
        w = DDLWorker(self.db, sql)
        def on_done(_):
            self._status.setText(f"✔ {msg}")
            if callback:
                callback()
        w.done.connect(on_done)
        w.error.connect(lambda e: (
            self._status.setText(f"✖ {e}"),
            QMessageBox.critical(self, "Error", e)))
        w.start(); self._worker = w

    def showEvent(self, event):
        super().showEvent(event)
        if self.db.is_connected():
            self._load_schemas()
            self._load_roles()
            self._load_gr_schemas()

    def _load_gr_schemas(self):
        w = LoadWorker(self.db, """
            SELECT schema_name FROM information_schema.schemata
            WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')
            ORDER BY schema_name
        """)
        def on_schemas(rows):
            schemas = [r[0] for r in rows]
            self._gr_schema.clear(); self._gr_schema.addItems(schemas)
            roles_w = LoadWorker(self.db,
                "SELECT rolname FROM pg_roles ORDER BY rolname")
            roles_w.done.connect(lambda rrows: (
                self._gr_role.clear(),
                self._gr_role.addItems([r[0] for r in rrows]),
                self._sch_owner.clear(),
                self._sch_owner.addItems([r[0] for r in rrows])))
            roles_w.start()
        w.done.connect(on_schemas)
        w.start()


# ── Utilities ─────────────────────────────────────────────────────────────────

def _make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    t.setSortingEnabled(True)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for i in range(1, len(headers)):
        hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
    return t


def _fill(table: QTableWidget, rows: list, bool_cols: set = None):
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            if bool_cols and c in bool_cols:
                text = "✔" if val else "✖"
                color = "#2E7D32" if val else "#C62828"
                item = QTableWidgetItem(text)
                item.setForeground(QColor(color))
            else:
                text = "" if val is None else str(val)
                item = QTableWidgetItem(text)
            table.setItem(r, c, item)
    table.setSortingEnabled(True)


def _cell(table: QTableWidget, row: int, col: int) -> str:
    item = table.item(row, col)
    return item.text() if item else ""
