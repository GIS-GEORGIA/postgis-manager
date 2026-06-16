"""DBManager — psycopg2 connection with full PostGIS awareness."""

from __future__ import annotations
import math
import threading
from typing import TYPE_CHECKING, Optional, Callable

if TYPE_CHECKING:
    import geopandas as gpd
import psycopg2
import psycopg2.extras
import psycopg2.extensions


class DBManager:
    """Thread-safe PostGIS connection manager."""

    def __init__(self):
        self.conn: Optional[psycopg2.extensions.connection] = None
        self.params: dict = {}
        self._lock = threading.Lock()

    # ── Connect / Disconnect ─────────────────────────────────────────────────

    def connect(self, host: str, port: str | int, dbname: str,
                user: str, password: str, ssl_mode: str = "prefer",
                timeout: int = 10) -> None:
        with self._lock:
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
            self.conn = psycopg2.connect(
                host=host, port=int(port), dbname=dbname,
                user=user, password=password,
                connect_timeout=timeout,
                sslmode=ssl_mode,
            )
            self.conn.autocommit = True
            self.params = dict(host=host, port=port, dbname=dbname,
                               user=user, password=password)

    def disconnect(self) -> None:
        with self._lock:
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
                self.params = {}

    def is_connected(self) -> bool:
        try:
            if self.conn is None or self.conn.closed:
                return False
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    # ── Server Info ──────────────────────────────────────────────────────────

    def server_info(self) -> dict:
        info = {}

        def _q(sql, default=None):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql)
                    row = cur.fetchone()
                    return row[0] if row else default
            except Exception:
                self.conn.rollback()
                return default

        def _exists(sql) -> bool:
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql)
                    return bool(cur.fetchone())
            except Exception:
                self.conn.rollback()
                return False

        info["pg_version"]       = _q("SELECT version()")
        info["postgis_version"]  = _q("SELECT PostGIS_Lib_Version()")
        info["pgrouting_version"] = _q("SELECT pgr_version()")
        info["topology"] = _exists(
            "SELECT 1 FROM pg_extension WHERE extname='postgis_topology'")
        info["raster"] = _exists(
            "SELECT 1 FROM pg_extension WHERE extname='postgis_raster'")
        return info

    # ── Schema / Layer Browsing ──────────────────────────────────────────────

    def get_schemas(self) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT schema_name FROM information_schema.schemata
                WHERE schema_name NOT IN (
                    'information_schema','pg_catalog','pg_toast',
                    'pg_temp_1','pg_toast_temp_1'
                )
                ORDER BY schema_name
            """)
            return [r[0] for r in cur.fetchall()]

    def get_geometry_layers(self, schema: str) -> list[tuple]:
        """Returns (name, geom_col, geom_type, srid) for vector layers.
        Returns empty list if PostGIS is not installed."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT f_table_name, f_geometry_column, type, srid
                    FROM geometry_columns
                    WHERE f_table_schema = %s
                    ORDER BY f_table_name
                """, (schema,))
                return cur.fetchall()
        except Exception:
            self.conn.rollback()
            return []

    def get_raster_layers(self, schema: str) -> list[tuple]:
        """Returns (name, r_raster_column, srid, scale_x, scale_y) for raster tables."""
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT r_table_name, r_raster_column, srid, scale_x, scale_y
                    FROM raster_columns
                    WHERE r_table_schema = %s
                    ORDER BY r_table_name
                """, (schema,))
                return cur.fetchall()
        except Exception:
            return []

    def get_views(self, schema: str) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.views
                WHERE table_schema = %s
                ORDER BY table_name
            """, (schema,))
            return [r[0] for r in cur.fetchall()]

    def get_all_tables(self, schema: str) -> list[tuple]:
        """All tables with column count and row estimate."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT
                    t.table_name,
                    COUNT(c.column_name) AS col_count,
                    COALESCE(s.n_live_tup, 0) AS row_estimate
                FROM information_schema.tables t
                LEFT JOIN information_schema.columns c
                    ON c.table_schema = t.table_schema AND c.table_name = t.table_name
                LEFT JOIN pg_stat_user_tables s
                    ON s.schemaname = t.table_schema AND s.relname = t.table_name
                WHERE t.table_schema = %s AND t.table_type = 'BASE TABLE'
                GROUP BY t.table_name, s.n_live_tup
                ORDER BY t.table_name
            """, (schema,))
            return cur.fetchall()

    # ── Columns / Metadata ───────────────────────────────────────────────────

    def get_columns(self, schema: str, table: str,
                    exclude_geom: bool = True) -> list[tuple]:
        """Returns (column_name, udt_name, is_nullable, column_default)."""
        with self.conn.cursor() as cur:
            extra = "AND udt_name <> 'geometry'" if exclude_geom else ""
            cur.execute(f"""
                SELECT column_name, udt_name, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s {extra}
                ORDER BY ordinal_position
            """, (schema, table))
            return cur.fetchall()

    def get_geometry_column(self, schema: str, table: str) -> Optional[str]:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT f_geometry_column FROM geometry_columns
                WHERE f_table_schema=%s AND f_table_name=%s LIMIT 1
            """, (schema, table))
            row = cur.fetchone()
            return row[0] if row else None

    def get_table_srid(self, schema: str, table: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT srid FROM geometry_columns
                WHERE f_table_schema=%s AND f_table_name=%s LIMIT 1
            """, (schema, table))
            row = cur.fetchone()
            return row[0] if row else 4326

    def get_feature_count(self, schema: str, table: str,
                          where: str = "") -> int:
        where_sql = f"WHERE {where}" if where.strip() else ""
        with self.conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}" {where_sql}')
            return cur.fetchone()[0]

    def get_extent(self, schema: str, table: str,
                   geom_col: str = "geom") -> Optional[tuple]:
        """Returns (xmin, ymin, xmax, ymax) or None."""
        try:
            with self.conn.cursor() as cur:
                cur.execute(f"""
                    SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)
                    FROM (SELECT ST_Extent("{geom_col}") AS e
                          FROM "{schema}"."{table}") sub
                """)
                return cur.fetchone()
        except Exception:
            return None

    def get_table_indexes(self, schema: str, table: str) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname=%s AND tablename=%s
            """, (schema, table))
            return [{"name": r[0], "def": r[1]} for r in cur.fetchall()]

    # ── Data Read / Write ────────────────────────────────────────────────────

    def fetch_rows(self, schema: str, table: str,
                   limit: int = 1000, offset: int = 0,
                   where: str = "") -> tuple[list, list]:
        cols = [c[0] for c in self.get_columns(schema, table)]
        col_sql = ", ".join(f'"{c}"' for c in cols)
        where_clause = f"WHERE {where}" if where.strip() else ""
        with self.conn.cursor() as cur:
            cur.execute(f"""
                SELECT ctid, {col_sql}
                FROM "{schema}"."{table}"
                {where_clause}
                ORDER BY ctid
                LIMIT %s OFFSET %s
            """, (limit, offset))
            return cols, cur.fetchall()

    def update_cell(self, schema: str, table: str,
                    ctid: str, col: str, value) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                f'UPDATE "{schema}"."{table}" SET "{col}" = %s WHERE ctid = %s',
                (value, ctid)
            )
        self.conn.commit()

    def delete_rows(self, schema: str, table: str, ctids: list) -> int:
        with self.conn.cursor() as cur:
            for ctid in ctids:
                cur.execute(
                    f'DELETE FROM "{schema}"."{table}" WHERE ctid = %s', (ctid,))
        self.conn.commit()
        return len(ctids)

    def delete_all_rows(self, schema: str, table: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(f'TRUNCATE "{schema}"."{table}"')
        self.conn.commit()

    def drop_table(self, schema: str, table: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS "{schema}"."{table}" CASCADE')
        self.conn.commit()

    def execute_sql(self, sql: str) -> tuple[Optional[list], Optional[list], float]:
        """Execute arbitrary SQL; returns (columns, rows, elapsed_ms)."""
        import time
        t0 = time.time()
        with self.conn.cursor() as cur:
            cur.execute(sql)
            if cur.description:
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
            else:
                cols = None
                rows = None
            self.conn.commit()
        elapsed = (time.time() - t0) * 1000
        return cols, rows, elapsed

    # ── Vector Import ────────────────────────────────────────────────────────

    def create_table_from_gdf(self, gdf: "gpd.GeoDataFrame",
                               schema: str, table: str,
                               geom_col: str, srid: int) -> None:
        """Create a PostGIS table matching a GeoDataFrame schema."""
        type_map = {
            "int64": "BIGINT", "int32": "INTEGER", "int16": "SMALLINT",
            "float64": "DOUBLE PRECISION", "float32": "REAL",
            "bool": "BOOLEAN", "object": "TEXT",
            "datetime64[ns]": "TIMESTAMP",
        }
        cols_sql = []
        for col in gdf.columns:
            if col == gdf.geometry.name:
                continue
            pg_type = type_map.get(str(gdf[col].dtype), "TEXT")
            cols_sql.append(f'"{col}" {pg_type}')
        geom_wkb_type = gdf.geom_type.iloc[0].upper() if len(gdf) else "GEOMETRY"
        cols_sql.append(f'"{geom_col}" geometry({geom_wkb_type},{srid})')
        ddl = (f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" '
               f'({", ".join(cols_sql)})')
        with self.conn.cursor() as cur:
            cur.execute(ddl)
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS "{table}_{geom_col}_idx"
                ON "{schema}"."{table}" USING GIST ("{geom_col}")
            """)
        self.conn.commit()

    def import_geodataframe(self, gdf: "gpd.GeoDataFrame",
                             schema: str, table: str,
                             geom_col: str, target_srid: int,
                             log_fn: Callable[[str, str], None],
                             mode: str = "append") -> int:
        """
        Import GeoDataFrame into PostGIS table.
        mode: 'append' | 'replace' | 'create'
        """
        import pandas as pd

        # ── CRS handling ──
        if gdf.crs is None:
            log_fn("CRS not defined — assuming EPSG:4326", "warn")
            gdf = gdf.set_crs(epsg=4326)
        src_epsg = None
        try:
            src_epsg = gdf.crs.to_epsg()
        except Exception:
            pass
        if src_epsg is None:
            try:
                src_epsg = int(gdf.crs.to_authority()[1])
            except Exception:
                pass
        if src_epsg and src_epsg != target_srid:
            log_fn(f"Reprojecting EPSG:{src_epsg} → EPSG:{target_srid}", "info")
            gdf = gdf.to_crs(epsg=target_srid)

        if mode == "replace":
            with self.conn.cursor() as cur:
                cur.execute(f'TRUNCATE "{schema}"."{table}"')
            self.conn.commit()
        elif mode == "create":
            self.create_table_from_gdf(gdf, schema, table, geom_col, target_srid)

        db_cols = [c[0] for c in self.get_columns(schema, table)]
        shp_cols = [c for c in gdf.columns
                    if c != gdf.geometry.name and c in db_cols]

        def _safe_val(v):
            if v is None:
                return None
            try:
                if pd.isna(v):
                    return None
            except Exception:
                pass
            try:
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    return None
            except Exception:
                pass
            s = str(v).strip()
            return None if s.lower() in ("nan", "nat", "none", "", "null") else s

        col_list = ", ".join(f'"{c}"' for c in shp_cols)
        val_list = ", ".join(["%s"] * len(shp_cols))
        geom_part = f"ST_SetSRID(ST_GeomFromText(%s), {target_srid})"
        if col_list:
            sql = (f'INSERT INTO "{schema}"."{table}" '
                   f'({col_list}, "{geom_col}") VALUES ({val_list}, {geom_part})')
        else:
            sql = (f'INSERT INTO "{schema}"."{table}" '
                   f'("{geom_col}") VALUES ({geom_part})')

        try:
            self.conn.rollback()
        except Exception:
            pass

        inserted = skipped = 0
        with self.conn.cursor() as cur:
            for idx, (_, row) in enumerate(gdf.iterrows()):
                geom_wkt = row.geometry.wkt if row.geometry else None
                if not geom_wkt:
                    skipped += 1
                    continue
                vals = ([_safe_val(row[c]) for c in shp_cols] + [geom_wkt]
                        if col_list else [geom_wkt])
                try:
                    cur.execute("SAVEPOINT row_insert")
                    cur.execute(sql, vals)
                    cur.execute("RELEASE SAVEPOINT row_insert")
                    inserted += 1
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT row_insert")
                    skipped += 1
                    if skipped <= 5:
                        log_fn(f"Row {idx} skipped: {e}", "warn")
        self.conn.commit()
        if skipped:
            log_fn(f"Total skipped rows: {skipped}", "warn")
        return inserted

    # ── Export ───────────────────────────────────────────────────────────────

    def export_to_geodataframe(self, schema: str, table: str,
                                geom_col: str, srid: int,
                                where: str = "") -> "gpd.GeoDataFrame":
        import geopandas as gpd
        where_clause = f"WHERE {where}" if where.strip() else ""
        sql = (f'SELECT *, ST_AsText("{geom_col}") AS _wkt '
               f'FROM "{schema}"."{table}" {where_clause}')
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        if not rows:
            return gpd.GeoDataFrame()
        from shapely import wkt as shapely_wkt
        import pandas as pd
        df = pd.DataFrame([dict(r) for r in rows])
        df["geometry"] = df["_wkt"].apply(
            lambda w: shapely_wkt.loads(w) if w else None)
        df = df.drop(columns=[geom_col, "_wkt"], errors="ignore")
        return gpd.GeoDataFrame(df, geometry="geometry", crs=f"EPSG:{srid}")

    # ── Style Manager (db-style-manager pattern) ─────────────────────────────

    def ensure_layer_styles_table(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS public.layer_styles (
                    id SERIAL PRIMARY KEY,
                    f_table_catalog VARCHAR,
                    f_table_schema VARCHAR,
                    f_table_name VARCHAR,
                    f_geometry_column VARCHAR,
                    stylename VARCHAR,
                    styleqml TEXT,
                    stylesld TEXT,
                    useasdefault BOOLEAN DEFAULT FALSE,
                    description TEXT,
                    owner VARCHAR DEFAULT CURRENT_USER,
                    ui TEXT,
                    update_time TIMESTAMP DEFAULT NOW()
                )
            """)
        self.conn.commit()

    def save_style(self, schema: str, table: str, geom_col: str,
                   style_name: str, qml: str, is_default: bool = False) -> None:
        self.ensure_layer_styles_table()
        with self.conn.cursor() as cur:
            cur.execute("""
                DELETE FROM public.layer_styles
                WHERE f_table_schema=%s AND f_table_name=%s AND stylename=%s
            """, (schema, table, style_name))
            if is_default:
                cur.execute("""
                    UPDATE public.layer_styles SET useasdefault=FALSE
                    WHERE f_table_schema=%s AND f_table_name=%s
                """, (schema, table))
            cur.execute("""
                INSERT INTO public.layer_styles
                (f_table_schema, f_table_name, f_geometry_column, stylename, styleqml, useasdefault)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (schema, table, geom_col, style_name, qml, is_default))
        self.conn.commit()

    def get_styles(self, schema: str, table: str) -> list[dict]:
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT id, stylename, useasdefault, description, update_time
                    FROM public.layer_styles
                    WHERE f_table_schema=%s AND f_table_name=%s
                    ORDER BY useasdefault DESC, stylename
                """, (schema, table))
                cols = ["id", "stylename", "useasdefault", "description", "update_time"]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:
            return []

    def load_style(self, style_id: int) -> Optional[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT styleqml FROM public.layer_styles WHERE id=%s",
                        (style_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def delete_style(self, style_id: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM public.layer_styles WHERE id=%s", (style_id,))
        self.conn.commit()

    # ── pgRouting ────────────────────────────────────────────────────────────

    def pgrouting_dijkstra(self, edge_table: str, source_col: str,
                            target_col: str, cost_col: str,
                            from_node: int, to_node: int,
                            directed: bool = True,
                            reverse_cost_col: Optional[str] = None) -> list[dict]:
        rev = f", {reverse_cost_col}" if reverse_cost_col else ""
        sql = f"""
            SELECT seq, path_seq, node, edge, cost, agg_cost
            FROM pgr_dijkstra(
                'SELECT id, {source_col} AS source, {target_col} AS target,
                         {cost_col} AS cost{rev} FROM {edge_table}',
                {from_node}, {to_node}, {str(directed).lower()}
            )
        """
        with self.conn.cursor() as cur:
            cur.execute(sql)
            cols = ["seq", "path_seq", "node", "edge", "cost", "agg_cost"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def pgrouting_driving_distance(self, edge_table: str, source_col: str,
                                    target_col: str, cost_col: str,
                                    from_node: int, max_cost: float) -> list[dict]:
        sql = f"""
            SELECT seq, node, edge, cost, agg_cost
            FROM pgr_drivingDistance(
                'SELECT id, {source_col} AS source, {target_col} AS target,
                         {cost_col} AS cost FROM {edge_table}',
                {from_node}, {max_cost}, FALSE
            )
        """
        with self.conn.cursor() as cur:
            cur.execute(sql)
            cols = ["seq", "node", "edge", "cost", "agg_cost"]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ── Topology ─────────────────────────────────────────────────────────────

    def list_topologies(self) -> list[dict]:
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT id, name, srid, precision FROM topology.topology")
                return [{"id": r[0], "name": r[1], "srid": r[2], "precision": r[3]}
                        for r in cur.fetchall()]
        except Exception:
            return []

    def create_topology(self, name: str, srid: int, precision: float) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT topology.CreateTopology(%s, %s, %s)",
                        (name, srid, precision))
        self.conn.commit()

    def validate_topology(self, topo_name: str) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT error, id1, id2
                FROM topology.ValidateTopology(%s)
            """, (topo_name,))
            return [{"error": r[0], "id1": r[1], "id2": r[2]}
                    for r in cur.fetchall()]

    # ── pgVersion (row-level versioning) ─────────────────────────────────────

    def pgversion_init(self, schema: str, table: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT pgversion.pgv_enable(%s, %s)", (schema, table))
        self.conn.commit()

    def pgversion_commit(self, schema: str, table: str,
                          log_msg: str = "") -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT pgversion.pgv_commit(%s, %s, %s)",
                        (schema, table, log_msg))
        self.conn.commit()

    def pgversion_checkout(self, schema: str, table: str, revision) -> None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT pgversion.pgv_checkout(%s, %s, %s)",
                        (schema, table, revision))
        self.conn.commit()

    def pgversion_diff(self, schema: str, table: str,
                       rev_a, rev_b) -> list[dict]:
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT action, row_data
                    FROM pgversion.pgv_diff(%s, %s, %s, %s)
                """, (schema, table, rev_a, rev_b))
                return [{"action": r[0], "row_data": r[1]}
                        for r in cur.fetchall()]
        except Exception:
            return []

    def pgversion_log(self, schema: str, table: str) -> list[dict]:
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT revision, log_msg, commit_time, branch, is_head
                    FROM pgversion.pgv_log(%s, %s)
                    ORDER BY commit_time DESC
                """, (schema, table))
                cols = ["revision", "log_msg", "commit_time", "branch", "is_head"]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:
            return []

    # ── PostGIS Geoprocessing ────────────────────────────────────────────────

    def geoprocess(self, schema: str, table: str, geom_col: str,
                   operation: str, output_schema: str, output_table: str,
                   distance: float = 100.0, tolerance: float = 1.0) -> str:
        """
        Run a PostGIS operation on a layer and save results to a new table.
        operation: 'buffer' | 'simplify' | 'convex_hull' | 'centroid'
        Returns a human-readable result string.
        """
        if operation == "buffer":
            expr = f'ST_Buffer("{geom_col}"::geography, {distance})::geometry'
        elif operation == "simplify":
            expr = f'ST_Simplify("{geom_col}", {tolerance})'
        elif operation == "convex_hull":
            expr = f'ST_ConvexHull("{geom_col}")'
        elif operation == "centroid":
            expr = f'ST_Centroid("{geom_col}")'
        else:
            expr = f'"{geom_col}"'

        with self.conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE "{output_schema}"."{output_table}" AS
                SELECT *, {expr} AS geom_result
                FROM "{schema}"."{table}"
            """)
            cur.execute(
                f'SELECT COUNT(*) FROM "{output_schema}"."{output_table}"')
            count = cur.fetchone()[0]
        self.conn.commit()
        return (f"{operation} → {output_schema}.{output_table} "
                f"({count:,} features)")

    # ── Linear Referencing / Chainage (pgChainage pattern) ──────────────────

    def generate_chainage_points(self, schema: str, line_table: str,
                                  geom_col: str, id_col: str,
                                  interval: float, start_offset: float,
                                  out_schema: str, out_table: str,
                                  srid: int) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS "{out_schema}"."{out_table}" (
                    id SERIAL PRIMARY KEY,
                    line_id TEXT,
                    m_value DOUBLE PRECISION,
                    geom geometry(Point, {srid})
                )
            """)
            cur.execute(f"""
                INSERT INTO "{out_schema}"."{out_table}" (line_id, m_value, geom)
                SELECT
                    "{id_col}"::TEXT AS line_id,
                    generate_series AS m_value,
                    ST_LineInterpolatePoint(
                        ST_LineMerge("{geom_col}"),
                        LEAST(generate_series / ST_Length("{geom_col}"::geography), 1.0)
                    ) AS geom
                FROM "{schema}"."{line_table}"
                CROSS JOIN generate_series(
                    {start_offset},
                    ST_Length("{geom_col}"::geography),
                    {interval}
                )
                WHERE "{geom_col}" IS NOT NULL
            """)
            cur.execute(f'SELECT COUNT(*) FROM "{out_schema}"."{out_table}"')
            count = cur.fetchone()[0]
        self.conn.commit()
        return count

    # ── Quality / Validation Queries (PostGISQueries pattern) ───────────────

    VALIDATION_QUERIES = {
        "Invalid geometries": """
            SELECT '{schema}' AS schema, '{table}' AS table_name,
                   COUNT(*) AS invalid_count
            FROM "{schema}"."{table}"
            WHERE NOT ST_IsValid("{geom_col}")
        """,
        "Null geometries": """
            SELECT '{schema}' AS schema, '{table}' AS table_name,
                   COUNT(*) AS null_count
            FROM "{schema}"."{table}"
            WHERE "{geom_col}" IS NULL
        """,
        "Duplicate geometries": """
            SELECT '{schema}' AS schema, '{table}' AS table_name,
                   COUNT(*) AS dup_count
            FROM (
                SELECT ST_AsEWKB("{geom_col}"), COUNT(*)
                FROM "{schema}"."{table}"
                GROUP BY ST_AsEWKB("{geom_col}")
                HAVING COUNT(*) > 1
            ) sub
        """,
        "Self-intersecting": """
            SELECT '{schema}' AS schema, '{table}' AS table_name,
                   COUNT(*) AS self_intersect_count
            FROM "{schema}"."{table}"
            WHERE NOT ST_IsSimple("{geom_col}")
        """,
        "Wrong SRID": """
            SELECT '{schema}' AS schema, '{table}' AS table_name,
                   ST_SRID("{geom_col}") AS found_srid, COUNT(*) AS count
            FROM "{schema}"."{table}"
            WHERE ST_SRID("{geom_col}") != {srid}
            GROUP BY ST_SRID("{geom_col}")
        """,
        "Empty geometries": """
            SELECT '{schema}' AS schema, '{table}' AS table_name,
                   COUNT(*) AS empty_count
            FROM "{schema}"."{table}"
            WHERE ST_IsEmpty("{geom_col}")
        """,
    }

    def run_validation(self, schema: str, table: str,
                       geom_col: str, srid: int,
                       checks: list[str] | None = None) -> dict[str, list]:
        run = set(checks) if checks else set(self.VALIDATION_QUERIES)
        results = {}
        for name, sql_tpl in self.VALIDATION_QUERIES.items():
            if name not in run:
                continue
            sql = sql_tpl.format(schema=schema, table=table,
                                 geom_col=geom_col, srid=srid)
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql)
                    cols = [d[0] for d in cur.description]
                    results[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
            except Exception as e:
                results[name] = [{"error": str(e)}]
        return results

    def fix_invalid_geometries(self, schema: str, table: str,
                               geom_col: str) -> int:
        """Apply ST_MakeValid to all invalid geometries in-place."""
        with self.conn.cursor() as cur:
            cur.execute(f"""
                UPDATE "{schema}"."{table}"
                SET "{geom_col}" = ST_MakeValid("{geom_col}")
                WHERE NOT ST_IsValid("{geom_col}")
                  AND "{geom_col}" IS NOT NULL
            """)
            count = cur.rowcount
        self.conn.commit()
        return count
