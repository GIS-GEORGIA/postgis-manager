# Contributing to PostGIS Manager

Thank you for your interest in contributing! Every contribution — bug fix, new feature, translation, or documentation — is welcome.

## Ways to contribute

- **Bug reports** — open an [Issue](https://github.com/GIS-GEORGIA/postgis-manager/issues/new?template=bug_report.yml)
- **Feature requests** — open an [Issue](https://github.com/GIS-GEORGIA/postgis-manager/issues/new?template=feature_request.yml)
- **Code** — fork → branch → pull request
- **Translations** — add `postgis_manager/i18n/<lang>.json` based on `en.json`
- **Documentation** — improve `docs/index.html` or the README

## Development setup

```bash
git clone https://github.com/GIS-GEORGIA/postgis-manager.git
cd postgis-manager
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest pytest-qt ruff
```

## Running tests

```bash
pytest tests/ -v
```

All 38 tests must pass before submitting a PR. CI runs automatically on every push.

## Code style

```bash
ruff check postgis_manager/ standalone/ --select E,F,W --ignore E501
```

- No comments explaining *what* the code does — only *why* if non-obvious
- No bare `except:` — always catch specific exceptions
- All DB operations in `QThread` workers — never block the UI thread
- New panels follow the existing pattern: `class XyzPanel(QWidget)` in `postgis_manager/ui/panels/xyz.py`

## Adding a new panel

1. Create `postgis_manager/ui/panels/my_panel.py` with a `MyPanel(QWidget)` class
2. Import it in `postgis_manager/ui/main_window.py`
3. Add it to `_build_central()` under the appropriate nav group
4. Add i18n keys to both `en.json` and `ka.json`
5. Write at least one test in `tests/`

## Adding a translation

1. Copy `postgis_manager/i18n/en.json` to `postgis_manager/i18n/<lang>.json`
2. Translate all values (keep keys unchanged)
3. Add `"<lang>": "<Language name>"` to `available_languages()` in `postgis_manager/utils/i18n.py`
4. Open a pull request

## Pull request checklist

- [ ] `pytest tests/ -v` passes
- [ ] `ruff check` passes
- [ ] New i18n keys added to both `en.json` and `ka.json`
- [ ] No commented-out code left in
- [ ] PR description explains *why*, not just *what*

## Contact

- GitHub Issues: https://github.com/GIS-GEORGIA/postgis-manager/issues
- Author: Giorgi Kapanadze — kapan.gio777@gmail.com
- Website: https://pg.qgis.ge
