"""Log panel — filter chips, search, wrap toggle, status bar.
Uses the app theme engine so it works in both Light and Dark mode.
"""

from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QFileDialog, QLineEdit, QLabel,
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

# Keys into theme.colors() for fg color of each level
_LEVEL_THEME_KEY = {
    "info":    "info",
    "success": "success",
    "warn":    "warn",
    "error":   "error",
    "dim":     "fg_dim",
    "sql":     "accent",
}

# Chip accent colors — fixed, readable on any background
_CHIP_COLORS = {
    "all":     ("#888899", "#888899"),   # (normal-fg, active-fg)
    "info":    ("#01579B", "#1565C0"),
    "success": ("#2E7D32", "#388E3C"),
    "warn":    ("#E65100", "#F57C00"),
    "error":   ("#C62828", "#D32F2F"),
    "sql":     ("#4527A0", "#5E35B1"),
}
# Dark-mode overrides for chip fg
_CHIP_COLORS_DARK = {
    "all":     ("#aaaaaa", "#cccccc"),
    "info":    ("#58A6FF", "#90CAF9"),
    "success": ("#3FB950", "#81C784"),
    "warn":    ("#D29922", "#FFB74D"),
    "error":   ("#FF7B72", "#EF9A9A"),
    "sql":     ("#A78BFA", "#CE93D8"),
}

_TOOLBAR_H  = 28
_FILTERBAR_H = 26
_STATUSBAR_H = 22


# ── Chip widget ────────────────────────────────────────────────────────────────

class _Chip(QPushButton):
    def __init__(self, label: str, level: str, parent=None):
        super().__init__(label, parent)
        self._level = level
        self._active = False
        self.setFixedHeight(18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh()

    def set_active(self, v: bool):
        self._active = v
        self._refresh()

    def _refresh(self):
        dark = _theme.current() == "dark"
        colors = _CHIP_COLORS_DARK if dark else _CHIP_COLORS
        normal_fg, active_fg = colors.get(self._level, colors["all"])
        bg      = _theme.c("bg_card")
        bg_act  = _theme.c("selection_bg")
        fg      = active_fg if self._active else normal_fg
        bg_use  = bg_act if self._active else bg
        bw      = "2px" if self._active else "1px"
        self.setStyleSheet(
            f"QPushButton{{"
            f"color:{fg};background:{bg_use};"
            f"border:{bw} solid {fg};"
            f"border-radius:8px;font-size:11px;padding:0 7px;"
            f"font-weight:{'600' if self._active else '400'};}}"
            f"QPushButton:hover{{background:{_theme.c('selection_bg')};}}"
        )


# ── Log Panel ──────────────────────────────────────────────────────────────────

class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[tuple[str, str, str]] = []   # (ts, level, msg)
        self._active_level = "all"
        self._search = ""
        self._wrap = False
        self._collapsed = False
        self._build_ui()
        _theme.on_theme_change(self._on_theme)

    # ── Build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar
        self._toolbar = self._make_toolbar()
        root.addWidget(self._toolbar)

        # Thin divider
        self._div1 = self._hline()
        root.addWidget(self._div1)

        # Filter row
        self._filter_row = self._make_filter_row()
        root.addWidget(self._filter_row)

        # Thin divider
        self._div2 = self._hline()
        root.addWidget(self._div2)

        # Log text area
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 10))
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._log.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._log, 1)

        # Thin divider
        self._div3 = self._hline()
        root.addWidget(self._div3)

        # Status bar
        self._status_bar = self._make_status_bar()
        root.addWidget(self._status_bar)

        self._apply_theme()

    def _make_toolbar(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(_TOOLBAR_H)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(4)

        self._title_lbl = QLabel("▼  Log")
        self._title_lbl.setStyleSheet("font-weight:600;font-size:12px;")
        lay.addWidget(self._title_lbl)

        self._cnt_badge = QLabel("0")
        self._cnt_badge.setFixedHeight(16)
        self._cnt_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cnt_badge.setMinimumWidth(24)
        lay.addWidget(self._cnt_badge)

        lay.addStretch()

        self._wrap_btn = self._small_btn("⇌ Wrap", self._toggle_wrap)
        lay.addWidget(self._wrap_btn)

        lay.addWidget(self._vsep())

        lay.addWidget(self._small_btn("🧹 Clear", self.clear))
        lay.addWidget(self._small_btn("💾 Save",  self._save_log))

        lay.addWidget(self._vsep())

        self._collapse_btn = self._small_btn("▼", self._toggle_collapse)
        self._collapse_btn.setFixedWidth(28)
        lay.addWidget(self._collapse_btn)

        return w

    def _make_filter_row(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(_FILTERBAR_H)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(4)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Filter messages…")
        self._search_box.setFixedHeight(20)
        self._search_box.textChanged.connect(self._on_search)
        lay.addWidget(self._search_box, 1)

        lay.addWidget(self._vsep())

        self._chips: dict[str, _Chip] = {}
        for level, label in [
            ("all", "All"), ("info", "info"), ("success", "ok"),
            ("warn", "warn"), ("error", "err"), ("sql", "sql"),
        ]:
            chip = _Chip(label, level)
            chip.clicked.connect(lambda _, lv=level: self._set_level(lv))
            self._chips[level] = chip
            lay.addWidget(chip)

        self._chips["all"].set_active(True)
        return w

    def _make_status_bar(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(_STATUSBAR_H)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(6)

        self._dot = QLabel("●")
        self._dot.setFixedWidth(12)
        lay.addWidget(self._dot)

        self._last_lbl = QLabel("")
        self._last_lbl.setStyleSheet("font-size:11px;")
        self._last_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._last_lbl)

        self._count_lbl = QLabel("0 / 0")
        self._count_lbl.setStyleSheet("font-size:11px;")
        lay.addWidget(self._count_lbl)

        lay.addWidget(self._vsep())

        bottom_btn = QPushButton("↓ bottom")
        bottom_btn.setFixedHeight(16)
        bottom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        bottom_btn.clicked.connect(self._scroll_bottom)
        lay.addWidget(bottom_btn)

        return w

    # ── Theme ──────────────────────────────────────────────────────────────

    def _apply_theme(self):
        co = _theme.colors()

        # Toolbar + filter row
        tb_bg = co["bg_toolbar"]
        border = co["border"]
        for w in (self._toolbar, self._filter_row, self._status_bar):
            w.setStyleSheet(f"background:{tb_bg};")

        # Dividers
        for d in (self._div1, self._div2, self._div3):
            d.setStyleSheet(f"background:{border};")

        # Badge
        self._cnt_badge.setStyleSheet(
            f"background:{co['bg_card']};color:{co['fg_secondary']};"
            f"border:1px solid {border};border-radius:7px;"
            f"padding:0 5px;font-size:11px;")

        # Buttons
        btn_style = (
            f"QPushButton{{background:transparent;color:{co['fg_secondary']};"
            f"font-size:11px;border:1px solid {border};"
            f"border-radius:4px;padding:0 6px;}}"
            f"QPushButton:hover{{background:{co['selection_bg']};"
            f"color:{co['fg']};}}")
        for btn in (self._wrap_btn, self._collapse_btn):
            btn.setStyleSheet(btn_style)
        for btn in self._toolbar.findChildren(QPushButton):
            if btn not in (self._wrap_btn, self._collapse_btn):
                btn.setStyleSheet(btn_style)

        # Search box
        self._search_box.setStyleSheet(
            f"QLineEdit{{background:{co['bg_input']};color:{co['fg']};"
            f"border:1px solid {border};border-radius:4px;"
            f"padding:0 6px;font-size:11px;font-family:'Courier New';}}"
            f"QLineEdit:focus{{border-color:{co['border_focus']};}}"
        )

        # Bottom button
        bb_style = (
            f"QPushButton{{background:transparent;color:{co['fg_dim']};"
            f"font-size:11px;border:1px solid {border};"
            f"border-radius:3px;padding:0 5px;}}"
            f"QPushButton:hover{{color:{co['fg']};border-color:{co['fg_secondary']};}}")
        for btn in self._status_bar.findChildren(QPushButton):
            btn.setStyleSheet(bb_style)

        # Status dot + labels
        self._dot.setStyleSheet(f"color:{co['success']};font-size:9px;")
        self._last_lbl.setStyleSheet(
            f"font-size:11px;color:{co['fg_dim']};")
        self._count_lbl.setStyleSheet(
            f"font-size:11px;color:{co['fg_secondary']};")
        self._title_lbl.setStyleSheet(
            f"font-weight:600;font-size:12px;color:{co['fg']};")

        # Log area
        self._log.setStyleSheet(
            f"QTextEdit{{background:{co['bg_log']};color:{co['fg']};"
            f"border:none;"
            f"selection-background-color:{co['selection_bg']};"
            f"selection-color:{co['selection_fg']};}}"
            f"QScrollBar:vertical{{background:{co['bg_card']};"
            f"width:8px;border:none;}}"
            f"QScrollBar::handle:vertical{{background:{co['scrollbar']};"
            f"border-radius:4px;min-height:20px;}}"
            f"QScrollBar:horizontal{{background:{co['bg_card']};"
            f"height:8px;border:none;}}"
            f"QScrollBar::handle:horizontal{{background:{co['scrollbar']};"
            f"border-radius:4px;min-width:20px;}}"
        )

        # Refresh chips
        for chip in self._chips.values():
            chip._refresh()

        # Wrap button highlight if active
        if self._wrap:
            self._wrap_btn.setStyleSheet(
                btn_style.replace(
                    f"color:{co['fg_secondary']}", f"color:{co['accent']}"))

    def _on_theme(self, _name: str):
        self._apply_theme()

    # ── Small helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _small_btn(label: str, slot) -> QPushButton:
        btn = QPushButton(label)
        btn.setFixedHeight(22)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _vsep() -> QWidget:
        sep = QWidget()
        sep.setFixedSize(1, 14)
        return sep

    @staticmethod
    def _hline() -> QWidget:
        line = QWidget()
        line.setFixedHeight(1)
        return line

    # ── Actions ────────────────────────────────────────────────────────────

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        arrow = "▶" if self._collapsed else "▼"
        self._collapse_btn.setText(arrow)
        self._title_lbl.setText(f"{arrow}  Log")

        # Hide/show everything below the toolbar
        for w in (self._div1, self._filter_row,
                  self._div2, self._log,
                  self._div3, self._status_bar):
            w.setVisible(not self._collapsed)

        # Adjust splitter
        from PyQt6.QtWidgets import QSplitter
        splitter = self.parent()
        while splitter and not isinstance(splitter, QSplitter):
            splitter = splitter.parent()

        if splitter:
            if self._collapsed:
                self._saved_sizes = splitter.sizes()
                total = sum(self._saved_sizes)
                self.setMinimumHeight(_TOOLBAR_H + 2)
                splitter.setSizes([total - _TOOLBAR_H - 2, _TOOLBAR_H + 2])
            else:
                self.setMinimumHeight(80)
                splitter.setSizes(
                    getattr(self, "_saved_sizes", [620, 180]))

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

        co = _theme.colors()
        if level == "error":
            self._dot.setStyleSheet(f"color:{co['error']};font-size:9px;")

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

        icon = LEVEL_ICONS.get(level, "·")
        fmt  = QTextCharFormat()
        fmt.setForeground(QColor(fg_color))
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
            1 for _, lv, msg in self._entries
            if self._matches(lv, msg))
        self._count_lbl.setText(f"{visible} / {len(self._entries)}")

    def clear(self):
        self._entries.clear()
        self._log.clear()
        self._cnt_badge.setText("0")
        self._count_lbl.setText("0 / 0")
        self._last_lbl.setText("")
        co = _theme.colors()
        self._dot.setStyleSheet(f"color:{co['success']};font-size:9px;")

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Log", "postgis_manager.log", "Log (*.log *.txt)")
        if not path:
            return
        lines = [
            f"[{ts}] {LEVEL_ICONS.get(lv, '·')}  {msg}"
            for ts, lv, msg in self._entries
            if self._matches(lv, msg)
        ]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
