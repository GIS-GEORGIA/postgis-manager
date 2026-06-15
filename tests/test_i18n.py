"""i18n engine tests."""
import pytest
from postgis_manager.utils import i18n


def test_load_english():
    i18n.load("en")
    assert i18n.current_lang() == "en"


def test_load_georgian():
    i18n.load("ka")
    assert i18n.current_lang() == "ka"


def test_translate_key():
    i18n.load("en")
    assert i18n.t("app_title") == "PostGIS Manager"


def test_translate_key_georgian():
    i18n.load("ka")
    title = i18n.t("app_title")
    assert "PostGIS" in title


def test_missing_key_returns_key():
    i18n.load("en")
    result = i18n.t("this_key_does_not_exist_xyz")
    assert result == "this_key_does_not_exist_xyz"


def test_all_english_keys_in_georgian():
    import json, os
    i18n_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "postgis_manager", "i18n")
    with open(os.path.join(i18n_dir, "en.json"), encoding="utf-8") as f:
        en_keys = set(json.load(f).keys())
    with open(os.path.join(i18n_dir, "ka.json"), encoding="utf-8") as f:
        ka_keys = set(json.load(f).keys())
    missing = en_keys - ka_keys
    assert not missing, f"Missing Georgian translations: {sorted(missing)}"


def test_language_switch_callback():
    called = []
    i18n.on_language_change(lambda lang: called.append(lang))
    i18n.load("en")
    i18n.load("ka")
    assert "ka" in called


def test_available_languages():
    langs = i18n.available_languages()
    assert "en" in langs
    assert "ka" in langs
