"""Attribute Table Dialog — pagination, SQL filter, inline edit, CSV export."""

from __future__ import annotations
import csv
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QTextEdit, QGroupBox, QComboBox,
    QFileDialog, QMessageBox, QHeaderView, QAbstractItemView,
    QWidget, QLineEdit,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont

from ..db.connection import DBManager
from ..utils import i18n

SQL_TEMPLATES = {
    "Text":     [("Contains",    "{col} ILIKE '%{val}%'"),
                 ("Starts with", "{col} ILIKE '{val}%'"),
                 ("Is NULL",     "{col} IS NULL"),
                 ("Not NULL",    "{col} IS NOT NULL")],
    "Number":   [("Equals",      "{col} = {val}"),
                 ("> than",      "{col} > {val}"),
                 ("Between",     "{col} BETWEEN {min} AND {max}"),
                 ("Is NULL",     "{col} IS NULL")],
    "Date":     [("Exact",       "{col}::date = '2024-01-01'"),
                 ("Range",       "{col} BETWEEN '2024-01-01' AND '2024-12-31'"),
                 ("Last 30d",    "{col} >= NOW() - INTERVAL '30 days'")],
    "Geometry": [("Area >",      "ST_Area({geom}) > {val}"),
                 ("Null geom",   "{geom} IS NULL"),
                 ("Invalid",     "NOT ST_IsValid({geom})")],
    "Logic":    [("AND",         "{cond1} AND {cond2}"),
                 ("OR",          "{cond1} OR {cond2}"),
                 ("NOT",         "NOT ({cond})")],
}


class DataWorker(QThread):
    done  = pyqtSignal(list, list)
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, limit, offset, where):
        super().__init__()
        self.db = db; self.schema = schema; self.table = table
        self.limit = limit; self.offset = offset; self.where = where

    def run(self):
        try:
            cols, rows = self.db.fetch_rows(
                self.schema, self.table, self.limit, self.offset, self.where)
            self.done.emit(cols, rows)
        except Exception as e:
            self.error.emit(str(e))


class AttributeTableDialog(QDialog):
    def __init__(self, db: DBManager, schema: str, table: str, parent=None):
        super().__init__(parent)
        self.db = db
        self.schema = schema
        self.table = table
        self.limit = 1000
        self.offset = 0
        self.where_filter = ""
        self.cols: list = []
        self.total_count = 0
        self._worker = None

        self.setWindowTitle(
            i18n.t("attr_table_title", schema=schema, table=table))
        self.setMinimumSize(1100, 650)
        self.resize(1300, 750)
        self._build_ui()
        self._load_count()
        self._load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # ── Filter ──
        filter_group = QGroupBox("WHERE filter")
        fg = QVBoxLayout(filter_group)

        self._col_row = QHBoxLayout()
        col_widget = QWidget()
        col_widget.setLayout(self._col_row)
        fg.addWidget(QLabel("Insert column:"))
        fg.addWidget(col_widget)

        self._filter_edit = QTextEdit()
        self._filter_edit.setMaximumHeight(65)
        self._filter_edit.setFont(QFont("Courier New", 11))
        self._filter_edit.setPlaceholderText(
            "e.g.  name ILIKE '%tbilisi%' AND population > 50000")
        fg.addWidget(self._filter_edit)

        tpl_row = QHBoxLayout()
        for cat, templates in SQL_TEMPLATES.items():
            btn = QPushButton(cat)
            btn.setFixedHeight(24)
            btn.clicked.connect(
                lambda _, t=templates, b=btn: self._show_tpl_menu(b, t))
            tpl_row.addWidget(btn)
        tpl_row.addStretch()
        fg.addLayout(tpl_row)

        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton(i18n.t("attr_filter_apply"))
        self._apply_btn.clicked.connect(self._apply_filter)
        btn_row.addWidget(self._apply_btn)
        self._clear_btn = QPushButton(i18n.t("attr_filter_clear"))
        self._clear_btn.clicked.connect(self._clear_filter)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()
        self._csv_btn = QPushButton(i18n.t("attr_export_csv"))
        self._csv_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(self._csv_btn)
        self._del_btn = QPushButton(i18n.t("attr_delete_selected"))
        self._del_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(self._del_btn)
        fg.addLayout(btn_row)
        layout.addWidget(filter_group)

        # ── Batch Edit ──
        batch_group = QGroupBox("Batch Edit")
        bg = QHBoxLayout(batch_group)
        bg.addWidget(QLabel("Column:"))
        self._batch_col = QComboBox()
        self._batch_col.setMinimumWidth(140)
        bg.addWidget(self._batch_col)
        bg.addWidget(QLabel("New value:"))
        self._batch_val = QLineEdit()
        self._batch_val.setPlaceholderText("value or SQL expression")
        bg.addWidget(self._batch_val, 1)
        self._batch_sel_btn = QPushButton("Apply to selected rows")
        self._batch_sel_btn.setToolTip(
            "UPDATE … WHERE ctid = ANY(selected)")
        self._batch_sel_btn.clicked.connect(self._batch_update_selected)
        bg.addWidget(self._batch_sel_btn)
        self._batch_all_btn = QPushButton("Apply to all filtered rows")
        self._batch_all_btn.setToolTip(
            "UPDATE … WHERE <current filter>")
        self._batch_all_btn.clicked.connect(self._batch_update_all)
        bg.addWidget(self._batch_all_btn)
        layout.addWidget(batch_group)

        # ── Table ──
        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemChanged.connect(self._on_cell_changed)
        layout.addWidget(self._table)

        # ── Pagination ──
        page_row = QHBoxLayout()
        self._prev_btn = QPushButton("◀ " + i18n.t("attr_page_prev"))
        self._prev_btn.clicked.connect(self._prev_page)
        page_row.addWidget(self._prev_btn)
        self._status_label = QLabel("")
        page_row.addWidget(self._status_label, 1)
        self._next_btn = QPushButton(i18n.t("attr_page_next") + " ▶")
        self._next_btn.clicked.connect(self._next_page)
        page_row.addWidget(self._next_btn)
        self._page_combo = QComboBox()
        for n in [100, 250, 500, 1000, 2500]:
            self._page_combo.addItem(f"{n} rows", n)
        self._page_combo.setCurrentIndex(3)
        self._page_combo.currentIndexChanged.connect(self._change_page_size)
        page_row.addWidget(QLabel("Per page:"))
        page_row.addWidget(self._page_combo)
        layout.addLayout(page_row)

    def _load_count(self):
        try:
            self.total_count = self.db.get_feature_count(
                self.schema, self.table, self.where_filter)
        except Exception:
            self.total_count = 0

    def _load_data(self):
        if self._worker and self._worker.isRunning():
            return
        self._worker = DataWorker(
            self.db, self.schema, self.table,
            self.limit, self.offset, self.where_filter)
        self._worker.done.connect(self._populate)
        self._worker.error.connect(
            lambda e: QMessageBox.critical(self, "Error", e))
        self._worker.start()

    def _populate(self, cols: list, rows: list):
        self.cols = cols
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)

        # rebuild column chips
        while self._col_row.count():
            w = self._col_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for col in cols:
            chip = QPushButton(col)
            chip.setFixedHeight(22)
            chip.clicked.connect(lambda _, c=col: self._insert_text(f'"{c}"'))
            self._col_row.addWidget(chip)
        self._col_row.addStretch()

        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            ctid = row[0]
            for c, val in enumerate(row[1:]):
                item = QTableWidgetItem(
                    str(val) if val is not None else "")
                item.setData(Qt.ItemDataRole.UserRole,
                             {"ctid": str(ctid), "col": cols[c]})
                self._table.setItem(r, c, item)

        self._table.blockSignals(False)
        # update batch-edit column combo
        self._batch_col.clear()
        self._batch_col.addItems(cols)
        showing = len(rows)
        filt = "  |  🔍 Filter active" if self.where_filter else ""
        end = min(self.offset + showing, self.total_count)
        self._status_label.setText(
            f"Total: {self.total_count:,}  |  "
            f"Showing: {self.offset + 1}–{end}{filt}")
        self._prev_btn.setEnabled(self.offset > 0)
        self._next_btn.setEnabled(self.offset + showing < self.total_count)

    def _on_cell_changed(self, item: QTableWidgetItem):
        d = item.data(Qt.ItemDataRole.UserRole)
        if not d:
            return
        try:
            self.db.update_cell(
                self.schema, self.table, d["ctid"], d["col"], item.text())
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _apply_filter(self):
        self.where_filter = self._filter_edit.toPlainText().strip()
        self.offset = 0
        self._load_count()
        self._load_data()

    def _clear_filter(self):
        self._filter_edit.clear()
        self.where_filter = ""
        self.offset = 0
        self._load_count()
        self._load_data()

    def _prev_page(self):
        self.offset = max(0, self.offset - self.limit)
        self._load_data()

    def _next_page(self):
        self.offset += self.limit
        self._load_data()

    def _change_page_size(self, _):
        self.limit = self._page_combo.currentData()
        self.offset = 0
        self._load_data()

    def _delete_selected(self):
        rows = {i.row() for i in self._table.selectedItems()}
        if not rows:
            return
        if QMessageBox.question(
                self, "Confirm",
                i18n.t("confirm_delete_n", n=len(rows))) \
                != QMessageBox.StandardButton.Yes:
            return
        ctids = []
        for r in rows:
            item = self._table.item(r, 0)
            if item:
                d = item.data(Qt.ItemDataRole.UserRole)
                if d:
                    ctids.append(d["ctid"])
        try:
            self.db.delete_rows(self.schema, self.table, ctids)
            self._load_count()
            self._load_data()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _batch_update_selected(self):
        col = self._batch_col.currentText()
        val = self._batch_val.text().strip()
        if not col or not val:
            QMessageBox.warning(self, "Batch Edit", "Choose column and enter value.")
            return
        selected_rows = sorted({i.row() for i in self._table.selectedItems()})
        if not selected_rows:
            QMessageBox.warning(self, "Batch Edit", "Select at least one row first.")
            return
        ctids = []
        for r in selected_rows:
            item = self._table.item(r, 0)
            if item:
                d = item.data(Qt.ItemDataRole.UserRole)
                if d:
                    ctids.append(d["ctid"])
        if not ctids:
            return
        confirm = QMessageBox.question(
            self, "Confirm Batch Update",
            f"Update column '{col}' = {val}\nfor {len(ctids)} row(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self.db.params)
            cur = conn.cursor()
            ctid_list = ", ".join(f"'{c}'::tid" for c in ctids)
            sql = (f'UPDATE "{self.schema}"."{self.table}" '
                   f'SET "{col}" = {val} '
                   f'WHERE ctid IN ({ctid_list})')
            cur.execute(sql)
            conn.commit()
            cur.close(); conn.close()
            self._load_data()
            QMessageBox.information(
                self, "Done", f"Updated {len(ctids)} row(s).")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _batch_update_all(self):
        col = self._batch_col.currentText()
        val = self._batch_val.text().strip()
        if not col or not val:
            QMessageBox.warning(self, "Batch Edit", "Choose column and enter value.")
            return
        where = f"WHERE {self.where_filter}" if self.where_filter else ""
        n_est = self.total_count
        confirm = QMessageBox.question(
            self, "Confirm Batch Update",
            f"Update column '{col}' = {val}\n"
            f"for ALL ~{n_est:,} filtered row(s)?\n\n"
            f"This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self.db.params)
            cur = conn.cursor()
            sql = (f'UPDATE "{self.schema}"."{self.table}" '
                   f'SET "{col}" = {val} {where}')
            cur.execute(sql)
            affected = cur.rowcount
            conn.commit()
            cur.close(); conn.close()
            self._load_count()
            self._load_data()
            QMessageBox.information(
                self, "Done", f"Updated {affected} row(s).")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", f"{self.table}.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            cols, rows = self.db.fetch_rows(
                self.schema, self.table, 100000, 0, self.where_filter)
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                for row in rows:
                    w.writerow(row[1:])
            QMessageBox.information(self, "Done",
                                    f"Exported {len(rows)} rows to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _insert_text(self, text: str):
        cursor = self._filter_edit.textCursor()
        cursor.insertText(text)
        self._filter_edit.setFocus()

    def _show_tpl_menu(self, btn, templates):
        menu = __import__("PyQt6.QtWidgets", fromlist=["QMenu"]).QMenu(self)
        for label, sql in templates:
            act = menu.addAction(f"{label}  —  {sql}")
            act.setData(sql)
        chosen = menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if chosen:
            self._insert_text(chosen.data())
