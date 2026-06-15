"""Sidebar navigation widget — groups + icon/label buttons, maps to QStackedWidget."""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont


class NavSidebar(QWidget):
    """Left-side navigation bar with collapsible groups."""

    page_requested = pyqtSignal(int)   # stacked-widget index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NavSidebar")
        self.setFixedWidth(170)
        self.setSizePolicy(QSizePolicy.Policy.Fixed,
                           QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(4, 6, 4, 6)
        self._layout.setSpacing(1)
        self._layout.addStretch()

        scroll.setWidget(self._container)
        outer.addWidget(scroll)

        self._buttons: list[QPushButton] = []
        self._current_idx: int = -1

    # ── Public API ────────────────────────────────────────────────────────────

    def add_group(self, label: str):
        """Non-clickable category header."""
        lbl = QLabel(label.upper())
        lbl.setObjectName("NavGroupLabel")
        lbl.setFixedHeight(22)
        lbl.setContentsMargins(8, 4, 4, 0)
        lbl.setFont(_group_font())
        self._layout.insertWidget(self._layout.count() - 1, lbl)

    def add_item(self, icon: str, label: str, page_index: int) -> QPushButton:
        """Clickable navigation item."""
        btn = QPushButton(f"  {icon}  {label}")
        btn.setObjectName("NavItem")
        btn.setCheckable(True)
        btn.setFixedHeight(32)
        btn.setFont(_item_font())
        btn.clicked.connect(lambda _, idx=page_index: self._select(idx))
        btn.setProperty("page_index", page_index)
        self._buttons.append(btn)
        self._layout.insertWidget(self._layout.count() - 1, btn)
        return btn

    def select_page(self, page_index: int):
        """Programmatically activate a nav item."""
        self._select(page_index)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _select(self, idx: int):
        self._current_idx = idx
        for btn in self._buttons:
            btn.setChecked(btn.property("page_index") == idx)
        self.page_requested.emit(idx)


def _group_font() -> QFont:
    f = QFont()
    f.setPointSize(9)
    f.setBold(True)
    return f


def _item_font() -> QFont:
    f = QFont()
    f.setPointSize(10)
    return f


