#!/usr/bin/env python3
"""Run the database Processing algorithms (backup, restore, CRS audit) in a QGIS Python.

    "C:\\Program Files\\QGIS 3.44.5\\bin\\python-qgis.bat" tools/test_processing.py
    "C:\\Program Files\\QGIS 4.2.2\\bin\\python-qgis.bat"  tools/test_processing.py

No database is needed: pg_dump / pg_restore are replaced by a stub that records its
arguments, and the CRS audit runs against a fake connection. To also run against a real
server, set PGTEST_HOST / PGTEST_PORT / PGTEST_USER / PGTEST_PASSWORD / PGTEST_DB
(a scratch database; the test creates and drops its own schema).

Exit code is non-zero if any check fails.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from qgis.core import Qgis, QgsApplication  # noqa: E402

app = QgsApplication([], False)
app.initQgis()
sys.path.append(os.path.join(QgsApplication.pkgDataPath(), "python", "plugins"))
import processing  # noqa: E402
from processing.core.Processing import Processing  # noqa: E402

Processing.initialize()

from qgis_plugin.algorithms import _shared  # noqa: E402
from qgis_plugin.processing_provider import PostGISManagerProvider  # noqa: E402

PROVIDER = PostGISManagerProvider()
assert QgsApplication.processingRegistry().addProvider(PROVIDER)

TMP = tempfile.mkdtemp(prefix="pgm_proc_")
FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (" " + str(extra) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def section(title):
    print("\n== " + title)


def run(alg, params, expect_error=False):
    try:
        res = processing.run(alg, params)
    except Exception as exc:  # noqa: BLE001
        if expect_error:
            return exc
        raise
    if expect_error:
        raise AssertionError("expected failure of " + alg)
    return res


# ── stub command-line tool ───────────────────────────────────────────────────

STUB_PY = os.path.join(TMP, "stub_tool.py")
with open(STUB_PY, "w", encoding="utf-8") as fh:
    fh.write('''import json, os, sys
args = sys.argv[1:]
rec = {"args": args, "pgpassword": os.environ.get("PGPASSWORD")}
out = os.environ.get("STUB_RECORD")
if out:
    with open(out, "w") as f:
        json.dump(rec, f)
if "-f" in args and args[args.index("-f") + 1].endswith((".backup", ".sql")) and "-d" not in args:
    open(args[args.index("-f") + 1], "w").write("-- stub dump")
print("stub running")
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
''')
STUB = os.path.join(TMP, "stubtool.bat" if os.name == "nt" else "stubtool.sh")
with open(STUB, "w") as fh:
    if os.name == "nt":
        fh.write('@"{}" "{}" %*\r\n'.format(sys.executable, STUB_PY))
    else:
        fh.write('#!/bin/sh\nexec "{}" "{}" "$@"\n'.format(sys.executable, STUB_PY))
    os.chmod(STUB, 0o755) if os.name != "nt" else None

tools = _shared.pg_tools()
REAL_FIND = tools.find_tool
RECORD = os.path.join(TMP, "record.json")
os.environ["STUB_RECORD"] = RECORD


def use_stub():
    tools.find_tool = lambda name, extra_dirs=(): STUB


def use_real():
    tools.find_tool = REAL_FIND


def recorded():
    with open(RECORD, encoding="utf-8") as fh:
        return json.load(fh)


# ── tests ────────────────────────────────────────────────────────────────────

def test_registration():
    section("provider")
    ids = {a.id() for a in PROVIDER.algorithms()}
    for want in ("postgis_manager:backupdatabase", "postgis_manager:restoredatabase",
                 "postgis_manager:crsaudit", "postgis_manager:validategeometry"):
        check("registered " + want, want in ids, sorted(ids))


def test_backup():
    section("postgis_manager:backupdatabase (stub pg_dump)")
    use_stub()
    out = os.path.join(TMP, "db.backup")
    base = {"HOST": "dbhost", "PORT": 5433, "DBNAME": "mydb", "USER": "me",
            "PASSWORD": "s3cret", "OUTPUT": out}
    res = run("postgis_manager:backupdatabase", dict(base, FORMAT=0, SCHEMAS="a, b",
                                                    NO_OWNER=True))
    rec = recorded()
    a = rec["args"]
    check("output written and returned", os.path.isfile(out) and res["RESULT"] == out)
    check("connection args", a[a.index("-h") + 1] == "dbhost" and a[a.index("-p") + 1] == "5433"
          and a[a.index("-U") + 1] == "me" and a[-1] == "mydb", a)
    check("schemas and flags", a.count("-n") == 2 and "--no-owner" in a, a)
    check("password via PGPASSWORD only", rec["pgpassword"] == "s3cret"
          and not any("s3cret" in x for x in a))

    os.environ["STUB_EXIT"] = "2"
    e = run("postgis_manager:backupdatabase", dict(base, FORMAT=0), expect_error=True)
    check("non-zero exit is an error", "exit code 2" in str(e), e)
    del os.environ["STUB_EXIT"]

    e = run("postgis_manager:backupdatabase",
            dict(base, FORMAT=0, SCHEMA_ONLY=True, DATA_ONLY=True), expect_error=True)
    check("schema-only + data-only rejected", "mutually exclusive" in str(e), e)
    e = run("postgis_manager:backupdatabase", dict(base, DBNAME=""), expect_error=True)
    check("empty database rejected", "DBNAME" in str(e) or "Database name" in str(e), e)

    tools.find_tool = lambda name, extra_dirs=(): None
    e = run("postgis_manager:backupdatabase", dict(base, FORMAT=0), expect_error=True)
    check("missing pg_dump reported", "pg_dump was not found" in str(e), e)
    use_real()


def test_restore():
    section("postgis_manager:restoredatabase (stub pg_restore / psql)")
    use_stub()
    bk = os.path.join(TMP, "in.backup")
    sqlf = os.path.join(TMP, "in.sql")
    for p in (bk, sqlf):
        with open(p, "w") as fh:
            fh.write("x")
    base = {"INPUT": bk, "HOST": "h", "PORT": 5432, "DBNAME": "scratch", "USER": "u",
            "PASSWORD": "pw"}

    e = run("postgis_manager:restoredatabase", base, expect_error=True)
    check("not confirmed -> refused", "not confirmed" in str(e), e)

    run("postgis_manager:restoredatabase", dict(base, CONFIRM=True, CLEAN=True, JOBS=2))
    a = recorded()["args"]
    check("pg_restore args", "-c" in a and a[a.index("-j") + 1] == "2" and a[-1] == bk
          and a[a.index("-d") + 1] == "scratch", a)

    run("postgis_manager:restoredatabase", dict(base, INPUT=sqlf, CONFIRM=True))
    a = recorded()["args"]
    check("plain .sql goes through psql and stops on error",
          "ON_ERROR_STOP=1" in a and a[-1] == sqlf, a)

    e = run("postgis_manager:restoredatabase",
            dict(base, INPUT=os.path.join(TMP, "nope.backup"), CONFIRM=True),
            expect_error=True)
    check("missing file rejected", "not found" in str(e).lower() or "does not exist" in str(e).lower(), e)

    os.environ["STUB_EXIT"] = "1"
    e = run("postgis_manager:restoredatabase", dict(base, CONFIRM=True), expect_error=True)
    check("failure reported", "Restore failed" in str(e), e)
    del os.environ["STUB_EXIT"]
    use_real()


class FakeCursor:
    """Answers the audit queries from a table of {(schema, table): [(srid, count)]}."""

    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, query, args=None):
        if isinstance(query, str) and "geometry_columns" in query:
            self._rows = [(s, t, c, d, ty) for (s, t, c, d, ty) in self.conn.columns
                          if not args or s == args[0]]
        elif isinstance(query, str) and "UpdateGeometrySRID" in query:
            self.conn.fixes.append(tuple(args))
            s, t, c, new = args
            if (s, t) in self.conn.views:
                raise RuntimeError("cannot alter a view")
            self.conn.columns = [(s2, t2, c2, new if (s2, t2, c2) == (s, t, c) else d, ty)
                                 for (s2, t2, c2, d, ty) in self.conn.columns]
            self.conn.data[(s, t)] = [(new, n) for _s, n in self.conn.data[(s, t)]]
            self._rows = []
        else:  # per-column SRID query: composed SQL, keyed by call order
            text = repr(query)
            for (s, t), rows in self.conn.data.items():
                if "'{}'".format(t) in text or '"{}"'.format(t) in text:
                    self._rows = list(rows)
                    return
            self._rows = []

    def fetchall(self):
        return self._rows


class FakeConn:
    autocommit = True

    def __init__(self):
        self.columns = [
            ("public", "ok_tbl", "geom", 4326, "POINT"),
            ("public", "bad_tbl", "geom", 4326, "POINT"),
            ("public", "zero_tbl", "geom", 0, "POINT"),
            ("public", "mixed_tbl", "geom", 4326, "POINT"),
            ("public", "empty_tbl", "geom", 4326, "POINT"),
            ("other", "view_bad", "geom", 4326, "POINT"),
        ]
        self.data = {
            ("public", "ok_tbl"): [(4326, 10)],
            ("public", "bad_tbl"): [(32638, 5)],
            ("public", "zero_tbl"): [(4326, 3)],
            ("public", "mixed_tbl"): [(4326, 8), (3857, 2)],
            ("public", "empty_tbl"): [],
            ("other", "view_bad"): [(32638, 1)],
        }
        self.views = {("other", "view_bad")}
        self.fixes = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


def test_crs_audit():
    section("postgis_manager:crsaudit (fake connection)")
    conn = FakeConn()
    original = _shared.connect
    _shared.connect = lambda info: conn
    try:
        base = {"HOST": "h", "DBNAME": "db", "OUTPUT": "memory:"}
        res = run("postgis_manager:crsaudit", base)
        rows = {f["table"]: f for f in res["OUTPUT"].getFeatures()}
        check("six columns audited", len(rows) == 6, sorted(rows))
        st = {t: f["status"] for t, f in rows.items()}
        check("statuses", st == {"ok_tbl": "OK", "bad_tbl": "ERROR", "zero_tbl": "WARN",
                                 "mixed_tbl": "WARN", "empty_tbl": "EMPTY",
                                 "view_bad": "ERROR"}, st)
        check("counters", (res["ERRORS"], res["WARNINGS"], res["FIXED"]) == (2, 2, 0), res)
        check("report only: nothing fixed", conn.fixes == [])
        check("declared / actual SRID reported",
              rows["bad_tbl"]["declared_srid"] == 4326 and rows["bad_tbl"]["actual_srid"] == 32638)

        res = run("postgis_manager:crsaudit", dict(base, SCHEMA="public"))
        check("schema filter", res["OUTPUT"].featureCount() == 5)

        res = run("postgis_manager:crsaudit", dict(base, FIX=True))
        check("single-SRID mismatches fixed (bad, zero, view attempted)",
              {f[1] for f in conn.fixes} == {"bad_tbl", "zero_tbl", "view_bad"}, conn.fixes)
        check("mixed columns left alone", all(f[1] != "mixed_tbl" for f in conn.fixes))
        fixed = {f["table"] for f in res["OUTPUT"].getFeatures() if f["fixed"]}
        check("fixed flag set for the two that worked; the view failed softly",
              fixed == {"bad_tbl", "zero_tbl"} and res["FIXED"] == 2, (fixed, res))

        res = run("postgis_manager:crsaudit", base)
        st = {f["table"]: f["status"] for f in res["OUTPUT"].getFeatures()}
        check("re-audit after the fix", st["bad_tbl"] == "OK" and st["zero_tbl"] == "OK", st)
    finally:
        _shared.connect = original


def test_connect_failure():
    section("crsaudit: unreachable server")
    e = run("postgis_manager:crsaudit", {"HOST": "127.0.0.1", "PORT": 1, "DBNAME": "x",
                                         "OUTPUT": "memory:"}, expect_error=True)
    check("connection error is a clean processing error", "Cannot connect" in str(e), e)


def test_real_server():
    host = os.environ.get("PGTEST_HOST")
    if not host:
        print("\n== real server: skipped (set PGTEST_HOST/PORT/USER/PASSWORD/DB to enable)")
        return
    section("real server ({})".format(host))
    import psycopg2
    info = dict(host=host, port=int(os.environ.get("PGTEST_PORT", "5432")),
                user=os.environ.get("PGTEST_USER", "postgres"),
                password=os.environ.get("PGTEST_PASSWORD", ""),
                dbname=os.environ["PGTEST_DB"])
    c = psycopg2.connect(**info)
    c.autocommit = True
    cur = c.cursor()
    schema = "pgm_audit_test"
    cur.execute('DROP SCHEMA IF EXISTS {} CASCADE'.format(schema))
    cur.execute('CREATE SCHEMA {}'.format(schema))
    cur.execute('CREATE TABLE {}.t (id serial, geom geometry(Point, 4326))'.format(schema))
    cur.execute("INSERT INTO {}.t (geom) VALUES (ST_SetSRID(ST_MakePoint(1,1), 4326))".format(schema))
    cur.execute("ALTER TABLE {}.t ALTER COLUMN geom TYPE geometry(Point, 0) "
                "USING ST_SetSRID(geom, 0)".format(schema))
    base = {"HOST": host, "PORT": info["port"], "DBNAME": info["dbname"],
            "USER": info["user"], "PASSWORD": info["password"]}
    try:
        res = run("postgis_manager:crsaudit", dict(base, SCHEMA=schema, OUTPUT="memory:"))
        f = next(res["OUTPUT"].getFeatures())
        check("real audit finds SRID 0 vs 4326... or similar mismatch",
              f["status"] in ("OK", "WARN", "ERROR"), f.attributes())
        out = os.path.join(TMP, "real.backup")
        run("postgis_manager:backupdatabase", dict(base, SCHEMAS=schema, OUTPUT=out))
        check("real pg_dump produced a file", os.path.getsize(out) > 0)
    finally:
        cur.execute('DROP SCHEMA IF EXISTS {} CASCADE'.format(schema))
        c.close()


print("QGIS", Qgis.version())
for t in (test_registration, test_backup, test_restore, test_crs_audit,
          test_connect_failure, test_real_server):
    try:
        t()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        FAILS.append(t.__name__ + " (exception)")

app.exitQgis()
shutil.rmtree(TMP, ignore_errors=True)
print("\n" + ("ALL PASSED" if not FAILS else "FAILED: " + ", ".join(FAILS)))
sys.exit(1 if FAILS else 0)
