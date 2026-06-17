"""Layer Browser Panel — schema/table/view/raster tree with context menu."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLineEdit, QLabel, QMenu, QSizePolicy, QMessageBox,
    QApplication, QDialog, QTableWidget, QTableWidgetItem,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread, pyqtSlot
from PyQt6.QtGui import QColor, QBrush

from ...db.connection import DBManager
from ...utils import i18n

GEOM_COLORS = {
    "POINT":           "#4FC3F7",
    "MULTIPOINT":      "#81D4FA",
    "LINESTRING":      "#A5D6A7",
    "MULTILINESTRING": "#C8E6C9",
    "POLYGON":         "#FFB74D",
    "MULTIPOLYGON":    "#FFCC02",
    "GEOMETRY":        "#CE93D8",
}


class RefreshWorker(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager):
        super().__init__()
        self.db = db

    def run(self):
        try:
            result = {}
            for schema in self.db.get_schemas():
                result[schema] = {
                    "geom":    self.db.get_geometry_layers(schema),
                    "rasters": self.db.get_raster_layers(schema),
                    "views":   self.db.get_views(schema),
                    "tables":  self.db.get_all_tables(schema),
                }
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class LayerBrowserPanel(QWidget):
    layer_selected = pyqtSignal(str, str, str, int, str)
    load_in_qgis   = pyqtSignal(str, str, str, int)

    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._data: dict = {}
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QHBoxLayout()
        self._title = QLabel("📋  Browser")
        self._title.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(self._title)
        header.addStretch()
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.setToolTip("Refresh layers from database")
        self._refresh_btn.setFixedHeight(26)
        self._refresh_btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #888; "
            "border-radius: 3px; padding: 0 6px; }"
            "QPushButton:hover { background: rgba(128,128,128,0.2); }"
            "QPushButton:pressed { background: rgba(128,128,128,0.35); }"
        )
        self._refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self._refresh_btn)
        layout.addLayout(header)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText(i18n.t("browser_filter"))
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._context_menu)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._tree)

        self._import_schema_btn = QPushButton(i18n.t("browser_schema_import"))
        self._import_schema_btn.setEnabled(False)
        self._import_schema_btn.clicked.connect(
            lambda: self._import_schema_to_qgis())
        layout.addWidget(self._import_schema_btn)

        self.setMinimumWidth(240)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def refresh(self):
        if not self.db.is_connected():
            self._tree.clear()
            QTreeWidgetItem(self._tree, [i18n.t("browser_no_connection")])
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("⏳")
        self._worker = RefreshWorker(self.db)
        self._worker.done.connect(self._populate, Qt.ConnectionType.QueuedConnection)
        self._worker.error.connect(self._on_refresh_error, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._on_worker_done)
        self._worker.start()

    def _on_worker_done(self):
        self._worker = None

    def clear(self):
        self._tree.clear()
        self._data.clear()

    @pyqtSlot(dict)
    def _populate(self, data: dict):
        self._data = data
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("↻")
        self._tree.clear()
        flt = self._filter.text().lower()

        for schema, contents in data.items():
            schema_item = QTreeWidgetItem([f"📂  {schema}"])
            schema_item.setData(0, Qt.ItemDataRole.UserRole,
                                {"type": "schema", "schema": schema})
            f = schema_item.font(0)
            f.setBold(True)
            schema_item.setFont(0, f)

            geom_layers = contents.get("geom", [])
            if geom_layers:
                vec_group = QTreeWidgetItem(schema_item, ["🗺  Layers"])
                vec_group.setData(0, Qt.ItemDataRole.UserRole, {"type": "group"})
                for name, geom_col, geom_type, srid in geom_layers:
                    if flt and flt not in name.lower():
                        continue
                    gt = (geom_type or "GEOMETRY").upper().split("(")[0].split(" ")[0]
                    color = GEOM_COLORS.get(gt, "#CCCCCC")
                    item = QTreeWidgetItem(
                        vec_group,
                        [f"⬡  {name}  [{gt}]  EPSG:{srid}"])
                    item.setForeground(0, QBrush(QColor(color)))
                    item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "layer", "schema": schema, "table": name,
                        "geom_col": geom_col, "srid": srid, "geom_type": gt,
                    })
                vec_group.setExpanded(True)

            rasters = contents.get("rasters", [])
            if rasters:
                rast_group = QTreeWidgetItem(schema_item, ["🖼  Rasters"])
                rast_group.setData(0, Qt.ItemDataRole.UserRole, {"type": "group"})
                for name, rcol, srid, sx, sy in rasters:
                    if flt and flt not in name.lower():
                        continue
                    item = QTreeWidgetItem(rast_group, [f"🏔  {name}  EPSG:{srid}"])
                    item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "raster", "schema": schema, "table": name,
                        "rast_col": rcol, "srid": srid,
                    })
                rast_group.setExpanded(True)

            views = contents.get("views", [])
            if views:
                view_group = QTreeWidgetItem(schema_item, ["👁  Views"])
                view_group.setData(0, Qt.ItemDataRole.UserRole, {"type": "group"})
                for vname in views:
                    if flt and flt not in vname.lower():
                        continue
                    item = QTreeWidgetItem(view_group, [f"  {vname}"])
                    item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "view", "schema": schema, "table": vname,
                    })

            # All tables (including those without PostGIS geometry)
            geom_names = {row[0] for row in geom_layers}
            all_tables = contents.get("tables", [])
            plain_tables = [r for r in all_tables if r[0] not in geom_names]
            if plain_tables:
                tbl_group = QTreeWidgetItem(schema_item, ["🗃  Tables"])
                tbl_group.setData(0, Qt.ItemDataRole.UserRole, {"type": "group"})
                for tname, col_count, row_est in plain_tables:
                    if flt and flt not in tname.lower():
                        continue
                    item = QTreeWidgetItem(
                        tbl_group,
                        [f"▦  {tname}  ({col_count} cols, ~{row_est} rows)"])
                    item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "table", "schema": schema, "table": tname,
                    })

            schema_item.setExpanded(True)
            self._tree.addTopLevelItem(schema_item)

    def _on_refresh_error(self, error: str):
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("↻")
        QMessageBox.critical(self, "Error", error)

    def _apply_filter(self, _):
        self._populate(self._data)

    def _on_item_clicked(self, item: QTreeWidgetItem, col: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") != "layer":
            return
        self.layer_selected.emit(
            data["schema"], data["table"],
            data.get("geom_col", "geom"),
            data.get("srid", 4326),
            data.get("geom_type", "GEOMETRY"),
        )

    def _on_item_double_clicked(self, item: QTreeWidgetItem, col: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data.get("type") not in ("layer", "table", "view"):
            return
        schema = data.get("schema", "")
        table  = data.get("table", "")
        if not schema or not table:
            return
        self._open_attr_table(schema, table)

    def _open_attr_table(self, schema: str, table: str):
        """Open a simple floating attribute table for the given table."""
        LIMIT = 2000
        try:
            with self.db.conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s "
                    "ORDER BY ordinal_position LIMIT 60",
                    (schema, table))
                cols = [r[0] for r in cur.fetchall()]

                if not cols:
                    QMessageBox.information(
                        self, "Attribute Table",
                        f"No columns found for {schema}.{table}")
                    return

                # Exclude geometry/raster columns
                cols = [c for c in cols
                        if c not in ("geom", "geometry", "wkb_geometry",
                                     "rast", "the_geom", "shape")]

                col_sql = ", ".join(f'"{c}"' for c in cols)
                cur.execute(
                    f'SELECT {col_sql} FROM "{schema}"."{table}" LIMIT {LIMIT}')
                rows = cur.fetchall()
        except Exception as e:
            self.db.conn.rollback()
            QMessageBox.critical(self, "Error", str(e))
            return

        dlg = QDialog(self.window())
        dlg.setWindowTitle(f"Attributes — {schema}.{table}  ({len(rows)} rows)")
        dlg.setMinimumSize(700, 400)
        dlg.resize(900, 500)
        dlg.setWindowFlag(Qt.WindowType.Window)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(4, 4, 4, 4)

        info = QLabel(
            f"{schema}.{table}   {len(rows)} row{'s' if len(rows) != 1 else ''}"
            + (f"  (limit {LIMIT})" if len(rows) == LIMIT else ""))
        info.setStyleSheet("font-size:11px;color:#888;padding:2px;")
        layout.addWidget(info)

        tbl = QTableWidget(len(rows), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setAlternatingRowColors(True)
        tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.verticalHeader().setDefaultSectionSize(22)

        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                text = "" if val is None else str(val)
                tbl.setItem(r, c, QTableWidgetItem(text))

        # Right-click copy
        tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def _ctx(pos):
            item = tbl.itemAt(pos)
            menu = QMenu(tbl)
            act_cell = menu.addAction("📋 Copy cell")
            act_row  = menu.addAction("📋 Copy row")
            act_all  = menu.addAction("📋 Copy all")
            chosen = menu.exec(tbl.viewport().mapToGlobal(pos))
            if chosen == act_cell and item:
                QApplication.clipboard().setText(item.text())
            elif chosen == act_row:
                ri = tbl.currentRow()
                if ri >= 0:
                    vals = [(tbl.item(ri, ci).text()
                             if tbl.item(ri, ci) else "")
                            for ci in range(tbl.columnCount())]
                    QApplication.clipboard().setText(
                        "\t".join(cols) + "\n" + "\t".join(vals))
            elif chosen == act_all:
                lines = ["\t".join(cols)]
                for ri in range(tbl.rowCount()):
                    lines.append("\t".join(
                        (tbl.item(ri, ci).text()
                         if tbl.item(ri, ci) else "")
                        for ci in range(tbl.columnCount())))
                QApplication.clipboard().setText("\n".join(lines))

        tbl.customContextMenuRequested.connect(_ctx)
        layout.addWidget(tbl)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(26)
        close_btn.clicked.connect(dlg.close)
        layout.addWidget(close_btn)

        dlg.show()

    def _context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        menu = QMenu(self)
        if data.get("type") == "layer":
            act_attr   = menu.addAction(i18n.t("browser_open_attr_table"))
            act_import = menu.addAction(i18n.t("browser_import_to"))
            act_qgis   = menu.addAction(i18n.t("browser_load_in_qgis"))
            menu.addSeparator()
            act_copy   = menu.addAction(i18n.t("browser_copy_name"))
            menu.addSeparator()
            act_del    = menu.addAction(i18n.t("browser_delete_all"))

            chosen = menu.exec(self._tree.viewport().mapToGlobal(pos))
            if chosen == act_attr:
                self._on_item_double_clicked(item, 0)
            elif chosen == act_qgis:
                self.load_in_qgis.emit(
                    data["schema"], data["table"],
                    data.get("geom_col", "geom"), data.get("srid", 4326))
            elif chosen == act_copy:
                QApplication.clipboard().setText(
                    f'{data["schema"]}.{data["table"]}')
            elif chosen == act_import:
                self._open_import(data)
            elif chosen == act_del:
                self._delete_all_features(data)

        elif data.get("type") == "schema":
            act_sch = menu.addAction(i18n.t("browser_schema_import"))
            if menu.exec(self._tree.viewport().mapToGlobal(pos)) == act_sch:
                self._import_schema_to_qgis(data["schema"])

    def _open_import(self, data: dict):
        from ..dialogs.import_dialog import ImportDialog
        dlg = ImportDialog(self.db, data["schema"], data["table"], self.parent())
        dlg.exec()

    def _delete_all_features(self, data: dict):
        schema, table = data["schema"], data["table"]
        msg = i18n.t("confirm_delete_all", schema=schema, table=table)
        reply = QMessageBox.warning(self, "Warning", msg,
                                    QMessageBox.StandardButton.Yes |
                                    QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.db.delete_all_rows(schema, table)
                if self.parent() and hasattr(self.parent(), "log"):
                    self.parent().log(
                        f"All features deleted from {schema}.{table}", "warn")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _import_schema_to_qgis(self, schema: str = None):
        parent = self.parent()
        if not parent or not getattr(parent, "iface", None):
            QMessageBox.information(
                self, "Info", "QGIS interface not available in standalone mode.")
            return
        try:
            from qgis.core import QgsVectorLayer, QgsProject
            loaded = 0
            for name, geom_col, geom_type, srid in \
                    self._data.get(schema, {}).get("geom", []):
                uri = (f"dbname='{self.db.params['dbname']}' "
                       f"host={self.db.params['host']} "
                       f"port={self.db.params['port']} "
                       f"user='{self.db.params['user']}' "
                       f"key='ctid' srid={srid} "
                       f"table=\"{schema}\".\"{name}\" ({geom_col}) sql=")
                lyr = QgsVectorLayer(uri, f"{schema}.{name}", "postgres")
                if lyr.isValid():
                    QgsProject.instance().addMapLayer(lyr)
                    loaded += 1
            parent.log(
                f"Loaded {loaded} layers from schema '{schema}'", "success")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
