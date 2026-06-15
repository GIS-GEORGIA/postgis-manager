"""Table Designer — create/alter tables, manage columns and spatial indexes."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QComboBox, QLineEdit, QCheckBox, QMessageBox,
    QSplitter, QTabWidget, QSpinBox, QDialog, QDialogButtonBox,
    QFormLayout, QTextEdit, QProgressBar,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor

from ...db.connection import DBManager
from ...utils import i18n

# ── Common PostgreSQL + PostGIS types ─────────────────────────────────────────

PG_TYPES = [
    "integer", "bigint", "smallint", "serial", "bigserial",
    "real", "double precision", "numeric", "decimal",
    "boolean",
    "text", "varchar", "char", "uuid",
    "date", "timestamp", "timestamptz", "time",
    "json", "jsonb",
    "bytea", "oid",
    # PostGIS
    "geometry", "geography",
    "geometry(Point,4326)", "geometry(MultiPoint,4326)",
    "geometry(LineString,4326)", "geometry(MultiLineString,4326)",
    "geometry(Polygon,4326)", "geometry(MultiPolygon,4326)",
    "geometry(GeometryCollection,4326)",
    "raster",
]

GEOM_TYPES = [
    "POINT", "MULTIPOINT", "LINESTRING", "MULTILINESTRING",
    "POLYGON", "MULTIPOLYGON", "GEOMETRYCOLLECTION",
    "POINTZ", "POLYGONZ", "LINESTRINGZ",
]


# ── Workers ───────────────────────────────────────────────────────────────────

class TableWorker(QThread):
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


class LoadSchemaWorker(QThread):
    done  = pyqtSignal(list, list)   # schemas, tables_for_first_schema
    error = pyqtSignal(str)

    def __init__(self, db: DBManager):
        super().__init__()
        self.db = db

    def run(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT schema_name FROM information_schema.schemata
                    WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')
                    ORDER BY schema_name
                """)
                schemas = [r[0] for r in cur.fetchall()]
            self.done.emit(schemas, [])
        except Exception as e:
            self.error.emit(str(e))


class LoadColumnsWorker(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, schema: str, table: str):
        super().__init__()
        self.db = db
        self.schema = schema
        self.table = table

    def run(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name, data_type, character_maximum_length,
                           is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                """, (self.schema, self.table))
                self.done.emit(cur.fetchall())
        except Exception as e:
            self.error.emit(str(e))


class LoadIndexWorker(QThread):
    done  = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, schema: str, table: str):
        super().__init__()
        self.db = db; self.schema = schema; self.table = table

    def run(self):
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT indexname, indexdef,
                           pg_size_pretty(pg_relation_size(indexrelid)) AS size,
                           idx_scan
                    FROM pg_stat_user_indexes
                    WHERE schemaname = %s AND relname = %s
                    ORDER BY indexname
                """, (self.schema, self.table))
                self.done.emit(cur.fetchall())
        except Exception as e:
            self.error.emit(str(e))


# ── Column row widget ─────────────────────────────────────────────────────────

class ColumnRow:
    """Holds widgets for one column in the 'Create table' column builder."""

    def __init__(self, table: QTableWidget, row: int):
        self.table = table
        self.row   = row
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("column_name")
        self.type_combo = QComboBox()
        self.type_combo.setEditable(True)
        self.type_combo.addItems(PG_TYPES)
        self.pk_chk    = QCheckBox()
        self.nn_chk    = QCheckBox()
        self.nn_chk.setChecked(True)
        self.default_edit = QLineEdit()
        self.default_edit.setPlaceholderText("default value (optional)")
        table.setCellWidget(row, 0, self.name_edit)
        table.setCellWidget(row, 1, self.type_combo)
        table.setCellWidget(row, 2, self.pk_chk)
        table.setCellWidget(row, 3, self.nn_chk)
        table.setCellWidget(row, 4, self.default_edit)

    def to_sql_fragment(self) -> str | None:
        name = self.name_edit.text().strip()
        typ  = self.type_combo.currentText().strip()
        if not name or not typ:
            return None
        parts = [f'"{name}" {typ}']
        if self.pk_chk.isChecked():
            parts.append("PRIMARY KEY")
        elif self.nn_chk.isChecked():
            parts.append("NOT NULL")
        default = self.default_edit.text().strip()
        if default:
            parts.append(f"DEFAULT {default}")
        return " ".join(parts)


# ── Main Panel ────────────────────────────────────────────────────────────────

class TableDesignerPanel(QWidget):
    layer_changed = pyqtSignal()

    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._column_rows: list[ColumnRow] = []
        self._current_schema: str | None = None
        self._current_table:  str | None = None
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"🛠  {i18n.t('td_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        self._inner_tabs = QTabWidget()
        layout.addWidget(self._inner_tabs)

        self._inner_tabs.addTab(self._build_create_tab(),  i18n.t("td_tab_create"))
        self._inner_tabs.addTab(self._build_alter_tab(),   i18n.t("td_tab_alter"))
        self._inner_tabs.addTab(self._build_index_tab(),   i18n.t("td_tab_indexes"))
        self._inner_tabs.addTab(self._build_maintain_tab(),i18n.t("td_tab_maintain"))

        self._status = QLabel("")
        layout.addWidget(self._status)

    # ── Create table tab ──────────────────────────────────────────────────────

    def _build_create_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)

        form = QFormLayout()
        self._cr_schema = QComboBox(); self._cr_schema.setEditable(True)
        self._cr_table  = QLineEdit(); self._cr_table.setPlaceholderText("new_table")
        form.addRow(i18n.t("td_schema"), self._cr_schema)
        form.addRow(i18n.t("td_table"),  self._cr_table)
        l.addLayout(form)

        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(f"<b>{i18n.t('td_columns')}</b>"))
        hdr.addStretch()
        add_btn = QPushButton(f"＋ {i18n.t('td_add_column')}")
        add_btn.clicked.connect(self._add_column_row)
        del_btn = QPushButton(f"－ {i18n.t('td_remove_column')}")
        del_btn.clicked.connect(self._remove_column_row)
        hdr.addWidget(add_btn); hdr.addWidget(del_btn)
        l.addLayout(hdr)

        self._col_table = QTableWidget(0, 5)
        self._col_table.setHorizontalHeaderLabels([
            i18n.t("td_col_name"), i18n.t("td_col_type"),
            "PK", "NOT NULL", i18n.t("td_col_default")])
        self._col_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._col_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self._col_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        l.addWidget(self._col_table)

        # Geometry group
        geo_box = QGroupBox(i18n.t("td_geom_options"))
        geo_box.setCheckable(True); geo_box.setChecked(False)
        geo_l = QFormLayout(geo_box)
        self._geom_col  = QLineEdit("geom")
        self._geom_type = QComboBox(); self._geom_type.addItems(GEOM_TYPES)
        self._geom_srid = QSpinBox()
        self._geom_srid.setRange(1, 999999); self._geom_srid.setValue(4326)
        self._geom_dim  = QComboBox(); self._geom_dim.addItems(["2D", "3D"])
        geo_l.addRow(i18n.t("td_geom_col"),  self._geom_col)
        geo_l.addRow(i18n.t("td_geom_type"), self._geom_type)
        geo_l.addRow("SRID:", self._geom_srid)
        geo_l.addRow(i18n.t("td_geom_dim"),  self._geom_dim)
        self._geo_box = geo_box
        l.addWidget(geo_box)

        self._cr_preview = QTextEdit()
        self._cr_preview.setReadOnly(True)
        self._cr_preview.setMaximumHeight(80)
        self._cr_preview.setPlaceholderText("SQL preview…")
        l.addWidget(self._cr_preview)

        btns = QHBoxLayout()
        prev_btn = QPushButton(i18n.t("td_preview_sql"))
        prev_btn.clicked.connect(self._preview_create)
        cr_btn   = QPushButton(f"✔ {i18n.t('td_create_table')}")
        cr_btn.clicked.connect(self._create_table)
        btns.addStretch(); btns.addWidget(prev_btn); btns.addWidget(cr_btn)
        l.addLayout(btns)

        self._add_column_row()  # start with one row
        return w

    def _add_column_row(self):
        row = self._col_table.rowCount()
        self._col_table.insertRow(row)
        self._column_rows.append(ColumnRow(self._col_table, row))

    def _remove_column_row(self):
        row = self._col_table.currentRow()
        if row >= 0:
            self._col_table.removeRow(row)
            if row < len(self._column_rows):
                self._column_rows.pop(row)
            for i, cr in enumerate(self._column_rows):
                cr.row = i

    def _build_create_sql(self) -> str | None:
        schema = self._cr_schema.currentText().strip()
        table  = self._cr_table.text().strip()
        if not schema or not table:
            return None
        frags = []
        for cr in self._column_rows:
            f = cr.to_sql_fragment()
            if f:
                frags.append(f"    {f}")
        if not frags:
            return None
        sql = f'CREATE TABLE "{schema}"."{table}" (\n'
        sql += ",\n".join(frags) + "\n);"
        if self._geo_box.isChecked():
            gcol = self._geom_col.text().strip() or "geom"
            gtyp = self._geom_type.currentText()
            srid = self._geom_srid.value()
            dim  = "Z" if self._geom_dim.currentIndex() == 1 else ""
            sql += (f'\nALTER TABLE "{schema}"."{table}" '
                    f'ADD COLUMN "{gcol}" geometry({gtyp}{dim},{srid});')
            sql += (f'\nCREATE INDEX ON "{schema}"."{table}" '
                    f'USING GIST ("{gcol}");')
        return sql

    def _preview_create(self):
        sql = self._build_create_sql()
        self._cr_preview.setPlainText(sql or "-- Fill schema, table name and at least one column")

    def _create_table(self):
        sql = self._build_create_sql()
        if not sql:
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("td_fill_required"))
            return
        if not self.db.is_connected():
            QMessageBox.warning(self, "PostGIS Manager",
                                i18n.t("err_not_connected"))
            return
        self._run_sql(sql, i18n.t("td_table_created"))

    # ── Alter table tab ───────────────────────────────────────────────────────

    def _build_alter_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)

        sel = QHBoxLayout()
        self._alt_schema = QComboBox(); self._alt_schema.setEditable(True)
        self._alt_table  = QComboBox(); self._alt_table.setEditable(True)
        self._alt_schema.currentTextChanged.connect(self._load_alter_tables)
        self._alt_table.currentTextChanged.connect(self._load_columns)
        load_btn = QPushButton(i18n.t("td_load"))
        load_btn.clicked.connect(self._load_columns)
        sel.addWidget(QLabel(i18n.t("td_schema"))); sel.addWidget(self._alt_schema)
        sel.addWidget(QLabel(i18n.t("td_table")));  sel.addWidget(self._alt_table)
        sel.addWidget(load_btn)
        l.addLayout(sel)

        self._alt_col_table = QTableWidget(0, 5)
        self._alt_col_table.setHorizontalHeaderLabels([
            i18n.t("td_col_name"), i18n.t("td_col_type"),
            "Max Len", "Nullable", "Default"])
        self._alt_col_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._alt_col_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self._alt_col_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        l.addWidget(self._alt_col_table)

        ops = QGroupBox(i18n.t("td_operations"))
        op_l = QVBoxLayout(ops)

        # Add column
        add_row = QHBoxLayout()
        self._add_col_name = QLineEdit(); self._add_col_name.setPlaceholderText("column_name")
        self._add_col_type = QComboBox(); self._add_col_type.setEditable(True)
        self._add_col_type.addItems(PG_TYPES)
        add_c_btn = QPushButton(f"＋ {i18n.t('td_add_column')}")
        add_c_btn.clicked.connect(self._do_add_column)
        add_row.addWidget(QLabel(i18n.t("td_add_column") + ":"))
        add_row.addWidget(self._add_col_name)
        add_row.addWidget(self._add_col_type)
        add_row.addWidget(add_c_btn)
        op_l.addLayout(add_row)

        # Rename column
        ren_row = QHBoxLayout()
        self._ren_old = QLineEdit(); self._ren_old.setPlaceholderText("old_name")
        self._ren_new = QLineEdit(); self._ren_new.setPlaceholderText("new_name")
        ren_btn = QPushButton(i18n.t("td_rename_column"))
        ren_btn.clicked.connect(self._do_rename_column)
        ren_row.addWidget(QLabel(i18n.t("td_rename_column") + ":"))
        ren_row.addWidget(self._ren_old)
        ren_row.addWidget(QLabel("→"))
        ren_row.addWidget(self._ren_new)
        ren_row.addWidget(ren_btn)
        op_l.addLayout(ren_row)

        # Drop column
        drp_row = QHBoxLayout()
        self._drp_col = QLineEdit(); self._drp_col.setPlaceholderText("column_name")
        drp_btn = QPushButton(f"✖ {i18n.t('td_drop_column')}")
        drp_btn.clicked.connect(self._do_drop_column)
        drp_row.addWidget(QLabel(i18n.t("td_drop_column") + ":"))
        drp_row.addWidget(self._drp_col)
        drp_row.addWidget(drp_btn)
        op_l.addLayout(drp_row)

        # Rename / drop table
        tbl_row = QHBoxLayout()
        self._tbl_new_name = QLineEdit()
        self._tbl_new_name.setPlaceholderText(i18n.t("td_new_table_name"))
        ren_tbl = QPushButton(i18n.t("td_rename_table"))
        ren_tbl.clicked.connect(self._do_rename_table)
        drp_tbl = QPushButton(f"🗑 {i18n.t('td_drop_table')}")
        drp_tbl.clicked.connect(self._do_drop_table)
        tbl_row.addWidget(self._tbl_new_name)
        tbl_row.addWidget(ren_tbl)
        tbl_row.addStretch()
        tbl_row.addWidget(drp_tbl)
        op_l.addLayout(tbl_row)

        l.addWidget(ops)
        return w

    def _load_alter_tables(self, schema: str):
        if not self.db.is_connected() or not schema.strip():
            return
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = %s
                    ORDER BY table_name
                """, (schema.strip(),))
                tables = [r[0] for r in cur.fetchall()]
            self._alt_table.clear()
            self._alt_table.addItems(tables)
        except Exception:
            pass

    def _load_columns(self):
        schema = self._alt_schema.currentText().strip()
        table  = self._alt_table.currentText().strip()
        if not schema or not table or not self.db.is_connected():
            return
        self._current_schema = schema
        self._current_table  = table
        w = LoadColumnsWorker(self.db, schema, table)
        w.done.connect(self._on_columns_loaded)
        w.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        w.start()

    def _on_columns_loaded(self, rows: list):
        t = self._alt_col_table
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                t.setItem(r, c, QTableWidgetItem("" if val is None else str(val)))

    def _do_add_column(self):
        s, tb = self._current_schema, self._current_table
        name = self._add_col_name.text().strip()
        typ  = self._add_col_type.currentText().strip()
        if not s or not tb or not name or not typ:
            return
        self._run_sql(
            f'ALTER TABLE "{s}"."{tb}" ADD COLUMN "{name}" {typ};',
            i18n.t("td_column_added"))

    def _do_rename_column(self):
        s, tb = self._current_schema, self._current_table
        old = self._ren_old.text().strip(); new = self._ren_new.text().strip()
        if not s or not tb or not old or not new:
            return
        self._run_sql(
            f'ALTER TABLE "{s}"."{tb}" RENAME COLUMN "{old}" TO "{new}";',
            i18n.t("td_column_renamed"))

    def _do_drop_column(self):
        s, tb = self._current_schema, self._current_table
        col = self._drp_col.text().strip()
        if not s or not tb or not col:
            return
        if QMessageBox.question(self, "PostGIS Manager",
                                i18n.t("td_confirm_drop_column",
                                       col=col, table=tb)) != \
                QMessageBox.StandardButton.Yes:
            return
        self._run_sql(
            f'ALTER TABLE "{s}"."{tb}" DROP COLUMN "{col}";',
            i18n.t("td_column_dropped"))

    def _do_rename_table(self):
        s, tb = self._current_schema, self._current_table
        new = self._tbl_new_name.text().strip()
        if not s or not tb or not new:
            return
        self._run_sql(
            f'ALTER TABLE "{s}"."{tb}" RENAME TO "{new}";',
            i18n.t("td_table_renamed"))

    def _do_drop_table(self):
        s, tb = self._current_schema, self._current_table
        if not s or not tb:
            return
        if QMessageBox.question(self, "PostGIS Manager",
                                i18n.t("td_confirm_drop_table",
                                       schema=s, table=tb)) != \
                QMessageBox.StandardButton.Yes:
            return
        self._run_sql(
            f'DROP TABLE "{s}"."{tb}";',
            i18n.t("td_table_dropped"))

    # ── Index tab ─────────────────────────────────────────────────────────────

    def _build_index_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)

        sel = QHBoxLayout()
        self._idx_schema = QComboBox(); self._idx_schema.setEditable(True)
        self._idx_table  = QComboBox(); self._idx_table.setEditable(True)
        self._idx_schema.currentTextChanged.connect(self._load_idx_tables)
        load_btn = QPushButton(i18n.t("td_load"))
        load_btn.clicked.connect(self._load_indexes)
        sel.addWidget(QLabel(i18n.t("td_schema"))); sel.addWidget(self._idx_schema)
        sel.addWidget(QLabel(i18n.t("td_table")));  sel.addWidget(self._idx_table)
        sel.addWidget(load_btn)
        l.addLayout(sel)

        self._idx_list = QTableWidget(0, 4)
        self._idx_list.setHorizontalHeaderLabels(
            ["Index Name", "Definition", "Size", "Scans"])
        self._idx_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._idx_list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self._idx_list.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i in [0, 2, 3]:
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        l.addWidget(self._idx_list)

        create_box = QGroupBox(i18n.t("td_create_index"))
        ci_l = QFormLayout(create_box)
        self._idx_name   = QLineEdit()
        self._idx_col    = QLineEdit(); self._idx_col.setPlaceholderText("col1, col2")
        self._idx_method = QComboBox()
        self._idx_method.addItems(["GIST", "BTREE", "GIN", "BRIN", "HASH", "SP-GIST"])
        self._idx_unique = QCheckBox(i18n.t("td_unique"))
        ci_l.addRow(i18n.t("td_index_name"), self._idx_name)
        ci_l.addRow(i18n.t("td_index_cols"), self._idx_col)
        ci_l.addRow(i18n.t("td_index_method"), self._idx_method)
        ci_l.addRow("", self._idx_unique)
        l.addWidget(create_box)

        btn_row = QHBoxLayout()
        ci_btn  = QPushButton(f"＋ {i18n.t('td_create_index')}")
        ci_btn.clicked.connect(self._create_index)
        di_btn  = QPushButton(f"✖ {i18n.t('td_drop_index')}")
        di_btn.clicked.connect(self._drop_index)
        btn_row.addStretch(); btn_row.addWidget(ci_btn); btn_row.addWidget(di_btn)
        l.addLayout(btn_row)
        return w

    def _load_idx_tables(self, schema):
        if not self.db.is_connected() or not schema.strip():
            return
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = %s ORDER BY table_name
                """, (schema.strip(),))
                self._idx_table.clear()
                self._idx_table.addItems([r[0] for r in cur.fetchall()])
        except Exception:
            pass

    def _load_indexes(self):
        s = self._idx_schema.currentText().strip()
        tb = self._idx_table.currentText().strip()
        if not s or not tb or not self.db.is_connected():
            return
        self._current_schema = s; self._current_table = tb
        w = LoadIndexWorker(self.db, s, tb)
        w.done.connect(self._on_indexes_loaded)
        w.error.connect(lambda e: self._status.setText(f"Error: {e}"))
        w.start()

    def _on_indexes_loaded(self, rows: list):
        t = self._idx_list
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                item = QTableWidgetItem("" if val is None else str(val))
                if c == 0 and "_pkey" in str(val):
                    item.setForeground(QColor("#1B5E20"))
                t.setItem(r, c, item)

    def _create_index(self):
        s, tb = self._current_schema, self._current_table
        if not s or not tb:
            return
        cols   = self._idx_col.text().strip()
        method = self._idx_method.currentText()
        name   = self._idx_name.text().strip() or \
                 f"{tb}_{cols.replace(', ','_').replace(',','_')}_{method.lower()}_idx"
        unique = "UNIQUE " if self._idx_unique.isChecked() else ""
        sql = (f'CREATE {unique}INDEX "{name}" '
               f'ON "{s}"."{tb}" USING {method} ({cols});')
        self._run_sql(sql, i18n.t("td_index_created"))

    def _drop_index(self):
        row = self._idx_list.currentRow()
        if row < 0:
            return
        name = self._idx_list.item(row, 0).text() if self._idx_list.item(row, 0) else ""
        if not name:
            return
        if QMessageBox.question(self, "PostGIS Manager",
                                i18n.t("td_confirm_drop_index", index=name)) != \
                QMessageBox.StandardButton.Yes:
            return
        self._run_sql(f'DROP INDEX "{self._current_schema}"."{name}";',
                      i18n.t("td_index_dropped"))

    # ── Maintenance tab ───────────────────────────────────────────────────────

    def _build_maintain_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)

        sel = QHBoxLayout()
        self._mnt_schema = QComboBox(); self._mnt_schema.setEditable(True)
        self._mnt_table  = QComboBox(); self._mnt_table.setEditable(True)
        self._mnt_schema.currentTextChanged.connect(self._load_mnt_tables)
        sel.addWidget(QLabel(i18n.t("td_schema"))); sel.addWidget(self._mnt_schema)
        sel.addWidget(QLabel(i18n.t("td_table")));  sel.addWidget(self._mnt_table)
        l.addLayout(sel)

        btns = [
            (f"🔄 VACUUM ANALYZE", "VACUUM ANALYZE", i18n.t("td_vacuum_done")),
            (f"🔄 VACUUM FULL",    "VACUUM FULL",    i18n.t("td_vacuum_done")),
            (f"📊 ANALYZE",        "ANALYZE",        i18n.t("td_analyze_done")),
            (f"🔁 CLUSTER",        "CLUSTER",        i18n.t("td_cluster_done")),
            (f"🔢 REINDEX TABLE",  "REINDEX TABLE",  i18n.t("td_reindex_done")),
        ]
        for label, cmd, msg in btns:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, c=cmd, m=msg: self._run_maintenance(c, m))
            l.addWidget(btn)

        l.addStretch()
        self._mnt_log = QTextEdit()
        self._mnt_log.setReadOnly(True)
        self._mnt_log.setMaximumHeight(150)
        l.addWidget(self._mnt_log)
        return w

    def _load_mnt_tables(self, schema):
        if not self.db.is_connected() or not schema.strip():
            return
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = %s ORDER BY table_name
                """, (schema.strip(),))
                self._mnt_table.clear()
                self._mnt_table.addItems([r[0] for r in cur.fetchall()])
        except Exception:
            pass

    def _run_maintenance(self, cmd: str, msg: str):
        s  = self._mnt_schema.currentText().strip()
        tb = self._mnt_table.currentText().strip()
        if not s or not tb or not self.db.is_connected():
            return
        sql = f'{cmd} "{s}"."{tb}";'
        self._mnt_log.append(f"▶ {sql}")
        # Maintenance must run outside transaction
        try:
            old_iso = self.db.conn.isolation_level
            self.db.conn.set_isolation_level(0)
            with self.db.conn.cursor() as cur:
                cur.execute(sql)
            self.db.conn.set_isolation_level(old_iso)
            self._mnt_log.append(f"✔ {msg}")
            self._status.setText(msg)
        except Exception as e:
            self._mnt_log.append(f"✖ {e}")
            try:
                self.db.conn.rollback()
            except Exception:
                pass

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _run_sql(self, sql: str, success_msg: str):
        self._status.setText(i18n.t("status_running"))
        w = TableWorker(self.db, sql)
        w.done.connect(lambda _: self._on_done(success_msg))
        w.error.connect(lambda e: self._on_error(e))
        w.start(); self._worker = w

    def _on_done(self, msg: str):
        self._status.setText(f"✔ {msg}")
        self.layer_changed.emit()

    def _on_error(self, msg: str):
        self._status.setText(f"✖ {msg}")
        QMessageBox.critical(self, "PostGIS Manager", msg)

    def showEvent(self, event):
        super().showEvent(event)
        if self.db.is_connected():
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
            for combo in [self._cr_schema, self._alt_schema,
                          self._idx_schema, self._mnt_schema]:
                cur_text = combo.currentText()
                combo.clear()
                combo.addItems(schemas)
                if cur_text in schemas:
                    combo.setCurrentText(cur_text)
        except Exception:
            pass
