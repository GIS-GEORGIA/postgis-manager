"""Function Browser — browse, view source and execute PostgreSQL functions/procedures."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QSplitter, QTextEdit, QLineEdit, QComboBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor

from ...db.connection import DBManager
from ...utils import i18n
from ...utils.workers import launch

# SQL Keywords for minimal highlighting
_KW = ("SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "IS",
       "NULL", "AS", "ON", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
       "FULL", "CROSS", "WITH", "UNION", "ALL", "ORDER", "BY", "GROUP",
       "HAVING", "LIMIT", "OFFSET", "RETURNS", "RETURN", "BEGIN", "END",
       "DECLARE", "IF", "THEN", "ELSE", "ELSIF", "LOOP", "WHILE", "FOR",
       "FOREACH", "EXECUTE", "PERFORM", "RAISE", "EXCEPTION", "NOTICE",
       "INSERT", "UPDATE", "DELETE", "INTO", "VALUES", "SET", "CREATE",
       "OR", "REPLACE", "FUNCTION", "PROCEDURE", "LANGUAGE", "PLPGSQL",
       "SQL", "STRICT", "VOLATILE", "STABLE", "IMMUTABLE", "SECURITY",
       "DEFINER", "INVOKER", "CALLED", "ON", "NULL", "INPUT",
       "$$", "$func$", "$body$")


class FuncLoadWorker(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, schema_filter: str, search: str):
        super().__init__()
        self.db = db
        self.schema_filter = schema_filter
        self.search = search

    def run(self):
        try:
            conditions = ["n.nspname NOT IN ('pg_catalog','information_schema')"]
            params = []
            if self.schema_filter and self.schema_filter != "(all)":
                conditions.append("n.nspname = %s")
                params.append(self.schema_filter)
            if self.search:
                conditions.append("p.proname ILIKE %s")
                params.append(f"%{self.search}%")
            where = " AND ".join(conditions)
            with self.db.conn.cursor() as cur:
                cur.execute(f"""
                    SELECT
                        n.nspname                          AS schema,
                        p.proname                          AS name,
                        CASE p.prokind
                            WHEN 'f' THEN 'function'
                            WHEN 'p' THEN 'procedure'
                            WHEN 'a' THEN 'aggregate'
                            WHEN 'w' THEN 'window'
                            ELSE p.prokind::text
                        END                                AS kind,
                        pg_get_function_result(p.oid)      AS returns,
                        l.lanname                          AS language,
                        p.pronargs                         AS nargs,
                        pg_get_function_arguments(p.oid)   AS args,
                        p.oid
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    JOIN pg_language l  ON l.oid = p.prolang
                    WHERE {where}
                    ORDER BY n.nspname, p.proname
                    LIMIT 500
                """, params or None)
                self.done.emit(cur.fetchall())
        except Exception as e:
            self.error.emit(str(e))


class FuncSourceWorker(QThread):
    done  = pyqtSignal(str, str)   # source, language
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, oid: int):
        super().__init__()
        self.db = db
        self.oid = oid

    def run(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT prosrc, l.lanname,
                           pg_get_functiondef(p.oid) AS full_def
                    FROM pg_proc p
                    JOIN pg_language l ON l.oid = p.prolang
                    WHERE p.oid = %s
                """, (self.oid,))
                row = cur.fetchone()
                if row:
                    src, lang, full_def = row
                    self.done.emit(full_def or src or "", lang or "")
                else:
                    self.done.emit("-- source not available", "")
        except Exception as e:
            self.error.emit(str(e))


class FuncExecWorker(QThread):
    done  = pyqtSignal(list, list)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, sql: str):
        super().__init__()
        self.db = db
        self.sql = sql

    def run(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute(self.sql)
                cols = [d[0] for d in (cur.description or [])]
                rows = [list(r) for r in (cur.fetchmany(200) if cur.description else [])]
            self.db.conn.commit()
            self.done.emit(rows, cols)
        except Exception as e:
            try:
                self.db.conn.rollback()
            except Exception:
                pass
            self.error.emit(str(e))


# ── Panel ──────────────────────────────────────────────────────────────────────

class FunctionBrowserPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._func_rows: list = []
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"ƒ  {i18n.t('fb_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        # Filter bar
        flt = QHBoxLayout()
        flt.addWidget(QLabel(i18n.t("td_schema") + ":"))
        self._schema_combo = QComboBox()
        self._schema_combo.addItem("(all)")
        self._schema_combo.setMinimumWidth(130)
        flt.addWidget(self._schema_combo)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(i18n.t("fb_search"))
        self._search_edit.returnPressed.connect(self._load_functions)
        flt.addWidget(self._search_edit)

        load_btn = QPushButton(f"🔍 {i18n.t('fb_load')}")
        load_btn.clicked.connect(self._load_functions)
        flt.addWidget(load_btn)
        layout.addLayout(flt)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)

        # Function list
        top = QWidget(); tl = QVBoxLayout(top); tl.setContentsMargins(0,0,0,0)
        self._func_table = QTableWidget(0, 7)
        self._func_table.setHorizontalHeaderLabels([
            "Schema", i18n.t("fb_col_name"), i18n.t("fb_col_kind"),
            i18n.t("fb_col_returns"), i18n.t("fb_col_lang"),
            i18n.t("fb_col_nargs"), i18n.t("fb_col_args")])
        self._func_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._func_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._func_table.setAlternatingRowColors(True)
        self._func_table.setSortingEnabled(True)
        hh = self._func_table.horizontalHeader()
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        for i in range(6):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._func_table.itemSelectionChanged.connect(self._on_selection_changed)
        tl.addWidget(self._func_table)
        splitter.addWidget(top)

        # Bottom: source + execute
        bottom = QWidget(); bl = QVBoxLayout(bottom); bl.setContentsMargins(0,0,0,0)
        inner_split = QSplitter(Qt.Orientation.Horizontal)

        # Source view
        src_w = QWidget(); src_l = QVBoxLayout(src_w)
        src_l.setContentsMargins(0, 0, 4, 0)
        src_l.addWidget(QLabel(f"<b>{i18n.t('fb_source')}</b>"))
        self._src_view = QTextEdit()
        self._src_view.setReadOnly(True)
        self._src_view.setFont(QFont("Courier New", 10))
        src_l.addWidget(self._src_view)
        inner_split.addWidget(src_w)

        # Execute pane
        exec_w = QWidget(); exec_l = QVBoxLayout(exec_w)
        exec_l.setContentsMargins(4, 0, 0, 0)
        exec_l.addWidget(QLabel(f"<b>{i18n.t('fb_execute')}</b>"))
        self._exec_edit = QTextEdit()
        self._exec_edit.setFont(QFont("Courier New", 10))
        self._exec_edit.setPlaceholderText("SELECT my_function(arg1, arg2);")
        self._exec_edit.setMaximumHeight(100)
        exec_l.addWidget(self._exec_edit)

        exec_btn = QPushButton(f"▶ {i18n.t('action_run')}")
        exec_btn.clicked.connect(self._execute)
        exec_l.addWidget(exec_btn)

        exec_l.addWidget(QLabel(f"<b>{i18n.t('fb_result')}</b>"))
        self._result_table = QTableWidget()
        self._result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._result_table.setAlternatingRowColors(True)
        rhh = self._result_table.horizontalHeader()
        rhh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        exec_l.addWidget(self._result_table)

        self._exec_status = QLabel("")
        exec_l.addWidget(self._exec_status)
        inner_split.addWidget(exec_w)
        inner_split.setSizes([500, 500])

        bl.addWidget(inner_split)
        splitter.addWidget(bottom)
        splitter.setSizes([300, 400])

        self._status = QLabel(i18n.t("fb_not_connected"))
        layout.addWidget(self._status)

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load_functions(self):
        if not self.db.is_connected():
            self._status.setText(i18n.t("fb_not_connected"))
            return
        schema = self._schema_combo.currentText()
        search = self._search_edit.text().strip()
        self._status.setText(i18n.t("status_loading"))
        w = FuncLoadWorker(self.db, schema, search)
        w.done.connect(self._on_funcs_loaded)
        w.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        w.start(); self._worker = w

    def _on_funcs_loaded(self, rows: list):
        self._func_rows = rows
        t = self._func_table
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        kind_colors = {
            "function":  "#1B5E20",
            "procedure": "#0D47A1",
            "aggregate": "#E65100",
            "window":    "#6A1B9A",
        }
        for r, row in enumerate(rows):
            for c, val in enumerate(row[:-1]):  # skip oid col
                text = "" if val is None else str(val)
                item = QTableWidgetItem(text)
                if c == 2:  # kind column
                    color = kind_colors.get(text, "#333")
                    item.setForeground(QColor(color))
                t.setItem(r, c, item)
        t.setSortingEnabled(True)
        self._status.setText(
            i18n.t("fb_loaded", n=len(rows)))

    def _on_selection_changed(self):
        rows = self._func_table.selectedItems()
        if rows:
            self._on_row_changed(self._func_table.currentRow())

    def _on_row_changed(self, row: int):
        if row < 0 or row >= len(self._func_rows):
            return
        func_row = self._func_rows[row]
        oid = func_row[-1]
        # Pre-fill execute box
        schema = func_row[0]; name = func_row[1]; args = func_row[6]
        kind   = func_row[2]
        if kind == "procedure":
            self._exec_edit.setPlainText(f'CALL "{schema}"."{name}"({args});')
        else:
            self._exec_edit.setPlainText(
                f'SELECT "{schema}"."{name}"({args});')
        # Load source
        w = FuncSourceWorker(self.db, oid)
        w.done.connect(self._on_source_loaded)
        w.error.connect(lambda e: self._src_view.setPlainText(f"-- Error: {e}"))
        launch(w)

    def _on_source_loaded(self, src: str, lang: str):
        self._src_view.setPlainText(src)

    def _execute(self):
        sql = self._exec_edit.toPlainText().strip()
        if not sql or not self.db.is_connected():
            return
        self._exec_status.setText(i18n.t("status_running"))
        w = FuncExecWorker(self.db, sql)
        w.done.connect(self._on_exec_done)
        w.error.connect(lambda e: self._exec_status.setText(f"✖ {e}"))
        launch(w)

    def _on_exec_done(self, rows: list, cols: list):
        t = self._result_table
        if not cols:
            self._exec_status.setText(i18n.t("fb_exec_ok"))
            t.setRowCount(0); t.setColumnCount(0)
            return
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(
                    "" if val is None else str(val)))
        self._exec_status.setText(
            i18n.t("result_rows") + f": {len(rows)}")

    def showEvent(self, event):
        super().showEvent(event)
        if self.db.is_connected() and self._func_table.rowCount() == 0:
            self._load_schemas()
            self._load_functions()

    def _load_schemas(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT n.nspname
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname NOT IN ('pg_catalog','information_schema')
                    ORDER BY n.nspname
                """)
                schemas = [r[0] for r in cur.fetchall()]
            self._schema_combo.clear()
            self._schema_combo.addItem("(all)")
            self._schema_combo.addItems(schemas)
        except Exception:
            pass
