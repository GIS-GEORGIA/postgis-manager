"""Audit Log panel — paginated, filtered view of postgis_manager.audit_log.

Memory safety:
  - Never loads more than PAGE_SIZE (200) rows into QTableWidget at once.
  - All filtering happens server-side (SQL WHERE).
  - Worker guard: new fetch cancels/waits for the previous one.
  - JSONB detail shown in a separate dialog on double-click, not inline.
"""

from __future__ import annotations
import json

import psycopg2
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QLineEdit, QComboBox,
    QTextEdit, QGroupBox, QMessageBox, QDialog, QDialogButtonBox,
    QHeaderView, QAbstractItemView, QFileDialog, QDateEdit,
    QCheckBox, QSplitter, QSizePolicy, QFormLayout, QTabWidget,
    QTreeWidget, QTreeWidgetItem,
)
from PyQt6.QtCore import QDate

from ...utils.audit_manager import (
    ensure_audit_schema, install_trigger, drop_trigger,
    list_audited_tables, get_pgaudit_settings,
    fetch_logs, export_csv, PAGE_SIZE,
)

OP_COLORS = {
    "INSERT": "#3fb950",
    "UPDATE": "#d29922",
    "DELETE": "#ff7b72",
}


# ── Workers ───────────────────────────────────────────────────────────────

class SetupWorker(QThread):
    log      = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, conn_params: dict):
        super().__init__()
        self.conn_params = conn_params

    def run(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            ensure_audit_schema(conn)
            conn.close()
            self.log.emit("✓ Audit schema ready (postgis_manager.audit_log)", "success")
        except Exception as e:
            self.log.emit(f"✗ Setup failed: {e}", "error")
        finally:
            self.finished.emit()


class TriggerWorker(QThread):
    log      = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, conn_params: dict, schema: str, table: str,
                 action: str):  # "install" | "drop"
        super().__init__()
        self.conn_params = conn_params
        self.schema = schema
        self.table  = table
        self.action = action

    def run(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            if self.action == "install":
                ensure_audit_schema(conn)
                install_trigger(conn, self.schema, self.table)
                self.log.emit(
                    f"✓ Audit trigger installed on {self.schema}.{self.table}",
                    "success")
            else:
                drop_trigger(conn, self.schema, self.table)
                self.log.emit(
                    f"✓ Audit trigger removed from {self.schema}.{self.table}",
                    "success")
            conn.close()
        except Exception as e:
            self.log.emit(f"✗ {e}", "error")
        finally:
            self.finished.emit()


class FetchWorker(QThread):
    ready    = pyqtSignal(list, int)   # rows, total_count
    error    = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, conn_params: dict, filters: dict, page: int):
        super().__init__()
        self.conn_params = conn_params
        self.filters = filters
        self.page    = page
        self._abort  = False

    def abort(self):
        self._abort = True

    def run(self):
        if self._abort:
            self.finished.emit()
            return
        try:
            conn = psycopg2.connect(**self.conn_params)
            rows, total = fetch_logs(conn, page=self.page, **self.filters)
            conn.close()
            if not self._abort:
                self.ready.emit(rows, total)
        except Exception as e:
            if not self._abort:
                self.error.emit(str(e))
        finally:
            self.finished.emit()


class ExportWorker(QThread):
    log      = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, conn_params: dict, path: str, filters: dict):
        super().__init__()
        self.conn_params = conn_params
        self.path    = path
        self.filters = filters

    def run(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            export_csv(conn, self.path, **self.filters)
            conn.close()
            self.log.emit(f"✓ Exported to {self.path}", "success")
        except Exception as e:
            self.log.emit(f"✗ Export failed: {e}", "error")
        finally:
            self.finished.emit()


# ── Row detail dialog ─────────────────────────────────────────────────────

class RowDetailDialog(QDialog):
    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            f"Audit Detail — {row.get('operation')} on "
            f"{row.get('schema_name')}.{row.get('table_name')}  "
            f"[{row.get('ts')}]")
        self.setMinimumSize(640, 480)
        lay = QVBoxLayout(self)

        tabs = QTabWidget()

        # Summary tab
        summary = QWidget()
        fl = QFormLayout(summary)
        for k, v in [
            ("Time",      str(row.get("ts", ""))),
            ("User",      str(row.get("db_user", ""))),
            ("Operation", str(row.get("operation", ""))),
            ("Schema",    str(row.get("schema_name", ""))),
            ("Table",     str(row.get("table_name", ""))),
            ("PK",        f"{row.get('pk_column')} = {row.get('pk_value')}"),
            ("Changed columns",
             ", ".join(row.get("changed_columns") or []) or "—"),
        ]:
            lbl = QLabel(str(v))
            lbl.setWordWrap(True)
            fl.addRow(f"<b>{k}:</b>", lbl)
        tabs.addTab(summary, "Summary")

        # Old / New data
        def _json_widget(data) -> QTextEdit:
            te = QTextEdit()
            te.setReadOnly(True)
            te.setFont(QFont("Courier New", 10))
            if data:
                te.setPlainText(json.dumps(data, indent=2, default=str))
            else:
                te.setPlainText("—")
            return te

        old_w = _json_widget(row.get("old_data"))
        new_w = _json_widget(row.get("new_data"))
        tabs.addTab(old_w, "OLD data")
        tabs.addTab(new_w, "NEW data")

        # Geometry diff
        geom_w = QTextEdit()
        geom_w.setReadOnly(True)
        geom_w.setFont(QFont("Courier New", 10))
        old_g = row.get("old_geom") or "—"
        new_g = row.get("new_geom") or "—"
        geom_w.setPlainText(f"OLD geometry:\n{old_g}\n\nNEW geometry:\n{new_g}")
        tabs.addTab(geom_w, "Geometry diff")

        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


# ── Main Panel ────────────────────────────────────────────────────────────

class AuditPanel(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._conn_params: dict = {}
        self._current_page = 0
        self._total_rows   = 0
        self._fetch_worker: FetchWorker | None = None
        self._rows: list[dict] = []
        self._setup_ui()

    def set_connection(self, params: dict):
        self._conn_params = params
        self._setup_schema()

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        tabs = QTabWidget()
        tabs.addTab(self._build_log_tab(),      "📋  Audit Log")
        tabs.addTab(self._build_triggers_tab(), "⚙  Managed Tables")
        tabs.addTab(self._build_pgaudit_tab(),  "🔍  pgAudit")
        root.addWidget(tabs)

    # ── Tab 1: Log viewer ─────────────────────────────────────────────────

    def _build_log_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        # Filter bar
        fbox = QGroupBox("Filter")
        fl = QHBoxLayout(fbox)
        fl.setSpacing(6)

        self._f_schema = QLineEdit()
        self._f_schema.setPlaceholderText("schema")
        self._f_schema.setFixedWidth(100)
        self._f_table = QLineEdit()
        self._f_table.setPlaceholderText("table")
        self._f_table.setFixedWidth(120)
        self._f_user = QLineEdit()
        self._f_user.setPlaceholderText("db user")
        self._f_user.setFixedWidth(100)
        self._f_op = QComboBox()
        self._f_op.addItems(["ALL", "INSERT", "UPDATE", "DELETE"])
        self._f_op.setFixedWidth(90)

        self._f_from = QDateEdit()
        self._f_from.setCalendarPopup(True)
        self._f_from.setDate(QDate.currentDate().addDays(-7))
        self._f_from.setDisplayFormat("yyyy-MM-dd")
        self._f_from.setFixedWidth(110)
        self._use_date = QCheckBox("Date range")
        self._use_date.toggled.connect(lambda v: (
            self._f_from.setEnabled(v), self._f_to.setEnabled(v)))
        self._f_to = QDateEdit()
        self._f_to.setCalendarPopup(True)
        self._f_to.setDate(QDate.currentDate())
        self._f_to.setDisplayFormat("yyyy-MM-dd")
        self._f_to.setFixedWidth(110)
        self._f_from.setEnabled(False)
        self._f_to.setEnabled(False)

        btn_search = QPushButton("🔍 Search")
        btn_search.clicked.connect(self._search)
        btn_export = QPushButton("📥 CSV")
        btn_export.clicked.connect(self._export_csv)

        for widget in (QLabel("Schema:"), self._f_schema,
                       QLabel("Table:"),  self._f_table,
                       QLabel("User:"),   self._f_user,
                       QLabel("Op:"),     self._f_op,
                       self._use_date,    self._f_from,
                       QLabel("→"),       self._f_to,
                       btn_search, btn_export):
            fl.addWidget(widget)
        fl.addStretch()
        lay.addWidget(fbox)

        # Table
        self._log_table = QTableWidget(0, 8)
        self._log_table.setHorizontalHeaderLabels([
            "Time", "User", "Schema", "Table",
            "Op", "PK", "Changed", "Geom?"])
        hh = self._log_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self._log_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._log_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._log_table.setAlternatingRowColors(True)
        self._log_table.doubleClicked.connect(self._show_detail)
        lay.addWidget(self._log_table, 1)

        # Pagination bar
        pg_bar = QHBoxLayout()
        self._btn_prev = QPushButton("← Prev")
        self._btn_next = QPushButton("Next →")
        self._btn_prev.setFixedWidth(80)
        self._btn_next.setFixedWidth(80)
        self._btn_prev.clicked.connect(self._prev_page)
        self._btn_next.clicked.connect(self._next_page)
        self._page_lbl = QLabel("Page 1")
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("color:gray; font-size:11px;")
        pg_bar.addWidget(self._btn_prev)
        pg_bar.addWidget(self._page_lbl)
        pg_bar.addWidget(self._btn_next)
        pg_bar.addStretch()
        pg_bar.addWidget(self._count_lbl)
        lay.addLayout(pg_bar)

        # Log strip
        self._log_strip = QTextEdit()
        self._log_strip.setReadOnly(True)
        self._log_strip.setMaximumHeight(70)
        self._log_strip.setFont(QFont("Courier New", 10))
        lay.addWidget(self._log_strip)
        return w

    # ── Tab 2: Trigger manager ────────────────────────────────────────────

    def _build_triggers_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        info = QLabel(
            "Install the audit trigger on any spatial table. "
            "Every INSERT / UPDATE / DELETE will be recorded in "
            "<b>postgis_manager.audit_log</b>. "
            "Geometry is stored as truncated WKT (max 300 chars).")
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(info)

        # Install form
        inst_box = QGroupBox("Install / Remove Trigger")
        fl = QFormLayout(inst_box)
        self._trig_schema = QLineEdit("public")
        self._trig_table  = QLineEdit()
        self._trig_table.setPlaceholderText("table name")
        fl.addRow("Schema:", self._trig_schema)
        fl.addRow("Table:",  self._trig_table)

        btn_row = QHBoxLayout()
        btn_install = QPushButton("✔ Install Trigger")
        btn_remove  = QPushButton("✖ Remove Trigger")
        btn_install.clicked.connect(self._install_trigger)
        btn_remove.clicked.connect(self._remove_trigger)
        btn_row.addWidget(btn_install)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch()
        fl.addRow("", self._wrap(btn_row))
        lay.addWidget(inst_box)

        # Audited tables list
        audited_box = QGroupBox("Currently Audited Tables")
        ab_lay = QVBoxLayout(audited_box)
        self._audited_table = QTableWidget(0, 2)
        self._audited_table.setHorizontalHeaderLabels(["Schema", "Table"])
        self._audited_table.horizontalHeader().setStretchLastSection(True)
        self._audited_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._audited_table.setMaximumHeight(200)
        ab_lay.addWidget(self._audited_table)

        ref_row = QHBoxLayout()
        btn_refresh = QPushButton("↺ Refresh list")
        btn_refresh.clicked.connect(self._refresh_audited)
        btn_purge   = QPushButton("🗑 Purge audit_log…")
        btn_purge.clicked.connect(self._purge_log)
        ref_row.addWidget(btn_refresh)
        ref_row.addWidget(btn_purge)
        ref_row.addStretch()
        ab_lay.addLayout(ref_row)
        lay.addWidget(audited_box)

        lay.addStretch()
        return w

    # ── Tab 3: pgAudit ───────────────────────────────────────────────────

    def _build_pgaudit_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        info = QLabel(
            "<b>pgaudit</b> is a PostgreSQL extension that writes detailed "
            "audit records to the server log (pg_log). "
            "It does not require our trigger — it works at the server level.\n\n"
            "To enable: <code>CREATE EXTENSION pgaudit;</code> and set "
            "<code>pgaudit.log = 'ddl, write'</code> in postgresql.conf.")
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)

        btn_check = QPushButton("🔍 Check pgAudit Status")
        btn_check.clicked.connect(self._check_pgaudit)
        lay.addWidget(btn_check)

        self._pgaudit_table = QTableWidget(0, 3)
        self._pgaudit_table.setHorizontalHeaderLabels(
            ["Setting", "Value", "Description"])
        self._pgaudit_table.horizontalHeader().setStretchLastSection(True)
        self._pgaudit_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self._pgaudit_table, 1)

        install_box = QGroupBox("Quick Enable")
        ql = QVBoxLayout(install_box)
        ql.addWidget(QLabel("Run these SQL commands on your server:"))
        sql_te = QTextEdit()
        sql_te.setReadOnly(True)
        sql_te.setFont(QFont("Courier New", 10))
        sql_te.setMaximumHeight(120)
        sql_te.setPlainText(
            "-- 1. Install extension\n"
            "CREATE EXTENSION IF NOT EXISTS pgaudit;\n\n"
            "-- 2. In postgresql.conf:\n"
            "-- pgaudit.log = 'ddl, write'\n"
            "-- pgaudit.log_relation = on\n\n"
            "-- 3. Reload\n"
            "SELECT pg_reload_conf();"
        )
        ql.addWidget(sql_te)
        lay.addWidget(install_box)
        return w

    # ── Slots: Log tab ────────────────────────────────────────────────────

    def _get_filters(self) -> dict:
        return {
            "schema":    self._f_schema.text().strip(),
            "table":     self._f_table.text().strip(),
            "user":      self._f_user.text().strip(),
            "operation": self._f_op.currentText(),
            "date_from": (self._f_from.date().toString("yyyy-MM-dd")
                          if self._use_date.isChecked() else ""),
            "date_to":   (self._f_to.date().toString("yyyy-MM-dd") + " 23:59:59"
                          if self._use_date.isChecked() else ""),
        }

    def _search(self):
        self._current_page = 0
        self._fetch_page()

    def _prev_page(self):
        if self._current_page > 0:
            self._current_page -= 1
            self._fetch_page()

    def _next_page(self):
        if (self._current_page + 1) * PAGE_SIZE < self._total_rows:
            self._current_page += 1
            self._fetch_page()

    def _fetch_page(self):
        if not self._conn_params:
            self._log_msg("Not connected", "error")
            return
        # Cancel previous worker safely
        if self._fetch_worker and self._fetch_worker.isRunning():
            self._fetch_worker.abort()
            self._fetch_worker.quit()
            self._fetch_worker.wait(2000)

        self._log_msg(f"Loading page {self._current_page + 1}…", "info")
        w = FetchWorker(self._conn_params, self._get_filters(),
                        self._current_page)
        w.ready.connect(self._on_rows)
        w.error.connect(lambda e: self._log_msg(f"Error: {e}", "error"))
        w.finished.connect(w.deleteLater)
        self._fetch_worker = w
        w.start()

    def _on_rows(self, rows: list, total: int):
        self._rows = rows
        self._total_rows = total
        t = self._log_table
        t.setRowCount(0)
        for r in rows:
            row_idx = t.rowCount()
            t.insertRow(row_idx)
            op = r.get("operation", "")
            color = QColor(OP_COLORS.get(op, "#aaa"))
            geom_changed = bool(
                r.get("old_geom") or r.get("new_geom"))
            vals = [
                str(r.get("ts", ""))[:19],
                r.get("db_user", ""),
                r.get("schema_name", ""),
                r.get("table_name", ""),
                op,
                f"{r.get('pk_column') or ''}={r.get('pk_value') or ''}",
                ", ".join(r.get("changed_columns") or []) or "—",
                "✔" if geom_changed else "",
            ]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, r)
                if col == 4:
                    item.setForeground(color)
                    item.setFont(QFont("", -1, QFont.Weight.Bold))
                t.setItem(row_idx, col, item)

        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self._page_lbl.setText(f"Page {self._current_page + 1} / {pages}")
        self._count_lbl.setText(f"Total: {total:,} rows  ·  showing {len(rows)}")
        self._btn_prev.setEnabled(self._current_page > 0)
        self._btn_next.setEnabled(
            (self._current_page + 1) * PAGE_SIZE < total)

    def _show_detail(self, index):
        row = index.row()
        if 0 <= row < len(self._rows):
            dlg = RowDetailDialog(self._rows[row], self)
            dlg.exec()

    def _export_csv(self):
        if not self._conn_params:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Audit Log", "audit_log.csv",
            "CSV files (*.csv);;All files (*)")
        if not path:
            return
        w = ExportWorker(self._conn_params, path, self._get_filters())
        w.log.connect(self._log_msg)
        w.finished.connect(w.deleteLater)
        w.start()

    # ── Slots: Triggers tab ───────────────────────────────────────────────

    def _setup_schema(self):
        w = SetupWorker(self._conn_params)
        w.log.connect(self._log_msg)
        w.finished.connect(self._refresh_audited)
        w.finished.connect(w.deleteLater)
        w.start()

    def _install_trigger(self):
        schema = self._trig_schema.text().strip()
        table  = self._trig_table.text().strip()
        if not table:
            QMessageBox.warning(self, "Missing", "Enter a table name.")
            return
        w = TriggerWorker(self._conn_params, schema, table, "install")
        w.log.connect(self._log_msg)
        w.finished.connect(self._refresh_audited)
        w.finished.connect(w.deleteLater)
        w.start()

    def _remove_trigger(self):
        schema = self._trig_schema.text().strip()
        table  = self._trig_table.text().strip()
        if not table:
            QMessageBox.warning(self, "Missing", "Enter a table name.")
            return
        w = TriggerWorker(self._conn_params, schema, table, "drop")
        w.log.connect(self._log_msg)
        w.finished.connect(self._refresh_audited)
        w.finished.connect(w.deleteLater)
        w.start()

    def _refresh_audited(self):
        if not self._conn_params:
            return
        try:
            conn = psycopg2.connect(**self._conn_params)
            tables = list_audited_tables(conn)
            conn.close()
            t = self._audited_table
            t.setRowCount(0)
            for row in tables:
                r = t.rowCount()
                t.insertRow(r)
                t.setItem(r, 0, QTableWidgetItem(row["schema"]))
                t.setItem(r, 1, QTableWidgetItem(row["table"]))
        except Exception as e:
            self._log_msg(f"Could not list audited tables: {e}", "warn")

    def _purge_log(self):
        reply = QMessageBox.question(
            self, "Purge Audit Log",
            "Delete ALL records from postgis_manager.audit_log?\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            conn = psycopg2.connect(**self._conn_params)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("TRUNCATE postgis_manager.audit_log RESTART IDENTITY;")
            conn.close()
            self._log_msg("✓ audit_log purged", "success")
            self._log_table.setRowCount(0)
            self._count_lbl.setText("Total: 0 rows")
        except Exception as e:
            self._log_msg(f"✗ {e}", "error")

    # ── Slots: pgAudit tab ────────────────────────────────────────────────

    def _check_pgaudit(self):
        if not self._conn_params:
            return
        try:
            conn = psycopg2.connect(**self._conn_params)
            settings = get_pgaudit_settings(conn)
            conn.close()
            t = self._pgaudit_table
            t.setRowCount(0)
            if not settings:
                self._log_msg(
                    "pgaudit extension not installed or not in shared_preload_libraries",
                    "warn")
                t.insertRow(0)
                t.setItem(0, 0, QTableWidgetItem("pgaudit"))
                item = QTableWidgetItem("NOT INSTALLED")
                item.setForeground(QColor("#ff7b72"))
                t.setItem(0, 1, item)
                return
            for s in settings:
                r = t.rowCount()
                t.insertRow(r)
                t.setItem(r, 0, QTableWidgetItem(s["name"]))
                t.setItem(r, 1, QTableWidgetItem(s["value"]))
                t.setItem(r, 2, QTableWidgetItem(s["desc"]))
            self._log_msg(f"✓ pgaudit active — {len(settings)} settings", "success")
        except Exception as e:
            self._log_msg(f"✗ {e}", "error")

    # ── Helpers ───────────────────────────────────────────────────────────

    def _log_msg(self, msg: str, level: str = "info"):
        colors = {
            "success": "#3fb950", "error": "#ff7b72",
            "warn": "#d29922",    "info":  "#58a6ff",
        }
        color = colors.get(level, "#aaa")
        self._log_strip.append(
            f'<span style="color:{color};">{msg}</span>')

    def _wrap(self, hlay: QHBoxLayout) -> QWidget:
        w = QWidget(); w.setLayout(hlay); return w
