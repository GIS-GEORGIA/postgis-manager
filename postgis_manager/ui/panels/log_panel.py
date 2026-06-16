"""Log panel — color-coded levels, filter chips, search, wrap toggle, status bar."""

from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QFileDialog, QLineEdit, QLabel, QSizePolicy,
)
from PyQt6.QtGui import QTextCharFormat, QColor, QFont, QTextCursor
from PyQt6.QtCore import Qt

LEVEL_COLORS = {
    "info":    "#58A6FF",
    "success": "#3FB950",
    "warn":    "#D29922",
    "error":   "#FF7B72",
    "dim":     "#888899",
    "sql":     "#A78BFA",
}
LEVEL_ICONS = {
    "info": "ℹ", "success": "✔", "warn": "⚠",
    "error": "✖", "dim": "·", "sql": "⌗",
}

_CHIP_STYLE = {
    "all":     ("background:#3a3a4a;color:#ccc;border:1px solid #555;",
                "background:#555;color:#fff;border:1px solid #888;"),
    "info":    ("background:#0d2137;color:#58A6FF;border:1px solid #1a4a7a;",
                "background:#1a4a7a;color:#8fc8ff;border:1px solid #58A6FF;"),
    "success": ("background:#0d2215;color:#3FB950;border:1px solid #1a4a25;",
                "background:#1a4a25;color:#7de38a;border:1px solid #3FB950;"),
    "warn":    ("background:#2a1f00;color:#D29922;border:1px solid #5a4200;",
                "background:#5a4200;color:#f5c842;border:1px solid #D29922;"),
    "error":   ("background:#2a0d0d;color:#FF7B72;border:1px solid #5a1a1a;",
                "background:#5a1a1a;color:#ffaaa4;border:1px solid #FF7B72;"),
    "sql":     ("background:#1a1030;color:#A78BFA;border:1px solid #3a2070;",
                "background:#3a2070;color:#c4b0ff;border:1px solid #A78BFA;"),
}


class _Chip(QPushButton):
    def __init__(self, label: str, level: str, parent=None):
        super().__init__(label, parent)
        self._level = level
        self._active = False
        self.setFixedHeight(20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(False)
        self._apply()

    def set_active(self, v: bool):
        self._active = v
        self._apply()

    def _apply(self):
        base, active = _CHIP_STYLE.get(self._level, _CHIP_STYLE["all"])
        s = active if self._active else base
        self.setStyleSheet(
            f"QPushButton{{{s}border-radius:9px;font-size:11px;"
            f"padding:0 8px;font-weight:{'600' if self._active else '400'};}}"
            f"QPushButton:hover{{opacity:0.85;}}"
        )


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[tuple[str, str, str]] = []  # (ts, level, msg)
        self._active_level = "all"
        self._search = ""
        self._wrap = False
        self._collapsed = False
        self._build_ui()

    # ── Build ─────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setFixedHeight(30)
        toolbar.setStyleSheet("background:#1e1e2e;border-bottom:1px solid #333;")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(8, 0, 8, 0)
        tb.setSpacing(4)

        self._title_lbl = QLabel("▼  Log")
        self._title_lbl.setStyleSheet(
            "color:#ccc;font-weight:600;font-size:12px;")
        tb.addWidget(self._title_lbl)

        self._cnt_badge = QLabel("0")
        self._cnt_badge.setStyleSheet(
            "background:#2a2a3a;color:#888;border-radius:8px;"
            "padding:0 6px;font-size:11px;")
        tb.addWidget(self._cnt_badge)
        tb.addStretch()

        self._wrap_btn = self._tb_btn("⇌ Wrap", self._toggle_wrap)
        tb.addWidget(self._wrap_btn)

        self._sep(tb)

        tb.addWidget(self._tb_btn("🧹 Clear", self.clear))
        tb.addWidget(self._tb_btn("💾 Save",  self._save_log))

        self._sep(tb)

        self._collapse_btn = self._tb_btn("▼", self._toggle_collapse)
        self._collapse_btn.setFixedWidth(24)
        tb.addWidget(self._collapse_btn)

        root.addWidget(toolbar)

        # ── Filter row ────────────────────────────────────────────────────
        self._filter_row = QWidget()
        self._filter_row.setFixedHeight(28)
        self._filter_row.setStyleSheet(
            "background:#181825;border-bottom:1px solid #2a2a3a;")
        fr = QHBoxLayout(self._filter_row)
        fr.setContentsMargins(8, 0, 8, 0)
        fr.setSpacing(4)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Filter messages…")
        self._search_box.setFixedHeight(20)
        self._search_box.setStyleSheet(
            "QLineEdit{background:#2a2a3a;color:#ccc;border:1px solid #444;"
            "border-radius:4px;padding:0 6px;font-size:11px;font-family:'Courier New';}"
            "QLineEdit:focus{border-color:#58A6FF;}")
        self._search_box.textChanged.connect(self._on_search)
        fr.addWidget(self._search_box, 1)

        self._sep(fr)

        self._chips: dict[str, _Chip] = {}
        for level, label in [("all","All"),("info","info"),("success","ok"),
                              ("warn","warn"),("error","err"),("sql","sql")]:
            chip = _Chip(label, level)
            chip.clicked.connect(lambda _, lv=level: self._set_level(lv))
            self._chips[level] = chip
            fr.addWidget(chip)

        self._chips["all"].set_active(True)
        root.addWidget(self._filter_row)

        # ── Log area ──────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 10))
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log.setStyleSheet(
            "QTextEdit{background:#11111b;color:#cdd6f4;"
            "border:none;selection-background-color:#313244;}"
            "QScrollBar:vertical{background:#1e1e2e;width:8px;}"
            "QScrollBar::handle:vertical{background:#45475a;border-radius:4px;}"
            "QScrollBar:horizontal{background:#1e1e2e;height:8px;}"
            "QScrollBar::handle:horizontal{background:#45475a;border-radius:4px;}")
        root.addWidget(self._log)

        # ── Status bar ────────────────────────────────────────────────────
        self._status_bar = QWidget()
        self._status_bar.setFixedHeight(22)
        self._status_bar.setStyleSheet(
            "background:#1e1e2e;border-top:1px solid #333;")
        sb = QHBoxLayout(self._status_bar)
        sb.setContentsMargins(8, 0, 8, 0)
        sb.setSpacing(6)

        self._dot = QLabel("●")
        self._dot.setStyleSheet("color:#3FB950;font-size:9px;")
        sb.addWidget(self._dot)

        self._last_lbl = QLabel("")
        self._last_lbl.setStyleSheet("color:#666;font-size:11px;")
        self._last_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._last_lbl.setMaximumWidth(400)
        sb.addWidget(self._last_lbl)
        sb.addStretch()

        self._count_lbl = QLabel("0 / 0")
        self._count_lbl.setStyleSheet("color:#555;font-size:11px;")
        sb.addWidget(self._count_lbl)

        self._sep(sb)

        bottom_btn = QPushButton("↓ bottom")
        bottom_btn.setFixedHeight(16)
        bottom_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#555;font-size:11px;"
            "border:1px solid #333;border-radius:3px;padding:0 5px;}"
            "QPushButton:hover{color:#aaa;border-color:#666;}")
        bottom_btn.clicked.connect(
            lambda: self._log.verticalScrollBar().setValue(
                self._log.verticalScrollBar().maximum()))
        sb.addWidget(bottom_btn)

        root.addWidget(self._status_bar)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _tb_btn(label: str, slot) -> QPushButton:
        btn = QPushButton(label)
        btn.setFixedHeight(22)
        btn.setStyleSheet(
            "QPushButton{background:transparent;color:#aaa;font-size:11px;"
            "border:1px solid #333;border-radius:4px;padding:0 7px;}"
            "QPushButton:hover{color:#fff;border-color:#666;}")
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _sep(layout):
        sep = QLabel()
        sep.setFixedSize(1, 14)
        sep.setStyleSheet("background:#333;")
        layout.addWidget(sep)

    # ── Actions ───────────────────────────────────────────────────────────

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        self._filter_row.setVisible(not self._collapsed)
        self._log.setVisible(not self._collapsed)
        self._status_bar.setVisible(not self._collapsed)
        self._collapse_btn.setText("▶" if self._collapsed else "▼")
        self._title_lbl.setText(
            ("▶" if self._collapsed else "▼") + "  Log")

        from PyQt6.QtWidgets import QSplitter
        splitter = self.parent()
        while splitter and not isinstance(splitter, QSplitter):
            splitter = splitter.parent()
        if splitter:
            if self._collapsed:
                self._saved_sizes = splitter.sizes()
                total = sum(self._saved_sizes)
                splitter.setSizes([total - 30, 30])
            else:
                splitter.setSizes(getattr(self, "_saved_sizes", [620, 180]))

    def _toggle_wrap(self):
        self._wrap = not self._wrap
        mode = (QTextEdit.LineWrapMode.WidgetWidth
                if self._wrap else QTextEdit.LineWrapMode.NoWrap)
        self._log.setLineWrapMode(mode)
        self._wrap_btn.setStyleSheet(
            self._wrap_btn.styleSheet().replace(
                "color:#aaa", "color:#58A6FF" if self._wrap else "color:#aaa"))

    def _set_level(self, level: str):
        self._active_level = level
        for lv, chip in self._chips.items():
            chip.set_active(lv == level)
        self._rebuild_display()

    def _on_search(self, text: str):
        self._search = text.lower()
        self._rebuild_display()

    # ── Core ──────────────────────────────────────────────────────────────

    def append(self, message: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self._entries.append((ts, level, message))
        self._cnt_badge.setText(str(len(self._entries)))

        # update status bar dot + last message
        if level == "error":
            self._dot.setStyleSheet("color:#FF7B72;font-size:9px;")
        self._last_lbl.setText(
            message[:60] + ("…" if len(message) > 60 else ""))

        if self._matches(level, message):
            self._append_line(ts, level, message)
            self._update_count()

    def _matches(self, level: str, msg: str) -> bool:
        if self._active_level != "all" and level != self._active_level:
            return False
        if self._search and self._search not in msg.lower():
            return False
        return True

    def _append_line(self, ts: str, level: str, msg: str):
        color = LEVEL_COLORS.get(level, "#AAAAAA")
        icon  = LEVEL_ICONS.get(level, "·")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # timestamp in dim color
        dim = QTextCharFormat()
        dim.setForeground(QColor("#555577"))
        cursor.insertText(f"[{ts}] ", dim)

        cursor.insertText(f"{icon}  {msg}\n", fmt)
        self._log.ensureCursorVisible()

    def _rebuild_display(self):
        self._log.clear()
        for ts, level, msg in self._entries:
            if self._matches(level, msg):
                self._append_line(ts, level, msg)
        self._update_count()

    def _update_count(self):
        visible = sum(
            1 for _, lv, msg in self._entries if self._matches(lv, msg))
        self._count_lbl.setText(f"{visible} / {len(self._entries)}")

    def clear(self):
        self._entries.clear()
        self._log.clear()
        self._cnt_badge.setText("0")
        self._count_lbl.setText("0 / 0")
        self._last_lbl.setText("")
        self._dot.setStyleSheet("color:#3FB950;font-size:9px;")

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Log", "postgis_manager.log", "Log (*.log *.txt)")
        if path:
            lines = [
                f"[{ts}] {LEVEL_ICONS.get(lv,'·')}  {msg}"
                for ts, lv, msg in self._entries
                if self._matches(lv, msg)
            ]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
