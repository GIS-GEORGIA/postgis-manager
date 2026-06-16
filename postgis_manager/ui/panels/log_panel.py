"""Log panel — filter chips, search, wrap toggle, status bar.
Uses the app theme engine; one root stylesheet with #objectName selectors
so child widget layouts are never broken by cascaded background rules.
"""

from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QFileDialog, QLineEdit, QLabel, QFrame,
    QSizePolicy,
)
from PyQt6.QtGui import QTextCharFormat, QColor, QFont, QTextCursor
from PyQt6.QtCore import Qt

from ...utils import theme as _theme

# ── Level metadata ─────────────────────────────────────────────────────────────

LEVEL_ICONS = {
    "info": "ℹ", "success": "✔", "warn": "⚠",
    "error": "✖", "dim": "·", "sql": "⌗",
}

_LEVEL_THEME_KEY = {
    "info":    "info",
    "success": "success",
    "warn":    "warn",
    "error":   "error",
    "dim":     "fg_dim",
    "sql":     "accent",
}

# Chip accent colors (light / dark) per level
_CHIP_FG = {
    "light": {
        "all": "#555577", "info": "#01579B", "success": "#2E7D32",
        "warn": "#E65100", "error": "#C62828", "sql": "#4527A0",
    },
    "dark": {
        "all": "#aaaacc", "info": "#58A6FF", "success": "#3FB950",
        "warn": "#D29922", "error": "#FF7B72", "sql": "#A78BFA",
    },
}

_H_TOOLBAR   = 32
_H_FILTERBAR = 36
_H_STATUSBAR = 26


# ── Chip ──────────────────────────────────────────────────────────────────────

class _Chip(QPushButton):
    def __init__(self, label: str, level: str, parent=None):
        super().__init__(label, parent)
        self._level = level
        self._active = False
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_style()

    def set_active(self, v: bool):
        self._active = v
        self.refresh_style()

    def refresh_style(self):
        mode = _theme.current()
        fg      = _CHIP_FG[mode].get(self._level, _CHIP_FG[mode]["all"])
        bg_act  = _theme.c("selection_bg")
        bg_norm = "transparent"
        bg      = bg_act if self._active else bg_norm
        bw      = "2px" if self._active else "1px"
        fw      = "600"  if self._active else "400"
        self.setStyleSheet(
            f"QPushButton{{color:{fg};background:{bg};"
            f"border:{bw} solid {fg};"
            f"border-radius:8px;font-size:11px;padding:0 7px;font-weight:{fw};}}"
            f"QPushButton:hover{{background:{_theme.c('selection_bg')};}}"
        )


# ── Log Panel ─────────────────────────────────────────────────────────────────

class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[tuple[str, str, str]] = []
        self._active_level = "all"
        self._search = ""
        self._wrap = False
        self._build_ui()
        _theme.on_theme_change(self._on_theme)

    # ── Build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Toolbar ────────────────────────────────────────────────────────
        self._toolbar = QWidget(self)
        self._toolbar.setObjectName("logToolbar")
        self._toolbar.setFixedHeight(_H_TOOLBAR)
        tb = QHBoxLayout(self._toolbar)
        tb.setContentsMargins(8, 4, 8, 4)
        tb.setSpacing(6)

        self._title_lbl = QLabel("Log")
        self._title_lbl.setObjectName("logTitle")
        tb.addWidget(self._title_lbl)

        self._cnt_badge = QLabel("0")
        self._cnt_badge.setObjectName("logBadge")
        self._cnt_badge.setFixedHeight(16)
        self._cnt_badge.setMinimumWidth(24)
        self._cnt_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(self._cnt_badge)

        tb.addStretch()

        self._wrap_btn = self._mkbtn("⇌ Wrap", self._toggle_wrap, "logBtn")
        tb.addWidget(self._wrap_btn)

        tb.addWidget(self._vsep())
        tb.addWidget(self._mkbtn("🧹 Clear", self.clear, "logBtn"))
        tb.addWidget(self._mkbtn("💾 Save",  self._save_log, "logBtn"))

        root.addWidget(self._toolbar)

        # divider
        self._div1 = self._hline()
        root.addWidget(self._div1)

        # ── Filter row ─────────────────────────────────────────────────────
        self._filter_row = QWidget(self)
        self._filter_row.setObjectName("logFilterBar")
        self._filter_row.setFixedHeight(_H_FILTERBAR)
        fr = QHBoxLayout(self._filter_row)
        fr.setContentsMargins(8, 6, 8, 6)
        fr.setSpacing(6)

        self._search_box = QLineEdit()
        self._search_box.setObjectName("logSearch")
        self._search_box.setPlaceholderText("Filter messages…")
        self._search_box.setMinimumHeight(22)
        self._search_box.textChanged.connect(self._on_search)
        fr.addWidget(self._search_box, 1)

        fr.addWidget(self._vsep())

        self._chips: dict[str, _Chip] = {}
        for level, label in [
            ("all","All"), ("info","info"), ("success","ok"),
            ("warn","warn"), ("error","err"), ("sql","sql"),
        ]:
            c = _Chip(label, level, self)
            c.clicked.connect(lambda _, lv=level: self._set_level(lv))
            self._chips[level] = c
            fr.addWidget(c)

        self._chips["all"].set_active(True)
        root.addWidget(self._filter_row)

        # divider
        self._div2 = self._hline()
        root.addWidget(self._div2)

        # ── Log area ───────────────────────────────────────────────────────
        self._log = QTextEdit(self)
        self._log.setObjectName("logText")
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 10))
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log.setMinimumHeight(30)
        self._log.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._log, 1)

        # divider
        self._div3 = self._hline()
        root.addWidget(self._div3)

        # ── Status bar ─────────────────────────────────────────────────────
        self._status_bar = QWidget(self)
        self._status_bar.setObjectName("logStatusBar")
        self._status_bar.setFixedHeight(_H_STATUSBAR)
        sb = QHBoxLayout(self._status_bar)
        sb.setContentsMargins(8, 3, 8, 3)
        sb.setSpacing(6)

        self._dot = QLabel("●")
        self._dot.setObjectName("logDot")
        self._dot.setFixedWidth(12)
        sb.addWidget(self._dot)

        self._last_lbl = QLabel("")
        self._last_lbl.setObjectName("logLastMsg")
        self._last_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sb.addWidget(self._last_lbl)

        self._count_lbl = QLabel("0 / 0")
        self._count_lbl.setObjectName("logCount")
        sb.addWidget(self._count_lbl)

        sb.addWidget(self._vsep())

        self._bottom_btn = self._mkbtn("↓ bottom", self._scroll_bottom, "logBtn")
        self._bottom_btn.setFixedHeight(16)
        sb.addWidget(self._bottom_btn)

        root.addWidget(self._status_bar)

        self._apply_theme()

    # ── Theme ──────────────────────────────────────────────────────────────

    def _apply_theme(self):
        co = _theme.colors()
        bg   = co["bg_toolbar"]
        bgL  = co["bg_log"]
        fg   = co["fg"]
        fgS  = co["fg_secondary"]
        fgD  = co["fg_dim"]
        bdr  = co["border"]
        bdrF = co["border_focus"]
        bgI  = co["bg_input"]
        bgC  = co["bg_card"]
        sel  = co["selection_bg"]
        selF = co["selection_fg"]
        scr  = co["scrollbar"]
        ok   = co["success"]
        err  = co["error"]

        # One stylesheet on root — #objectName selectors avoid cascade issues
        self.setStyleSheet(f"""
QWidget#logToolbar, QWidget#logFilterBar, QWidget#logStatusBar {{
    background: {bg};
}}
QLabel#logTitle {{
    font-weight: 600;
    font-size: 12px;
    color: {fg};
}}
QLabel#logBadge {{
    background: {bgC};
    color: {fgS};
    border: 1px solid {bdr};
    border-radius: 7px;
    padding: 0 5px;
    font-size: 11px;
}}
QLabel#logLastMsg {{
    font-size: 11px;
    color: {fgD};
}}
QLabel#logCount {{
    font-size: 11px;
    color: {fgS};
}}
QLabel#logDot {{
    color: {ok};
    font-size: 9px;
}}
QPushButton#logBtn {{
    background: transparent;
    color: {fgS};
    font-size: 11px;
    border: 1px solid {bdr};
    border-radius: 4px;
    padding: 0 6px;
    height: 20px;
}}
QPushButton#logBtn:hover {{
    background: {sel};
    color: {fg};
    border-color: {fgS};
}}
QLineEdit#logSearch {{
    background: {bgI};
    color: {fg};
    border: 1px solid {bdr};
    border-radius: 4px;
    padding: 0 6px;
    font-size: 11px;
    font-family: "Courier New";
}}
QLineEdit#logSearch:focus {{
    border-color: {bdrF};
}}
QTextEdit#logText {{
    background: {bgL};
    color: {fg};
    border: none;
    selection-background-color: {sel};
    selection-color: {selF};
}}
QScrollBar:vertical {{
    background: {bgC};
    width: 8px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {scr};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {bgC};
    height: 8px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {scr};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QFrame#logDivider {{
    background: {bdr};
}}
""")

        # Wrap button accent when active
        if self._wrap:
            self._wrap_btn.setStyleSheet(
                f"QPushButton#logBtn{{background:transparent;"
                f"color:{co['accent']};font-size:11px;"
                f"border:1px solid {co['accent']};"
                f"border-radius:4px;padding:0 6px;height:20px;}}"
                f"QPushButton#logBtn:hover{{background:{sel};}}"
            )

        # Error dot override
        has_err = any(lv == "error" for _, lv, _ in self._entries)
        dot_color = err if has_err else ok
        self._dot.setStyleSheet(
            f"QLabel#logDot{{color:{dot_color};font-size:9px;}}")

        # Refresh chips
        for chip in self._chips.values():
            chip.refresh_style()

    def _on_theme(self, _: str):
        self._apply_theme()

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _mkbtn(label: str, slot, obj_name: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setObjectName(obj_name)
        btn.setFixedHeight(22)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _vsep() -> QWidget:
        w = QWidget()
        w.setFixedSize(1, 14)
        return w

    @staticmethod
    def _hline() -> QFrame:
        f = QFrame()
        f.setObjectName("logDivider")
        f.setFrameShape(QFrame.Shape.NoFrame)
        f.setFixedHeight(1)
        return f

    # ── Actions ────────────────────────────────────────────────────────────

    def _toggle_wrap(self):
        self._wrap = not self._wrap
        mode = (QTextEdit.LineWrapMode.WidgetWidth
                if self._wrap else QTextEdit.LineWrapMode.NoWrap)
        self._log.setLineWrapMode(mode)
        self._apply_theme()

    def _set_level(self, level: str):
        self._active_level = level
        for lv, chip in self._chips.items():
            chip.set_active(lv == level)
        self._rebuild_display()

    def _on_search(self, text: str):
        self._search = text.lower()
        self._rebuild_display()

    def _scroll_bottom(self):
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Core ───────────────────────────────────────────────────────────────

    def append(self, message: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self._entries.append((ts, level, message))
        self._cnt_badge.setText(str(len(self._entries)))

        if level == "error":
            co = _theme.colors()
            self._dot.setStyleSheet(
                f"QLabel#logDot{{color:{co['error']};font-size:9px;}}")

        preview = message[:70] + ("…" if len(message) > 70 else "")
        self._last_lbl.setText(preview)

        if self._matches(level, message):
            self._write_line(ts, level, message)
            self._update_count()

    def _matches(self, level: str, msg: str) -> bool:
        if self._active_level != "all" and level != self._active_level:
            return False
        if self._search and self._search not in msg.lower():
            return False
        return True

    def _write_line(self, ts: str, level: str, msg: str):
        co = _theme.colors()
        theme_key = _LEVEL_THEME_KEY.get(level, "fg_secondary")
        fg_color  = co.get(theme_key, co["fg"])

        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        dim = QTextCharFormat()
        dim.setForeground(QColor(co["fg_dim"]))
        cursor.insertText(f"[{ts}] ", dim)

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(fg_color))
        icon = LEVEL_ICONS.get(level, "·")
        cursor.insertText(f"{icon}  {msg}\n", fmt)

        self._log.ensureCursorVisible()

    def _rebuild_display(self):
        self._log.clear()
        for ts, level, msg in self._entries:
            if self._matches(level, msg):
                self._write_line(ts, level, msg)
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
        co = _theme.colors()
        self._dot.setStyleSheet(
            f"QLabel#logDot{{color:{co['success']};font-size:9px;}}")

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Log", "postgis_manager.log", "Log (*.log *.txt)")
        if not path:
            return
        lines = [
            f"[{ts}] {LEVEL_ICONS.get(lv,'·')}  {msg}"
            for ts, lv, msg in self._entries
            if self._matches(lv, msg)
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
