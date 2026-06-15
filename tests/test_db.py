"""DBManager unit tests (no live database required)."""
import pytest
from unittest.mock import MagicMock, patch
from postgis_manager.db.connection import DBManager


def test_initial_state(db):
    assert not db.is_connected()


def test_disconnect_when_not_connected(db):
    db.disconnect()  # must not raise


def test_connect_bad_host():
    d = DBManager()
    with pytest.raises(Exception):
        d.connect(host="127.0.0.1", port=9999, dbname="none",
                  user="none", password="none", timeout=1)


def test_execute_without_connection(db):
    with pytest.raises(Exception):
        db.execute("SELECT 1")


def test_params_empty_before_connect():
    d = DBManager()
    assert d.params == {}


@patch("psycopg2.connect")
def test_connect_success_mocked(mock_connect):
    mock_conn = MagicMock()
    mock_conn.closed = 0
    mock_connect.return_value = mock_conn

    d = DBManager()
    d.connect(host="localhost", port=5432, dbname="gisdb",
               user="postgres", password="secret", timeout=5)
    assert d.is_connected()
    assert d.params["dbname"] == "gisdb"
    assert d.params["host"] == "localhost"
    d.disconnect()
    assert not d.is_connected()
