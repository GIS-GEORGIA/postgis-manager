"""Internationalization — runtime language switching."""

import json
import os

_LANG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "i18n")
_STRINGS: dict = {}
_CURRENT_LANG = "en"
_CALLBACKS: list = []


def load(lang: str) -> None:
    global _STRINGS, _CURRENT_LANG
    path = os.path.join(_LANG_DIR, f"{lang}.json")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        _STRINGS = json.load(f)
    _CURRENT_LANG = lang
    for cb in _CALLBACKS:
        try:
            cb(lang)
        except Exception:
            pass


def t(key: str, **kwargs) -> str:
    """Translate key, substituting {placeholder} values."""
    text = _STRINGS.get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


def current_lang() -> str:
    return _CURRENT_LANG


def available_languages() -> dict[str, str]:
    return {"en": "English", "ka": "ქართული"}


def on_language_change(callback) -> None:
    """Register a callback invoked when language switches."""
    _CALLBACKS.append(callback)


# Load default on import
load("en")
