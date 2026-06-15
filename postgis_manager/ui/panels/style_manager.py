"""DB Style Manager panel — PyQt6."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPushButton, QListWidget, QCheckBox, QMessageBox,
    QFileDialog, QListWidgetItem,
)
from PyQt6.QtCore import Qt
from ...db.connection import DBManager
from ...utils import i18n


class StyleManagerPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db; self._schema = self._table = self._geom_col = ""; self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel(f"🎨  {i18n.t('style_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)
        self._layer_lbl = QLabel("(select a layer in browser)")
        layout.addWidget(self._layer_lbl)
        layout.addWidget(QLabel(i18n.t("style_styles")))
        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._load_style)
        layout.addWidget(self._list)
        btn_row = QHBoxLayout()
        ref = QPushButton("↻ Refresh"); ref.clicked.connect(self._load_styles)
        btn_row.addWidget(ref)
        del_btn = QPushButton(i18n.t("style_delete"))
        del_btn.clicked.connect(self._delete_style)
        btn_row.addWidget(del_btn)
        layout.addLayout(btn_row)
        form = QFormLayout()
        self._name = QLineEdit(); self._name.setPlaceholderText("My style")
        form.addRow(i18n.t("style_name"), self._name)
        self._default = QCheckBox(i18n.t("style_default"))
        form.addRow("", self._default)
        layout.addLayout(form)
        save_row = QHBoxLayout()
        save_btn = QPushButton(i18n.t("style_save")); save_btn.clicked.connect(self._save_style)
        save_row.addWidget(save_btn)
        load_btn = QPushButton(i18n.t("style_load")); load_btn.clicked.connect(self._load_style)
        save_row.addWidget(load_btn)
        layout.addLayout(save_row)
        layout.addStretch()

    def set_active_layer(self, schema, table, geom_col):
        self._schema = schema; self._table = table; self._geom_col = geom_col
        self._layer_lbl.setText(f"Layer: {schema}.{table}")
        self._load_styles()

    def _load_styles(self):
        if not self._schema or not self.db.is_connected(): return
        try:
            self._list.clear()
            for s in self.db.get_styles(self._schema, self._table):
                label = s["stylename"] + ("  ★" if s.get("useasdefault") else "")
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, s["id"])
                self._list.addItem(item)
        except Exception: pass

    def _save_style(self):
        name = self._name.text().strip()
        if not name: QMessageBox.warning(self, "Error", "Enter a style name."); return
        if not self._schema: QMessageBox.warning(self, "Error", "Select a layer first."); return
        parent = self.parent()
        try:
            if parent and getattr(parent, "iface", None):
                from qgis.core import QgsProject
                layers = QgsProject.instance().mapLayersByName(self._table)
                if not layers: return
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".qml", delete=False) as f:
                    tmp = f.name
                layers[0].saveNamedStyle(tmp)
                with open(tmp, encoding="utf-8") as f: qml = f.read()
            else:
                path, _ = QFileDialog.getOpenFileName(self, "Select QML", filter="QML (*.qml)")
                if not path: return
                with open(path, encoding="utf-8") as f: qml = f.read()
            self.db.save_style(self._schema, self._table, self._geom_col,
                               name, qml, self._default.isChecked())
            QMessageBox.information(self, "OK", f"Style '{name}' saved.")
            self._load_styles()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _load_style(self):
        item = self._list.currentItem()
        if not item: return
        parent = self.parent()
        if not parent or not getattr(parent, "iface", None):
            QMessageBox.information(self, "Info", "Style loading requires QGIS."); return
        try:
            qml = self.db.load_style(item.data(Qt.ItemDataRole.UserRole))
            if not qml: return
            from qgis.core import QgsProject
            layers = QgsProject.instance().mapLayersByName(self._table)
            if not layers: return
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".qml", delete=False, mode="w", encoding="utf-8") as f:
                f.write(qml); tmp = f.name
            layers[0].loadNamedStyle(tmp); layers[0].triggerRepaint()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_style(self):
        item = self._list.currentItem()
        if not item: return
        if QMessageBox.question(self, "Confirm", "Delete this style?") \
                == QMessageBox.StandardButton.Yes:
            try:
                self.db.delete_style(item.data(Qt.ItemDataRole.UserRole))
                self._load_styles()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
