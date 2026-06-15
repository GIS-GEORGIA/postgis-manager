"""
PostGIS Manager - GUI Application
ავტორი: Kapo / GGTC GIS Team
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont
import psycopg2
import psycopg2.extras
import geopandas as gpd
import threading
import json
import os
import sys
from datetime import datetime
from typing import Optional

# ─────────────────────────────────────────────
#  Constants & Defaults
# ─────────────────────────────────────────────
APP_TITLE  = "PostGIS Manager — GGTC"
ICON_DB    = "🗄️"
ICON_LAYER = "⬡"
ICON_SCHEMA= "📂"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".postgis_manager_cfg.json")

FONT_SIZES = {"პატარა": 11, "საშუალო": 13, "დიდი": 15, "ძალიან დიდი": 17}
DEFAULT_FONT_SIZE = "საშუალო"

THEMES = {"☀️ ნათელი": "light", "🌙 მუქი": "dark"}

GEOM_TYPE_COLORS = {
    "POINT":           "#4FC3F7",
    "MULTIPOINT":      "#81D4FA",
    "LINESTRING":      "#A5D6A7",
    "MULTILINESTRING": "#C8E6C9",
    "POLYGON":         "#FFB74D",
    "MULTIPOLYGON":    "#FFCC02",
    "GEOMETRY":        "#CE93D8",
    "UNKNOWN":         "#EEEEEE",
}


# ─────────────────────────────────────────────
#  Global Font Registry  (created once, updated live)
# ─────────────────────────────────────────────
class AppFonts:
    """Single source of truth for all fonts.
    Call apply(size) to change everything at once."""

    def __init__(self, size: int):
        self._size = size
        # CTkFont objects — shared references passed to every widget
        self.title  = ctk.CTkFont(family="Helvetica", size=size + 2, weight="bold")
        self.bold   = ctk.CTkFont(family="Helvetica", size=size,     weight="bold")
        self.reg    = ctk.CTkFont(family="Helvetica", size=size)
        self.small  = ctk.CTkFont(family="Helvetica", size=size - 1)
        self.mono   = ctk.CTkFont(family="Courier New", size=size - 1)
        # ttk style names that need updating
        self._ttk_styles = []

    def register_ttk(self, style_name: str):
        self._ttk_styles.append(style_name)

    def apply(self, size: int):
        """Update all fonts live — no restart needed."""
        self._size = size
        self.title .configure(size=size + 2)
        self.bold  .configure(size=size)
        self.reg   .configure(size=size)
        self.small .configure(size=size - 1)
        self.mono  .configure(size=size - 1)
        # Update every registered ttk style
        style = ttk.Style()
        rh = size + 12  # row height
        for name in self._ttk_styles:
            if "Heading" in name:
                style.configure(name, font=("Helvetica", size, "bold"))
            else:
                style.configure(name, font=("Courier New", size - 1), rowheight=rh)

    @property
    def size(self):
        return self._size


# ─────────────────────────────────────────────
#  Database Helper
# ─────────────────────────────────────────────
class DBManager:
    def __init__(self):
        self.conn: Optional[psycopg2.extensions.connection] = None
        self.params: dict = {}

    def connect(self, host, port, dbname, user, password):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = psycopg2.connect(
            host=host, port=int(port), dbname=dbname,
            user=user, password=password, connect_timeout=10
        )
        self.conn.autocommit = False
        self.params = dict(host=host, port=port, dbname=dbname, user=user)

    def disconnect(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def is_connected(self):
        try:
            if self.conn is None:
                return False
            self.conn.cursor().execute("SELECT 1")
            return True
        except Exception:
            return False

    def get_schemas(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT table_schema
                FROM information_schema.columns
                WHERE udt_name = 'geometry'
                  AND table_schema NOT IN ('information_schema','pg_catalog','topology')
                ORDER BY table_schema
            """)
            return [r[0] for r in cur.fetchall()]

    def get_layers(self, schema):
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT
                    f_table_name AS name,
                    f_geometry_column AS geom_col,
                    type AS geom_type,
                    srid
                FROM geometry_columns
                WHERE f_table_schema = %s
                ORDER BY f_table_name
            """, (schema,))
            return cur.fetchall()  # (name, geom_col, type, srid)

    def get_feature_count(self, schema, table):
        with self.conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{schema}"."{table}"')
            return cur.fetchone()[0]

    def get_columns(self, schema, table):
        """Returns list of (col_name, data_type) excluding geometry columns."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT column_name, udt_name
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name=%s
                  AND udt_name <> 'geometry'
                ORDER BY ordinal_position
            """, (schema, table))
            return cur.fetchall()

    def get_geometry_column(self, schema, table):
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT f_geometry_column FROM geometry_columns
                WHERE f_table_schema=%s AND f_table_name=%s
                LIMIT 1
            """, (schema, table))
            row = cur.fetchone()
            return row[0] if row else None

    def get_table_srid(self, schema, table):
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT srid FROM geometry_columns
                WHERE f_table_schema=%s AND f_table_name=%s
                LIMIT 1
            """, (schema, table))
            row = cur.fetchone()
            return row[0] if row else 4326

    def fetch_rows(self, schema, table, limit=500, offset=0, where=""):
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

    def update_cell(self, schema, table, ctid, col, value):
        with self.conn.cursor() as cur:
            cur.execute(
                f'UPDATE "{schema}"."{table}" SET "{col}" = %s WHERE ctid = %s',
                (value, ctid)
            )
        self.conn.commit()

    def delete_rows(self, schema, table, ctids):
        with self.conn.cursor() as cur:
            for ctid in ctids:
                cur.execute(f'DELETE FROM "{schema}"."{table}" WHERE ctid = %s', (ctid,))
        self.conn.commit()

    def delete_all_rows(self, schema, table):
        with self.conn.cursor() as cur:
            cur.execute(f'DELETE FROM "{schema}"."{table}"')
        self.conn.commit()

    def import_geodataframe(self, gdf: gpd.GeoDataFrame, schema: str, table: str,
                            geom_col: str, target_srid: int, log_fn):
        """Imports GeoDataFrame rows into existing PostGIS table."""
        # ── CRS / reprojection ──
        if gdf.crs is None:
            log_fn("⚠️ CRS განსაზღვრული არ არის — EPSG:4326 მიეწერება", "warn")
            gdf = gdf.set_crs(epsg=4326)

        src_epsg = None
        try:
            src_epsg = gdf.crs.to_epsg()
        except Exception:
            pass

        # if to_epsg() returned None, try matching by authority
        if src_epsg is None:
            try:
                src_epsg = int(gdf.crs.to_authority()[1])
            except Exception:
                pass

        if src_epsg is None:
            log_fn("⚠️ EPSG ვერ განისაზღვრა — პროექცია გამოტოვდება", "warn")
        elif src_epsg != target_srid:
            log_fn(f"🔄 პროექცია: EPSG:{src_epsg} → EPSG:{target_srid}", "info")
            gdf = gdf.to_crs(epsg=target_srid)

        db_cols = [c[0] for c in self.get_columns(schema, table)]
        shp_cols = [c for c in gdf.columns if c != gdf.geometry.name and c in db_cols]

        import math
        import pandas as pd

        def _safe_val(v):
            """NaN / NaT / inf → SQL NULL; everything else → string."""
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

        inserted = 0
        skipped = 0
        # Make sure we start from a clean transaction state
        try:
            self.conn.rollback()
        except Exception:
            pass

        with self.conn.cursor() as cur:
            for idx, (_, row) in enumerate(gdf.iterrows()):
                geom_wkt = row.geometry.wkt if row.geometry else None
                if geom_wkt is None:
                    skipped += 1
                    continue
                if col_list:
                    vals = [_safe_val(row[c]) for c in shp_cols] + [geom_wkt]
                else:
                    vals = [geom_wkt]
                # Savepoint per row — bad row is skipped, rest continue
                try:
                    cur.execute("SAVEPOINT row_insert")
                    cur.execute(sql, vals)
                    cur.execute("RELEASE SAVEPOINT row_insert")
                    inserted += 1
                except Exception as row_err:
                    cur.execute("ROLLBACK TO SAVEPOINT row_insert")
                    skipped += 1
                    if skipped <= 5:  # log first 5 skipped rows only
                        log_fn(f"⚠️ სტრიქონი {idx} გამოტოვდა: {row_err}", "warn")
        self.conn.commit()
        if skipped:
            log_fn(f"ℹ️ გამოტოვებული სტრიქონები: {skipped}", "warn")
        return inserted


# ─────────────────────────────────────────────
#  Attribute Table Dialog
# ─────────────────────────────────────────────

# SQL filter templates: (label, template_sql, tooltip)
SQL_TEMPLATES = {
    "📝 ტექსტი": [
        ("შეიცავს",          "{col} ILIKE '%{val}%'",            "ნაწილობრივი დამთხვევა (რეგისტრი იგნ.)"),
        ("იწყება",           "{col} ILIKE '{val}%'",             "სტრიქონი იწყება..."),
        ("მთავრდება",        "{col} ILIKE '%{val}'",             "სტრიქონი მთავრდება..."),
        ("ზუსტი დამთხვევა",  "{col} = '{val}'",                  "ზუსტი მნიშვნელობა"),
        ("სიაში (OR)",       "{col} IN ('val1','val2','val3')",  "ერთ-ერთი სიიდან"),
        ("NULL / ცარიელი",   "{col} IS NULL OR {col} = ''",     "ცარიელი ველები"),
        ("არ არის NULL",     "{col} IS NOT NULL AND {col} != ''","შევსებული ველები"),
    ],
    "🔢 რიცხვი": [
        ("უდრის",            "{col} = {val}",                   "ზუსტი რიცხვი"),
        ("მეტია",            "{col} > {val}",                   "მეტი ვიდრე..."),
        ("ნაკლებია",         "{col} < {val}",                   "ნაკლები ვიდრე..."),
        ("შუალედი",          "{col} BETWEEN {min} AND {max}",   "მნიშვნელობა შუალედში"),
        ("სიაში",            "{col} IN (1, 2, 3)",              "ერთ-ერთი სიიდან"),
        ("NULL",             "{col} IS NULL",                   "ცარიელი ველები"),
    ],
    "🔗 ლოგიკა": [
        ("AND — ორივე",      "{cond1} AND {cond2}",             "ორივე პირობა სრულდება"),
        ("OR — ერთ-ერთი",    "{cond1} OR {cond2}",             "ერთ-ერთი პირობა"),
        ("NOT",              "NOT ({cond})",                    "პირობის უარყოფა"),
        ("დაჯგუფება ()",     "({cond1}) AND ({cond2})",         "ფრჩხილებით დაჯგუფება"),
    ],
    "📅 თარიღი": [
        ("კონკრეტული",       "{col}::date = '2024-01-01'",      "კონკრეტული თარიღი"),
        ("პერიოდი",          "{col} BETWEEN '2024-01-01' AND '2024-12-31'", "თარიღის დიაპაზონი"),
        ("წელი",             "EXTRACT(YEAR FROM {col}) = 2024", "კონკრეტული წელი"),
        ("ბოლო N დღე",       "{col} >= NOW() - INTERVAL '30 days'", "ბოლო 30 დღის ჩანაწ."),
    ],
    "🌍 გეომ.": [
        ("ფართობი >",        "ST_Area(geom) > {val}",           "გეომეტრიის ფართობი"),
        ("სიგრძე >",         "ST_Length(geom) > {val}",         "გეომეტრიის სიგრძე"),
        ("NULL geom",        "geom IS NULL",                    "ცარიელი გეომეტრია"),
        ("SRID",             "ST_SRID(geom) = 4326",            "კოორდინ. სისტემა"),
    ],
}


class FilterHelperPanel(ctk.CTkToplevel):
    """Floating SQL filter builder panel."""

    def __init__(self, parent, target_text_widget, cols, fonts):
        super().__init__(parent)
        self.target = target_text_widget
        self.cols   = cols
        self.F      = fonts

        self.title("🔧 SQL ფილტრის დამხმარე")
        self.geometry("540x620")
        self.resizable(True, True)
        self.transient(parent)   # stays on top of parent

        self._build()

    def _build(self):
        F = self.F

        # ── Column picker ──
        col_frame = ctk.CTkFrame(self, corner_radius=8)
        col_frame.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(col_frame, text="📋 სვეტის ჩასმა:", font=F.bold).pack(anchor="w", padx=8, pady=(6,2))

        col_scroll = ctk.CTkScrollableFrame(col_frame, height=56, orientation="horizontal")
        col_scroll.pack(fill="x", padx=6, pady=(0,6))
        for col in self.cols:
            ctk.CTkButton(
                col_scroll, text=col, font=F.small,
                width=max(60, len(col)*8), height=26,
                fg_color="#1A237E", hover_color="#283593",
                command=lambda c=col: self._insert(c)
            ).pack(side="left", padx=2, pady=2)

        # ── Template tabs ──
        ctk.CTkLabel(self, text="📌 შაბლონები (დააჭირეთ ჩასასმელად):",
                     font=F.bold).pack(anchor="w", padx=12, pady=(6,2))

        tab_view = ctk.CTkTabview(self, anchor="w")
        tab_view.pack(fill="both", expand=True, padx=10, pady=(0,4))

        for category, templates in SQL_TEMPLATES.items():
            tab = tab_view.add(category)
            tab.grid_columnconfigure(0, weight=1)
            for i, (label, sql, tip) in enumerate(templates):
                row_f = ctk.CTkFrame(tab, corner_radius=6, fg_color=("gray90","gray20"))
                row_f.grid(row=i, column=0, sticky="ew", padx=4, pady=3)
                row_f.grid_columnconfigure(1, weight=1)

                ctk.CTkButton(
                    row_f, text=f"  ⊕  {label}", anchor="w",
                    font=F.reg, height=30, width=150,
                    fg_color="#0D47A1", hover_color="#1565C0",
                    command=lambda s=sql: self._insert(s)
                ).grid(row=0, column=0, padx=(6,4), pady=4)

                ctk.CTkLabel(
                    row_f, text=sql, font=F.mono,
                    text_color=("gray40","gray70"), anchor="w"
                ).grid(row=0, column=1, padx=4, sticky="w")

                ctk.CTkLabel(
                    row_f, text=f"💡 {tip}", font=ctk.CTkFont(size=F.size-2),
                    text_color=("gray50","gray60"), anchor="w"
                ).grid(row=1, column=0, columnspan=2, padx=12, pady=(0,4), sticky="w")

        # ── Quick operators ──
        op_frame = ctk.CTkFrame(self, corner_radius=8, height=40)
        op_frame.pack(fill="x", padx=10, pady=(0,6))
        op_frame.pack_propagate(False)
        ctk.CTkLabel(op_frame, text="სწრაფი:", font=F.small).pack(side="left", padx=(8,4))
        for op in ["AND", "OR", "NOT", "ILIKE", "IS NULL", "IS NOT NULL", "BETWEEN", "IN"]:
            ctk.CTkButton(
                op_frame, text=op, font=F.small,
                width=max(40, len(op)*9), height=26,
                fg_color="#4A148C", hover_color="#6A1B9A",
                command=lambda o=op: self._insert(f" {o} ")
            ).pack(side="left", padx=2, pady=4)

    def _insert(self, text):
        """Insert text at cursor position in the target Text widget."""
        try:
            self.target.insert(tk.INSERT, text)
        except Exception:
            self.target.insert("insert", text)
        self.target.focus_set()


class AttributeTableDialog(ctk.CTkToplevel):
    def __init__(self, parent, db: DBManager, schema, table, fonts: "AppFonts"):
        super().__init__(parent)
        self.db = db
        self.schema = schema
        self.table = table
        self.fonts = fonts
        self.limit = 500
        self.offset = 0
        self.where_filter = ""
        self.cols = []
        self.rows = []
        self._helper_win = None

        self.title(f"ატრიბუტული ცხრილი — {schema}.{table}")
        self.geometry("1200x700")
        self.resizable(True, True)
        self._build_ui()
        self._load_data()

    def _build_ui(self):
        F = self.fonts

        # ═══════════════════════════════════════════
        # TOP: filter area (collapsible panel)
        # ═══════════════════════════════════════════
        filter_outer = ctk.CTkFrame(self, corner_radius=0)
        filter_outer.pack(fill="x", padx=0, pady=0)

        # ── filter toolbar row ──
        toolbar = ctk.CTkFrame(filter_outer, fg_color="transparent")
        toolbar.pack(fill="x", padx=6, pady=(6,2))

        ctk.CTkLabel(toolbar, text="WHERE", font=F.bold,
                     text_color="#90CAF9").pack(side="left", padx=(4,6))

        # Multi-line SQL text box
        self.filter_text = tk.Text(
            filter_outer,
            height=3,
            font=("Courier New", F.size),
            wrap="word",
            relief="flat",
            bd=0,
            padx=6, pady=4
        )
        self.filter_text.pack(fill="x", padx=8, pady=(0,4))
        self.filter_text.bind("<Control-Return>", lambda e: self._apply_filter())
        self.filter_text.bind("<Control-BackSpace>", lambda e: self._clear_filter())
        self._update_filter_colors()

        # ── action buttons row ──
        btn_row = ctk.CTkFrame(filter_outer, fg_color="transparent")
        btn_row.pack(fill="x", padx=6, pady=(0,6))

        ctk.CTkButton(btn_row, text="🔍  გაფილტრე  (Ctrl+↵)", font=F.bold,
                      fg_color="#1565C0", width=190,
                      command=self._apply_filter).pack(side="left", padx=(0,4))
        ctk.CTkButton(btn_row, text="♻️  გასუფთავება  (Ctrl+⌫)", font=F.reg,
                      fg_color="#37474F", width=190,
                      command=self._clear_filter).pack(side="left", padx=4)
        ctk.CTkButton(btn_row, text="🔧  SQL დამხმარე", font=F.reg,
                      fg_color="#4A148C", width=150,
                      command=self._open_helper).pack(side="left", padx=4)

        # filter status badge
        self.filter_badge = ctk.CTkLabel(btn_row, text="", font=F.small,
                                          text_color="#FFB74D")
        self.filter_badge.pack(side="left", padx=8)

        ctk.CTkButton(btn_row, text="🗑 წაშლა (მონიშნული)",
                      font=F.bold, fg_color="#C62828", width=170,
                      command=self._delete_selected).pack(side="right", padx=(4,6))

        # ── separator ──
        sep = ctk.CTkFrame(self, height=2, fg_color=("#CCCCCC","#333333"))
        sep.pack(fill="x")

        # ═══════════════════════════════════════════
        # MIDDLE: treeview
        # ═══════════════════════════════════════════
        tree_frame = ctk.CTkFrame(self, corner_radius=0)
        tree_frame.pack(fill="both", expand=True, padx=0, pady=0)

        style = ttk.Style()
        fs = F.size
        style.configure("PostGIS.Treeview",
                         font=("Courier New", fs - 1), rowheight=fs + 12)
        style.configure("PostGIS.Treeview.Heading",
                         font=("Helvetica", fs, "bold"))
        F.register_ttk("PostGIS.Treeview")
        F.register_ttk("PostGIS.Treeview.Heading")

        self.tree = ttk.Treeview(tree_frame, selectmode="extended",
                                  style="PostGIS.Treeview")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_double_click)

        # ═══════════════════════════════════════════
        # BOTTOM: status bar
        # ═══════════════════════════════════════════
        self.status_var = tk.StringVar(value="")
        status_bar = ctk.CTkFrame(self, height=26, corner_radius=0)
        status_bar.pack(fill="x")
        status_bar.pack_propagate(False)
        ctk.CTkLabel(status_bar, textvariable=self.status_var,
                     font=F.small, anchor="w").pack(side="left", padx=10)

    def _update_filter_colors(self):
        """Apply theme-aware colors to the raw tk.Text widget."""
        mode = ctk.get_appearance_mode()
        if mode == "Dark":
            self.filter_text.configure(bg="#1e1e2e", fg="#e0e0e0",
                                        insertbackground="white",
                                        selectbackground="#1565C0")
        else:
            self.filter_text.configure(bg="#f0f4f8", fg="#1a1a2e",
                                        insertbackground="black",
                                        selectbackground="#90CAF9")

    def _get_filter_sql(self):
        return self.filter_text.get("1.0", "end").strip()

    def _open_helper(self):
        """Open or focus the SQL helper panel."""
        if self._helper_win and self._helper_win.winfo_exists():
            self._helper_win.lift()
            self._helper_win.focus_force()
            return
        self._helper_win = FilterHelperPanel(
            self, self.filter_text, self.cols, self.fonts
        )

    def _load_data(self):
        try:
            self.cols, self.rows = self.db.fetch_rows(
                self.schema, self.table, self.limit, self.offset, self.where_filter
            )
            self._populate_tree()
            # Refresh column list in helper if open
            if self._helper_win and self._helper_win.winfo_exists():
                pass  # helper reads self.cols on demand
        except Exception as e:
            messagebox.showerror("შეცდომა", str(e), parent=self)

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = self.cols
        self.tree["show"] = "headings"
        for col in self.cols:
            self.tree.heading(col, text=col, anchor="w")
            self.tree.column(col, width=max(100, len(col)*10), minwidth=60)
        for row in self.rows:
            ctid = row[0]
            vals = row[1:]
            self.tree.insert("", "end", iid=str(ctid), values=vals)
        total = self.db.get_feature_count(self.schema, self.table)
        showing = len(self.rows)
        filt = f"  |  🔍 ფილტრი აქტიური" if self.where_filter else ""
        self.status_var.set(
            f"სულ: {total:,}  |  ნაჩვენებია: {showing}  |  offset: {self.offset}{filt}"
        )
        if self.where_filter:
            self.filter_badge.configure(text=f"🔍 ფილტრი: {self.where_filter[:40]}{'…' if len(self.where_filter)>40 else ''}")
        else:
            self.filter_badge.configure(text="")

    def _apply_filter(self):
        self.where_filter = self._get_filter_sql()
        self.offset = 0
        self._load_data()

    def _clear_filter(self):
        self.filter_text.delete("1.0", "end")
        self.where_filter = ""
        self.offset = 0
        self._load_data()

    def _on_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or not col_id:
            return
        col_idx = int(col_id[1:]) - 1
        col_name = self.cols[col_idx]
        old_val = self.tree.item(row_id, "values")[col_idx]
        self._inline_edit(row_id, col_id, col_name, old_val)

    def _inline_edit(self, row_id, col_id, col_name, old_val):
        bbox = self.tree.bbox(row_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox
        entry = tk.Entry(self.tree, font=("Courier New", self.fonts.size))
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, old_val)
        entry.focus_set()

        def commit(e=None):
            new_val = entry.get()
            entry.destroy()
            if new_val == old_val:
                return
            try:
                self.db.update_cell(self.schema, self.table, row_id, col_name, new_val)
                vals = list(self.tree.item(row_id, "values"))
                col_idx = int(col_id[1:]) - 1
                vals[col_idx] = new_val
                self.tree.item(row_id, values=vals)
            except Exception as ex:
                messagebox.showerror("შეცდომა", str(ex), parent=self)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda e: entry.destroy())

    def _delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("ინფო", "მონიშნეთ სტრიქონები წასაშლელად", parent=self)
            return
        if not messagebox.askyesno("დადასტურება",
                                    f"წაიშლება {len(selected)} ობიექტი. გაგრძელება?",
                                    parent=self):
            return
        try:
            self.db.delete_rows(self.schema, self.table, list(selected))
            for iid in selected:
                self.tree.delete(iid)
            total = self.db.get_feature_count(self.schema, self.table)
            self.status_var.set(
                f"სულ: {total:,}  |  ნაჩვენებია: {len(self.tree.get_children())}")
        except Exception as e:
            messagebox.showerror("შეცდომა", str(e), parent=self)

    def _save_changes(self):
        messagebox.showinfo("ინფო",
                             "ცვლილებები ავტომატურად ინახება თითოეული რედაქტირებისას.",
                             parent=self)


# ─────────────────────────────────────────────
#  Main Application
# ─────────────────────────────────────────────
class PostGISManagerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.db = DBManager()
        self._theme_mode = "dark"
        self._schema_layers: dict = {}
        self._selected_schema = None
        self._selected_layer = None
        self._selected_srid   = 4326
        self._selected_geom_col = None

        ctk.set_appearance_mode(self._theme_mode)
        ctk.set_default_color_theme("blue")

        self.title(APP_TITLE)
        self.geometry("1300x800")
        self.minsize(900, 600)

        self._load_config()
        # ── Shared font registry — must be created AFTER Tk root exists ──
        self.F = AppFonts(FONT_SIZES[DEFAULT_FONT_SIZE])
        self._build_ui()

    # ── Config persistence ──────────────────
    def _load_config(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                self._cfg = json.load(f)
        except Exception:
            self._cfg = {"host": "localhost", "port": "5432",
                         "dbname": "", "user": "postgres", "password": ""}

    def _save_config(self):
        data = {
            "host":     self.host_entry.get(),
            "port":     self.port_entry.get(),
            "dbname":   self.db_entry.get(),
            "user":     self.user_entry.get(),
            "password": self.pass_entry.get(),
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ── UI Builder ──────────────────────────
    def _build_ui(self):
        # Horizontal PanedWindow: sidebar (left) <-> main area (right)
        self.h_pane = tk.PanedWindow(
            self, orient=tk.HORIZONTAL,
            sashwidth=7, sashrelief=tk.RAISED,
            bg="#1a1a2e", bd=0
        )
        self.h_pane.pack(fill="both", expand=True)

        # Left sidebar
        self.sidebar = ctk.CTkFrame(self.h_pane, corner_radius=0)
        self.h_pane.add(self.sidebar, minsize=220, width=300, stretch="never")
        self._build_sidebar()

        # Right: wrap in a regular Frame so vertical pane can live inside
        self.main_area = ctk.CTkFrame(self.h_pane, corner_radius=0)
        self.h_pane.add(self.main_area, minsize=500, stretch="always")
        self._build_main_area()

    def _build_sidebar(self):
        F = self.F

        # ── Title ──
        title_frame = ctk.CTkFrame(self.sidebar, corner_radius=8, fg_color="#1565C0")
        title_frame.pack(fill="x", padx=8, pady=(10, 6))
        ctk.CTkLabel(title_frame, text=f"{ICON_DB}  PostGIS Manager",
                     font=F.title, text_color="white").pack(pady=8)

        # ── Connection ──
        conn_frame = ctk.CTkFrame(self.sidebar, corner_radius=8)
        conn_frame.pack(fill="x", padx=8, pady=4)

        ctk.CTkLabel(conn_frame, text="🔌 კავშირის პარამეტრები",
                     font=F.bold).pack(anchor="w", padx=10, pady=(8, 4))

        def row_input(parent, label, default, show=None):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            f.pack(fill="x", padx=6, pady=2)
            ctk.CTkLabel(f, text=label, font=F.reg, width=80, anchor="e").pack(side="left", padx=(0,6))
            e = ctk.CTkEntry(f, font=F.reg, show=show)
            e.insert(0, default)
            e.pack(side="left", fill="x", expand=True)
            return e

        self.host_entry = row_input(conn_frame, "Host:", self._cfg.get("host","localhost"))
        self.port_entry = row_input(conn_frame, "Port:", self._cfg.get("port","5432"))
        self.db_entry   = row_input(conn_frame, "DB Name:", self._cfg.get("dbname",""))
        self.user_entry = row_input(conn_frame, "User:", self._cfg.get("user","postgres"))
        self.pass_entry = row_input(conn_frame, "Password:", self._cfg.get("password",""), show="•")

        btn_row = ctk.CTkFrame(conn_frame, fg_color="transparent")
        btn_row.pack(fill="x", padx=6, pady=(4, 8))
        self.connect_btn = ctk.CTkButton(btn_row, text="🔗 დაკავშირება",
                                          font=F.bold, fg_color="#1565C0",
                                          command=self._connect_thread)
        self.connect_btn.pack(side="left", fill="x", expand=True, padx=(0, 3))
        ctk.CTkButton(btn_row, text="✂️", width=36, font=F.bold,
                      fg_color="#444",
                      command=self._disconnect).pack(side="left")

        self.status_dot = ctk.CTkLabel(conn_frame, text="⬤ გათიშული",
                                        font=F.small, text_color="#EF5350")
        self.status_dot.pack(anchor="w", padx=10, pady=(0,6))

        # ── Layers Tree ──
        ctk.CTkLabel(self.sidebar, text="📋 სქემები / შრეები",
                     font=F.bold).pack(anchor="w", padx=12, pady=(8, 2))

        tree_frame = ctk.CTkFrame(self.sidebar, corner_radius=8)
        tree_frame.pack(fill="both", expand=True, padx=8, pady=4)

        style = ttk.Style()
        fs = F.size
        style.configure("Layer.Treeview",
                         font=("Helvetica", fs - 1), rowheight=fs + 10)
        style.configure("Layer.Treeview.Heading",
                         font=("Helvetica", fs - 1, "bold"))
        F.register_ttk("Layer.Treeview")
        F.register_ttk("Layer.Treeview.Heading")

        self.layer_tree = ttk.Treeview(tree_frame, style="Layer.Treeview",
                                        show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                             command=self.layer_tree.yview)
        self.layer_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.layer_tree.pack(fill="both", expand=True, padx=2, pady=2)
        self.layer_tree.bind("<<TreeviewSelect>>", self._on_layer_select)
        self.layer_tree.bind("<Double-1>", self._on_layer_double_click)

        ctk.CTkButton(self.sidebar, text="🔄 განახლება", font=F.reg,
                      fg_color="#1B5E20", command=self._refresh_layers
                      ).pack(fill="x", padx=8, pady=4)

        # ── Settings ──
        settings_frame = ctk.CTkFrame(self.sidebar, corner_radius=8)
        settings_frame.pack(fill="x", padx=8, pady=(4, 8))

        ctk.CTkLabel(settings_frame, text="⚙️ პარამეტრები", font=F.bold
                     ).pack(anchor="w", padx=10, pady=(6, 2))

        row1 = ctk.CTkFrame(settings_frame, fg_color="transparent")
        row1.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(row1, text="თემა:", font=F.reg, width=60).pack(side="left")
        self.theme_menu = ctk.CTkOptionMenu(
            row1, values=list(THEMES.keys()), font=F.reg,
            command=self._change_theme)
        self.theme_menu.set("🌙 მუქი")
        self.theme_menu.pack(side="left", fill="x", expand=True)

        row2 = ctk.CTkFrame(settings_frame, fg_color="transparent")
        row2.pack(fill="x", padx=6, pady=(2, 8))
        ctk.CTkLabel(row2, text="შრიფტი:", font=F.reg, width=60).pack(side="left")
        self.font_menu = ctk.CTkOptionMenu(
            row2, values=list(FONT_SIZES.keys()), font=F.reg,
            command=self._change_font)
        self.font_menu.set(DEFAULT_FONT_SIZE)
        self.font_menu.pack(side="left", fill="x", expand=True)

    def _build_main_area(self):
        F = self.F

        # ── Top fixed area: info card + buttons ──
        top_fixed = ctk.CTkFrame(self.main_area, corner_radius=0, fg_color="transparent")
        top_fixed.pack(fill="x")

        # Layer Info Card
        self.info_card = ctk.CTkFrame(top_fixed, corner_radius=10,
                                       fg_color="#1A237E")
        self.info_card.pack(fill="x", padx=12, pady=(10, 4))
        self.info_label = ctk.CTkLabel(
            self.info_card,
            text="← სქემა / შრე შეარჩიეთ მარცნიდან",
            font=F.bold, text_color="#90CAF9"
        )
        self.info_label.pack(side="left", padx=14, pady=10)
        self.count_label = ctk.CTkLabel(
            self.info_card, text="", font=F.reg, text_color="#B3E5FC"
        )
        self.count_label.pack(side="right", padx=14, pady=10)

        # Action Buttons
        btn_frame = ctk.CTkFrame(top_fixed, corner_radius=10)
        btn_frame.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(btn_frame, text="🛠  ოპერაციები:", font=F.bold
                     ).pack(side="left", padx=10, pady=8)
        self.import_btn = ctk.CTkButton(
            btn_frame, text="📥  Shapefile-ის იმპორტი",
            font=F.bold, fg_color="#1565C0", width=180,
            state="disabled", command=self._import_shapefile)
        self.import_btn.pack(side="left", padx=6, pady=8)
        self.table_btn = ctk.CTkButton(
            btn_frame, text="📊  ატრიბუტული ცხრილი",
            font=F.bold, fg_color="#4A148C", width=180,
            state="disabled", command=self._open_attribute_table)
        self.table_btn.pack(side="left", padx=6, pady=8)
        self.delall_btn = ctk.CTkButton(
            btn_frame, text="🗑  ყველა ობიექტის წაშლა",
            font=F.bold, fg_color="#B71C1C", width=190,
            state="disabled", command=self._delete_all)
        self.delall_btn.pack(side="left", padx=6, pady=8)

        # ── Vertical PanedWindow: preview (top) <-> log (bottom) ──
        self.v_pane = tk.PanedWindow(
            self.main_area, orient=tk.VERTICAL,
            sashwidth=7, sashrelief=tk.RAISED,
            bg="#1a1a2e", bd=0
        )
        self.v_pane.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Shapefile Preview ──
        self.preview_frame = ctk.CTkFrame(self.v_pane, corner_radius=10)
        self.v_pane.add(self.preview_frame, minsize=80, height=260, stretch="always")

        ctk.CTkLabel(self.preview_frame,
                     text="📋 Shapefile პრევიუ / სტატუსი",
                     font=F.bold).pack(anchor="w", padx=10, pady=(8, 4))

        style = ttk.Style()
        fs = F.size
        style.configure("Preview.Treeview",
                         font=("Courier New", fs - 1), rowheight=fs + 8)
        style.configure("Preview.Treeview.Heading",
                         font=("Helvetica", fs - 1, "bold"))
        F.register_ttk("Preview.Treeview")
        F.register_ttk("Preview.Treeview.Heading")

        pf = ctk.CTkFrame(self.preview_frame, corner_radius=0)
        pf.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.preview_tree = ttk.Treeview(pf, style="Preview.Treeview", show="headings")
        pvsb = ttk.Scrollbar(pf, orient="vertical", command=self.preview_tree.yview)
        phsb = ttk.Scrollbar(pf, orient="horizontal", command=self.preview_tree.xview)
        self.preview_tree.configure(yscrollcommand=pvsb.set, xscrollcommand=phsb.set)
        pvsb.pack(side="right", fill="y")
        phsb.pack(side="bottom", fill="x")
        self.preview_tree.pack(fill="both", expand=True)

        # ── Log Panel ──
        log_frame = ctk.CTkFrame(self.v_pane, corner_radius=10)
        self.v_pane.add(log_frame, minsize=60, height=180, stretch="never")

        log_header = ctk.CTkFrame(log_frame, fg_color="transparent")
        log_header.pack(fill="x", padx=6, pady=(4, 0))
        ctk.CTkLabel(log_header, text="📜 ლოგი", font=F.bold).pack(side="left")
        ctk.CTkButton(log_header, text="🧹 გასუფთავება", width=100,
                      font=F.small, fg_color="#333",
                      command=self._clear_log).pack(side="right", padx=4)

        self.log_text = ctk.CTkTextbox(
            log_frame, corner_radius=6,
            font=F.mono, wrap="word"
        )
        self.log_text.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        self.log_text.configure(state="disabled")

        self.log_text._textbox.tag_configure("info",    foreground="#64B5F6")
        self.log_text._textbox.tag_configure("success", foreground="#81C784")
        self.log_text._textbox.tag_configure("warn",    foreground="#FFB74D")
        self.log_text._textbox.tag_configure("error",   foreground="#EF9A9A")
        self.log_text._textbox.tag_configure("dim",     foreground="#888888")


    # ── Logging ─────────────────────────────
    def log(self, msg: str, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        icons = {"info": "ℹ", "success": "✔", "warn": "⚠", "error": "✖", "dim": "·"}
        icon = icons.get(level, "ℹ")
        line = f"[{ts}] {icon}  {msg}\n"
        self.log_text.configure(state="normal")
        self.log_text._textbox.insert("end", line, level)
        self.log_text._textbox.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ── Connection ──────────────────────────
    def _connect_thread(self):
        self.connect_btn.configure(state="disabled", text="⏳ დაკავშირება...")
        t = threading.Thread(target=self._do_connect, daemon=True)
        t.start()

    def _do_connect(self):
        host   = self.host_entry.get().strip()
        port   = self.port_entry.get().strip()
        dbname = self.db_entry.get().strip()
        user   = self.user_entry.get().strip()
        pwd    = self.pass_entry.get()
        try:
            self.log(f"🔗 კავშირი: {user}@{host}:{port}/{dbname}", "info")
            self.db.connect(host, port, dbname, user, pwd)
            self._save_config()
            self.after(0, self._on_connect_success)
        except Exception as e:
            err = str(e)
            self.after(0, lambda m=err: self._on_connect_fail(m))

    def _on_connect_success(self):
        self.connect_btn.configure(state="normal", text="🔗 დაკავშირება")
        self.status_dot.configure(text="⬤ დაკავშირებულია", text_color="#66BB6A")
        self.log("✅ წარმატებით დაუკავშირდა!", "success")
        self._refresh_layers()

    def _on_connect_fail(self, err):
        self.connect_btn.configure(state="normal", text="🔗 დაკავშირება")
        self.status_dot.configure(text="⬤ შეცდომა", text_color="#EF5350")
        self.log(f"კავშირის შეცდომა: {err}", "error")
        messagebox.showerror("კავშირის შეცდომა", err)

    def _disconnect(self):
        self.db.disconnect()
        self.status_dot.configure(text="⬤ გათიშული", text_color="#EF5350")
        self.layer_tree.delete(*self.layer_tree.get_children())
        self._schema_layers.clear()
        self._set_layer_buttons_state("disabled")
        self.log("🔌 კავშირი გაწყვეტილია", "warn")

    # ── Layers ──────────────────────────────
    def _refresh_layers(self):
        if not self.db.is_connected():
            self.log("ბაზა არ არის დაკავშირებული", "warn")
            return
        try:
            self.layer_tree.delete(*self.layer_tree.get_children())
            self._schema_layers.clear()
            schemas = self.db.get_schemas()
            self.log(f"სქემები ნაპოვნია: {len(schemas)}", "info")
            for schema in schemas:
                layers = self.db.get_layers(schema)
                self._schema_layers[schema] = layers
                schema_node = self.layer_tree.insert(
                    "", "end", iid=f"schema:{schema}",
                    text=f"  {ICON_SCHEMA}  {schema}  ({len(layers)})",
                    open=True
                )
                for name, geom_col, geom_type, srid in layers:
                    gt = geom_type.upper().split("(")[0].split(" ")[0] if geom_type else "UNKNOWN"
                    display = f"  {ICON_LAYER}  {name}  [{gt}] EPSG:{srid}"
                    self.layer_tree.insert(schema_node, "end",
                                            iid=f"layer:{schema}:{name}",
                                            text=display)
                self.log(f"  {schema}: {len(layers)} შრე", "dim")
        except Exception as e:
            self.log(f"შრეების ჩატვირთვის შეცდომა: {e}", "error")

    def _on_layer_select(self, event):
        sel = self.layer_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if not iid.startswith("layer:"):
            return
        _, schema, table = iid.split(":", 2)
        self._selected_schema = schema
        self._selected_layer  = table
        layers = self._schema_layers.get(schema, [])
        info   = next((l for l in layers if l[0] == table), None)
        if info:
            _, geom_col, geom_type, srid = info
            self._selected_geom_col = geom_col
            self._selected_srid     = srid
        try:
            count = self.db.get_feature_count(schema, table)
            self.info_label.configure(
                text=f"{ICON_SCHEMA} {schema}  /  {ICON_LAYER} {table}")
            self.count_label.configure(
                text=f"ობიექტები: {count:,}  |  EPSG:{self._selected_srid}")
        except Exception:
            pass
        self._set_layer_buttons_state("normal")

    def _on_layer_double_click(self, event):
        sel = self.layer_tree.selection()
        if sel and sel[0].startswith("layer:"):
            self._open_attribute_table()

    def _set_layer_buttons_state(self, state):
        for btn in (self.import_btn, self.table_btn, self.delall_btn):
            btn.configure(state=state)

    # ── Import Shapefile ────────────────────
    def _import_shapefile(self):
        if not self._selected_layer:
            return
        path = filedialog.askopenfilename(
            title="Shapefile-ის არჩევა",
            filetypes=[("Shapefile", "*.shp"), ("All files", "*.*")]
        )
        if not path:
            return
        self._show_shapefile_preview(path)
        if not messagebox.askyesno("იმპორტი",
                                    f"ჩაიტვირთოს {os.path.basename(path)} შრეში\n"
                                    f"  {self._selected_schema}.{self._selected_layer}?"):
            return
        t = threading.Thread(target=self._do_import, args=(path,), daemon=True)
        t.start()

    def _show_shapefile_preview(self, path):
        try:
            gdf = gpd.read_file(path)
            self.preview_tree.delete(*self.preview_tree.get_children())
            cols = [c for c in gdf.columns if c != gdf.geometry.name]
            self.preview_tree["columns"] = cols
            self.preview_tree["show"] = "headings"
            for col in cols:
                self.preview_tree.heading(col, text=col)
                self.preview_tree.column(col, width=100, minwidth=60)
            for _, row in gdf.head(20).iterrows():
                self.preview_tree.insert("", "end", values=[str(row[c]) for c in cols])
            crs_str = str(gdf.crs) if gdf.crs else "N/A"
            self.log(f"📂 Shapefile: {os.path.basename(path)} | "
                     f"{len(gdf)} ობიექტი | CRS: {crs_str}", "info")
        except Exception as e:
            self.log(f"Shapefile წაკითხვის შეცდომა: {e}", "error")

    def _do_import(self, path):
        try:
            self.log(f"⏳ იმპორტი იწყება: {os.path.basename(path)}", "info")
            gdf = gpd.read_file(path)
            n = self.db.import_geodataframe(
                gdf,
                self._selected_schema,
                self._selected_layer,
                self._selected_geom_col,
                self._selected_srid,
                lambda msg, lvl="info": self.after(0, lambda m=msg, l=lvl: self.log(m, l))
            )
            self.after(0, lambda cnt=n: self.log(
                f"✅ იმპორტი დასრულდა: {cnt} ობიექტი ჩაიტვირთა", "success"))
            self.after(0, self._refresh_count)
        except Exception as e:
            try:
                self.db.conn.rollback()
            except Exception:
                pass
            err_msg = str(e)
            self.after(0, lambda m=err_msg: self.log(f"❌ იმპორტის შეცდომა: {m}", "error"))

    def _refresh_count(self):
        if self._selected_layer:
            try:
                count = self.db.get_feature_count(
                    self._selected_schema, self._selected_layer)
                self.count_label.configure(
                    text=f"ობიექტები: {count:,}  |  EPSG:{self._selected_srid}")
            except Exception:
                pass

    # ── Attribute Table ─────────────────────
    def _open_attribute_table(self):
        if not self._selected_layer:
            return
        try:
            dlg = AttributeTableDialog(
                self, self.db,
                self._selected_schema,
                self._selected_layer,
                self.F
            )
            dlg.grab_set()
        except Exception as e:
            messagebox.showerror("შეცდომა", str(e))
            self.log(f"ატრიბუტული ცხრილი: {e}", "error")

    # ── Delete All ──────────────────────────
    def _delete_all(self):
        if not self._selected_layer:
            return
        if not messagebox.askyesno(
            "⚠️ წაშლა",
            f"ყველა ობიექტი წაიშლება შრიდან\n"
            f"  {self._selected_schema}.{self._selected_layer}\n\n"
            "ეს ოპერაცია შეუქცევადია! გაგრძელება?",
            icon="warning"
        ):
            return
        try:
            self.db.delete_all_rows(self._selected_schema, self._selected_layer)
            self.log(f"🗑 {self._selected_schema}.{self._selected_layer} — "
                     f"ყველა ობიექტი წაიშალა", "warn")
            self._refresh_count()
        except Exception as e:
            self.log(f"წაშლის შეცდომა: {e}", "error")
            messagebox.showerror("შეცდომა", str(e))

    # ── Theme / Font ────────────────────────
    def _change_theme(self, choice):
        mode = THEMES.get(choice, "dark")
        self._theme_mode = mode
        ctk.set_appearance_mode(mode)
        self.log(f"თემა: {choice}", "dim")

    def _change_font(self, choice):
        new_size = FONT_SIZES.get(choice, 13)
        self.F.apply(new_size)
        self.log(f"შრიფტის ზომა შეიცვალა: {new_size}px ✅", "success")

    # ── Run ─────────────────────────────────
    def run(self):
        self.log(f"PostGIS Manager გაშვებულია — {datetime.now().strftime('%Y-%m-%d %H:%M')}", "success")
        self.log("ბაზასთან დასაკავშირებლად შეიყვანეთ პარამეტრები და დააჭირეთ 'დაკავშირება'", "dim")
        self.mainloop()


# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = PostGISManagerApp()
    app.run()