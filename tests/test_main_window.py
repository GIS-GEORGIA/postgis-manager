"""MainWindow integration tests (headless)."""
import pytest
from postgis_manager.utils import i18n, theme


def test_main_window_creates(qapp):
    i18n.load("en")
    theme.set_theme("light")
    from postgis_manager.ui.main_window import MainWindow
    w = MainWindow()
    assert w is not None
    count = w._stack.count()
    assert count == len(w._nav._buttons)
    assert count == len(w._nav_labels)
    assert count > 0


def test_main_window_page_switch(qapp):
    from postgis_manager.ui.main_window import MainWindow
    w = MainWindow()
    for idx in range(w._stack.count()):
        w._nav.select_page(idx)
        assert w._stack.currentIndex() == idx


def test_main_window_language_switch(qapp):
    i18n.load("en")
    from postgis_manager.ui.main_window import MainWindow
    w = MainWindow()
    en_title = w.windowTitle()
    i18n.load("ka")
    ka_title = w.windowTitle()
    assert en_title != ka_title


def test_main_window_theme_switch(qapp):
    from postgis_manager.ui.main_window import MainWindow
    w = MainWindow()
    w._switch_theme(None)
    w._switch_theme(None)


def test_main_window_not_connected(qapp):
    from postgis_manager.ui.main_window import MainWindow
    w = MainWindow()
    assert not w.db.is_connected()


def test_main_window_close(qapp):
    from postgis_manager.ui.main_window import MainWindow
    w = MainWindow()
    w.close()
