"""Connection profile dialog — PyQt6."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QComboBox, QSpinBox, QHBoxLayout,
    QDialogButtonBox, QMessageBox,
)
from PyQt6.QtCore import Qt

from ...utils import i18n


class ConnectionDialog(QDialog):
    def __init__(self, parent=None, profile: dict = None):
        super().__init__(parent)
        self._profile = profile or {}
        self.setWindowTitle(i18n.t("action_new_connection"))
        self.setMinimumWidth(400)
        self._build_ui()
        if profile:
            self._load_profile(profile)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit()
        self._name.setPlaceholderText("My PostGIS DB")
        form.addRow(i18n.t("conn_name"), self._name)

        self._host = QLineEdit("localhost")
        form.addRow(i18n.t("conn_host"), self._host)

        self._port = QLineEdit("5432")
        form.addRow(i18n.t("conn_port"), self._port)

        self._dbname = QLineEdit()
        form.addRow(i18n.t("conn_database"), self._dbname)

        self._user = QLineEdit("postgres")
        form.addRow(i18n.t("conn_user"), self._user)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(i18n.t("conn_password"), self._password)

        self._ssl_combo = QComboBox()
        for m in ["prefer", "disable", "allow", "require", "verify-ca", "verify-full"]:
            self._ssl_combo.addItem(m)
        form.addRow(i18n.t("conn_ssl"), self._ssl_combo)

        self._timeout = QSpinBox()
        self._timeout.setRange(1, 120)
        self._timeout.setValue(10)
        form.addRow(i18n.t("conn_timeout"), self._timeout)

        self._save_pw = QCheckBox(i18n.t("conn_save_password"))
        self._save_pw.setChecked(True)
        form.addRow("", self._save_pw)

        layout.addLayout(form)

        test_row = QHBoxLayout()
        test_btn = QPushButton(i18n.t("conn_test"))
        test_btn.clicked.connect(self._test_connection)
        test_row.addWidget(test_btn)
        self._test_label = QLabel("")
        test_row.addWidget(self._test_label)
        test_row.addStretch()
        layout.addLayout(test_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_profile(self, p: dict):
        self._name.setText(p.get("name", ""))
        self._host.setText(p.get("host", "localhost"))
        self._port.setText(str(p.get("port", "5432")))
        self._dbname.setText(p.get("dbname", ""))
        self._user.setText(p.get("user", "postgres"))
        self._password.setText(p.get("password", ""))
        ssl_idx = self._ssl_combo.findText(p.get("ssl_mode", "prefer"))
        if ssl_idx >= 0:
            self._ssl_combo.setCurrentIndex(ssl_idx)
        self._timeout.setValue(int(p.get("timeout", 10)))

    def _test_connection(self):
        from ...db.connection import DBManager
        db = DBManager()
        try:
            self._test_label.setText(i18n.t("conn_testing"))
            db.connect(
                host=self._host.text().strip(),
                port=self._port.text().strip(),
                dbname=self._dbname.text().strip(),
                user=self._user.text().strip(),
                password=self._password.text(),
                ssl_mode=self._ssl_combo.currentText(),
                timeout=self._timeout.value(),
            )
            info = db.server_info()
            postgis = info.get("postgis_version") or "N/A"
            self._test_label.setText(f"✔  PostGIS {postgis}")
            self._test_label.setStyleSheet("color: #2E7D32;")
        except Exception as e:
            self._test_label.setText(f"✖  {str(e)[:60]}")
            self._test_label.setStyleSheet("color: #C62828;")
        finally:
            db.disconnect()

    def get_profile(self) -> dict:
        return {
            "name": self._name.text().strip() or self._dbname.text().strip(),
            "host": self._host.text().strip(),
            "port": self._port.text().strip(),
            "dbname": self._dbname.text().strip(),
            "user": self._user.text().strip(),
            "password": self._password.text() if self._save_pw.isChecked() else "",
            "ssl_mode": self._ssl_combo.currentText(),
            "timeout": self._timeout.value(),
        }
