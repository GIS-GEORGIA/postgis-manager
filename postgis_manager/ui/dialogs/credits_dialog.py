"""Credits dialog — renders CREDITS.md."""

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
from ...utils import i18n


class CreditsDialog(QDialog):
    def __init__(self, parent=None, credits_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("action_credits"))
        self.resize(750, 620)
        layout = QVBoxLayout(self)
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        try:
            with open(credits_path, encoding="utf-8") as f:
                self._text.setMarkdown(f.read())
        except Exception:
            self._text.setPlainText("CREDITS.md not found.")
        layout.addWidget(self._text)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
