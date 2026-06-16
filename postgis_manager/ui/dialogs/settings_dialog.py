"""Settings dialog — PyQt6."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QComboBox,
    QSpinBox, QCheckBox, QDialogButtonBox, QGroupBox,
)
from PyQt6.QtGui import QFontDatabase, QFont
from PyQt6.QtWidgets import QApplication

from ...utils import i18n, theme, config


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("settings_title"))
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        appear_group = QGroupBox("Appearance")
        form = QFormLayout(appear_group)

        self._lang_combo = QComboBox()
        for code, name in i18n.available_languages().items():
            self._lang_combo.addItem(name, code)
        idx = self._lang_combo.findData(i18n.current_lang())
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
        form.addRow(i18n.t("settings_language"), self._lang_combo)

        self._theme_combo = QComboBox()
        self._theme_combo.addItem(i18n.t("settings_theme_light"), "light")
        self._theme_combo.addItem(i18n.t("settings_theme_dark"), "dark")
        idx = self._theme_combo.findData(theme.current())
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        form.addRow(i18n.t("settings_theme"), self._theme_combo)

        self._font_size = QSpinBox()
        self._font_size.setRange(9, 22)
        self._font_size.setValue(config.get("font_size", 13))
        form.addRow(i18n.t("settings_font_size"), self._font_size)

        self._font_family = QComboBox()
        all_families = QFontDatabase.families()
        for fam in ["Segoe UI", "Arial", "Helvetica", "Roboto", "Open Sans",
                    "Noto Sans", "DejaVu Sans", "Verdana", "Tahoma"]:
            self._font_family.addItem(fam)
        current_fam = config.get("font_family", "Segoe UI")
        idx = self._font_family.findText(current_fam)
        if idx >= 0:
            self._font_family.setCurrentIndex(idx)
        form.addRow(i18n.t("settings_font_family"), self._font_family)

        layout.addWidget(appear_group)

        behavior_group = QGroupBox("Behavior")
        bform = QFormLayout(behavior_group)

        self._confirm_delete = QCheckBox()
        self._confirm_delete.setChecked(config.get("confirm_delete", True))
        bform.addRow(i18n.t("settings_confirm_delete"), self._confirm_delete)

        self._max_rows = QSpinBox()
        self._max_rows.setRange(100, 50000)
        self._max_rows.setSingleStep(100)
        self._max_rows.setValue(config.get("max_rows", 1000))
        bform.addRow(i18n.t("settings_max_rows"), self._max_rows)

        layout.addWidget(behavior_group)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _apply(self):
        lang = self._lang_combo.currentData()
        thm = self._theme_combo.currentData()
        size = self._font_size.value()
        family = self._font_family.currentText()

        i18n.load(lang)
        theme.set_theme(thm)

        # Detect if we're running inside QGIS (embedded mode) by checking
        # whether the top-level parent has embedded=True.
        embedded = getattr(self.parent(), "embedded", False)
        qss = theme.build_qss()
        app = QApplication.instance()
        if app and not embedded:
            app.setFont(QFont(family, size))
            app.setStyleSheet(qss)
        elif embedded and self.parent():
            self.parent().setFont(QFont(family, size))
            self.parent().setStyleSheet(qss)

        config.set("language", lang)
        config.set("theme", thm)
        config.set("font_size", size)
        config.set("font_family", family)
        config.set("confirm_delete", self._confirm_delete.isChecked())
        config.set("max_rows", self._max_rows.value())
        self.accept()
