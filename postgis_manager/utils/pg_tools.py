"""PostgreSQL command-line tools and CRS audit — GUI-free logic.

Self-contained on purpose (standard library + lazy ``psycopg2``): the Backup panel,
the CRS Audit panel and the Processing algorithms all use it, and the algorithms load
it straight from its file so it works before the manager window has ever been opened.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable, Iterable, Sequence

# --------------------------------------------------------------------------- #
# Command-line tools (pg_dump / pg_restore / psql)
# --------------------------------------------------------------------------- #

DUMP_FORMATS = {"custom": "c", "directory": "d", "plain": "p", "tar": "t"}


def find_tool(name: str, extra_dirs: Iterable[str] = ()) -> str | None:
    """Find pg_dump / pg_restore / psql on PATH, in ``extra_dirs`` (e.g. the QGIS
    ``bin`` folder, which ships them) or near known Windows PostgreSQL installs."""
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if os.name == "nt" else "")
    for d in extra_dirs:
        candidate = os.path.join(d, exe)
        if os.path.isfile(candidate):
            return candidate
    for base in (
        r"C:\Program Files\PostgreSQL",
        r"C:\Program Files (x86)\PostgreSQL",
        r"C:\Program Files\pgAdmin 4\runtime",
    ):
        if os.path.isdir(base):
            for ver in sorted(os.listdir(base), reverse=True):
                candidate = os.path.join(base, ver, "bin", exe)
                if os.path.isfile(candidate):
                    return candidate
    return None


def db_env(password: str | None = None, base: dict | None = None) -> dict:
    """Environment for the tools. The password goes in PGPASSWORD, never on the
    command line (where other users could read it from the process list)."""
    env = dict(os.environ if base is None else base)
    if password:
        env["PGPASSWORD"] = password
    return env


def build_dump_cmd(tool: str, *, host: str, port, user: str, dbname: str,
                   outfile: str, fmt: str = "c", compress: bool = True,
                   jobs: int = 1, schema_only: bool = False,
                   data_only: bool = False, no_owner: bool = False,
                   schemas: Sequence[str] = ()) -> list[str]:
    """pg_dump command line. ``fmt`` is a pg_dump -F letter (c, d, p, t)."""
    if fmt not in DUMP_FORMATS.values():
        raise ValueError(f"Unknown pg_dump format: {fmt!r}")
    if schema_only and data_only:
        raise ValueError("schema-only and data-only are mutually exclusive")
    cmd = [tool, "-h", host or "localhost", "-p", str(port or 5432),
           "-U", user or "postgres", "-F", fmt, "-f", outfile]
    if fmt in ("c", "d", "t") and compress:
        cmd += ["-Z", "6"]
    if jobs and int(jobs) > 1 and fmt == "d":
        cmd += ["-j", str(int(jobs))]
    if schema_only:
        cmd.append("--schema-only")
    if data_only:
        cmd.append("--data-only")
    if no_owner:
        cmd.append("--no-owner")
    for s in schemas:
        cmd += ["-n", s]
    cmd.append(dbname)
    return cmd


def build_restore_cmd(infile: str, *, restore_tool: str | None,
                      psql_tool: str | None, host: str, port, user: str,
                      dbname: str, create: bool = False, clean: bool = False,
                      no_owner: bool = False, schema_only: bool = False,
                      data_only: bool = False, jobs: int = 1,
                      stop_on_error: bool = False) -> list[str]:
    """psql for plain ``.sql`` files, pg_restore for everything else."""
    conn = ["-h", host or "localhost", "-p", str(port or 5432),
            "-U", user or "postgres", "-d", dbname]
    if infile.lower().endswith(".sql"):
        if not psql_tool:
            raise FileNotFoundError("psql")
        cmd = [psql_tool, *conn]
        if stop_on_error:
            cmd += ["-v", "ON_ERROR_STOP=1"]
        return cmd + ["-f", infile]
    if not restore_tool:
        raise FileNotFoundError("pg_restore")
    cmd = [restore_tool, *conn]
    if create:
        cmd.append("-C")
    if clean:
        cmd.append("-c")
    if no_owner:
        cmd.append("--no-owner")
    if schema_only:
        cmd.append("--schema-only")
    if data_only:
        cmd.append("--data-only")
    if jobs and int(jobs) > 1:
        cmd += ["-j", str(int(jobs))]
    return cmd + [infile]


def run_command(cmd: Sequence[str], env: dict | None = None,
                on_line: Callable[[str], None] | None = None,
                is_canceled: Callable[[], bool] | None = None) -> int:
    """Run ``cmd``, feeding merged stdout/stderr lines to ``on_line``.

    Returns the exit code; -1 when cancelled (the process is killed)."""
    proc = subprocess.Popen(
        list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    import threading

    def _watch():
        import time
        while proc.poll() is None:
            if is_canceled and is_canceled():
                proc.kill()
                return
            time.sleep(0.2)

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    for line in proc.stdout:
        if on_line:
            on_line(line.rstrip())
    proc.stdout.close()
    proc.wait()
    watcher.join(timeout=1)
    if is_canceled and is_canceled():
        return -1
    return proc.returncode


# --------------------------------------------------------------------------- #
# CRS audit
# --------------------------------------------------------------------------- #

def classify_srid(declared: int, actual_srids: Sequence[int]) -> tuple[str, str]:
    """(status, issue) for a geometry column: OK / EMPTY / WARN / ERROR.

    ``actual_srids`` are the SRIDs found in the data, most frequent first."""
    unique_count = len(actual_srids)
    dominant = actual_srids[0] if actual_srids else None
    if dominant is None:
        return "EMPTY", "No geometries"
    if declared == 0 and dominant != 0:
        return "WARN", f"Declared SRID=0, actual={dominant}"
    if declared != 0 and dominant != declared:
        return "ERROR", f"Declared={declared}, actual={dominant}"
    if unique_count > 1:
        return "WARN", f"Mixed SRIDs: {list(actual_srids)}"
    return "OK", ""


def audit_crs(conn, schema: str | None = None,
              progress: Callable[[int, str], None] | None = None) -> list[dict]:
    """Compare declared and actual SRIDs of every geometry column.

    ``conn`` is an open psycopg2 connection; ``schema`` limits the scan
    (None or "(all)" = every schema). Row keys: schema, table, col, type,
    declared, actual, unique_srids, status, issue."""
    from psycopg2 import sql

    cur = conn.cursor()
    query = ("SELECT f_table_schema, f_table_name, f_geometry_column, srid, type "
             "FROM geometry_columns")
    args: tuple = ()
    if schema and schema != "(all)":
        query += " WHERE f_table_schema = %s"
        args = (schema,)
    query += " ORDER BY f_table_schema, f_table_name, f_geometry_column"
    cur.execute(query, args)
    cols = cur.fetchall()

    rows = []
    total = len(cols)
    for i, (sch, table, col, declared, gtype) in enumerate(cols):
        if progress:
            progress(10 + int(80 * i / max(total, 1)), f"Checking {sch}.{table}")
        try:
            cur.execute(sql.SQL(
                "SELECT ST_SRID({c}) AS s, count(*) FROM {t} "
                "WHERE {c} IS NOT NULL GROUP BY s ORDER BY count(*) DESC LIMIT 5"
            ).format(c=sql.Identifier(col), t=sql.Identifier(sch, table)))
            actual = [r[0] for r in cur.fetchall()]
        except Exception:
            if not conn.autocommit:
                conn.rollback()
            actual = []
        status, issue = classify_srid(declared, actual)
        rows.append({
            "schema": sch, "table": table, "col": col, "type": gtype,
            "declared": declared, "actual": actual[0] if actual else None,
            "unique_srids": len(actual), "status": status, "issue": issue,
        })
    if progress:
        progress(100, "Done")
    return rows


def fixable(row: dict) -> bool:
    """A single, different SRID in the data: relabelling the column is safe."""
    return (row["actual"] is not None and row["actual"] != row["declared"]
            and row["unique_srids"] == 1)


def fix_crs(conn, rows: Iterable[dict],
            on_error: Callable[[dict, Exception], None] | None = None) -> int:
    """Run UpdateGeometrySRID for each row (declared -> actual). Only the metadata
    changes, coordinates are not transformed. Returns the number of columns fixed;
    failures (e.g. views) go to ``on_error`` and do not stop the rest."""
    cur = conn.cursor()
    n = 0
    for r in rows:
        if r["actual"] is None or r["declared"] == r["actual"]:
            continue
        try:
            cur.execute("SELECT UpdateGeometrySRID(%s, %s, %s, %s)",
                        (r["schema"], r["table"], r["col"], int(r["actual"])))
            if not conn.autocommit:
                conn.commit()
            n += 1
        except Exception as exc:  # noqa: BLE001
            if not conn.autocommit:
                conn.rollback()
            if on_error:
                on_error(r, exc)
    return n
