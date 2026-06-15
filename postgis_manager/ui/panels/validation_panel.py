"""Geometry Validation panel — PyQt6."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QProgressBar, QCheckBox, QMessageBox, QFormLayout,
)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QColor

from ...db.connection import DBManager
from ...utils import i18n


class ValidationWorker(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, geom_col, srid, checks):
        super().__init__()
        self.db = db; self.schema = schema; self.table = table
        self.geom_col = geom_col; self.srid = srid; self.checks = checks

    def run(self):
        try:
            result = self.db.run_validation(
                self.schema, self.table, self.geom_col, self.srid,
                checks=self.checks)
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class FixWorker(QThread):
    done  = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, geom_col):
        super().__init__()
        self.db = db; self.schema = schema
        self.table = table; self.geom_col = geom_col

    def run(self):
        try:
            n = self.db.fix_invalid_geometries(
                self.schema, self.table, self.geom_col)
            self.done.emit(n)
        except Exception as e:
            self.error.emit(str(e))


class ValidationPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._schema = self._table = self._geom_col = ""
        self._srid = 4326
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"✔  {i18n.t('val_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        # ── Layer info ────────────────────────────────────────────────────────
        layer_box = QGroupBox(i18n.t("val_layer"))
        lf = QFormLayout(layer_box)
        self._layer_lbl = QLabel(i18n.t("val_no_layer"))
        lf.addRow(i18n.t("val_layer"), self._layer_lbl)
        layout.addWidget(layer_box)

        # ── Checks selector ───────────────────────────────────────────────────
        checks_box = QGroupBox(i18n.t("val_checks"))
        checks_layout = QVBoxLayout(checks_box)
        self._check_boxes: dict[str, QCheckBox] = {}
        check_labels = {
            "Invalid geometries":    i18n.t("val_check_invalid"),
            "Null geometries":       i18n.t("val_check_null"),
            "Duplicate geometries":  i18n.t("val_check_duplicate"),
            "Self-intersecting":     i18n.t("val_check_self_intersect"),
            "Wrong SRID":            i18n.t("val_check_wrong_srid"),
            "Empty geometries":      i18n.t("val_check_empty"),
        }
        row_i = 0
        row_layout = QHBoxLayout()
        for key, label in check_labels.items():
            cb = QCheckBox(label)
            cb.setChecked(True)
            self._check_boxes[key] = cb
            row_layout.addWidget(cb)
            row_i += 1
            if row_i % 3 == 0:
                checks_layout.addLayout(row_layout)
                row_layout = QHBoxLayout()
        if row_i % 3 != 0:
            row_layout.addStretch()
            checks_layout.addLayout(row_layout)
        # select all / none
        sel_row = QHBoxLayout()
        all_btn = QPushButton(i18n.t("val_select_all"))
        all_btn.clicked.connect(lambda: [cb.setChecked(True)
                                          for cb in self._check_boxes.values()])
        none_btn = QPushButton(i18n.t("val_select_none"))
        none_btn.clicked.connect(lambda: [cb.setChecked(False)
                                           for cb in self._check_boxes.values()])
        sel_row.addWidget(all_btn); sel_row.addWidget(none_btn)
        sel_row.addStretch()
        checks_layout.addLayout(sel_row)
        layout.addWidget(checks_box)

        # ── Run buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton(f"▶  {i18n.t('val_run')}")
        self._run_btn.setStyleSheet("font-weight: bold;")
        self._run_btn.clicked.connect(self._run)
        btn_row.addWidget(self._run_btn)
        self._fix_btn = QPushButton(f"🔧  {i18n.t('val_fix_invalid')}")
        self._fix_btn.setToolTip(i18n.t("val_fix_tip"))
        self._fix_btn.clicked.connect(self._fix_invalid)
        btn_row.addWidget(self._fix_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # ── Results summary ───────────────────────────────────────────────────
        self._summary_table = QTableWidget()
        self._summary_table.setColumnCount(3)
        self._summary_table.setHorizontalHeaderLabels([
            i18n.t("val_col_check"),
            i18n.t("val_col_count"),
            i18n.t("val_col_status"),
        ])
        hh = self._summary_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._summary_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._summary_table.setAlternatingRowColors(True)
        self._summary_table.setMinimumHeight(220)
        layout.addWidget(self._summary_table)

        self._status_lbl = QLabel("")
        layout.addWidget(self._status_lbl)
        layout.addStretch()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_active_layer(self, schema, table, geom_col, srid):
        self._schema = schema; self._table = table
        self._geom_col = geom_col; self._srid = srid
        self._layer_lbl.setText(f"{schema}.{table}  [{geom_col}]  EPSG:{srid}")
        self._summary_table.setRowCount(0)
        self._status_lbl.setText("")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _run(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected")); return
        if not self._schema:
            QMessageBox.warning(self, "Error", i18n.t("val_no_layer")); return
        enabled = [k for k, cb in self._check_boxes.items() if cb.isChecked()]
        if not enabled:
            QMessageBox.warning(self, "Error", i18n.t("val_select_at_least_one")); return

        self._run_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._status_lbl.setText(i18n.t("val_running"))

        w = ValidationWorker(self.db, self._schema, self._table,
                             self._geom_col, self._srid, enabled)
        w.done.connect(self._on_done)
        w.error.connect(self._on_error)
        w.start(); self._worker = w

    def _on_done(self, results: dict):
        self._run_btn.setEnabled(True)
        self._progress.setVisible(False)

        rows = list(results.items())
        self._summary_table.setRowCount(len(rows))
        total_issues = 0
        for i, (check_name, data) in enumerate(rows):
            if not data:
                count = 0
            elif "error" in data[0]:
                count = -1
            else:
                # sum any numeric column that isn't schema/table_name
                count = 0
                for d in data:
                    for k, v in d.items():
                        if k not in ("schema", "table_name", "found_srid"):
                            try: count += int(v or 0)
                            except Exception: pass

            if count < 0:
                status = "⚠ Error"
                color = QColor("#E65100")
            elif count == 0:
                status = "✔ OK"
                color = QColor("#2E7D32")
            else:
                status = f"✖ {i18n.t('val_issues_found')}"
                color = QColor("#C62828")
                total_issues += count

            items = [
                QTableWidgetItem(check_name),
                QTableWidgetItem("—" if count < 0 else str(count)),
                QTableWidgetItem(status),
            ]
            for j, item in enumerate(items):
                if count != 0:
                    item.setForeground(color)
                self._summary_table.setItem(i, j, item)

        if total_issues == 0:
            self._status_lbl.setText(f"✔  {i18n.t('val_all_ok')}")
            self._status_lbl.setStyleSheet("color: #2E7D32; font-weight: bold;")
        else:
            self._status_lbl.setText(
                f"✖  {i18n.t('val_total_issues', n=total_issues)}")
            self._status_lbl.setStyleSheet("color: #C62828; font-weight: bold;")

        if self.parent() and hasattr(self.parent(), "log"):
            self.parent().log(
                f"Validation complete: {total_issues} issue(s) in "
                f"{self._schema}.{self._table}", "success" if total_issues == 0 else "warn")

    def _fix_invalid(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected")); return
        if not self._schema:
            QMessageBox.warning(self, "Error", i18n.t("val_no_layer")); return
        reply = QMessageBox.question(
            self, i18n.t("val_fix_invalid"),
            i18n.t("val_fix_confirm",
                   table=f"{self._schema}.{self._table}"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return
        w = FixWorker(self.db, self._schema, self._table, self._geom_col)
        w.done.connect(lambda n: (
            QMessageBox.information(
                self, "OK", i18n.t("val_fix_done", n=n)),
            self._run()))
        w.error.connect(self._on_error)
        w.start(); self._fix_worker = w

    def _on_error(self, msg: str):
        self._run_btn.setEnabled(True)
        self._progress.setVisible(False)
        QMessageBox.critical(self, "Error", msg)
