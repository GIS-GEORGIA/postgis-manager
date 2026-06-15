"""Log panel with color-coded levels."""

from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QLabel, QFileDialog, QComboBox,
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
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.addWidget(QLabel("📜  Log"))
        header.addStretch()
        clear_btn = QPushButton("🧹 Clear")
        clear_btn.setFixedHeight(24)
        clear_btn.clicked.connect(self.clear)
        header.addWidget(clear_btn)
        save_btn = QPushButton("💾 Save")
        save_btn.setFixedHeight(24)
        save_btn.clicked.connect(self._save_log)
        header.addWidget(save_btn)
        layout.addLayout(header)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Courier New", 11))
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._log)

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
