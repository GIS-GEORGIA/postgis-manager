"""pgVersion row-level versioning panel — PyQt6."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox, QTextEdit,
)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QColor

from ...db.connection import DBManager
from ...utils import i18n


class VersionWorker(QThread):
    done  = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, db, action, **kw):
        super().__init__()
        self.db = db; self.action = action; self.kw = kw

    def run(self):
        try:
            if self.action == "init":
                self.db.pgversion_init(self.kw["schema"], self.kw["table"])
                self.done.emit("ok_init")
            elif self.action == "commit":
                self.db.pgversion_commit(
                    self.kw["schema"], self.kw["table"], self.kw["msg"])
                self.done.emit("ok_commit")
            elif self.action == "log":
                rows = self.db.pgversion_log(self.kw["schema"], self.kw["table"])
                self.done.emit(rows)
            elif self.action == "checkout":
                self.db.pgversion_checkout(
                    self.kw["schema"], self.kw["table"], self.kw["revision"])
                self.done.emit("ok_checkout")
            elif self.action == "diff":
                rows = self.db.pgversion_diff(
                    self.kw["schema"], self.kw["table"],
                    self.kw["rev_a"], self.kw["rev_b"])
                self.done.emit(rows)
        except Exception as e:
            self.error.emit(str(e))


class VersioningPanel(QWidget):
    """UI for pgVersion — init, commit, log, checkout, diff."""

    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._schema = self._table = ""
        self._log_rows: list[dict] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(f"🕓  {i18n.t('ver_title')}")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(title)

        # ── Layer selector ────────────────────────────────────────────────────
        top = QGroupBox(i18n.t("ver_active_layer"))
        tl = QFormLayout(top)
        self._layer_lbl = QLabel(i18n.t("ver_no_layer"))
        tl.addRow(i18n.t("ver_layer"), self._layer_lbl)
        btn_row = QHBoxLayout()
        self._init_btn = QPushButton(f"⚡ {i18n.t('ver_init')}")
        self._init_btn.clicked.connect(self._init_versioning)
        btn_row.addWidget(self._init_btn)
        tl.addRow("", btn_row)
        layout.addWidget(top)

        # ── Commit ────────────────────────────────────────────────────────────
        commit_box = QGroupBox(i18n.t("ver_commit"))
        cl = QFormLayout(commit_box)
        self._msg_edit = QLineEdit()
        self._msg_edit.setPlaceholderText(i18n.t("ver_commit_msg_placeholder"))
        cl.addRow(i18n.t("ver_commit_msg"), self._msg_edit)
        commit_btn = QPushButton(f"💾  {i18n.t('ver_commit')}")
        commit_btn.clicked.connect(self._commit)
        cl.addRow("", commit_btn)
        layout.addWidget(commit_box)

        # ── History log ───────────────────────────────────────────────────────
        log_box = QGroupBox(i18n.t("ver_history"))
        ll = QVBoxLayout(log_box)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel(i18n.t("ver_history")))
        log_header.addStretch()
        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self._load_log)
        log_header.addWidget(refresh_btn)
        ll.addLayout(log_header)

        self._log_table = QTableWidget()
        self._log_table.setColumnCount(5)
        self._log_table.setHorizontalHeaderLabels([
            i18n.t("ver_col_revision"),
            i18n.t("ver_col_branch"),
            i18n.t("ver_col_time"),
            i18n.t("ver_col_head"),
            i18n.t("ver_col_msg"),
        ])
        hh = self._log_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._log_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._log_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._log_table.setAlternatingRowColors(True)
        self._log_table.setMinimumHeight(160)
        ll.addWidget(self._log_table)

        action_row = QHBoxLayout()
        checkout_btn = QPushButton(f"⏪  {i18n.t('ver_checkout')}")
        checkout_btn.clicked.connect(self._checkout)
        action_row.addWidget(checkout_btn)

        diff_row = QHBoxLayout()
        diff_row.addWidget(QLabel(i18n.t("ver_diff_from")))
        self._rev_a = QLineEdit(); self._rev_a.setMaximumWidth(60)
        diff_row.addWidget(self._rev_a)
        diff_row.addWidget(QLabel(i18n.t("ver_diff_to")))
        self._rev_b = QLineEdit(); self._rev_b.setMaximumWidth(60)
        diff_row.addWidget(self._rev_b)
        diff_btn = QPushButton(i18n.t("ver_diff"))
        diff_btn.clicked.connect(self._show_diff)
        diff_row.addWidget(diff_btn)
        action_row.addLayout(diff_row)
        action_row.addStretch()
        ll.addLayout(action_row)
        layout.addWidget(log_box)

        # ── Diff output ───────────────────────────────────────────────────────
        diff_box = QGroupBox(i18n.t("ver_diff_result"))
        dl = QVBoxLayout(diff_box)
        self._diff_view = QTextEdit()
        self._diff_view.setReadOnly(True)
        self._diff_view.setMaximumHeight(160)
        self._diff_view.setFontFamily("Courier New")
        dl.addWidget(self._diff_view)
        layout.addWidget(diff_box)

        layout.addStretch()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_active_layer(self, schema: str, table: str):
        self._schema = schema; self._table = table
        self._layer_lbl.setText(f"{schema}.{table}")
        self._load_log()

    # ── Actions ───────────────────────────────────────────────────────────────

    def _check(self) -> bool:
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return False
        if not self._schema:
            QMessageBox.warning(self, "Error", i18n.t("ver_no_layer"))
            return False
        return True

    def _init_versioning(self):
        if not self._check(): return
        reply = QMessageBox.question(
            self, i18n.t("ver_init"),
            i18n.t("ver_init_confirm",
                   table=f"{self._schema}.{self._table}"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return
        w = VersionWorker(self.db, "init",
                          schema=self._schema, table=self._table)
        w.done.connect(lambda _: (
            QMessageBox.information(self, "OK", i18n.t("ver_init_done")),
            self._load_log()))
        w.error.connect(self._on_error)
        w.start(); self._worker = w

    def _commit(self):
        if not self._check(): return
        msg = self._msg_edit.text().strip()
        if not msg:
            QMessageBox.warning(self, "Error", i18n.t("ver_commit_msg_required"))
            return
        w = VersionWorker(self.db, "commit",
                          schema=self._schema, table=self._table, msg=msg)
        w.done.connect(lambda _: (
            self._msg_edit.clear(),
            self._load_log(),
            self._parent_log(i18n.t("ver_committed"), "success")))
        w.error.connect(self._on_error)
        w.start(); self._worker = w

    def _load_log(self):
        if not self.db.is_connected() or not self._schema: return
        w = VersionWorker(self.db, "log",
                          schema=self._schema, table=self._table)
        w.done.connect(self._populate_log)
        w.error.connect(lambda e: None)  # silently ignore if pgversion not installed
        w.start(); self._log_worker = w

    def _populate_log(self, rows):
        if not isinstance(rows, list): return
        self._log_rows = rows
        self._log_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            is_head = bool(r.get("is_head"))
            vals = [
                str(r.get("revision", "")),
                str(r.get("branch", "main")),
                str(r.get("commit_time", ""))[:19],
                "★ HEAD" if is_head else "",
                str(r.get("log_msg", "")),
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if is_head:
                    item.setForeground(QColor("#1B5E20"))
                    item.setFont(self._bold_font())
                self._log_table.setItem(i, j, item)

    def _checkout(self):
        if not self._check(): return
        row = self._log_table.currentRow()
        if row < 0 or row >= len(self._log_rows):
            QMessageBox.warning(self, "Error", i18n.t("ver_select_revision"))
            return
        rev = self._log_rows[row].get("revision")
        reply = QMessageBox.question(
            self, i18n.t("ver_checkout"),
            i18n.t("ver_checkout_confirm", revision=rev),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes: return
        w = VersionWorker(self.db, "checkout",
                          schema=self._schema, table=self._table,
                          revision=rev)
        w.done.connect(lambda _: (
            self._load_log(),
            self._parent_log(f"Checked out revision {rev}", "success")))
        w.error.connect(self._on_error)
        w.start(); self._worker = w

    def _show_diff(self):
        if not self._check(): return
        ra = self._rev_a.text().strip()
        rb = self._rev_b.text().strip()
        if not ra or not rb:
            # auto-fill from selected row and previous
            row = self._log_table.currentRow()
            if row >= 0 and len(self._log_rows) > 1:
                rb = str(self._log_rows[row].get("revision", ""))
                ra = str(self._log_rows[min(row + 1, len(self._log_rows) - 1)]
                         .get("revision", ""))
                self._rev_a.setText(ra); self._rev_b.setText(rb)
            else:
                QMessageBox.warning(self, "Error", i18n.t("ver_diff_enter_revs"))
                return
        w = VersionWorker(self.db, "diff",
                          schema=self._schema, table=self._table,
                          rev_a=ra, rev_b=rb)
        w.done.connect(self._show_diff_result)
        w.error.connect(self._on_error)
        w.start(); self._diff_worker = w

    def _show_diff_result(self, rows):
        if not isinstance(rows, list):
            return
        lines = [f"Diff: rev {self._rev_a.text()} → {self._rev_b.text()}",
                 "=" * 60]
        for r in rows:
            op = r.get("action", "?")
            prefix = {"I": "+ ", "D": "- ", "U": "~ "}.get(op, "  ")
            lines.append(f"{prefix}{r}")
        self._diff_view.setPlainText("\n".join(lines))

    def _on_error(self, msg: str):
        QMessageBox.critical(self, "Error", msg)

    def _parent_log(self, msg: str, level: str = "info"):
        if self.parent() and hasattr(self.parent(), "log"):
            self.parent().log(msg, level)

    def _bold_font(self):
        from PyQt6.QtGui import QFont
        f = QFont(); f.setBold(True); return f
