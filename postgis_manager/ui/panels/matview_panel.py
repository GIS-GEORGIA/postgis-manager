"""Materialized Views panel — list, create, refresh, drop."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QComboBox, QLineEdit, QCheckBox, QTextEdit,
    QMessageBox, QSplitter,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from ...db.connection import DBManager
from ...utils import i18n


class MatViewWorker(QThread):
    done  = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, task: str, **kw):
        super().__init__()
        self.db = db; self.task = task; self.kw = kw

    def run(self):
        try:
            if self.task == "list":
                self.done.emit(_list_matviews(self.db))
            elif self.task == "refresh":
                _refresh(self.db, self.kw["schema"], self.kw["name"],
                         self.kw["concurrent"])
                self.done.emit("refreshed")
            elif self.task == "create":
                _create(self.db, self.kw["schema"], self.kw["name"],
                        self.kw["sql"], self.kw["with_data"])
                self.done.emit("created")
            elif self.task == "drop":
                _drop(self.db, self.kw["schema"], self.kw["name"])
                self.done.emit("dropped")
            elif self.task == "preview":
                rows, cols = _preview(self.db, self.kw["schema"], self.kw["name"])
                self.done.emit((rows, cols))
        except Exception as e:
            try:
                self.db.conn.rollback()
            except Exception:
                pass
            self.error.emit(str(e))


def _q(db, sql, params=None):
    with db.conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _list_matviews(db):
    return _q(db, """
        SELECT
            n.nspname                AS schema,
            c.relname                AS name,
            r.rolname                AS owner,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
            CASE WHEN c.relispopulated THEN '✔' ELSE '✖' END AS populated,
            obj_description(c.oid, 'pg_class') AS comment
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_roles r     ON r.oid = c.relowner
        WHERE c.relkind = 'm'
        ORDER BY n.nspname, c.relname
    """)


def _refresh(db, schema, name, concurrent):
    conc = "CONCURRENTLY " if concurrent else ""
    with db.conn.cursor() as cur:
        cur.execute(f'REFRESH MATERIALIZED VIEW {conc}"{schema}"."{name}";')
    db.conn.commit()


def _create(db, schema, name, sql, with_data):
    data = "WITH DATA" if with_data else "WITH NO DATA"
    with db.conn.cursor() as cur:
        cur.execute(
            f'CREATE MATERIALIZED VIEW "{schema}"."{name}" AS\n{sql}\n{data};')
    db.conn.commit()


def _drop(db, schema, name):
    with db.conn.cursor() as cur:
        cur.execute(f'DROP MATERIALIZED VIEW "{schema}"."{name}" CASCADE;')
    db.conn.commit()


def _preview(db, schema, name):
    with db.conn.cursor() as cur:
        cur.execute(f'SELECT * FROM "{schema}"."{name}" LIMIT 200')
        cols = [d[0] for d in (cur.description or [])]
        rows = [list(r) for r in cur.fetchall()]
    return rows, cols


class MatViewPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"📋  {i18n.t('mv_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)

        # ── Top: list + actions ───────────────────────────────────────────────
        top = QWidget(); tl = QVBoxLayout(top); tl.setContentsMargins(0,0,0,0)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(f"<b>{i18n.t('mv_list')}</b>"))
        hdr.addStretch()
        ref_btn = QPushButton(f"↻ {i18n.t('action_refresh')}")
        ref_btn.clicked.connect(self._load_list)
        hdr.addWidget(ref_btn)
        tl.addLayout(hdr)

        self._list_table = QTableWidget(0, 6)
        self._list_table.setHorizontalHeaderLabels([
            "Schema", i18n.t("mv_name"), i18n.t("srm_owner"),
            "Size", i18n.t("mv_populated"), i18n.t("srm_comment")])
        self._list_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._list_table.setAlternatingRowColors(True)
        self._list_table.setSortingEnabled(True)
        hh = self._list_table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i in [0, 2, 3, 4, 5]:
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._list_table.itemSelectionChanged.connect(self._on_selected)
        tl.addWidget(self._list_table)

        act_row = QHBoxLayout()
        self._concurrent_chk = QCheckBox(i18n.t("mv_concurrent"))
        self._concurrent_chk.setChecked(True)
        act_row.addWidget(self._concurrent_chk)

        for label, fn in [
            (f"🔄 {i18n.t('mv_refresh')}", self._refresh_selected),
            (f"👁 {i18n.t('mv_preview')}",  self._preview_selected),
            (f"🗑 {i18n.t('mv_drop')}",    self._drop_selected),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(fn)
            act_row.addWidget(btn)
        tl.addLayout(act_row)
        splitter.addWidget(top)

        # ── Middle: create ────────────────────────────────────────────────────
        mid = QWidget(); ml = QVBoxLayout(mid); ml.setContentsMargins(0,0,0,0)
        ml.addWidget(QLabel(f"<b>{i18n.t('mv_create')}</b>"))

        cr_form = QHBoxLayout()
        self._cr_schema = QComboBox(); self._cr_schema.setEditable(True)
        self._cr_schema.setMinimumWidth(140)
        self._cr_name   = QLineEdit(); self._cr_name.setPlaceholderText("matview_name")
        self._cr_data   = QCheckBox(i18n.t("mv_with_data"))
        self._cr_data.setChecked(True)
        cr_form.addWidget(QLabel(i18n.t("td_schema"))); cr_form.addWidget(self._cr_schema)
        cr_form.addWidget(QLabel(i18n.t("mv_name") + ":")); cr_form.addWidget(self._cr_name)
        cr_form.addWidget(self._cr_data)
        ml.addLayout(cr_form)

        ml.addWidget(QLabel("SQL:"))
        self._cr_sql = QTextEdit()
        self._cr_sql.setFont(QFont("Courier New", 10))
        self._cr_sql.setPlaceholderText("SELECT ...")
        self._cr_sql.setMaximumHeight(120)
        ml.addWidget(self._cr_sql)

        cr_btn = QPushButton(f"＋ {i18n.t('mv_create')}")
        cr_btn.clicked.connect(self._create_matview)
        ml.addWidget(cr_btn)
        splitter.addWidget(mid)

        # ── Bottom: preview ───────────────────────────────────────────────────
        bot = QWidget(); bl = QVBoxLayout(bot); bl.setContentsMargins(0,0,0,0)
        bl.addWidget(QLabel(f"<b>{i18n.t('mv_preview_data')}</b>"))
        self._preview_table = QTableWidget()
        self._preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preview_table.setAlternatingRowColors(True)
        phh = self._preview_table.horizontalHeader()
        phh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        bl.addWidget(self._preview_table)
        splitter.addWidget(bot)
        splitter.setSizes([300, 200, 200])

        self._status = QLabel("")
        layout.addWidget(self._status)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _load_list(self):
        if not self.db.is_connected():
            return
        self._status.setText(i18n.t("status_loading"))
        w = MatViewWorker(self.db, "list")
        w.done.connect(self._on_list)
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    def _on_list(self, rows):
        self._list_table.setSortingEnabled(False)
        self._list_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                text = "" if val is None else str(val)
                item = QTableWidgetItem(text)
                if c == 4:  # populated
                    item.setForeground(QColor(
                        "#2E7D32" if val == "✔" else "#C62828"))
                self._list_table.setItem(r, c, item)
        self._list_table.setSortingEnabled(True)
        self._status.setText(i18n.t("mv_loaded", n=len(rows)))

    def _on_selected(self):
        row = self._list_table.currentRow()
        if row >= 0:
            schema = _cell(self._list_table, row, 0)
            name   = _cell(self._list_table, row, 1)
            self._cr_schema.setCurrentText(schema)
            self._cr_name.setText(name)

    def _current_schema_name(self):
        row = self._list_table.currentRow()
        if row < 0:
            return None, None
        return (_cell(self._list_table, row, 0),
                _cell(self._list_table, row, 1))

    def _refresh_selected(self):
        schema, name = self._current_schema_name()
        if not schema:
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("mv_select_one"))
            return
        conc = self._concurrent_chk.isChecked()
        self._status.setText(i18n.t("status_running"))
        w = MatViewWorker(self.db, "refresh",
                          schema=schema, name=name, concurrent=conc)
        w.done.connect(lambda _: (
            self._status.setText(f"✔ {i18n.t('mv_refreshed')}"),
            self._load_list()))
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    def _preview_selected(self):
        schema, name = self._current_schema_name()
        if not schema:
            return
        self._status.setText(i18n.t("status_loading"))
        w = MatViewWorker(self.db, "preview", schema=schema, name=name)
        w.done.connect(self._on_preview)
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    def _on_preview(self, data):
        rows, cols = data
        t = self._preview_table
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(
                    "" if val is None else str(val)))
        self._status.setText(f"{len(rows)} {i18n.t('result_rows')}")

    def _drop_selected(self):
        schema, name = self._current_schema_name()
        if not schema:
            return
        if QMessageBox.question(
                self, "PostGIS Manager",
                i18n.t("mv_confirm_drop", schema=schema, name=name)) \
                != QMessageBox.StandardButton.Yes:
            return
        w = MatViewWorker(self.db, "drop", schema=schema, name=name)
        w.done.connect(lambda _: (
            self._status.setText(f"✔ {i18n.t('mv_dropped')}"),
            self._load_list()))
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    def _create_matview(self):
        schema = self._cr_schema.currentText().strip()
        name   = self._cr_name.text().strip()
        sql    = self._cr_sql.toPlainText().strip()
        if not schema or not name or not sql:
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("mv_fill_required"))
            return
        w = MatViewWorker(self.db, "create",
                          schema=schema, name=name, sql=sql,
                          with_data=self._cr_data.isChecked())
        w.done.connect(lambda _: (
            self._status.setText(f"✔ {i18n.t('mv_created')}"),
            self._load_list()))
        w.error.connect(lambda e: self._status.setText(f"✖ {e}"))
        w.start(); self._worker = w

    def showEvent(self, event):
        super().showEvent(event)
        if self.db.is_connected():
            self._load_list()
            self._load_schemas()

    def _load_schemas(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT schema_name FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')
                    ORDER BY schema_name
                """)
                schemas = [r[0] for r in cur.fetchall()]
            self._cr_schema.clear()
            self._cr_schema.addItems(schemas)
        except Exception:
            pass


def _cell(table, row, col):
    item = table.item(row, col)
    return item.text() if item else ""
