"""Standalone application entry — creates QApplication and launches MainWindow."""

import sys
import os


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QFont

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName("PostGIS Manager")
    app.setOrganizationName("GIS-GEORGIA")
    app.setOrganizationDomain("gis-georgia.ge")
    app.setApplicationVersion("1.0.0")

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

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
