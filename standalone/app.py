"""Standalone application entry — creates QApplication and launches MainWindow."""

import sys
import os


def main():
    # Add project root to path
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont

    # High-DPI support
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)
    app.setApplicationName("PostGIS Manager")
    app.setOrganizationName("GIS-GEORGIA")
    app.setOrganizationDomain("gis-georgia.ge")
    app.setApplicationVersion("0.1.0")

    # Apply initial theme and font (config loads on import)
    from postgis_manager.utils import config, theme, i18n
    i18n.load(config.get("language", "en"))
    theme.set_theme(config.get("theme", "light"))
    fam = config.get("font_family", "Segoe UI")
    size = config.get("font_size", 13)
    app.setFont(QFont(fam, size))
    app.setStyleSheet(theme.build_qss())

    from postgis_manager.ui.main_window import MainWindow
    win = MainWindow(embedded=False, iface=None)
    win.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
