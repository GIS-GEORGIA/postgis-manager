"""Shared pytest fixtures."""
import sys
import os
import pytest

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Headless Qt for CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """Single QApplication for the whole test session."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def db():
    """Disconnected DBManager instance."""
    from postgis_manager.db.connection import DBManager
    return DBManager()
