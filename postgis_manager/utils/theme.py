"""Theme engine — Light / Dark QSS stylesheets with runtime switching."""

THEMES = {
    "light": {
        "name": "Light",
        "accent":         "#1565C0",
        "accent_hover":   "#1976D2",
        "accent_text":    "#FFFFFF",
        "bg":             "#F5F7FA",
        "bg_card":        "#FFFFFF",
        "bg_sidebar":     "#EAECF0",
        "bg_toolbar":     "#FFFFFF",
        "bg_input":       "#FFFFFF",
        "bg_tree":        "#FFFFFF",
        "bg_log":         "#FAFAFA",
        "fg":             "#1A1A2E",
        "fg_secondary":   "#555577",
        "fg_dim":         "#888899",
        "border":         "#D0D5DD",
        "border_focus":   "#1565C0",
        "selection_bg":   "#BBDEFB",
        "selection_fg":   "#0D47A1",
        "error":          "#C62828",
        "warn":           "#E65100",
        "success":        "#2E7D32",
        "info":           "#01579B",
        "row_alt":        "#F0F4FF",
        "sash":           "#C0C4CC",
        "scrollbar":      "#C0C4CC",
    },
    "dark": {
        "name": "Dark",
        "accent":         "#42A5F5",
        "accent_hover":   "#64B5F6",
        "accent_text":    "#0D1117",
        "bg":             "#0D1117",
        "bg_card":        "#161B22",
        "bg_sidebar":     "#13191F",
        "bg_toolbar":     "#161B22",
        "bg_input":       "#1C2433",
        "bg_tree":        "#161B22",
        "bg_log":         "#0D1117",
        "fg":             "#E6EDF3",
        "fg_secondary":   "#8B949E",
        "fg_dim":         "#484F58",
        "border":         "#30363D",
        "border_focus":   "#58A6FF",
        "selection_bg":   "#1F3558",
        "selection_fg":   "#58A6FF",
        "error":          "#FF7B72",
        "warn":           "#D29922",
        "success":        "#3FB950",
        "info":           "#58A6FF",
        "row_alt":        "#1A2233",
        "sash":           "#30363D",
        "scrollbar":      "#30363D",
    },
}

_CURRENT = "light"
_CALLBACKS: list = []


def current() -> str:
    return _CURRENT


def colors() -> dict:
    return THEMES[_CURRENT]


def c(key: str) -> str:
    return THEMES[_CURRENT].get(key, "#FF00FF")


def set_theme(name: str) -> None:
    global _CURRENT
    if name not in THEMES:
        return
    _CURRENT = name
    for cb in _CALLBACKS:
        try:
            cb(name)
        except Exception:
            pass


def on_theme_change(callback) -> None:
    _CALLBACKS.append(callback)


def build_qss() -> str:
    co = colors()
    return f"""
/* ── Global ── */
QWidget {{
    background-color: {co['bg']};
    color: {co['fg']};
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background-color: {co['bg']};
}}

/* ── Toolbar ── */
QToolBar {{
    background-color: {co['bg_toolbar']};
    border-bottom: 1px solid {co['border']};
    spacing: 4px;
    padding: 4px 8px;
}}
QToolBar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 4px 10px;
    color: {co['fg']};
}}
QToolBar QToolButton:hover {{
    background-color: {co['selection_bg']};
    border-color: {co['border']};
}}
QToolBar QToolButton:pressed {{
    background-color: {co['accent']};
    color: {co['accent_text']};
}}

/* ── Menu ── */
QMenuBar {{
    background-color: {co['bg_toolbar']};
    color: {co['fg']};
    border-bottom: 1px solid {co['border']};
}}
QMenuBar::item:selected {{
    background-color: {co['selection_bg']};
    color: {co['selection_fg']};
}}
QMenu {{
    background-color: {co['bg_card']};
    border: 1px solid {co['border']};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 22px 5px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {co['accent']};
    color: {co['accent_text']};
}}
QMenu::separator {{
    height: 1px;
    background: {co['border']};
    margin: 4px 8px;
}}

/* ── Sidebar / Panels ── */
QDockWidget {{
    titlebar-close-icon: url(none);
    color: {co['fg']};
}}
QDockWidget::title {{
    background-color: {co['bg_sidebar']};
    padding: 6px 10px;
    border-bottom: 1px solid {co['border']};
    font-weight: bold;
}}

/* ── Tab Widget ── */
QTabWidget::pane {{
    border: 1px solid {co['border']};
    border-top: none;
    background-color: {co['bg_card']};
    border-radius: 0 6px 6px 6px;
}}
QTabBar::tab {{
    background-color: {co['bg_sidebar']};
    color: {co['fg_secondary']};
    border: 1px solid {co['border']};
    border-bottom: none;
    padding: 6px 16px;
    margin-right: 2px;
    border-radius: 6px 6px 0 0;
}}
QTabBar::tab:selected {{
    background-color: {co['bg_card']};
    color: {co['fg']};
    border-bottom: 2px solid {co['accent']};
    font-weight: bold;
}}
QTabBar::tab:hover:!selected {{
    background-color: {co['selection_bg']};
    color: {co['selection_fg']};
}}

/* ── Push Buttons ── */
QPushButton {{
    background-color: {co['accent']};
    color: {co['accent_text']};
    border: none;
    border-radius: 6px;
    padding: 6px 16px;
    font-weight: 600;
    min-height: 28px;
}}
QPushButton:hover {{
    background-color: {co['accent_hover']};
}}
QPushButton:pressed {{
    background-color: {co['accent']};
    padding-top: 8px;
}}
QPushButton:disabled {{
    background-color: {co['border']};
    color: {co['fg_dim']};
}}
QPushButton.secondary {{
    background-color: {co['bg_card']};
    color: {co['fg']};
    border: 1px solid {co['border']};
}}
QPushButton.secondary:hover {{
    background-color: {co['selection_bg']};
    border-color: {co['accent']};
}}
QPushButton.danger {{
    background-color: {co['error']};
    color: #FFFFFF;
}}
QPushButton.danger:hover {{
    background-color: #D32F2F;
}}

/* ── Line Edit / Spin Box ── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {co['bg_input']};
    color: {co['fg']};
    border: 1px solid {co['border']};
    border-radius: 5px;
    padding: 4px 8px;
    min-height: 26px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {co['border_focus']};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {co['bg_card']};
    selection-background-color: {co['accent']};
    selection-color: {co['accent_text']};
    border: 1px solid {co['border']};
}}

/* ── Text Edit / Plain Text ── */
QTextEdit, QPlainTextEdit {{
    background-color: {co['bg_input']};
    color: {co['fg']};
    border: 1px solid {co['border']};
    border-radius: 5px;
    padding: 4px;
    selection-background-color: {co['selection_bg']};
    selection-color: {co['selection_fg']};
}}
QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {co['border_focus']};
}}

/* ── Tree View ── */
QTreeView, QTreeWidget {{
    background-color: {co['bg_tree']};
    alternate-background-color: {co['row_alt']};
    border: 1px solid {co['border']};
    border-radius: 5px;
    show-decoration-selected: 1;
    outline: none;
}}
QTreeView::item {{
    padding: 3px 4px;
    border-radius: 3px;
}}
QTreeView::item:selected {{
    background-color: {co['accent']};
    color: {co['accent_text']};
}}
QTreeView::item:hover:!selected {{
    background-color: {co['selection_bg']};
}}
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {{
    image: url(none);
}}

/* ── Table / List View ── */
QTableView, QTableWidget, QListView, QListWidget {{
    background-color: {co['bg_tree']};
    alternate-background-color: {co['row_alt']};
    gridline-color: {co['border']};
    border: 1px solid {co['border']};
    border-radius: 5px;
    selection-background-color: {co['accent']};
    selection-color: {co['accent_text']};
    outline: none;
}}
QHeaderView::section {{
    background-color: {co['bg_sidebar']};
    color: {co['fg']};
    border: none;
    border-right: 1px solid {co['border']};
    border-bottom: 1px solid {co['border']};
    padding: 5px 8px;
    font-weight: bold;
}}
QHeaderView::section:hover {{
    background-color: {co['selection_bg']};
}}

/* ── Scrollbar ── */
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {co['bg']};
    width: 8px;
    height: 8px;
    margin: 0;
    border-radius: 4px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {co['scrollbar']};
    border-radius: 4px;
    min-height: 30px;
    min-width: 30px;
}}
QScrollBar::handle:hover {{
    background: {co['accent']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}

/* ── Splitter ── */
QSplitter::handle {{
    background-color: {co['sash']};
}}
QSplitter::handle:horizontal {{
    width: 4px;
}}
QSplitter::handle:vertical {{
    height: 4px;
}}

/* ── Group Box ── */
QGroupBox {{
    border: 1px solid {co['border']};
    border-radius: 6px;
    margin-top: 12px;
    padding: 8px;
    font-weight: bold;
    color: {co['fg_secondary']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    background-color: {co['bg']};
}}

/* ── Check Box / Radio ── */
QCheckBox, QRadioButton {{
    spacing: 6px;
    color: {co['fg']};
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {co['border']};
    border-radius: 3px;
    background-color: {co['bg_input']};
}}
QCheckBox::indicator:checked {{
    background-color: {co['accent']};
    border-color: {co['accent']};
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QRadioButton::indicator:checked {{
    background-color: {co['accent']};
    border-color: {co['accent']};
}}

/* ── Progress Bar ── */
QProgressBar {{
    border: 1px solid {co['border']};
    border-radius: 5px;
    background-color: {co['bg_input']};
    text-align: center;
    color: {co['fg']};
    height: 16px;
}}
QProgressBar::chunk {{
    background-color: {co['accent']};
    border-radius: 4px;
}}

/* ── Status Bar ── */
QStatusBar {{
    background-color: {co['bg_toolbar']};
    border-top: 1px solid {co['border']};
    color: {co['fg_secondary']};
    padding: 2px 8px;
    font-size: 12px;
}}

/* ── Tooltip ── */
QToolTip {{
    background-color: {co['bg_card']};
    color: {co['fg']};
    border: 1px solid {co['border']};
    border-radius: 4px;
    padding: 4px 8px;
}}

/* ── Frames / Cards ── */
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: {co['border']};
}}
QFrame.card {{
    background-color: {co['bg_card']};
    border: 1px solid {co['border']};
    border-radius: 8px;
    padding: 8px;
}}
"""
