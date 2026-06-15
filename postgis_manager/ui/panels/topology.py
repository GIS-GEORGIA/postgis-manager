"""Topology panel — PyQt6."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QDoubleSpinBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QMessageBox, QComboBox, QHeaderView,
)
from ...db.connection import DBManager
from ...utils import i18n


class TopologyPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db; self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel(f"🕸  {i18n.t('topology_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        topo_group = QGroupBox("Existing Topologies")
        tg = QVBoxLayout(topo_group)
        QPushButton("↻ Refresh", clicked=self._load_topologies) \
            .__class__  # workaround — use proper addWidget
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self._load_topologies)
        tg.addWidget(refresh_btn)
        self._topo_table = QTableWidget()
        self._topo_table.setColumnCount(3)
        self._topo_table.setHorizontalHeaderLabels(["Name", "SRID", "Precision"])
        self._topo_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._topo_table.setAlternatingRowColors(True)
        tg.addWidget(self._topo_table)
        layout.addWidget(topo_group)

        create_group = QGroupBox(i18n.t("topology_create"))
        cf = QFormLayout(create_group)
        self._topo_name = QLineEdit(); self._topo_name.setPlaceholderText("my_topology")
        cf.addRow("Name:", self._topo_name)
        self._topo_srid = QSpinBox(); self._topo_srid.setMaximum(999999); self._topo_srid.setValue(4326)
        cf.addRow("SRID:", self._topo_srid)
        self._topo_tol = QDoubleSpinBox(); self._topo_tol.setDecimals(8)
        cf.addRow(i18n.t("topology_tolerance"), self._topo_tol)
        create_btn = QPushButton(f"➕ {i18n.t('topology_create')}")
        create_btn.clicked.connect(self._create_topology)
        cf.addRow("", create_btn)
        layout.addWidget(create_group)

        val_group = QGroupBox(i18n.t("topology_validate"))
        vl = QVBoxLayout(val_group)
        val_row = QHBoxLayout()
        self._val_combo = QComboBox()
        val_row.addWidget(QLabel("Topology:"))
        val_row.addWidget(self._val_combo)
        val_btn = QPushButton(i18n.t("topology_validate"))
        val_btn.clicked.connect(self._validate)
        val_row.addWidget(val_btn)
        vl.addLayout(val_row)
        self._errors_table = QTableWidget()
        self._errors_table.setColumnCount(3)
        self._errors_table.setHorizontalHeaderLabels(["Error", "ID1", "ID2"])
        self._errors_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._errors_table.setAlternatingRowColors(True)
        vl.addWidget(self._errors_table)
        layout.addWidget(val_group)
        layout.addStretch()

    def _load_topologies(self):
        if not self.db.is_connected(): return
        try:
            topos = self.db.list_topologies()
            self._topo_table.setRowCount(len(topos))
            self._val_combo.clear()
            for i, t in enumerate(topos):
                self._topo_table.setItem(i, 0, QTableWidgetItem(t["name"]))
                self._topo_table.setItem(i, 1, QTableWidgetItem(str(t["srid"])))
                self._topo_table.setItem(i, 2, QTableWidgetItem(str(t["precision"])))
                self._val_combo.addItem(t["name"])
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _create_topology(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected")); return
        name = self._topo_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Name required."); return
        try:
            self.db.create_topology(name, self._topo_srid.value(), self._topo_tol.value())
            QMessageBox.information(self, "OK", f"Topology '{name}' created.")
            self._load_topologies()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _validate(self):
        if not self.db.is_connected(): return
        name = self._val_combo.currentText()
        if not name: return
        try:
            errors = self.db.validate_topology(name)
            self._errors_table.setRowCount(len(errors))
            for i, e in enumerate(errors):
                self._errors_table.setItem(i, 0, QTableWidgetItem(e["error"]))
                self._errors_table.setItem(i, 1, QTableWidgetItem(str(e["id1"])))
                self._errors_table.setItem(i, 2, QTableWidgetItem(str(e["id2"])))
            if not errors:
                QMessageBox.information(self, "OK", f"Topology '{name}' is valid!")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
