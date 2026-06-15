"""SQL Editor panel — PyQt6, syntax highlighting, history, result table."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTextEdit, QTableWidget, QTableWidgetItem, QPushButton,
    QLabel, QComboBox, QFileDialog, QMessageBox, QHeaderView,
    QAbstractItemView,
)
from PyQt6.QtGui import (
    QFont, QSyntaxHighlighter, QTextCharFormat, QColor,
    QKeySequence, QShortcut,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRegularExpression

from ...db.connection import DBManager
from ...utils import i18n, config


class SQLHighlighter(QSyntaxHighlighter):
    KEYWORDS = (
        "SELECT FROM WHERE JOIN LEFT RIGHT INNER OUTER FULL ON AS AND OR NOT "
        "IN IS NULL LIKE ILIKE BETWEEN ORDER BY GROUP HAVING LIMIT OFFSET "
        "INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE INDEX VIEW DROP "
        "ALTER ADD COLUMN SCHEMA DATABASE WITH UNION ALL DISTINCT CASE WHEN "
        "THEN ELSE END EXISTS RETURNING TRUNCATE CASCADE VACUUM ANALYZE "
        "EXPLAIN BEGIN COMMIT ROLLBACK SAVEPOINT RELEASE TRUE FALSE"
    ).split()

    POSTGIS = (
        "ST_GeomFromText ST_AsText ST_AsGeoJSON ST_Transform ST_SRID "
        "ST_Area ST_Length ST_Perimeter ST_Distance ST_Buffer "
        "ST_Intersection ST_Union ST_Difference ST_Contains ST_Within "
        "ST_Overlaps ST_Touches ST_Intersects ST_DWithin ST_Envelope "
        "ST_ConvexHull ST_Centroid ST_Simplify ST_IsValid ST_IsSimple "
        "ST_IsEmpty ST_MakeValid ST_SetSRID ST_X ST_Y ST_Z "
        "ST_NPoints ST_GeometryN ST_ExteriorRing geometry_columns "
        "ST_LineInterpolatePoint ST_MakeLine ST_MakePoint "
        "postgis_version postgis_lib_version pgr_dijkstra "
        "pgr_drivingDistance pgr_version ST_SnapToGrid ST_Collect"
    ).split()

    def __init__(self, doc):
        super().__init__(doc)
        self.rules: list[tuple] = []

        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor("#569CD6"))
        kw_fmt.setFontWeight(700)
        for kw in self.KEYWORDS:
            self.rules.append((
                QRegularExpression(r'\b' + kw + r'\b',
                    QRegularExpression.PatternOption.CaseInsensitiveOption),
                kw_fmt))

        pg_fmt = QTextCharFormat()
        pg_fmt.setForeground(QColor("#4EC9B0"))
        for fn in self.POSTGIS:
            self.rules.append((
                QRegularExpression(r'\b' + fn + r'\b',
                    QRegularExpression.PatternOption.CaseInsensitiveOption),
                pg_fmt))

        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor("#CE9178"))
        self.rules.append((QRegularExpression(r"'[^']*'"), str_fmt))

        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor("#B5CEA8"))
        self.rules.append((QRegularExpression(r'\b\d+\.?\d*\b'), num_fmt))

        cmt_fmt = QTextCharFormat()
        cmt_fmt.setForeground(QColor("#6A9955"))
        cmt_fmt.setFontItalic(True)
        self.rules.append((QRegularExpression(r'--[^\n]*'), cmt_fmt))

        id_fmt = QTextCharFormat()
        id_fmt.setForeground(QColor("#DCDCAA"))
        self.rules.append((QRegularExpression(r'"[^"]+"'), id_fmt))

    def highlightBlock(self, text: str):
        for pattern, fmt in self.rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)


class QueryWorker(QThread):
    done  = pyqtSignal(object, object, float)
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, sql: str):
        super().__init__()
        self.db = db
        self.sql = sql

    def run(self):
        try:
            cols, rows, ms = self.db.execute_sql(self.sql)
            self.done.emit(cols, rows, ms)
        except Exception as e:
            self.error.emit(str(e))


class SQLEditorPanel(QWidget):
    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._history: list[str] = list(config.get("sql_history", []))
        self._worker: QueryWorker | None = None
        self._active_schema = ""
        self._active_table = ""
        self._build_ui()
        # Populate history combo from saved history
        for entry in self._history:
            self._history_combo.addItem(entry[:60].replace("\n", " "))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # Toolbar
        toolbar = QHBoxLayout()
        self._run_btn = QPushButton(i18n.t("sql_run"))
        self._run_btn.clicked.connect(self._run_query)
        toolbar.addWidget(self._run_btn)

        self._stop_btn = QPushButton(i18n.t("sql_stop"))
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_query)
        toolbar.addWidget(self._stop_btn)

        self._clear_btn = QPushButton(i18n.t("sql_clear"))
        self._clear_btn.clicked.connect(self._clear)
        toolbar.addWidget(self._clear_btn)

        toolbar.addStretch()

        self._save_btn = QPushButton(i18n.t("sql_save"))
        self._save_btn.clicked.connect(self._save_query)
        toolbar.addWidget(self._save_btn)

        self._load_btn = QPushButton(i18n.t("sql_load"))
        self._load_btn.clicked.connect(self._load_query)
        toolbar.addWidget(self._load_btn)

        self._history_combo = QComboBox()
        self._history_combo.setMinimumWidth(200)
        self._history_combo.setPlaceholderText("History...")
        self._history_combo.currentIndexChanged.connect(self._load_history)
        toolbar.addWidget(self._history_combo)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)

        # Editor
        editor_widget = QWidget()
        ed_layout = QVBoxLayout(editor_widget)
        ed_layout.setContentsMargins(0, 0, 0, 0)

        tpl_row = QHBoxLayout()
        for name, sql in [
            ("SELECT *", 'SELECT * FROM "{schema}"."{table}" LIMIT 100;'),
            ("COUNT",    'SELECT COUNT(*) FROM "{schema}"."{table}";'),
            ("Extent",   'SELECT ST_Extent(geom) FROM "{schema}"."{table}";'),
            ("Validate", 'SELECT ctid, ST_IsValidReason(geom) FROM "{schema}"."{table}" WHERE NOT ST_IsValid(geom);'),
            ("Columns",  "SELECT column_name, udt_name FROM information_schema.columns WHERE table_schema='{schema}' AND table_name='{table}';"),
        ]:
            btn = QPushButton(name)
            btn.setFixedHeight(24)
            btn.clicked.connect(lambda _, s=sql: self._insert_template(s))
            tpl_row.addWidget(btn)
        tpl_row.addStretch()
        ed_layout.addLayout(tpl_row)

        self._editor = QTextEdit()
        self._editor.setFont(QFont("Courier New", 12))
        self._editor.setPlaceholderText("-- Enter SQL here  (F5 to run)")
        self._highlighter = SQLHighlighter(self._editor.document())
        ed_layout.addWidget(self._editor)
        splitter.addWidget(editor_widget)

        # Results
        result_widget = QWidget()
        res_layout = QVBoxLayout(result_widget)
        res_layout.setContentsMargins(0, 0, 0, 0)

        self._result_label = QLabel("")
        res_layout.addWidget(self._result_label)

        self._result_table = QTableWidget()
        self._result_table.setAlternatingRowColors(True)
        self._result_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._result_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self._result_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        res_layout.addWidget(self._result_table)

        exp_row = QHBoxLayout()
        self._export_btn = QPushButton("💾 Export results to CSV")
        self._export_btn.clicked.connect(self._export_csv)
        exp_row.addWidget(self._export_btn)
        exp_row.addStretch()
        res_layout.addLayout(exp_row)
        splitter.addWidget(result_widget)
        splitter.setSizes([300, 300])

        QShortcut(QKeySequence("F5"), self, self._run_query)

    def set_active_layer(self, schema: str, table: str):
        self._active_schema = schema
        self._active_table = table

    def _insert_template(self, sql: str):
        sql = sql.replace("{schema}", self._active_schema or "public")
        sql = sql.replace("{table}", self._active_table or "layer_name")
        self._editor.setPlainText(sql)

    def _run_query(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return
        sql = self._editor.toPlainText().strip()
        if not sql:
            return
        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._result_label.setText("Running...")
        if not self._history or self._history[0] != sql:
            self._history.insert(0, sql)
            self._history = self._history[:50]
            self._history_combo.insertItem(0, sql[:60].replace("\n", " "))
            config.set("sql_history", self._history)
        self._worker = QueryWorker(self.db, sql)
        self._worker.done.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_result(self, cols, rows, ms: float):
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if cols and rows is not None:
            n = len(rows)
            self._result_label.setText(
                i18n.t("sql_rows_returned", n=n, ms=f"{ms:.1f}"))
            self._result_table.setRowCount(n)
            self._result_table.setColumnCount(len(cols))
            self._result_table.setHorizontalHeaderLabels(cols)
            for r, row in enumerate(rows):
                for c, val in enumerate(row):
                    self._result_table.setItem(
                        r, c, QTableWidgetItem(
                            str(val) if val is not None else ""))
        else:
            self._result_label.setText(
                i18n.t("sql_no_result") + f"  ({ms:.1f} ms)")
            self._result_table.setRowCount(0)
            self._result_table.setColumnCount(0)
        if self.parent() and hasattr(self.parent(), "log"):
            self.parent().log(f"SQL done in {ms:.1f} ms", "success")

    def _on_error(self, error: str):
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._result_label.setText(f"Error: {error}")
        if self.parent() and hasattr(self.parent(), "log"):
            self.parent().log(f"SQL Error: {error}", "error")

    def _stop_query(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    def _clear(self):
        self._editor.clear()
        self._result_table.setRowCount(0)
        self._result_table.setColumnCount(0)
        self._result_label.setText("")

    def _save_query(self):
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save Query", "Query name:")
        if ok and name:
            config.save_query(name, self._editor.toPlainText())

    def _load_query(self):
        from PyQt6.QtWidgets import QInputDialog
        queries = config.get_saved_queries()
        if not queries:
            QMessageBox.information(self, "Info", "No saved queries.")
            return
        names = [q["name"] for q in queries]
        name, ok = QInputDialog.getItem(
            self, "Load Query", "Select:", names, 0, False)
        if ok:
            q = next((q for q in queries if q["name"] == name), None)
            if q:
                self._editor.setPlainText(q["sql"])

    def _load_history(self, idx: int):
        if 0 <= idx < len(self._history):
            self._editor.setPlainText(self._history[idx])

    def _export_csv(self):
        import csv as csv_mod
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", "result.csv", "CSV (*.csv)")
        if not path:
            return
        rows = self._result_table.rowCount()
        cols = self._result_table.columnCount()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv_mod.writer(f)
            w.writerow([self._result_table.horizontalHeaderItem(c).text()
                        for c in range(cols)])
            for r in range(rows):
                w.writerow([
                    self._result_table.item(r, c).text()
                    if self._result_table.item(r, c) else ""
                    for c in range(cols)
                ])
        QMessageBox.information(self, "Done", f"Exported to {path}")
