"""pg_tools: command building, process running and SRID classification (no database)."""
import importlib.util
import os
import sys

import pytest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "postgis_manager", "utils", "pg_tools.py")
_spec = importlib.util.spec_from_file_location("pg_tools_under_test", _PATH)
pg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pg)


# ── classify_srid ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("declared, actual, status", [
    (4326, [4326], "OK"),
    (4326, [], "EMPTY"),
    (0, [4326], "WARN"),
    (4326, [32638], "ERROR"),
    (4326, [4326, 32638], "WARN"),
    (0, [0], "OK"),
])
def test_classify_srid(declared, actual, status):
    assert pg.classify_srid(declared, actual)[0] == status


def test_classify_srid_messages():
    assert pg.classify_srid(4326, [32638])[1] == "Declared=4326, actual=32638"
    assert pg.classify_srid(0, [4326])[1] == "Declared SRID=0, actual=4326"
    assert "Mixed" in pg.classify_srid(4326, [4326, 3857])[1]


def test_fixable():
    base = dict(declared=4326, actual=32638, unique_srids=1)
    assert pg.fixable(base)
    assert not pg.fixable(dict(base, unique_srids=2))
    assert not pg.fixable(dict(base, actual=None))
    assert not pg.fixable(dict(base, actual=4326))


# ── command lines ────────────────────────────────────────────────────────────

def test_dump_cmd_defaults():
    cmd = pg.build_dump_cmd("pg_dump", host="h", port=5433, user="u", dbname="db",
                            outfile="o.backup")
    assert cmd == ["pg_dump", "-h", "h", "-p", "5433", "-U", "u", "-F", "c",
                   "-f", "o.backup", "-Z", "6", "db"]


def test_dump_cmd_options():
    cmd = pg.build_dump_cmd("pg_dump", host="", port="", user="", dbname="db",
                            outfile="dir", fmt="d", jobs=4, no_owner=True,
                            schema_only=True, schemas=["a", "b"])
    assert cmd[1:7] == ["-h", "localhost", "-p", "5432", "-U", "postgres"]
    assert "-j" in cmd and cmd[cmd.index("-j") + 1] == "4"
    assert "--schema-only" in cmd and "--no-owner" in cmd
    assert cmd.count("-n") == 2 and cmd[-1] == "db"


def test_dump_cmd_plain_has_no_compression_or_jobs():
    cmd = pg.build_dump_cmd("pg_dump", host="h", port=1, user="u", dbname="db",
                            outfile="o.sql", fmt="p", jobs=8)
    assert "-Z" not in cmd and "-j" not in cmd


def test_dump_cmd_rejects_bad_input():
    with pytest.raises(ValueError):
        pg.build_dump_cmd("pg_dump", host="h", port=1, user="u", dbname="db",
                          outfile="o", fmt="x")
    with pytest.raises(ValueError):
        pg.build_dump_cmd("pg_dump", host="h", port=1, user="u", dbname="db",
                          outfile="o", schema_only=True, data_only=True)


def test_password_never_in_command_line():
    cmd = pg.build_dump_cmd("pg_dump", host="h", port=1, user="u", dbname="db",
                            outfile="o")
    env = pg.db_env("s3cret", base={})
    assert env == {"PGPASSWORD": "s3cret"}
    assert not any("s3cret" in part for part in cmd)
    assert "PGPASSWORD" not in pg.db_env(None, base={})


def test_restore_cmd_sql_uses_psql():
    cmd = pg.build_restore_cmd("x.sql", restore_tool="pg_restore", psql_tool="psql",
                               host="h", port=1, user="u", dbname="db",
                               stop_on_error=True)
    assert cmd[0] == "psql" and "ON_ERROR_STOP=1" in cmd and cmd[-2:] == ["-f", "x.sql"]


def test_restore_cmd_binary_uses_pg_restore():
    cmd = pg.build_restore_cmd("x.backup", restore_tool="pg_restore", psql_tool=None,
                               host="h", port=1, user="u", dbname="db", clean=True,
                               create=True, jobs=3)
    assert cmd[0] == "pg_restore" and "-c" in cmd and "-C" in cmd
    assert cmd[cmd.index("-j") + 1] == "3" and cmd[-1] == "x.backup"


def test_restore_cmd_missing_tool():
    with pytest.raises(FileNotFoundError):
        pg.build_restore_cmd("x.sql", restore_tool="r", psql_tool=None,
                             host="h", port=1, user="u", dbname="db")
    with pytest.raises(FileNotFoundError):
        pg.build_restore_cmd("x.backup", restore_tool=None, psql_tool="p",
                             host="h", port=1, user="u", dbname="db")


# ── run_command / find_tool ──────────────────────────────────────────────────

def test_run_command_streams_output_and_exit_code():
    lines = []
    code = pg.run_command([sys.executable, "-c", "print('a'); print('b')"],
                          on_line=lines.append)
    assert code == 0 and lines == ["a", "b"]
    assert pg.run_command([sys.executable, "-c", "import sys; sys.exit(3)"]) == 3


def test_run_command_passes_env():
    out = []
    pg.run_command([sys.executable, "-c", "import os; print(os.environ['PGPASSWORD'])"],
                   env=pg.db_env("pw"), on_line=out.append)
    assert out == ["pw"]


def test_run_command_cancel():
    code = pg.run_command([sys.executable, "-c", "import time; time.sleep(30)"],
                          is_canceled=lambda: True)
    assert code == -1


def test_find_tool_in_extra_dirs(tmp_path):
    exe = "mytool.exe" if os.name == "nt" else "mytool"
    f = tmp_path / exe
    f.write_text("")
    assert pg.find_tool("mytool", [str(tmp_path)]) == str(f)
    assert pg.find_tool("definitely_not_a_tool_xyz") is None
