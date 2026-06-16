"""Log panel with color-coded levels."""

from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QFileDialog,
)
from PyQt6.QtGui import QTextCharFormat, QColor, QFont, QTextCursor

LEVEL_COLORS = {
    "info":    "#58A6FF",
    "success": "#3FB950",
    "warn":    "#D29922",
    "error":   "#FF7B72",
    "dim":     "#888899",
    "sql":     "#D2A8FF",
}
LEVEL_ICONS = {
    "info": "ℹ", "success": "✔", "warn": "⚠",
    "error": "✖", "dim": "·", "sql": "⌗",
}


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self._toggle_btn = QPushButton("▼  Log")
        self._toggle_btn.setFixedHeight(22)
        self._toggle_btn.setStyleSheet(
            "QPushButton { text-align:left; font-weight:bold; "
            "border:none; background:transparent; padding-left:4px; }")
        self._toggle_btn.clicked.connect(self._toggle_log)
        header.addWidget(self._toggle_btn)
        header.addStretch()

        clear_btn = QPushButton("🧹 Clear")
        clear_btn.setFixedHeight(22)
        clear_btn.clicked.connect(self.clear)
        header.addWidget(clear_btn)
        save_btn = QPushButton("💾 Save")
        save_btn.setFixedHeight(22)
        save_btn.clicked.connect(self._save_log)
        header.addWidget(save_btn)
        layout.addLayout(header)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 11))
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._log)
        self._collapsed = False

    def _toggle_log(self):
        self._collapsed = not self._collapsed
        self._log.setVisible(not self._collapsed)
        self._toggle_btn.setText(
            "▶  Log" if self._collapsed else "▼  Log")
        # tell the splitter to shrink/restore
        splitter = self.parent()
        from PyQt6.QtWidgets import QSplitter
        while splitter and not isinstance(splitter, QSplitter):
            splitter = splitter.parent()
        if splitter:
            if self._collapsed:
                self._saved_sizes = splitter.sizes()
                total = sum(self._saved_sizes)
                splitter.setSizes([total - 28, 28])
            else:
                splitter.setSizes(getattr(self, "_saved_sizes", [620, 180]))

    def append(self, message: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        icon = LEVEL_ICONS.get(level, "·")
        color = LEVEL_COLORS.get(level, "#AAAAAA")
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(f"[{ts}] {icon}  {message}\n", fmt)
        self._log.ensureCursorVisible()

    def clear(self):
        self._log.clear()

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Log", "postgis_manager.log", "Log (*.log *.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._log.toPlainText())
