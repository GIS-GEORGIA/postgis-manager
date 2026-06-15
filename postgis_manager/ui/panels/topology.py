"""Topology panel — create, validate, and inspect PostGIS Topology (qgis_pgis_topoedit pattern)."""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QDoubleSpinBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QGroupBox, QMessageBox,
    QComboBox, QHeaderView,
)
from PyQt5.QtCore import QThread, pyqtSignal

from ...db.connection import DBManager
from ...utils import i18n


class TopologyPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel(f"🕸  {i18n.t('topology_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        # ── Existing topologies ──
        topo_group = QGroupBox("Existing Topologies")
        topo_layout = QVBoxLayout(topo_group)

        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self._load_topologies)
        topo_layout.addWidget(refresh_btn)

        self._topo_table = QTableWidget()
        self._topo_table.setColumnCount(4)
        self._topo_table.setHorizontalHeaderLabels(["Name", "SRID", "Precision", "Actions"])
        self._topo_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._topo_table.setAlternatingRowColors(True)
        topo_layout.addWidget(self._topo_table)
        layout.addWidget(topo_group)

        # ── Create Topology ──
        create_group = QGroupBox(i18n.t("topology_create"))
        create_layout = QFormLayout(create_group)

        self._topo_name = QLineEdit()
        self._topo_name.setPlaceholderText("my_topology")
        create_layout.addRow(i18n.t("topology_schema"), self._topo_name)

        self._topo_srid = QSpinBox()
        self._topo_srid.setMaximum(999999)
        self._topo_srid.setValue(4326)
        create_layout.addRow("SRID:", self._topo_srid)

        self._topo_tolerance = QDoubleSpinBox()
        self._topo_tolerance.setDecimals(8)
        self._topo_tolerance.setValue(0.0)
        create_layout.addRow(i18n.t("topology_tolerance"), self._topo_tolerance)

        create_btn = QPushButton(f"➕ {i18n.t('topology_create')}")
        create_btn.clicked.connect(self._create_topology)
        create_layout.addRow("", create_btn)
        layout.addWidget(create_group)

        # ── Validate ──
        validate_group = QGroupBox(i18n.t("topology_validate"))
        val_layout = QVBoxLayout(validate_group)

        val_row = QHBoxLayout()
        self._val_topo_combo = QComboBox()
        val_row.addWidget(QLabel("Topology:"))
        val_row.addWidget(self._val_topo_combo)
        val_btn = QPushButton(i18n.t("topology_validate"))
        val_btn.clicked.connect(self._validate)
        val_row.addWidget(val_btn)
        val_layout.addLayout(val_row)

        self._errors_table = QTableWidget()
        self._errors_table.setColumnCount(3)
        self._errors_table.setHorizontalHeaderLabels(["Error", "ID1", "ID2"])
        self._errors_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._errors_table.setAlternatingRowColors(True)
        val_layout.addWidget(self._errors_table)
        layout.addWidget(validate_group)

        layout.addStretch()

    def _load_topologies(self):
        if not self.db.is_connected():
            return
        try:
            topos = self.db.list_topologies()
            self._topo_table.setRowCount(len(topos))
            self._val_topo_combo.clear()
            for i, t in enumerate(topos):
                self._topo_table.setItem(i, 0, QTableWidgetItem(t["name"]))
                self._topo_table.setItem(i, 1, QTableWidgetItem(str(t["srid"])))
                self._topo_table.setItem(i, 2, QTableWidgetItem(str(t["precision"])))
                self._val_topo_combo.addItem(t["name"])
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _create_topology(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return
        name = self._topo_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Topology name required.")
            return
        try:
            self.db.create_topology(name, self._topo_srid.value(),
                                    self._topo_tolerance.value())
            QMessageBox.information(self, "Success", f"Topology '{name}' created.")
            self._load_topologies()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _validate(self):
        if not self.db.is_connected():
            return
        name = self._val_topo_combo.currentText()
        if not name:
            return
        try:
            errors = self.db.validate_topology(name)
            self._errors_table.setRowCount(len(errors))
            for i, err in enumerate(errors):
                self._errors_table.setItem(i, 0, QTableWidgetItem(err["error"]))
                self._errors_table.setItem(i, 1, QTableWidgetItem(str(err["id1"])))
                self._errors_table.setItem(i, 2, QTableWidgetItem(str(err["id2"])))
            if not errors:
                QMessageBox.information(self, "OK", f"Topology '{name}' is valid!")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
