"""Credits dialog — PyQt6."""

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton
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
        except FileNotFoundError:
            self._text.setMarkdown(
                "**CREDITS.md not found.**\n\n"
                f"Expected path: `{credits_path}`\n\n"
                "See the full credits at: "
                "https://github.com/GIS-GEORGIA/postgis-manager/blob/master/CREDITS.md"
            )
        except Exception as e:
            self._text.setPlainText(f"Could not load CREDITS.md: {e}")
        layout.addWidget(self._text)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
