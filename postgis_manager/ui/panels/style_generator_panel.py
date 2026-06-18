"""Thematic Style Generator — auto-classify numeric attributes → color ramps."""

from __future__ import annotations
import math
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QSpinBox, QFormLayout, QGroupBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QColorDialog, QMessageBox,
    QProgressBar,
)
from PyQt6.QtGui import QColor


# ── classification algorithms ─────────────────────────────────────────────────

def _equal_interval(values: list[float], n: int) -> list[float]:
    lo, hi = min(values), max(values)
    step = (hi - lo) / n
    return [lo + step * i for i in range(n + 1)]


def _quantile(values: list[float], n: int) -> list[float]:
    s = sorted(values)
    breaks = []
    for i in range(n + 1):
        idx = min(int(i * len(s) / n), len(s) - 1)
        breaks.append(s[idx])
    return breaks


def _natural_breaks(values: list[float], n: int) -> list[float]:
    """Jenks Natural Breaks (simplified O(n²) variant)."""
    s = sorted(values)
    m = len(s)
    if m <= n:
        return s + [s[-1]] * (n + 1 - m)

    mat_i = [[0.0] * (n + 1) for _ in range(m + 1)]
    mat_j = [[0]   * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        mat_j[i][1] = 1
        mat_i[i][1] = sum((s[k] - s[0])**2 for k in range(i))

    for c in range(2, n + 1):
        for i in range(c, m + 1):
            mat_i[i][c] = math.inf
            for t in range(c - 1, i):
                seg = s[t:i]
                mean = sum(seg) / len(seg)
                var  = sum((x - mean)**2 for x in seg)
                v = mat_i[t][c - 1] + var
                if v < mat_i[i][c]:
                    mat_i[i][c] = v
                    mat_j[i][c] = t

    breaks_idx = [m]
    k = m
    for c in range(n, 1, -1):
        k = mat_j[k][c]
        breaks_idx.insert(0, k)

    result = [s[0]] + [s[i - 1] for i in breaks_idx]
    result[-1] = s[-1]
    return result


METHODS = {
    "Natural Breaks (Jenks)": _natural_breaks,
    "Equal Interval":         _equal_interval,
    "Quantile":               _quantile,
}


def _lerp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    return QColor(
        int(c1.red()   + (c2.red()   - c1.red())   * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue()  + (c2.blue()  - c1.blue())  * t),
    )


# ── worker ────────────────────────────────────────────────────────────────────

class _ClassifyWorker(QThread):
    done  = pyqtSignal(list)   # list of class dicts
    error = pyqtSignal(str)

    def __init__(self, db, schema, table, column, method, n_classes,
                 color_lo: QColor, color_hi: QColor):
        super().__init__()
        self._db = db; self._schema = schema; self._table = table
        self._column = column; self._method = method; self._n = n_classes
        self._c_lo = color_lo; self._c_hi = color_hi

    def run(self):
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute(f"""
                SELECT "{self._column}"::float
                FROM "{self._schema}"."{self._table}"
                WHERE "{self._column}" IS NOT NULL
                LIMIT 50000
            """)
            values = [r[0] for r in cur.fetchall()]
            conn.close()
            if not values:
                self.error.emit("No numeric values found."); return
            fn = METHODS[self._method]
            breaks = fn(values, self._n)
            classes = []
            for i in range(self._n):
                t = i / max(self._n - 1, 1)
                color = _lerp_color(self._c_lo, self._c_hi, t)
                lo = breaks[i]; hi = breaks[i + 1]
                count = sum(1 for v in values if (i == 0 and lo <= v <= hi)
                            or (lo < v <= hi))
                classes.append({
                    "index": i + 1,
                    "lo": round(lo, 4), "hi": round(hi, 4),
                    "color": color.name(),
                    "count": count,
                    "label": f"{round(lo,2)} – {round(hi,2)}",
                })
            self.done.emit(classes)
        except Exception as e:
            self.error.emit(str(e))


# ── panel ─────────────────────────────────────────────────────────────────────

class StyleGeneratorPanel(QWidget):
    style_ready = pyqtSignal(dict)   # {"column":…, "classes": […], "schema":…, "table":…}

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._workers = []
        self._color_lo = QColor("#ffffcc")
        self._color_hi = QColor("#800026")
        self._classes: list = []
        self._build_ui()

    def set_db(self, db):
        self._db = db

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        sel_box = QGroupBox("Layer & attribute")
        sf = QFormLayout(sel_box)
        self._schema_combo = QComboBox(); self._schema_combo.setMinimumWidth(120)
        self._schema_combo.currentTextChanged.connect(self._on_schema)
        sf.addRow("Schema:", self._schema_combo)
        self._table_combo = QComboBox()
        self._table_combo.currentTextChanged.connect(self._on_table)
        sf.addRow("Table:", self._table_combo)
        self._col_combo = QComboBox()
        sf.addRow("Numeric column:", self._col_combo)
        root.addWidget(sel_box)

        cls_box = QGroupBox("Classification")
        cf = QFormLayout(cls_box)
        self._method_combo = QComboBox()
        for m in METHODS: self._method_combo.addItem(m)
        cf.addRow("Method:", self._method_combo)
        self._n_spin = QSpinBox(); self._n_spin.setRange(2, 20); self._n_spin.setValue(5)
        cf.addRow("Classes:", self._n_spin)

        # color pickers
        color_row = QHBoxLayout()
        self._btn_lo = QPushButton()
        self._btn_lo.setFixedWidth(60)
        self._btn_lo.setStyleSheet(f"background:{self._color_lo.name()}")
        self._btn_lo.clicked.connect(lambda: self._pick_color("lo"))
        self._btn_hi = QPushButton()
        self._btn_hi.setFixedWidth(60)
        self._btn_hi.setStyleSheet(f"background:{self._color_hi.name()}")
        self._btn_hi.clicked.connect(lambda: self._pick_color("hi"))
        color_row.addWidget(QLabel("Low:"))
        color_row.addWidget(self._btn_lo)
        color_row.addWidget(QLabel("High:"))
        color_row.addWidget(self._btn_hi)
        color_row.addStretch()
        cf.addRow("Color ramp:", color_row)
        root.addWidget(cls_box)

        self._classify_btn = QPushButton("🎨 Classify")
        self._classify_btn.setFixedHeight(30)
        self._classify_btn.clicked.connect(self._classify)
        root.addWidget(self._classify_btn)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setMaximumHeight(6)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # preview table
        self._preview = QTableWidget(0, 4)
        self._preview.setHorizontalHeaderLabels(["Class", "Range", "Count", "Color"])
        self._preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._preview.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._preview.setMaximumHeight(200)
        root.addWidget(self._preview)

        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton("✔ Apply to Map")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(self._apply_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)
        root.addStretch()

    def refresh_schemas(self):
        if not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') "
                "ORDER BY schema_name")
            schemas = [r[0] for r in cur.fetchall()]
            conn.close()
            self._schema_combo.blockSignals(True)
            self._schema_combo.clear()
            for s in schemas: self._schema_combo.addItem(s)
            self._schema_combo.blockSignals(False)
            self._on_schema(self._schema_combo.currentText())
        except Exception:
            pass

    def _on_schema(self, schema: str):
        if not schema or not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=%s ORDER BY table_name", (schema,))
            tables = [r[0] for r in cur.fetchall()]
            conn.close()
            self._table_combo.blockSignals(True)
            self._table_combo.clear()
            for t in tables: self._table_combo.addItem(t)
            self._table_combo.blockSignals(False)
            self._on_table(self._table_combo.currentText())
        except Exception:
            pass

    def _on_table(self, table: str):
        schema = self._schema_combo.currentText()
        if not table or not schema or not self._db or not self._db.is_connected():
            return
        try:
            import psycopg2
            conn = psycopg2.connect(**self._db.params)
            cur  = conn.cursor()
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s
                  AND data_type IN ('integer','bigint','smallint','numeric',
                                    'real','double precision','float')
                ORDER BY column_name
            """, (schema, table))
            cols = [r[0] for r in cur.fetchall()]
            conn.close()
            self._col_combo.clear()
            for c in cols: self._col_combo.addItem(c)
        except Exception:
            pass

    def _pick_color(self, which: str):
        initial = self._color_lo if which == "lo" else self._color_hi
        c = QColorDialog.getColor(initial, self)
        if not c.isValid(): return
        if which == "lo":
            self._color_lo = c
            self._btn_lo.setStyleSheet(f"background:{c.name()}")
        else:
            self._color_hi = c
            self._btn_hi.setStyleSheet(f"background:{c.name()}")

    def _classify(self):
        schema = self._schema_combo.currentText()
        table  = self._table_combo.currentText()
        col    = self._col_combo.currentText()
        if not all([schema, table, col]):
            QMessageBox.warning(self, "Missing input", "Select schema, table, and column.")
            return
        self._classify_btn.setEnabled(False)
        self._progress.setVisible(True)
        w = _ClassifyWorker(self._db, schema, table, col,
                            self._method_combo.currentText(),
                            self._n_spin.value(),
                            self._color_lo, self._color_hi)
        w.done.connect(self._on_classified)
        w.error.connect(lambda e: (
            QMessageBox.critical(self, "Error", e),
            self._classify_btn.setEnabled(True),
            self._progress.setVisible(False),
        ))
        w.start(); self._workers.append(w)

    def _on_classified(self, classes: list):
        self._classes = classes
        self._classify_btn.setEnabled(True)
        self._progress.setVisible(False)
        self._apply_btn.setEnabled(True)

        self._preview.setRowCount(len(classes))
        for ri, cls in enumerate(classes):
            self._preview.setItem(ri, 0, QTableWidgetItem(str(cls["index"])))
            self._preview.setItem(ri, 1, QTableWidgetItem(cls["label"]))
            self._preview.setItem(ri, 2, QTableWidgetItem(str(cls["count"])))
            color_item = QTableWidgetItem(cls["color"])
            color_item.setBackground(QColor(cls["color"]))
            self._preview.setItem(ri, 3, color_item)

    def _apply(self):
        if not self._classes: return
        style = {
            "schema": self._schema_combo.currentText(),
            "table":  self._table_combo.currentText(),
            "column": self._col_combo.currentText(),
            "method": self._method_combo.currentText(),
            "classes": self._classes,
        }
        self.style_ready.emit(style)
