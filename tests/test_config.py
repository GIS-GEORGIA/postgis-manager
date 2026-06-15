"""Config engine tests."""
import pytest
from postgis_manager.utils import config


def test_get_default():
    val = config.get("nonexistent_key_xyz", default="fallback")
    assert val == "fallback"


def test_set_and_get():
    config.set("_test_key", "hello")
    assert config.get("_test_key") == "hello"
    config.set("_test_key", None)


def test_set_integer():
    config.set("_test_int", 42)
    assert config.get("_test_int") == 42
    config.set("_test_int", None)


def test_save_and_load_connection():
    profile = {
        "name": "_test_conn",
        "host": "localhost",
        "port": 5432,
        "dbname": "testdb",
        "user": "postgres",
    }
    config.save_connection(profile)
    conns = config.get_connections()
    names = [c["name"] for c in conns]
    assert "_test_conn" in names
    # cleanup
    config.delete_connection("_test_conn")
    conns_after = config.get_connections()
    assert "_test_conn" not in [c["name"] for c in conns_after]


def test_get_connections_returns_list():
    conns = config.get_connections()
    assert isinstance(conns, list)
