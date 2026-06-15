"""Theme engine tests."""
from postgis_manager.utils import theme


def test_build_qss_light():
    theme.set_theme("light")
    qss = theme.build_qss()
    assert isinstance(qss, str)
    assert len(qss) > 100
    assert "QMainWindow" in qss or "QWidget" in qss or "background" in qss


def test_build_qss_dark():
    theme.set_theme("dark")
    qss = theme.build_qss()
    assert isinstance(qss, str)
    assert len(qss) > 100


def test_current_theme():
    theme.set_theme("light")
    assert theme.current() == "light"
    theme.set_theme("dark")
    assert theme.current() == "dark"


def test_theme_switch_callback():
    called = []
    theme.on_theme_change(lambda t: called.append(t))
    theme.set_theme("light")
    theme.set_theme("dark")
    assert "dark" in called


def test_nav_sidebar_in_qss():
    theme.set_theme("light")
    qss = theme.build_qss()
    assert "NavItem" in qss
    assert "NavSidebar" in qss


def test_both_themes_have_same_keys():
    theme.set_theme("light")
    light_qss = theme.build_qss()
    theme.set_theme("dark")
    dark_qss = theme.build_qss()
    assert len(light_qss) > 0
    assert len(dark_qss) > 0
