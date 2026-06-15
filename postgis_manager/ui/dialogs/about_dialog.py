"""About dialog — PyQt6."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton, QFrame, QTextEdit,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ...utils import i18n


class AboutDialog(QDialog):
    def __init__(self, parent=None, version: str = "0.1.0"):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("about_title"))
        self.setFixedWidth(480)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("PostGIS Manager")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont()
        f.setPointSize(20)
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        subtitle = QLabel("GIS GEORGIA | Giorgi Kapanadze")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addWidget(_sep())

        for key in ["about_version", "about_description",
                    "about_license", "about_github", "about_team"]:
            text = i18n.t(key)
            if key == "about_version":
                text = f"{text} {version}"
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(lbl)

        layout.addWidget(_sep())

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)


def _sep():
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    return sep
