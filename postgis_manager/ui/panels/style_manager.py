"""DB Style Manager panel (db-style-manager pattern)."""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QListWidget, QCheckBox, QMessageBox,
    QFileDialog, QListWidgetItem,
)
from PyQt5.QtCore import Qt

from ...db.connection import DBManager
from ...utils import i18n


class StyleManagerPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._schema = ""
        self._table = ""
        self._geom_col = "geom"
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel(f"🎨  {i18n.t('style_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        self._layer_label = QLabel("(select a layer in browser)")
        layout.addWidget(self._layer_label)

        layout.addWidget(QLabel(i18n.t("style_styles")))
        self._styles_list = QListWidget()
        self._styles_list.itemDoubleClicked.connect(self._load_selected_style)
        layout.addWidget(self._styles_list)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self._load_styles)
        btn_row.addWidget(refresh_btn)
        del_btn = QPushButton(i18n.t("style_delete"))
        del_btn.setProperty("class", "danger")
        del_btn.clicked.connect(self._delete_style)
        btn_row.addWidget(del_btn)
        layout.addLayout(btn_row)

        form = QFormLayout()
        self._style_name = QLineEdit()
        self._style_name.setPlaceholderText("My style")
        form.addRow(i18n.t("style_name"), self._style_name)
        self._is_default = QCheckBox(i18n.t("style_default"))
        form.addRow("", self._is_default)
        layout.addLayout(form)

        save_row = QHBoxLayout()
        self._save_from_qgis_btn = QPushButton(i18n.t("style_save"))
        self._save_from_qgis_btn.clicked.connect(self._save_style_from_qgis)
        save_row.addWidget(self._save_from_qgis_btn)
        self._load_btn = QPushButton(i18n.t("style_load"))
        self._load_btn.clicked.connect(self._load_selected_style)
        save_row.addWidget(self._load_btn)
        layout.addLayout(save_row)
        layout.addStretch()

    def set_active_layer(self, schema: str, table: str, geom_col: str):
        self._schema = schema
        self._table = table
        self._geom_col = geom_col
        self._layer_label.setText(f"Layer: {schema}.{table}")
        self._load_styles()

    def _load_styles(self):
        if not self._schema or not self.db.is_connected():
            return
        try:
            styles = self.db.get_styles(self._schema, self._table)
            self._styles_list.clear()
            for s in styles:
                label = s["stylename"]
                if s.get("useasdefault"):
                    label += "  ★ default"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, s["id"])
                self._styles_list.addItem(item)
        except Exception as e:
            pass

    def _save_style_from_qgis(self):
        parent = self.parent()
        name = self._style_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Enter a style name.")
            return
        if not self._schema:
            QMessageBox.warning(self, "Error", "Select a layer first.")
            return
        try:
            if parent and getattr(parent, "iface", None):
                from qgis.core import QgsProject
                layers = QgsProject.instance().mapLayersByName(self._table)
                if not layers:
                    QMessageBox.warning(self, "Error", f"Layer '{self._table}' not in QGIS project.")
                    return
                layer = layers[0]
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".qml", delete=False) as f:
                    tmp = f.name
                layer.saveNamedStyle(tmp)
                with open(tmp, encoding="utf-8") as f:
                    qml = f.read()
            else:
                path, _ = QFileDialog.getOpenFileName(
                    self, "Select QML file", filter="QML (*.qml)")
                if not path:
                    return
                with open(path, encoding="utf-8") as f:
                    qml = f.read()

            self.db.save_style(self._schema, self._table, self._geom_col,
                               name, qml, self._is_default.isChecked())
            QMessageBox.information(self, "Success", f"Style '{name}' saved to DB.")
            self._load_styles()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _load_selected_style(self):
        item = self._styles_list.currentItem()
        if not item:
            return
        style_id = item.data(Qt.UserRole)
        parent = self.parent()
        if not parent or not getattr(parent, "iface", None):
            QMessageBox.information(self, "Info", "Style loading requires QGIS.")
            return
        try:
            qml = self.db.load_style(style_id)
            if not qml:
                return
            from qgis.core import QgsProject
            layers = QgsProject.instance().mapLayersByName(self._table)
            if not layers:
                return
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".qml", delete=False,
                                              mode="w", encoding="utf-8") as f:
                f.write(qml)
                tmp = f.name
            layers[0].loadNamedStyle(tmp)
            layers[0].triggerRepaint()
            parent.log(f"Style applied to '{self._table}'", "success")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_style(self):
        item = self._styles_list.currentItem()
        if not item:
            return
        style_id = item.data(Qt.UserRole)
        if QMessageBox.question(self, "Confirm", "Delete this style?") == QMessageBox.Yes:
            try:
                self.db.delete_style(style_id)
                self._load_styles()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
