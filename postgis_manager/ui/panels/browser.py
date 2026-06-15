"""Layer Browser Panel — schema/table/view/raster tree with context menu."""

from __future__ import annotations
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLineEdit, QLabel, QMenu, QAction, QSizePolicy,
    QMessageBox, QApplication,
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, pyqtSlot
from PyQt5.QtGui import QIcon, QColor, QBrush, QFont

from ...db.connection import DBManager
from ...utils import i18n, theme

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
                geom_layers = self.db.get_geometry_layers(schema)
                rasters = self.db.get_raster_layers(schema)
                views = self.db.get_views(schema)
                all_tables = self.db.get_all_tables(schema)
                result[schema] = {
                    "geom": geom_layers,
                    "rasters": rasters,
                    "views": views,
                    "tables": all_tables,
                }
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class LayerBrowserPanel(QWidget):
    layer_selected = pyqtSignal(str, str, str, int, str)   # schema,table,geom,srid,type
    load_in_qgis = pyqtSignal(str, str, str, int)          # schema,table,geom,srid

    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._data: dict = {}
        self._worker: RefreshWorker | None = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header
        header = QHBoxLayout()
        self._title = QLabel("📋  Browser")
        self._title.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(self._title)
        header.addStretch()
        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setFixedWidth(30)
        self._refresh_btn.setToolTip(i18n.t("browser_refresh"))
        self._refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self._refresh_btn)
        layout.addLayout(header)

        # Filter
        self._filter = QLineEdit()
        self._filter.setPlaceholderText(i18n.t("browser_filter"))
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        # Tree
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._context_menu)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._tree)

        # Schema import button (qgis_pg_schema_importer pattern)
        self._import_schema_btn = QPushButton(i18n.t("browser_schema_import"))
        self._import_schema_btn.setEnabled(False)
        self._import_schema_btn.clicked.connect(self._import_schema_to_qgis)
        layout.addWidget(self._import_schema_btn)

        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def refresh(self):
        if not self.db.is_connected():
            self._tree.clear()
            root = QTreeWidgetItem(self._tree, [i18n.t("browser_no_connection")])
            return
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.setText("⏳")
        self._worker = RefreshWorker(self.db)
        self._worker.done.connect(self._populate)
        self._worker.error.connect(self._on_refresh_error)
        self._worker.start()

    def clear(self):
        self._tree.clear()
        self._data.clear()

    @pyqtSlot(dict)
    def _populate(self, data: dict):
        self._data = data
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("↻")
        self._tree.clear()
        filter_text = self._filter.text().lower()

        for schema, contents in data.items():
            schema_item = QTreeWidgetItem([f"📂  {schema}"])
            schema_item.setData(0, Qt.UserRole, {"type": "schema", "schema": schema})
            f = schema_item.font(0)
            f.setBold(True)
            schema_item.setFont(0, f)

            # Vector layers
            geom_layers = contents.get("geom", [])
            if geom_layers:
                vec_group = QTreeWidgetItem(schema_item, ["🗺  Layers"])
                vec_group.setData(0, Qt.UserRole, {"type": "group"})
                for name, geom_col, geom_type, srid in geom_layers:
                    if filter_text and filter_text not in name.lower():
                        continue
                    gt = (geom_type or "GEOMETRY").upper().split("(")[0].split(" ")[0]
                    color = GEOM_COLORS.get(gt, "#CCCCCC")
                    label = f"⬡  {name}  [{gt}]  EPSG:{srid}"
                    item = QTreeWidgetItem(vec_group, [label])
                    item.setForeground(0, QBrush(QColor(color)))
                    item.setData(0, Qt.UserRole, {
                        "type": "layer", "schema": schema, "table": name,
                        "geom_col": geom_col, "srid": srid, "geom_type": gt,
                    })
                vec_group.setExpanded(True)

            # Rasters
            rasters = contents.get("rasters", [])
            if rasters:
                rast_group = QTreeWidgetItem(schema_item, ["🖼  Rasters"])
                rast_group.setData(0, Qt.UserRole, {"type": "group"})
                for name, rcol, srid, sx, sy in rasters:
                    if filter_text and filter_text not in name.lower():
                        continue
                    label = f"🏔  {name}  EPSG:{srid}"
                    item = QTreeWidgetItem(rast_group, [label])
                    item.setData(0, Qt.UserRole, {
                        "type": "raster", "schema": schema, "table": name,
                        "rast_col": rcol, "srid": srid,
                    })
                rast_group.setExpanded(True)

            # Views
            views = contents.get("views", [])
            if views:
                view_group = QTreeWidgetItem(schema_item, ["👁  Views"])
                view_group.setData(0, Qt.UserRole, {"type": "group"})
                for vname in views:
                    if filter_text and filter_text not in vname.lower():
                        continue
                    item = QTreeWidgetItem(view_group, [f"  {vname}"])
                    item.setData(0, Qt.UserRole, {
                        "type": "view", "schema": schema, "table": vname,
                    })

            schema_item.setExpanded(True)
            self._tree.addTopLevelItem(schema_item)

    def _on_refresh_error(self, error: str):
        self._refresh_btn.setEnabled(True)
        self._refresh_btn.setText("↻")
        QMessageBox.critical(self, "Error", error)

    def _apply_filter(self, text: str):
        self._populate(self._data)

    def _on_item_clicked(self, item: QTreeWidgetItem, col: int):
        data = item.data(0, Qt.UserRole)
        if not data or data.get("type") != "layer":
            return
        self._import_schema_btn.setEnabled(data.get("type") == "schema")
        self.layer_selected.emit(
            data["schema"], data["table"],
            data.get("geom_col", "geom"),
            data.get("srid", 4326),
            data.get("geom_type", "GEOMETRY"),
        )

    def _on_item_double_clicked(self, item: QTreeWidgetItem, col: int):
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        if data.get("type") == "layer":
            parent = self.parent()
            if parent and hasattr(parent, "_tabs"):
                # Open attribute table
                from ..attribute_table import AttributeTableDialog
                dlg = AttributeTableDialog(
                    self.db, data["schema"], data["table"], parent
                )
                dlg.show()

    def _context_menu(self, pos):
        item = self._tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        menu = QMenu(self)
        if data.get("type") == "layer":
            act_attr = menu.addAction(i18n.t("browser_open_attr_table"))
            act_import = menu.addAction(i18n.t("browser_import_to"))
            act_qgis = menu.addAction(i18n.t("browser_load_in_qgis"))
            menu.addSeparator()
            act_info = menu.addAction(i18n.t("browser_layer_info"))
            act_copy = menu.addAction(i18n.t("browser_copy_name"))
            menu.addSeparator()
            act_del_all = menu.addAction(i18n.t("browser_delete_all"))
            act_del_all.setIcon(QIcon())

            chosen = menu.exec_(self._tree.viewport().mapToGlobal(pos))
            if chosen == act_attr:
                self._open_attr_table(data)
            elif chosen == act_qgis:
                self.load_in_qgis.emit(
                    data["schema"], data["table"],
                    data.get("geom_col", "geom"), data.get("srid", 4326)
                )
            elif chosen == act_copy:
                QApplication.clipboard().setText(
                    f'{data["schema"]}.{data["table"]}')
            elif chosen == act_import:
                self._open_import(data)
            elif chosen == act_del_all:
                self._delete_all_features(data)

        elif data.get("type") == "schema":
            act_import_schema = menu.addAction(i18n.t("browser_schema_import"))
            chosen = menu.exec_(self._tree.viewport().mapToGlobal(pos))
            if chosen == act_import_schema:
                self._import_schema_to_qgis(data["schema"])

    def _open_attr_table(self, data: dict):
        parent = self.parent()
        from ..attribute_table import AttributeTableDialog
        dlg = AttributeTableDialog(
            self.db, data["schema"], data["table"], parent
        )
        dlg.show()

    def _open_import(self, data: dict):
        from ..dialogs.import_dialog import ImportDialog
        parent = self.parent()
        dlg = ImportDialog(self.db, data["schema"], data["table"], parent)
        dlg.exec_()

    def _delete_all_features(self, data: dict):
        schema, table = data["schema"], data["table"]
        msg = i18n.t("confirm_delete_all", schema=schema, table=table)
        reply = QMessageBox.warning(self, "Warning", msg,
                                    QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                self.db.delete_all_rows(schema, table)
                if self.parent() and hasattr(self.parent(), "log"):
                    self.parent().log(f"All features deleted from {schema}.{table}", "warn")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _import_schema_to_qgis(self, schema: str = None):
        """Import all layers from a schema into QGIS (qgis_pg_schema_importer pattern)."""
        parent = self.parent()
        if not parent or not getattr(parent, "iface", None):
            QMessageBox.information(self, "Info",
                                    "QGIS interface not available in standalone mode.")
            return
        # Implementation via QGIS layer loading
        try:
            from qgis.core import QgsVectorLayer, QgsProject
            iface = parent.iface
            loaded = 0
            for name, geom_col, geom_type, srid in self._data.get(schema, {}).get("geom", []):
                uri = (f"dbname='{self.db.params['dbname']}' "
                       f"host={self.db.params['host']} port={self.db.params['port']} "
                       f"user='{self.db.params['user']}' key='ctid' srid={srid} "
                       f"table=\"{schema}\".\"{name}\" ({geom_col}) sql=")
                lyr = QgsVectorLayer(uri, f"{schema}.{name}", "postgres")
                if lyr.isValid():
                    QgsProject.instance().addMapLayer(lyr)
                    loaded += 1
            parent.log(f"Loaded {loaded} layers from schema '{schema}' into QGIS", "success")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
