"""SQL Editor panel — syntax highlighting, autocomplete, EXPLAIN, history."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QPushButton,
    QLabel, QComboBox, QFileDialog, QMessageBox, QHeaderView,
    QAbstractItemView, QCompleter, QPlainTextEdit, QFrame,
)
from PyQt6.QtGui import (
    QFont, QSyntaxHighlighter, QTextCharFormat, QColor,
    QKeySequence, QShortcut, QTextCursor,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QRegularExpression,
    QRect,
)

from ...db.connection import DBManager
from ...utils import i18n, config


# ── Syntax highlighter ─────────────────────────────────────────────────────

class SQLHighlighter(QSyntaxHighlighter):
    KEYWORDS = (
        "SELECT FROM WHERE JOIN LEFT RIGHT INNER OUTER FULL ON AS AND OR NOT "
        "IN IS NULL LIKE ILIKE BETWEEN ORDER BY GROUP HAVING LIMIT OFFSET "
        "INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE INDEX VIEW DROP "
        "ALTER ADD COLUMN SCHEMA DATABASE WITH UNION ALL DISTINCT CASE WHEN "
        "THEN ELSE END EXISTS RETURNING TRUNCATE CASCADE VACUUM ANALYZE "
        "EXPLAIN BEGIN COMMIT ROLLBACK SAVEPOINT RELEASE TRUE FALSE LATERAL "
        "CROSS NATURAL USING OVER PARTITION WINDOW FILTER RECURSIVE"
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
        "pgr_drivingDistance pgr_version ST_SnapToGrid ST_Collect "
        "ST_GeoHash ST_AsEWKT ST_AsEWKB ST_GeomFromEWKT ST_Force2D "
        "ST_FlipCoordinates ST_GeneratePoints ST_VoronoiPolygons "
        "ST_ClosestPoint ST_ShortestLine ST_LongestLine ST_Expand"
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


# ── SQL Editor widget with autocomplete ────────────────────────────────────

class SQLEditor(QPlainTextEdit):
    """QPlainTextEdit with Ctrl+Space autocomplete popup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(QFont("Courier New", 12))
        self.setPlaceholderText("-- Enter SQL here  (F5 to run, Ctrl+Space to autocomplete)")
        self._completer: QCompleter | None = None
        self._setup_completer([])

    def _setup_completer(self, words: list[str]):
        if self._completer:
            self._completer.setParent(None)
        all_words = sorted(set(
            SQLHighlighter.KEYWORDS + SQLHighlighter.POSTGIS + words
        ), key=str.lower)
        self._completer = QCompleter(all_words, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.activated.connect(self._insert_completion)

    def set_completions(self, words: list[str]):
        self._setup_completer(words)

    def _insert_completion(self, text: str):
        cur = self.textCursor()
        extra = len(text) - len(self._completer.completionPrefix())
        cur.movePosition(QTextCursor.MoveOperation.Left)
        cur.movePosition(QTextCursor.MoveOperation.EndOfWord)
        cur.insertText(text[-extra:])
        self.setTextCursor(cur)

    def _word_under_cursor(self) -> str:
        cur = self.textCursor()
        cur.select(QTextCursor.SelectionType.WordUnderCursor)
        return cur.selectedText()

    def keyPressEvent(self, event):
        popup = self._completer.popup()
        if popup.isVisible():
            if event.key() in (
                Qt.Key.Key_Enter, Qt.Key.Key_Return,
                Qt.Key.Key_Escape, Qt.Key.Key_Tab,
                Qt.Key.Key_Backtab,
            ):
                event.ignore()
                return
        # Ctrl+Space → trigger manually
        if (event.key() == Qt.Key.Key_Space and
                event.modifiers() == Qt.KeyboardModifier.ControlModifier):
            self._trigger_complete()
            return
        super().keyPressEvent(event)
        # Auto-trigger after typing ≥2 chars
        if event.text() and event.text().isalnum():
            self._trigger_complete()
        else:
            self._completer.popup().hide()

    def _trigger_complete(self):
        prefix = self._word_under_cursor()
        if len(prefix) < 2:
            self._completer.popup().hide()
            return
        if prefix != self._completer.completionPrefix():
            self._completer.setCompletionPrefix(prefix)
            self._completer.popup().setCurrentIndex(
                self._completer.completionModel().index(0, 0))
        cur_rect: QRect = self.cursorRect()
        cur_rect.setWidth(
            self._completer.popup().sizeHintForColumn(0)
            + self._completer.popup().verticalScrollBar().sizeHint().width())
        self._completer.complete(cur_rect)


# ── Workers ────────────────────────────────────────────────────────────────

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


class CompletionLoader(QThread):
    """Load schema/table/column/function names for autocomplete."""
    done = pyqtSignal(list)

    def __init__(self, db: DBManager):
        super().__init__()
        self.db = db

    def run(self):
        words: list[str] = []
        try:
            import psycopg2
            conn = psycopg2.connect(**self.db.params)
            cur = conn.cursor()
            # schemas
            cur.execute("SELECT schema_name FROM information_schema.schemata")
            words += [r[0] for r in cur.fetchall()]
            # tables + columns
            cur.execute("""
                SELECT table_schema, table_name, column_name
                FROM information_schema.columns
                WHERE table_schema NOT IN ('pg_catalog','information_schema')
                LIMIT 5000
            """)
            for schema, table, col in cur.fetchall():
                words += [table, f'"{schema}"."{table}"', col]
            # pg functions (public + postgis)
            cur.execute("""
                SELECT routine_name
                FROM information_schema.routines
                WHERE routine_schema IN ('public','postgis')
                LIMIT 1000
            """)
            words += [r[0] for r in cur.fetchall()]
            cur.close()
            conn.close()
        except Exception:
            pass
        self.done.emit(list(set(words)))


# ── Main panel ─────────────────────────────────────────────────────────────

# ── Geometry column detection ──────────────────────────────────────────────

_GEO_NAMES = frozenset({
    "geom", "geometry", "the_geom", "shape", "wkb_geometry",
    "geomfromtext", "geo", "geog", "geography", "point",
    "line", "polygon", "multipolygon", "multilinestring", "multipoint",
})

def _is_geo_col(col_name: str, sample_value) -> bool:
    """Return True if this column looks like a PostGIS geometry."""
    if col_name.lower() in _GEO_NAMES:
        return True
    if col_name.lower().startswith("st_"):
        return True
    if isinstance(sample_value, str) and len(sample_value) > 20:
        stripped = sample_value.strip()
        # WKB hex — all hex chars, typically starts with 0 or 1
        if all(c in "0123456789abcdefABCDEF" for c in stripped[:20]):
            return True
        # WKT
        if any(stripped.upper().startswith(k) for k in (
                "POINT", "LINESTRING", "POLYGON", "MULTI",
                "GEOMETRYCOLLECTION", "SRID=")):
            return True
    return False


class GeoQueryWorker(QThread):
    """Re-runs SQL wrapping geometry cols with ST_AsGeoJSON."""
    done  = pyqtSignal(list, list)   # features: [(geojson_str, attrs)], col_names
    error = pyqtSignal(str)

    def __init__(self, db: DBManager, sql: str, geo_cols: list[str], attr_cols: list[str]):
        super().__init__()
        self.db       = db
        self.sql      = sql
        self.geo_cols = geo_cols
        self.attr_cols = attr_cols

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self.db.params,
                                    options="-c client_encoding=UTF8")
            cur  = conn.cursor()

            # Build SELECT: ST_AsGeoJSON for each geo col, rest as-is
            selects = []
            for col in self.geo_cols:
                selects.append(f'ST_AsGeoJSON("{col}", 6) AS "_geojson_{col}"')
            for col in self.attr_cols:
                selects.append(f'"{col}"')

            wrapped = (f"SELECT {', '.join(selects)} "
                       f"FROM ({self.sql}) AS _sql_result LIMIT 50000")
            cur.execute(wrapped)
            desc  = [d[0] for d in cur.description]
            rows  = cur.fetchall()
            cur.close()
            conn.close()

            features = []
            for row in rows:
                rd = dict(zip(desc, row))
                # one feature per geometry column (use first geo col)
                geojson_str = rd.get(f"_geojson_{self.geo_cols[0]}")
                if geojson_str is None:
                    continue
                attrs = {c: rd.get(c) for c in self.attr_cols}
                features.append((geojson_str, attrs))

            self.done.emit(features, self.attr_cols)
        except Exception as e:
            self.error.emit(str(e))


class SQLEditorPanel(QWidget):
    # Emitted when user clicks "Show on Map"
    # payload: list of (geojson_str, attrs_dict), list of attr col names
    show_on_map = pyqtSignal(list, list, str)

    def __init__(self, db: DBManager, parent=None):
        super().__init__(parent)
        self.db = db
        self._history: list[str] = list(config.get("sql_history", []))
        self._worker: QueryWorker | None = None
        self._geo_worker: GeoQueryWorker | None = None
        self._comp_loader: CompletionLoader | None = None
        self._active_schema = ""
        self._active_table = ""
        self._last_sql = ""
        self._detected_geo_cols: list[str] = []
        self._detected_attr_cols: list[str] = []
        self._build_ui()
        for entry in self._history:
            self._history_combo.addItem(entry[:60].replace("\n", " "))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── Toolbar ──────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        self._run_btn = QPushButton(i18n.t("sql_run"))
        self._run_btn.clicked.connect(self._run_query)
        toolbar.addWidget(self._run_btn)

        self._stop_btn = QPushButton(i18n.t("sql_stop"))
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_query)
        toolbar.addWidget(self._stop_btn)

        self._explain_btn = QPushButton("⚡ EXPLAIN")
        self._explain_btn.setToolTip("Run EXPLAIN ANALYZE and show visual plan")
        self._explain_btn.clicked.connect(self._run_explain)
        toolbar.addWidget(self._explain_btn)

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

        # ── Splitter: editor | results ────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)

        # Editor
        editor_widget = QWidget()
        ed_layout = QVBoxLayout(editor_widget)
        ed_layout.setContentsMargins(0, 0, 0, 0)

        tpl_row = QHBoxLayout()
        for name, sql in [
            ("SELECT *",  'SELECT * FROM "{schema}"."{table}" LIMIT 100;'),
            ("COUNT",     'SELECT COUNT(*) FROM "{schema}"."{table}";'),
            ("Extent",    'SELECT ST_Extent(geom) FROM "{schema}"."{table}";'),
            ("Validate",  'SELECT ctid, ST_IsValidReason(geom) FROM "{schema}"."{table}" WHERE NOT ST_IsValid(geom);'),
            ("Columns",   "SELECT column_name, udt_name FROM information_schema.columns WHERE table_schema='{schema}' AND table_name='{table}';"),
        ]:
            btn = QPushButton(name)
            btn.setFixedHeight(24)
            btn.clicked.connect(lambda _, s=sql: self._insert_template(s))
            tpl_row.addWidget(btn)
        tpl_row.addStretch()
        ed_layout.addLayout(tpl_row)

        self._editor = SQLEditor()
        self._highlighter = SQLHighlighter(self._editor.document())
        ed_layout.addWidget(self._editor)
        splitter.addWidget(editor_widget)

        # Results tab
        result_widget = QWidget()
        res_layout = QVBoxLayout(result_widget)
        res_layout.setContentsMargins(0, 4, 0, 0)

        # result status + map button row
        status_row = QHBoxLayout()
        self._result_label = QLabel("")
        status_row.addWidget(self._result_label, 1)

        self._map_banner = QFrame()
        self._map_banner.setStyleSheet(
            "QFrame{background:#1a3a1a;border:1px solid #2e7d32;"
            "border-radius:4px;padding:2px;}")
        banner_lay = QHBoxLayout(self._map_banner)
        banner_lay.setContentsMargins(8, 2, 4, 2)
        self._map_banner_lbl = QLabel("🗺  Geometry column detected:")
        self._map_banner_lbl.setStyleSheet("color:#81c784;font-weight:bold;")
        self._btn_show_map = QPushButton("Show on Map")
        self._btn_show_map.setStyleSheet(
            "QPushButton{background:#2e7d32;color:#fff;border:none;"
            "border-radius:3px;padding:2px 10px;font-weight:bold;}"
            "QPushButton:hover{background:#388e3c;}"
            "QPushButton:disabled{background:#444;color:#777;}")
        self._btn_show_map.clicked.connect(self._request_show_on_map)
        banner_lay.addWidget(self._map_banner_lbl)
        banner_lay.addWidget(self._btn_show_map)
        self._map_banner.hide()
        status_row.addWidget(self._map_banner)

        res_layout.addLayout(status_row)

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
        QShortcut(QKeySequence("F6"), self, self._run_explain)

    # ── Autocomplete ──────────────────────────────────────────────────────

    def refresh_completions(self):
        if not self.db.is_connected():
            return
        if self._comp_loader and self._comp_loader.isRunning():
            return
        self._comp_loader = CompletionLoader(self.db)
        self._comp_loader.done.connect(self._editor.set_completions)
        self._comp_loader.start()

    # ── Layer context ─────────────────────────────────────────────────────

    def set_active_layer(self, schema: str, table: str):
        self._active_schema = schema
        self._active_table = table

    def _insert_template(self, sql: str):
        sql = sql.replace("{schema}", self._active_schema or "public")
        sql = sql.replace("{table}", self._active_table or "layer_name")
        self._editor.setPlainText(sql)

    # ── Query execution ───────────────────────────────────────────────────

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
        self._map_banner.hide()
        self._detected_geo_cols = []
        self._detected_attr_cols = []

        if cols and rows is not None:
            n = len(rows)
            self._result_label.setText(
                i18n.t("sql_rows_returned", n=n, ms=f"{ms:.1f}"))
            self._result_table.setRowCount(n)
            self._result_table.setColumnCount(len(cols))
            self._result_table.setHorizontalHeaderLabels(cols)

            # detect geometry columns from first non-null row
            first_vals = {}
            for row in rows:
                for c, col in enumerate(cols):
                    if row[c] is not None and col not in first_vals:
                        first_vals[col] = row[c]
                if len(first_vals) == len(cols):
                    break

            geo_cols  = []
            attr_cols = []
            for col in cols:
                sample = first_vals.get(col)
                if _is_geo_col(col, sample):
                    geo_cols.append(col)
                else:
                    attr_cols.append(col)

            for r, row in enumerate(rows):
                for c, val in enumerate(row):
                    display = str(val) if val is not None else ""
                    # truncate WKB blobs for display
                    if len(display) > 60 and cols[c] in geo_cols:
                        display = display[:30] + "…"
                    self._result_table.setItem(r, c, QTableWidgetItem(display))

            if geo_cols:
                self._detected_geo_cols  = geo_cols
                self._detected_attr_cols = attr_cols
                names = ", ".join(geo_cols)
                self._map_banner_lbl.setText(f"🗺  Geometry detected: {names}")
                self._map_banner.show()
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

    def _request_show_on_map(self):
        if not self._detected_geo_cols:
            return
        sql = self._editor.toPlainText().strip()
        if not sql:
            return
        self._btn_show_map.setEnabled(False)
        self._btn_show_map.setText("Loading…")
        label = sql[:60].replace("\n", " ")
        w = GeoQueryWorker(self.db, sql,
                           self._detected_geo_cols,
                           self._detected_attr_cols)
        w.done.connect(lambda feats, cols:
                       self._on_geo_ready(feats, cols, label))
        w.error.connect(self._on_geo_error)
        self._geo_worker = w
        w.start()

    def _on_geo_ready(self, features: list, attr_cols: list, label: str):
        self._btn_show_map.setEnabled(True)
        self._btn_show_map.setText("Show on Map")
        self.show_on_map.emit(features, attr_cols, label)

    def _on_geo_error(self, error: str):
        self._btn_show_map.setEnabled(True)
        self._btn_show_map.setText("Show on Map")
        self._result_label.setText(f"Map error: {error}")

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

    # ── EXPLAIN ───────────────────────────────────────────────────────────

    def _run_explain(self):
        if not self.db.is_connected():
            QMessageBox.warning(self, "Error", i18n.t("err_not_connected"))
            return
        sql = self._editor.toPlainText().strip()
        if not sql:
            return
        from ..dialogs.explain_dialog import ExplainDialog
        ExplainDialog(self.db, sql, self).exec()

    # ── Save / load ───────────────────────────────────────────────────────

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
