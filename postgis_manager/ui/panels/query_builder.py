"""Visual Query Builder panel — PyQt6."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QCheckBox,
    QSpinBox, QTextEdit, QSplitter, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from ...db.connection import DBManager
from ...utils import i18n, config


_OPS = ["=", "!=", "<", "<=", ">", ">=", "LIKE", "ILIKE",
        "IS NULL", "IS NOT NULL", "IN", "NOT IN", "BETWEEN"]
_LOGIC = ["AND", "OR"]
_ORDER = ["ASC", "DESC"]


class QueryRunWorker(QThread):
    done  = pyqtSignal(list, list, float)
    error = pyqtSignal(str)

    def __init__(self, db, sql):
        super().__init__()
        self.db = db; self.sql = sql

    def run(self):
        try:
            cols, rows, ms = self.db.execute_sql(self.sql)
            self.done.emit(cols or [], rows or [], ms)
        except Exception as e:
            self.error.emit(str(e))


class ConditionRow(QWidget):
    """One WHERE condition: [AND/OR] [column] [op] [value]."""
    removed = pyqtSignal(object)

    def __init__(self, columns: list[str], index: int, parent=None):
        super().__init__(parent)
        self._index = index
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)

        self._logic = QComboBox()
        self._logic.addItems(_LOGIC)
        self._logic.setVisible(index > 0)
        self._logic.setMaximumWidth(60)
        row.addWidget(self._logic)

        self._col = QComboBox()
        self._col.addItems(columns)
        self._col.setMinimumWidth(130)
        row.addWidget(self._col)

        self._op = QComboBox()
        self._op.addItems(_OPS)
        self._op.setMaximumWidth(110)
        self._op.currentTextChanged.connect(self._on_op_change)
        row.addWidget(self._op)

        self._val = QLineEdit()
        self._val.setPlaceholderText("value")
        row.addWidget(self._val)

        del_btn = QPushButton("✕")
        del_btn.setFixedWidth(28)
        del_btn.clicked.connect(lambda: self.removed.emit(self))
        row.addWidget(del_btn)

    def _on_op_change(self, op: str):
        no_val = op in ("IS NULL", "IS NOT NULL")
        self._val.setEnabled(not no_val)
        if no_val:
            self._val.clear()

    def to_sql(self) -> str:
        col = self._col.currentText()
        op  = self._op.currentText()
        val = self._val.text().strip()
        logic = self._logic.currentText() if self._logic.isVisible() else ""
        if op == "IS NULL":
            clause = f'"{col}" IS NULL'
        elif op == "IS NOT NULL":
            clause = f'"{col}" IS NOT NULL'
        elif op in ("IN", "NOT IN"):
            items = ", ".join(
                f"'{v.strip()}'" for v in val.split(",") if v.strip())
            clause = f'"{col}" {op} ({items})'
        elif op == "BETWEEN":
            parts = val.split(",")
            a = parts[0].strip() if parts else ""
            b = parts[1].strip() if len(parts) > 1 else ""
            clause = f'"{col}" BETWEEN \'{a}\' AND \'{b}\''
        elif val.lstrip("-").replace(".", "").isdigit():
            clause = f'"{col}" {op} {val}'
        else:
            clause = f'"{col}" {op} \'{val}\''
        return f"{logic} {clause}".strip()


class QueryBuilderPanel(QWidget):
    """Visual WHERE / SELECT / ORDER BY builder → generates SQL."""

    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._schema = self._table = ""
        self._columns: list[str] = []
        self._condition_rows: list[ConditionRow] = []
        self._worker: QueryRunWorker | None = None
        self._build_ui()

    def _build_ui(self):
        main = QSplitter(Qt.Orientation.Vertical)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(main)

        # ── Top: builder ──────────────────────────────────────────────────────
        top = QWidget()
        top_l = QVBoxLayout(top)
        top_l.setContentsMargins(8, 8, 8, 4)

        title = QLabel(f"🔧  {i18n.t('qb_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        top_l.addWidget(title)

        # Layer selector
        tbl_row = QHBoxLayout()
        tbl_row.addWidget(QLabel(i18n.t("qb_schema")))
        self._schema_combo = QComboBox(); self._schema_combo.setMinimumWidth(120)
        self._schema_combo.currentTextChanged.connect(self._on_schema_change)
        tbl_row.addWidget(self._schema_combo)
        tbl_row.addWidget(QLabel(i18n.t("qb_table")))
        self._table_combo = QComboBox(); self._table_combo.setMinimumWidth(150)
        self._table_combo.currentTextChanged.connect(self._on_table_change)
        tbl_row.addWidget(self._table_combo)
        reload_btn = QPushButton("↻")
        reload_btn.setFixedWidth(30)
        reload_btn.clicked.connect(self._reload_schemas)
        tbl_row.addWidget(reload_btn)
        tbl_row.addStretch()
        top_l.addLayout(tbl_row)

        # SELECT columns
        sel_box = QGroupBox(i18n.t("qb_select_cols"))
        sel_l = QVBoxLayout(sel_box)
        self._select_all = QCheckBox(i18n.t("qb_select_all"))
        self._select_all.setChecked(True)
        self._select_all.toggled.connect(self._on_select_all)
        sel_l.addWidget(self._select_all)
        self._col_checks: list[QCheckBox] = []
        self._col_check_layout = QHBoxLayout()
        sel_l.addLayout(self._col_check_layout)
        top_l.addWidget(sel_box)

        # WHERE conditions
        where_box = QGroupBox(i18n.t("qb_where"))
        self._where_layout = QVBoxLayout(where_box)
        add_cond_btn = QPushButton(f"+ {i18n.t('qb_add_condition')}")
        add_cond_btn.clicked.connect(self._add_condition)
        self._where_layout.addWidget(add_cond_btn)
        top_l.addWidget(where_box)

        # ORDER BY + LIMIT
        order_row = QHBoxLayout()
        order_row.addWidget(QLabel(i18n.t("qb_order_by")))
        self._order_col = QComboBox(); self._order_col.setMinimumWidth(130)
        order_row.addWidget(self._order_col)
        self._order_dir = QComboBox()
        self._order_dir.addItems(_ORDER)
        order_row.addWidget(self._order_dir)
        order_row.addWidget(QLabel(i18n.t("qb_limit")))
        self._limit = QSpinBox()
        self._limit.setRange(1, 100000); self._limit.setValue(
            config.get("max_rows", 1000))
        self._limit.setMaximumWidth(90)
        order_row.addWidget(self._limit)
        order_row.addStretch()
        top_l.addLayout(order_row)

        # Action buttons
        action_row = QHBoxLayout()
        build_btn = QPushButton(f"⚙  {i18n.t('qb_build')}")
        build_btn.clicked.connect(self._build_sql)
        action_row.addWidget(build_btn)
        run_btn = QPushButton(f"▶  {i18n.t('qb_run')}")
        run_btn.setStyleSheet("font-weight: bold;")
        run_btn.clicked.connect(self._run)
        action_row.addWidget(run_btn)
        save_btn = QPushButton(f"💾  {i18n.t('qb_save')}")
        save_btn.clicked.connect(self._save_query)
        action_row.addWidget(save_btn)
        action_row.addStretch()
        top_l.addLayout(action_row)

        # Generated SQL
        sql_box = QGroupBox(i18n.t("qb_generated_sql"))
        sql_l = QVBoxLayout(sql_box)
        self._sql_edit = QTextEdit()
        self._sql_edit.setMaximumHeight(90)
        self._sql_edit.setFontFamily("Courier New")
        sql_l.addWidget(self._sql_edit)
        top_l.addWidget(sql_box)

        main.addWidget(top)

        # ── Bottom: results ───────────────────────────────────────────────────
        bottom = QWidget()
        bot_l = QVBoxLayout(bottom)
        bot_l.setContentsMargins(8, 4, 8, 8)
        self._result_lbl = QLabel("")
        bot_l.addWidget(self._result_lbl)
        self._result_table = QTableWidget()
        self._result_table.setAlternatingRowColors(True)
        self._result_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._result_table.setSortingEnabled(True)
        bot_l.addWidget(self._result_table)
        main.addWidget(bottom)
        main.setSizes([500, 300])

    # ── Layer loading ─────────────────────────────────────────────────────────

    def set_active_layer(self, schema: str, table: str):
        idx = self._schema_combo.findText(schema)
        if idx >= 0: self._schema_combo.setCurrentIndex(idx)
        idx = self._table_combo.findText(table)
        if idx >= 0: self._table_combo.setCurrentIndex(idx)

    def _reload_schemas(self):
        if not self.db.is_connected(): return
        try:
            schemas = self.db.get_schemas()
            self._schema_combo.blockSignals(True)
            self._schema_combo.clear()
            self._schema_combo.addItems(schemas)
            self._schema_combo.blockSignals(False)
            self._on_schema_change(self._schema_combo.currentText())
        except Exception: pass

    def _on_schema_change(self, schema: str):
        self._schema = schema
        if not schema or not self.db.is_connected(): return
        try:
            tables = [t[0] for t in self.db.get_all_tables(schema)]
            self._table_combo.blockSignals(True)
            self._table_combo.clear()
            self._table_combo.addItems(tables)
            self._table_combo.blockSignals(False)
            self._on_table_change(self._table_combo.currentText())
        except Exception: pass

    def _on_table_change(self, table: str):
        self._table = table
        self._refresh_columns()

    def _refresh_columns(self):
        if not self._schema or not self._table or not self.db.is_connected():
            return
        try:
            cols_info = self.db.get_columns(self._schema, self._table,
                                            exclude_geom=False)
            self._columns = [c[0] for c in cols_info]
            # rebuild column checkboxes
            for cb in self._col_checks:
                cb.setParent(None)
            self._col_checks.clear()
            for col in self._columns:
                cb = QCheckBox(col)
                cb.setChecked(True)
                self._col_checks.append(cb)
                self._col_check_layout.addWidget(cb)
            # rebuild condition col combos
            for cr in self._condition_rows:
                current = cr._col.currentText()
                cr._col.clear()
                cr._col.addItems(self._columns)
                idx = cr._col.findText(current)
                if idx >= 0: cr._col.setCurrentIndex(idx)
            # order col
            current_order = self._order_col.currentText()
            self._order_col.clear()
            self._order_col.addItem("(none)")
            self._order_col.addItems(self._columns)
            idx = self._order_col.findText(current_order)
            if idx >= 0: self._order_col.setCurrentIndex(idx)
        except Exception: pass

    # ── Condition management ──────────────────────────────────────────────────

    def _add_condition(self):
        cr = ConditionRow(self._columns, len(self._condition_rows), self)
        cr.removed.connect(self._remove_condition)
        self._condition_rows.append(cr)
        self._where_layout.insertWidget(
            self._where_layout.count() - 1, cr)

    def _remove_condition(self, row: ConditionRow):
        self._condition_rows.remove(row)
        row.setParent(None)
        # Update visibility of logic combos
        for i, cr in enumerate(self._condition_rows):
            cr._logic.setVisible(i > 0)

    def _on_select_all(self, checked: bool):
        for cb in self._col_checks:
            cb.setChecked(checked)

    # ── SQL generation ────────────────────────────────────────────────────────

    def _build_sql(self) -> str:
        schema = self._schema; table = self._table
        if not schema or not table:
            return ""

        if self._select_all.isChecked():
            select_part = "*"
        else:
            selected = [f'"{cb.text()}"' for cb in self._col_checks
                        if cb.isChecked()]
            select_part = ", ".join(selected) if selected else "*"

        sql = f'SELECT {select_part}\nFROM "{schema}"."{table}"'

        where_parts = [cr.to_sql() for cr in self._condition_rows]
        where_parts = [p for p in where_parts if p]
        if where_parts:
            sql += f"\nWHERE {' '.join(where_parts)}"

        order_col = self._order_col.currentText()
        if order_col and order_col != "(none)":
            sql += (f'\nORDER BY "{order_col}" '
                    f'{self._order_dir.currentText()}')

        sql += f"\nLIMIT {self._limit.value()}"

        self._sql_edit.setPlainText(sql)
        return sql

    def _run(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return
        sql = self._build_sql()
        if not sql:
            QMessageBox.warning(self, "Error", i18n.t("qb_no_table"))
            return
        # Allow manual edits to sql box
        sql = self._sql_edit.toPlainText().strip()
        self._result_lbl.setText(i18n.t("status_running"))
        w = QueryRunWorker(self.db, sql)
        w.done.connect(self._on_result)
        w.error.connect(lambda e: (
            QMessageBox.critical(self, "Error", e),
            self._result_lbl.setText(f"✖ {e[:60]}")))
        w.start(); self._worker = w

    def _on_result(self, cols: list, rows: list, ms: float):
        self._result_lbl.setText(
            i18n.t("qb_result", n=len(rows), ms=round(ms, 1)))
        self._result_table.setRowCount(len(rows))
        self._result_table.setColumnCount(len(cols))
        self._result_table.setHorizontalHeaderLabels(cols)
        hh = self._result_table.horizontalHeader()
        for c in range(len(cols)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                self._result_table.setItem(
                    r, c, QTableWidgetItem("" if val is None else str(val)))
        if self.parent() and hasattr(self.parent(), "log"):
            self.parent().log(
                f"Query returned {len(rows)} rows in {round(ms,1)} ms", "success")

    def _save_query(self):
        sql = self._sql_edit.toPlainText().strip()
        if not sql: return
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, i18n.t("qb_save"), i18n.t("qb_save_name"))
        if ok and name.strip():
            config.save_query(name.strip(), sql)
            QMessageBox.information(self, "OK", i18n.t("qb_saved", name=name))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._schema_combo.count():
            self._reload_schemas()
