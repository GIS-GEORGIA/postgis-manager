"""About dialog."""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from ...utils import i18n


class AboutDialog(QDialog):
    def __init__(self, parent=None, version: str = "0.1.0"):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("about_title"))
        self.setFixedWidth(480)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("PostGIS Manager")
        title.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSize(20)
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        subtitle = QLabel("GIS-GEORGIA / GGTC GIS Team")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        layout.addWidget(_sep())

        for key in ["about_version", "about_description",
                    "about_license", "about_github", "about_team"]:
            text = i18n.t(key)
            if key == "about_version":
                text = f"{text} {version}"
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(lbl)

        layout.addWidget(_sep())

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignCenter)


class CreditsDialog(QDialog):
    def __init__(self, parent=None, credits_path: str = ""):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("action_credits"))
        self.resize(700, 600)
        from PyQt5.QtWidgets import QTextEdit, QVBoxLayout
        layout = QVBoxLayout(self)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        try:
            with open(credits_path, encoding="utf-8") as f:
                content = f.read()
            text_edit.setMarkdown(content)
        except Exception:
            text_edit.setPlainText("CREDITS.md not found.")
        layout.addWidget(text_edit)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


def _sep():
    from PyQt5.QtWidgets import QFrame
    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setFrameShadow(QFrame.Sunken)
    return sep
