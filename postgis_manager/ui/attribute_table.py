"""Attribute Table Dialog with SQL filter builder, pagination, inline edit."""

from __future__ import annotations
import csv
import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QLineEdit, QTextEdit, QSplitter, QGroupBox,
    QComboBox, QSpinBox, QFileDialog, QMessageBox, QHeaderView,
    QAbstractItemView, QStatusBar, QWidget, QSizePolicy, QFrame,
    QCompleter,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QStringListModel
from PyQt5.QtGui import QFont, QColor

from ..db.connection import DBManager
from ..utils import i18n, theme

SQL_TEMPLATES = {
    "Text": [
        ("Contains",     "{col} ILIKE '%{val}%'"),
        ("Starts with",  "{col} ILIKE '{val}%'"),
        ("Ends with",    "{col} ILIKE '%{val}'"),
        ("Exact match",  "{col} = '{val}'"),
        ("In list",      "{col} IN ('val1','val2')"),
        ("Is NULL",      "{col} IS NULL OR {col} = ''"),
        ("Not NULL",     "{col} IS NOT NULL AND {col} != ''"),
    ],
    "Number": [
        ("Equals",       "{col} = {val}"),
        ("Greater than", "{col} > {val}"),
        ("Less than",    "{col} < {val}"),
        ("Between",      "{col} BETWEEN {min} AND {max}"),
        ("In list",      "{col} IN (1, 2, 3)"),
        ("Is NULL",      "{col} IS NULL"),
    ],
    "Date": [
        ("Exact date",   "{col}::date = '2024-01-01'"),
        ("Range",        "{col} BETWEEN '2024-01-01' AND '2024-12-31'"),
        ("Year",         "EXTRACT(YEAR FROM {col}) = 2024"),
        ("Last N days",  "{col} >= NOW() - INTERVAL '30 days'"),
    ],
    "Geometry": [
        ("Area >",       "ST_Area({geom}) > {val}"),
        ("Length >",     "ST_Length({geom}) > {val}"),
        ("Null geom",    "{geom} IS NULL"),
        ("Valid",        "NOT ST_IsValid({geom})"),
        ("SRID",         "ST_SRID({geom}) = 4326"),
    ],
    "Logic": [
        ("AND",          "{cond1} AND {cond2}"),
        ("OR",           "{cond1} OR {cond2}"),
        ("NOT",          "NOT ({cond})"),
        ("Group",        "({cond1}) AND ({cond2})"),
    ],
}


class DataWorker(QThread):
    done = pyqtSignal(list, list)
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, limit, offset, where):
        super().__init__()
        self.db = db
        self.schema = schema
        self.table = table
        self.limit = limit
        self.offset = offset
        self.where = where

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
        self._worker: DataWorker | None = None

        self.setWindowTitle(i18n.t("attr_table_title", schema=schema, table=table))
        self.setMinimumSize(1100, 650)
        self.resize(1300, 750)
        self._build_ui()
        self._load_count()
        self._load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # ── Filter area ──
        filter_group = QGroupBox("WHERE filter")
        fg_layout = QVBoxLayout(filter_group)

        # Column chips
        self._col_row = QHBoxLayout()
        self._col_chips_widget = QWidget()
        self._col_chips_widget.setLayout(self._col_row)
        fg_layout.addWidget(QLabel("Insert column:"))
        fg_layout.addWidget(self._col_chips_widget)

        # SQL input
        self._filter_edit = QTextEdit()
        self._filter_edit.setMaximumHeight(70)
        self._filter_edit.setPlaceholderText(
            "e.g.  name ILIKE '%tbilisi%' AND population > 50000")
        self._filter_edit.setFont(QFont("Courier New", 11))
        fg_layout.addWidget(self._filter_edit)

        # Template buttons
        tpl_row = QHBoxLayout()
        for cat, templates in SQL_TEMPLATES.items():
            cat_btn = QPushButton(cat)
            cat_btn.setProperty("class", "secondary")
            cat_btn.setFixedHeight(26)
            menu_items = templates
            cat_btn.clicked.connect(
                lambda checked, t=templates, b=cat_btn: self._show_template_menu(b, t)
            )
            tpl_row.addWidget(cat_btn)
        tpl_row.addStretch()
        fg_layout.addLayout(tpl_row)

        # Action buttons
        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton(i18n.t("attr_filter_apply"))
        self._apply_btn.clicked.connect(self._apply_filter)
        btn_row.addWidget(self._apply_btn)

        self._clear_btn = QPushButton(i18n.t("attr_filter_clear"))
        self._clear_btn.setProperty("class", "secondary")
        self._clear_btn.clicked.connect(self._clear_filter)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()

        self._export_csv_btn = QPushButton(i18n.t("attr_export_csv"))
        self._export_csv_btn.setProperty("class", "secondary")
        self._export_csv_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(self._export_csv_btn)

        self._delete_btn = QPushButton(i18n.t("attr_delete_selected"))
        self._delete_btn.setProperty("class", "danger")
        self._delete_btn.clicked.connect(self._delete_selected)
        btn_row.addWidget(self._delete_btn)

        fg_layout.addLayout(btn_row)
        layout.addWidget(filter_group)

        # ── Table ──
        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.DoubleClicked)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.itemChanged.connect(self._on_cell_changed)
        layout.addWidget(self._table)

        # ── Pagination ──
        page_row = QHBoxLayout()
        self._prev_btn = QPushButton("◀  " + i18n.t("attr_page_prev"))
        self._prev_btn.setProperty("class", "secondary")
        self._prev_btn.clicked.connect(self._prev_page)
        page_row.addWidget(self._prev_btn)

        self._status_label = QLabel("")
        page_row.addWidget(self._status_label, 1)

        self._next_btn = QPushButton(i18n.t("attr_page_next") + "  ▶")
        self._next_btn.setProperty("class", "secondary")
        self._next_btn.clicked.connect(self._next_page)
        page_row.addWidget(self._next_btn)

        self._page_size_combo = QComboBox()
        for n in [100, 250, 500, 1000, 2500]:
            self._page_size_combo.addItem(f"{n} rows", n)
        self._page_size_combo.setCurrentIndex(3)
        self._page_size_combo.currentIndexChanged.connect(self._change_page_size)
        page_row.addWidget(QLabel("Per page:"))
        page_row.addWidget(self._page_size_combo)

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
            self.limit, self.offset, self.where_filter
        )
        self._worker.done.connect(self._populate_table)
        self._worker.error.connect(
            lambda e: QMessageBox.critical(self, "Error", e))
        self._worker.start()

    def _populate_table(self, cols: list, rows: list):
        self.cols = cols
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)

        # Column chips
        while self._col_row.count():
            w = self._col_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for col in cols:
            chip = QPushButton(col)
            chip.setFixedHeight(22)
            chip.setProperty("class", "secondary")
            chip.clicked.connect(lambda _, c=col: self._insert_col(c))
            self._col_row.addWidget(chip)
        self._col_row.addStretch()

        self._table.setRowCount(len(rows))
        for r_idx, row in enumerate(rows):
            ctid = row[0]
            for c_idx, val in enumerate(row[1:]):
                item = QTableWidgetItem(str(val) if val is not None else "")
                item.setData(Qt.UserRole, {"ctid": str(ctid), "col": cols[c_idx]})
                self._table.setItem(r_idx, c_idx, item)

        self._table.blockSignals(False)
        active = f"  |  🔍 Filter active" if self.where_filter else ""
        self._status_label.setText(
            f"Total: {self.total_count:,}  |  "
            f"Showing: {self.offset + 1}–{min(self.offset + len(rows), self.total_count)}"
            f"{active}"
        )
        self._prev_btn.setEnabled(self.offset > 0)
        self._next_btn.setEnabled(self.offset + len(rows) < self.total_count)

    def _on_cell_changed(self, item: QTableWidgetItem):
        data = item.data(Qt.UserRole)
        if not data:
            return
        try:
            self.db.update_cell(
                self.schema, self.table,
                data["ctid"], data["col"], item.text()
            )
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
        self.limit = self._page_size_combo.currentData()
        self.offset = 0
        self._load_data()

    def _delete_selected(self):
        rows = set(i.row() for i in self._table.selectedItems())
        if not rows:
            return
        msg = i18n.t("confirm_delete_n", n=len(rows))
        if QMessageBox.question(self, "Confirm", msg) != QMessageBox.Yes:
            return
        ctids = []
        for r in rows:
            item = self._table.item(r, 0)
            if item:
                d = item.data(Qt.UserRole)
                if d:
                    ctids.append(d["ctid"])
        try:
            self.db.delete_rows(self.schema, self.table, ctids)
            self._load_count()
            self._load_data()
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
                writer = csv.writer(f)
                writer.writerow(cols)
                for row in rows:
                    writer.writerow(row[1:])
            QMessageBox.information(self, "Done",
                                    f"Exported {len(rows)} rows to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _insert_col(self, col: str):
        cursor = self._filter_edit.textCursor()
        cursor.insertText(f'"{col}"')
        self._filter_edit.setFocus()

    def _show_template_menu(self, btn: QPushButton, templates: list):
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        for label, sql in templates:
            act = menu.addAction(f"{label}  —  {sql}")
            act.setData(sql)
        chosen = menu.exec_(btn.mapToGlobal(btn.rect().bottomLeft()))
        if chosen:
            self._insert_col(chosen.data())
