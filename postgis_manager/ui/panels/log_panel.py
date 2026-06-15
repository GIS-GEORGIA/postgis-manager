"""Log panel with color-coded levels and export."""

from datetime import datetime
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QLabel, QFileDialog, QComboBox,
)
from PyQt5.QtGui import QTextCharFormat, QColor, QFont, QTextCursor
from PyQt5.QtCore import Qt

LEVEL_COLORS = {
    "info":    "#58A6FF",
    "success": "#3FB950",
    "warn":    "#D29922",
    "error":   "#FF7B72",
    "dim":     "#484F58",
    "sql":     "#D2A8FF",
}

LEVEL_ICONS = {
    "info":    "ℹ",
    "success": "✔",
    "warn":    "⚠",
    "error":   "✖",
    "dim":     "·",
    "sql":     "⌗",
}


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._min_level = "dim"
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.addWidget(QLabel("📜  Log"))

        self._level_combo = QComboBox()
        self._level_combo.addItems(["All", "Info+", "Warn+", "Error"])
        header.addWidget(self._level_combo)
        header.addStretch()

        clear_btn = QPushButton("🧹 Clear")
        clear_btn.setFixedHeight(24)
        clear_btn.setProperty("class", "secondary")
        clear_btn.clicked.connect(self.clear)
        header.addWidget(clear_btn)

        export_btn = QPushButton("💾 Save")
        export_btn.setFixedHeight(24)
        export_btn.setProperty("class", "secondary")
        export_btn.clicked.connect(self._save_log)
        header.addWidget(export_btn)

        layout.addLayout(header)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 11))
        self._log.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self._log)

    def append(self, message: str, level: str = "info"):
        ts = datetime.now().strftime("%H:%M:%S")
        icon = LEVEL_ICONS.get(level, "·")
        color = LEVEL_COLORS.get(level, "#AAAAAA")
        line = f"[{ts}] {icon}  {message}"

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(line + "\n", fmt)
        self._log.ensureCursorVisible()

    def clear(self):
        self._log.clear()

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Log", "postgis_manager.log", "Log files (*.log *.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._log.toPlainText())
