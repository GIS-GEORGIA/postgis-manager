"""Audit log management — trigger-based + pgaudit status.

Installs a per-table audit trigger that writes to postgis_manager.audit_log.
Geometry columns are stored as ST_AsText truncated to 300 chars to avoid bloat.
"""

from __future__ import annotations
import psycopg2
import psycopg2.extras

# ── Schema SQL ────────────────────────────────────────────────────────────

AUDIT_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS postgis_manager;

CREATE TABLE IF NOT EXISTS postgis_manager.audit_log (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    db_user         TEXT NOT NULL DEFAULT current_user,
    app_user        TEXT,
    client_addr     TEXT,
    schema_name     TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    operation       TEXT NOT NULL,          -- INSERT | UPDATE | DELETE
    pk_column       TEXT,
    pk_value        TEXT,
    changed_columns TEXT[],                 -- list of modified column names
    old_data        JSONB,                  -- non-geometry columns only
    new_data        JSONB,
    old_geom        TEXT,                   -- ST_AsText truncated to 300 chars
    new_geom        TEXT,
    query           TEXT
);

CREATE INDEX IF NOT EXISTS audit_log_ts_idx
    ON postgis_manager.audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS audit_log_table_idx
    ON postgis_manager.audit_log (schema_name, table_name);
CREATE INDEX IF NOT EXISTS audit_log_user_idx
    ON postgis_manager.audit_log (db_user);
"""

# The trigger function — created once, reused by every audited table.
AUDIT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION postgis_manager.audit_trigger_fn()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $fn$
DECLARE
    _old_data    JSONB := NULL;
    _new_data    JSONB := NULL;
    _old_geom    TEXT  := NULL;
    _new_geom    TEXT  := NULL;
    _changed     TEXT[] := ARRAY[]::TEXT[];
    _geom_col    TEXT  := NULL;
    _pk_col      TEXT  := NULL;
    _pk_val      TEXT  := NULL;
    _col         TEXT;
BEGIN
    -- Detect primary key column (first pk column)
    SELECT kcu.column_name INTO _pk_col
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
        AND tc.table_schema = kcu.table_schema
    WHERE tc.constraint_type = 'PRIMARY KEY'
      AND tc.table_schema = TG_TABLE_SCHEMA
      AND tc.table_name  = TG_TABLE_NAME
    ORDER BY kcu.ordinal_position
    LIMIT 1;

    -- Detect geometry column (first geometry column)
    SELECT column_name INTO _geom_col
    FROM information_schema.columns
    WHERE table_schema = TG_TABLE_SCHEMA
      AND table_name   = TG_TABLE_NAME
      AND udt_name     = 'geometry'
    ORDER BY ordinal_position
    LIMIT 1;

    IF TG_OP = 'INSERT' THEN
        _new_data := to_jsonb(NEW);
        IF _geom_col IS NOT NULL THEN
            _new_data := _new_data - _geom_col;
            EXECUTE format('SELECT LEFT(ST_AsText($1.%I), 300)', _geom_col)
                INTO _new_geom USING NEW;
        END IF;
        IF _pk_col IS NOT NULL THEN
            EXECUTE format('SELECT ($1.%I)::TEXT', _pk_col)
                INTO _pk_val USING NEW;
        END IF;

    ELSIF TG_OP = 'UPDATE' THEN
        _old_data := to_jsonb(OLD);
        _new_data := to_jsonb(NEW);
        IF _geom_col IS NOT NULL THEN
            _old_data := _old_data - _geom_col;
            _new_data := _new_data - _geom_col;
            EXECUTE format('SELECT LEFT(ST_AsText($1.%I), 300)', _geom_col)
                INTO _old_geom USING OLD;
            EXECUTE format('SELECT LEFT(ST_AsText($1.%I), 300)', _geom_col)
                INTO _new_geom USING NEW;
        END IF;
        -- Track which columns actually changed
        FOR _col IN
            SELECT key FROM jsonb_each(_new_data)
            WHERE _new_data->key IS DISTINCT FROM _old_data->key
        LOOP
            _changed := array_append(_changed, _col);
        END LOOP;
        IF _geom_col IS NOT NULL AND _old_geom IS DISTINCT FROM _new_geom THEN
            _changed := array_append(_changed, _geom_col);
        END IF;
        IF _pk_col IS NOT NULL THEN
            EXECUTE format('SELECT ($1.%I)::TEXT', _pk_col)
                INTO _pk_val USING NEW;
        END IF;

    ELSIF TG_OP = 'DELETE' THEN
        _old_data := to_jsonb(OLD);
        IF _geom_col IS NOT NULL THEN
            _old_data := _old_data - _geom_col;
            EXECUTE format('SELECT LEFT(ST_AsText($1.%I), 300)', _geom_col)
                INTO _old_geom USING OLD;
        END IF;
        IF _pk_col IS NOT NULL THEN
            EXECUTE format('SELECT ($1.%I)::TEXT', _pk_col)
                INTO _pk_val USING OLD;
        END IF;
    END IF;

    INSERT INTO postgis_manager.audit_log (
        schema_name, table_name, operation,
        pk_column, pk_value,
        changed_columns,
        old_data, new_data,
        old_geom, new_geom,
        query
    ) VALUES (
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP,
        _pk_col, _pk_val,
        NULLIF(_changed, ARRAY[]::TEXT[]),
        _old_data, _new_data,
        _old_geom, _new_geom,
        current_query()
    );

    RETURN COALESCE(NEW, OLD);
END;
$fn$;
"""

_INSTALL_TRIGGER_SQL = """
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'audit_trigger'
          AND tgrelid = {quoted}::regclass
    ) THEN
        CREATE TRIGGER audit_trigger
        AFTER INSERT OR UPDATE OR DELETE
        ON {quoted}
        FOR EACH ROW EXECUTE FUNCTION postgis_manager.audit_trigger_fn();
    END IF;
END $$;
"""

_DROP_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS audit_trigger ON {quoted};
"""

_LIST_AUDITED_SQL = """
SELECT
    n.nspname  AS schema,
    c.relname  AS table
FROM pg_trigger t
JOIN pg_class   c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE t.tgname = 'audit_trigger'
  AND NOT t.tgisinternal
ORDER BY n.nspname, c.relname;
"""

_PGAUDIT_STATUS_SQL = """
SELECT
    name,
    setting,
    short_desc
FROM pg_settings
WHERE name LIKE 'pgaudit%'
ORDER BY name;
"""

PAGE_SIZE = 200


def ensure_audit_schema(conn: psycopg2.extensions.connection) -> None:
    """Create audit schema + table + indexes + trigger function if missing."""
    with conn.cursor() as cur:
        cur.execute(AUDIT_SCHEMA_SQL)
        cur.execute(AUDIT_FUNCTION_SQL)
    conn.commit()


def install_trigger(conn: psycopg2.extensions.connection,
                    schema: str, table: str) -> None:
    quoted = f'"{schema}"."{table}"'
    with conn.cursor() as cur:
        cur.execute(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS ("
            f"  SELECT 1 FROM pg_trigger "
            f"  WHERE tgname = 'audit_trigger' "
            f"  AND tgrelid = '{schema}.{table}'::regclass"
            f") THEN "
            f"  CREATE TRIGGER audit_trigger "
            f"  AFTER INSERT OR UPDATE OR DELETE "
            f"  ON {quoted} "
            f"  FOR EACH ROW EXECUTE FUNCTION postgis_manager.audit_trigger_fn();"
            f"END IF; END $$;"
        )
    conn.commit()


def drop_trigger(conn: psycopg2.extensions.connection,
                 schema: str, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f'DROP TRIGGER IF EXISTS audit_trigger ON "{schema}"."{table}";')
    conn.commit()


def list_audited_tables(conn: psycopg2.extensions.connection) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(_LIST_AUDITED_SQL)
        return [{"schema": r[0], "table": r[1]} for r in cur.fetchall()]


def get_pgaudit_settings(conn: psycopg2.extensions.connection) -> list[dict]:
    try:
        with conn.cursor() as cur:
            cur.execute(_PGAUDIT_STATUS_SQL)
            return [{"name": r[0], "value": r[1], "desc": r[2]}
                    for r in cur.fetchall()]
    except Exception:
        return []


def fetch_logs(conn: psycopg2.extensions.connection,
               schema: str = "", table: str = "",
               user: str = "", operation: str = "",
               date_from: str = "", date_to: str = "",
               page: int = 0) -> tuple[list[dict], int]:
    """Return (rows, total_count) with server-side filtering + pagination."""
    conditions = []
    params: list = []

    if schema:
        conditions.append("schema_name = %s")
        params.append(schema)
    if table:
        conditions.append("table_name ILIKE %s")
        params.append(f"%{table}%")
    if user:
        conditions.append("db_user ILIKE %s")
        params.append(f"%{user}%")
    if operation and operation != "ALL":
        conditions.append("operation = %s")
        params.append(operation)
    if date_from:
        conditions.append("ts >= %s::timestamptz")
        params.append(date_from)
    if date_to:
        conditions.append("ts <= %s::timestamptz")
        params.append(date_to)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM postgis_manager.audit_log {where}",
            params)
        total = cur.fetchone()["count"]

        cur.execute(
            f"""SELECT id, ts, db_user, schema_name, table_name,
                       operation, pk_column, pk_value,
                       changed_columns, old_data, new_data,
                       old_geom, new_geom
                FROM postgis_manager.audit_log
                {where}
                ORDER BY ts DESC
                LIMIT {PAGE_SIZE} OFFSET {page * PAGE_SIZE}""",
            params)
        rows = [dict(r) for r in cur.fetchall()]

    return rows, total


def export_csv(conn: psycopg2.extensions.connection,
               path: str,
               schema: str = "", table: str = "",
               user: str = "", operation: str = "") -> int:
    """Export filtered audit log to CSV using server-side COPY."""
    parts: list[str] = []
    if schema:
        parts.append(f"schema_name = '{schema}'")
    if table:
        parts.append(f"table_name ILIKE '%{table}%'")
    if user:
        parts.append(f"db_user ILIKE '%{user}%'")
    if operation and operation != "ALL":
        parts.append(f"operation = '{operation}'")
    where = ("WHERE " + " AND ".join(parts)) if parts else ""

    sql = (f"COPY (SELECT id, ts, db_user, schema_name, table_name, "
           f"operation, pk_column, pk_value, changed_columns "
           f"FROM postgis_manager.audit_log {where} ORDER BY ts DESC) "
           f"TO STDOUT WITH CSV HEADER")
    with conn.cursor() as cur, open(path, "w", encoding="utf-8") as f:
        cur.copy_expert(sql, f)
    return 0
