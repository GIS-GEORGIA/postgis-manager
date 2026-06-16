"""REST / OGC API Tester — pg_featureserv, PostgREST, OGC API Features endpoints."""

from __future__ import annotations
import json
import urllib.request
import urllib.error
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QLineEdit, QComboBox, QGroupBox, QFormLayout,
    QTableWidget, QTableWidgetItem, QTabWidget, QHeaderView,
    QAbstractItemView, QSplitter, QSpinBox, QMessageBox,
    QTreeWidget, QTreeWidgetItem,
)


# ── Worker ────────────────────────────────────────────────────────────────

class RequestWorker(QThread):
    done  = pyqtSignal(int, str, dict)  # status, body, headers
    error = pyqtSignal(str)

    def __init__(self, method: str, url: str, body: str = "",
                 headers: dict | None = None):
        super().__init__()
        self.method  = method
        self.url     = url
        self.body    = body
        self.headers = headers or {}

    def run(self):
        try:
            data = self.body.encode() if self.body else None
            req  = urllib.request.Request(
                self.url, data=data, method=self.method)
            req.add_header("Accept", "application/json")
            for k, v in self.headers.items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=30) as resp:
                body    = resp.read().decode("utf-8", errors="replace")
                status  = resp.status
                headers = dict(resp.headers)
            self.done.emit(status, body, headers)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            self.done.emit(e.code, body, {})
        except Exception as e:
            self.error.emit(str(e))


# ── Panel ─────────────────────────────────────────────────────────────────

class APITesterPanel(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._worker: RequestWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.addTab(self._build_request_tab(),    "🌐  HTTP Request")
        tabs.addTab(self._build_featureserv_tab(),"🗺  pg_featureserv")
        tabs.addTab(self._build_postgrest_tab(),  "🔌  PostgREST")
        tabs.addTab(self._build_ogc_tab(),        "📦  OGC API Features")
        root.addWidget(tabs, 1)

    # ── Generic HTTP tab ──────────────────────────────────────────────────

    def _build_request_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        # URL bar
        url_row = QHBoxLayout()
        self._method = QComboBox()
        self._method.addItems(["GET", "POST", "PUT", "PATCH", "DELETE"])
        self._method.setFixedWidth(80)
        self._url = QLineEdit()
        self._url.setPlaceholderText("https://your-server/api/...")
        self._send_btn = QPushButton("▶ Send")
        self._send_btn.setStyleSheet("font-weight:bold;")
        self._send_btn.clicked.connect(self._send_request)
        url_row.addWidget(self._method)
        url_row.addWidget(self._url, 1)
        url_row.addWidget(self._send_btn)
        lay.addLayout(url_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: request
        req_w = QWidget()
        rl = QVBoxLayout(req_w)
        rl.setContentsMargins(0, 0, 4, 0)

        hdr_box = QGroupBox("Request Headers")
        hfl = QVBoxLayout(hdr_box)
        self._headers_edit = QTextEdit()
        self._headers_edit.setMaximumHeight(80)
        self._headers_edit.setFont(QFont("Courier New", 10))
        self._headers_edit.setPlaceholderText(
            'Authorization: Bearer token\nContent-Type: application/json')
        hfl.addWidget(self._headers_edit)
        rl.addWidget(hdr_box)

        body_box = QGroupBox("Request Body (JSON)")
        bfl = QVBoxLayout(body_box)
        self._body_edit = QTextEdit()
        self._body_edit.setFont(QFont("Courier New", 10))
        self._body_edit.setPlaceholderText('{ "key": "value" }')
        bfl.addWidget(self._body_edit)
        rl.addWidget(body_box)
        splitter.addWidget(req_w)

        # Right: response
        resp_w = QWidget()
        rsl = QVBoxLayout(resp_w)
        rsl.setContentsMargins(4, 0, 0, 0)

        self._status_label = QLabel("—")
        self._status_label.setStyleSheet("font-weight:bold; font-size:13px;")
        rsl.addWidget(self._status_label)

        resp_tabs = QTabWidget()
        self._resp_body = QTextEdit()
        self._resp_body.setReadOnly(True)
        self._resp_body.setFont(QFont("Courier New", 10))
        resp_tabs.addTab(self._resp_body, "Body")

        self._resp_headers = QTableWidget(0, 2)
        self._resp_headers.setHorizontalHeaderLabels(["Header", "Value"])
        self._resp_headers.horizontalHeader().setStretchLastSection(True)
        resp_tabs.addTab(self._resp_headers, "Headers")

        self._resp_tree = QTreeWidget()
        self._resp_tree.setHeaderLabels(["Key", "Value"])
        self._resp_tree.header().setStretchLastSection(True)
        resp_tabs.addTab(self._resp_tree, "JSON Tree")
        rsl.addWidget(resp_tabs, 1)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("📋 Copy response")
        btn_copy.clicked.connect(lambda: __import__(
            'PyQt6.QtWidgets', fromlist=['QApplication']
        ).QApplication.clipboard().setText(self._resp_body.toPlainText()))
        btn_fmt = QPushButton("{ } Format JSON")
        btn_fmt.clicked.connect(self._format_json)
        btn_row.addWidget(btn_copy)
        btn_row.addWidget(btn_fmt)
        btn_row.addStretch()
        rsl.addLayout(btn_row)
        splitter.addWidget(resp_w)
        splitter.setSizes([400, 500])
        lay.addWidget(splitter, 1)
        return w

    def _send_request(self, url: str = "", method: str = "",
                      extra_params: str = ""):
        target_url = url or self._url.text().strip()
        if extra_params:
            sep = "&" if "?" in target_url else "?"
            target_url += sep + extra_params
        target_method = method or self._method.currentText()
        if not target_url:
            return
        headers = {}
        for line in self._headers_edit.toPlainText().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip()] = v.strip()
        body = self._body_edit.toPlainText().strip()
        self._send_btn.setEnabled(False)
        self._status_label.setText("⏳ Sending…")
        w = RequestWorker(target_method, target_url, body, headers)
        w.done.connect(self._on_response)
        w.error.connect(self._on_error)
        w.finished.connect(w.deleteLater)
        self._worker = w
        w.start()

    def _on_response(self, status: int, body: str, headers: dict):
        self._send_btn.setEnabled(True)
        color = "#3fb950" if 200 <= status < 300 else "#ff7b72"
        self._status_label.setText(
            f'<span style="color:{color};">HTTP {status}</span>')
        self._resp_body.setPlainText(body)
        # Headers table
        self._resp_headers.setRowCount(0)
        for k, v in headers.items():
            r = self._resp_headers.rowCount()
            self._resp_headers.insertRow(r)
            self._resp_headers.setItem(r, 0, QTableWidgetItem(k))
            self._resp_headers.setItem(r, 1, QTableWidgetItem(v))
        # JSON tree
        self._resp_tree.clear()
        try:
            data = json.loads(body)
            self._build_json_tree(
                self._resp_tree.invisibleRootItem(), data)
            self._resp_tree.expandToDepth(1)
        except Exception:
            pass

    def _on_error(self, error: str):
        self._send_btn.setEnabled(True)
        self._status_label.setText(f'<span style="color:#ff7b72;">Error</span>')
        self._resp_body.setPlainText(f"Error: {error}")

    def _format_json(self):
        try:
            data = json.loads(self._resp_body.toPlainText())
            self._resp_body.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            pass

    def _build_json_tree(self, parent, data, max_depth: int = 8):
        if max_depth <= 0:
            return
        if isinstance(data, dict):
            for k, v in list(data.items())[:200]:
                child = QTreeWidgetItem(parent, [str(k),
                    str(v) if not isinstance(v, (dict, list)) else ""])
                if isinstance(v, (dict, list)):
                    self._build_json_tree(child, v, max_depth - 1)
        elif isinstance(data, list):
            for i, v in enumerate(data[:100]):
                child = QTreeWidgetItem(parent, [f"[{i}]",
                    str(v) if not isinstance(v, (dict, list)) else ""])
                if isinstance(v, (dict, list)):
                    self._build_json_tree(child, v, max_depth - 1)
        else:
            QTreeWidgetItem(parent, ["", str(data)])

    # ── pg_featureserv tab ────────────────────────────────────────────────

    def _build_featureserv_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        svc_row = QHBoxLayout()
        self._fs_url = QLineEdit("http://localhost:9000")
        self._fs_url.setPlaceholderText("pg_featureserv base URL")
        btn_discover = QPushButton("🔍 Discover collections")
        btn_discover.clicked.connect(self._fs_discover)
        svc_row.addWidget(QLabel("Base URL:"))
        svc_row.addWidget(self._fs_url, 1)
        svc_row.addWidget(btn_discover)
        lay.addLayout(svc_row)

        self._fs_collections = QTableWidget(0, 3)
        self._fs_collections.setHorizontalHeaderLabels(
            ["Collection ID", "Title", "Description"])
        self._fs_collections.horizontalHeader().setStretchLastSection(True)
        self._fs_collections.setAlternatingRowColors(True)
        self._fs_collections.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self._fs_collections)

        params_box = QGroupBox("Query Parameters")
        pfl = QFormLayout(params_box)
        self._fs_limit  = QSpinBox()
        self._fs_limit.setRange(1, 10000)
        self._fs_limit.setValue(100)
        self._fs_bbox   = QLineEdit()
        self._fs_bbox.setPlaceholderText("minx,miny,maxx,maxy")
        self._fs_filter = QLineEdit()
        self._fs_filter.setPlaceholderText("CQL2 filter e.g. pop > 1000")
        self._fs_props  = QLineEdit()
        self._fs_props.setPlaceholderText("name,geom (leave blank = all)")
        pfl.addRow("Limit:", self._fs_limit)
        pfl.addRow("BBOX:", self._fs_bbox)
        pfl.addRow("Filter:", self._fs_filter)
        pfl.addRow("Properties:", self._fs_props)
        lay.addWidget(params_box)

        btn_row = QHBoxLayout()
        btn_get = QPushButton("▶ Get Features (GeoJSON)")
        btn_get.setStyleSheet("font-weight:bold;")
        btn_get.clicked.connect(self._fs_get_features)
        btn_row.addWidget(btn_get)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return w

    def _fs_discover(self):
        base = self._fs_url.text().rstrip("/")
        self._url.setText(f"{base}/collections?f=json")
        self._method.setCurrentText("GET")
        w = RequestWorker("GET", f"{base}/collections?f=json")
        w.done.connect(self._fs_on_collections)
        w.error.connect(self._on_error)
        w.finished.connect(w.deleteLater)
        w.start()

    def _fs_on_collections(self, status: int, body: str, headers: dict):
        self._on_response(status, body, headers)
        try:
            data = json.loads(body)
            cols = data.get("collections", [])
            self._fs_collections.setRowCount(0)
            for c in cols:
                r = self._fs_collections.rowCount()
                self._fs_collections.insertRow(r)
                self._fs_collections.setItem(r, 0, QTableWidgetItem(c.get("id", "")))
                self._fs_collections.setItem(r, 1, QTableWidgetItem(c.get("title", "")))
                self._fs_collections.setItem(r, 2, QTableWidgetItem(c.get("description", "")))
        except Exception:
            pass

    def _fs_get_features(self):
        row = self._fs_collections.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select collection",
                                "Click a collection first.")
            return
        col_id = self._fs_collections.item(row, 0).text()
        base = self._fs_url.text().rstrip("/")
        params = [f"limit={self._fs_limit.value()}", "f=json"]
        if self._fs_bbox.text().strip():
            params.append(f"bbox={self._fs_bbox.text().strip()}")
        if self._fs_filter.text().strip():
            params.append(f"filter={urllib.parse.quote(self._fs_filter.text().strip())}")
        if self._fs_props.text().strip():
            params.append(f"properties={self._fs_props.text().strip()}")
        url = f"{base}/collections/{col_id}/items?{'&'.join(params)}"
        self._url.setText(url)
        self._send_request(url, "GET")

    # ── PostgREST tab ─────────────────────────────────────────────────────

    def _build_postgrest_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        svc_row = QHBoxLayout()
        self._pr_url = QLineEdit("http://localhost:3000")
        self._pr_url.setPlaceholderText("PostgREST base URL")
        self._pr_jwt  = QLineEdit()
        self._pr_jwt.setPlaceholderText("JWT token (optional)")
        self._pr_jwt.setEchoMode(QLineEdit.EchoMode.Password)
        btn_disc = QPushButton("🔍 Introspect")
        btn_disc.clicked.connect(self._pr_introspect)
        svc_row.addWidget(QLabel("Base URL:"))
        svc_row.addWidget(self._pr_url, 1)
        lay.addLayout(svc_row)

        jwt_row = QHBoxLayout()
        jwt_row.addWidget(QLabel("JWT:"))
        jwt_row.addWidget(self._pr_jwt, 1)
        jwt_row.addWidget(btn_disc)
        lay.addLayout(jwt_row)

        self._pr_table = QComboBox()
        self._pr_table.setEditable(True)
        self._pr_table.setPlaceholderText("table / view name")

        params_box = QGroupBox("Query")
        qfl = QFormLayout(params_box)
        self._pr_select  = QLineEdit("*")
        self._pr_where   = QLineEdit()
        self._pr_where.setPlaceholderText("e.g. name=eq.Tbilisi")
        self._pr_order   = QLineEdit()
        self._pr_order.setPlaceholderText("e.g. population.desc")
        self._pr_limit   = QSpinBox()
        self._pr_limit.setRange(1, 10000)
        self._pr_limit.setValue(100)
        qfl.addRow("Endpoint:", self._pr_table)
        qfl.addRow("select=", self._pr_select)
        qfl.addRow("where filter:", self._pr_where)
        qfl.addRow("order=", self._pr_order)
        qfl.addRow("limit=", self._pr_limit)
        lay.addWidget(params_box)

        btn_row = QHBoxLayout()
        btn_get  = QPushButton("▶ GET (read)")
        btn_get.clicked.connect(self._pr_get)
        btn_row.addWidget(btn_get)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return w

    def _pr_introspect(self):
        base = self._pr_url.text().rstrip("/")
        headers = {"Accept": "application/openapi+json"}
        if self._pr_jwt.text().strip():
            headers["Authorization"] = f"Bearer {self._pr_jwt.text().strip()}"
        w = RequestWorker("GET", base, headers=headers)
        w.done.connect(lambda s, b, h: self._on_response(s, b, h))
        w.error.connect(self._on_error)
        w.finished.connect(w.deleteLater)
        w.start()

    def _pr_get(self):
        base  = self._pr_url.text().rstrip("/")
        table = self._pr_table.currentText().strip()
        if not table:
            return
        params = [f"select={self._pr_select.text() or '*'}",
                  f"limit={self._pr_limit.value()}"]
        where = self._pr_where.text().strip()
        if where:
            for part in where.split("&"):
                params.append(part.strip())
        order = self._pr_order.text().strip()
        if order:
            params.append(f"order={order}")
        url = f"{base}/{table}?{'&'.join(params)}"
        headers = {"Accept": "application/json"}
        if self._pr_jwt.text().strip():
            headers["Authorization"] = f"Bearer {self._pr_jwt.text().strip()}"
        self._url.setText(url)
        w = RequestWorker("GET", url, headers=headers)
        w.done.connect(self._on_response)
        w.error.connect(self._on_error)
        w.finished.connect(w.deleteLater)
        w.start()

    # ── OGC API Features tab ──────────────────────────────────────────────

    def _build_ogc_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        info = QLabel(
            "OGC API Features (WFS 3.0) — works with GeoServer, QGIS Server, "
            "ldproxy, pygeoapi, etc.")
        info.setWordWrap(True)
        info.setStyleSheet("font-size:11px; color:gray;")
        lay.addWidget(info)

        svc_row = QHBoxLayout()
        self._ogc_url = QLineEdit()
        self._ogc_url.setPlaceholderText(
            "https://demo.ldproxy.net/zoomstack")
        btn_land = QPushButton("Landing page")
        btn_land.clicked.connect(lambda: self._ogc_request(""))
        btn_coll = QPushButton("Collections")
        btn_coll.clicked.connect(lambda: self._ogc_request("collections"))
        btn_conf = QPushButton("Conformance")
        btn_conf.clicked.connect(lambda: self._ogc_request("conformance"))
        svc_row.addWidget(QLabel("Base URL:"))
        svc_row.addWidget(self._ogc_url, 1)
        lay.addLayout(svc_row)

        btn_row = QHBoxLayout()
        for btn in (btn_land, btn_coll, btn_conf):
            btn_row.addWidget(btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        col_box = QGroupBox("Collection Items")
        cfl = QFormLayout(col_box)
        self._ogc_col = QLineEdit()
        self._ogc_col.setPlaceholderText("collection ID")
        self._ogc_flimit = QSpinBox()
        self._ogc_flimit.setRange(1, 10000)
        self._ogc_flimit.setValue(100)
        self._ogc_bbox = QLineEdit()
        self._ogc_bbox.setPlaceholderText("-180,-90,180,90")
        self._ogc_datetime = QLineEdit()
        self._ogc_datetime.setPlaceholderText("2024-01-01/2024-12-31")
        cfl.addRow("Collection:", self._ogc_col)
        cfl.addRow("Limit:", self._ogc_flimit)
        cfl.addRow("bbox:", self._ogc_bbox)
        cfl.addRow("datetime:", self._ogc_datetime)
        lay.addWidget(col_box)

        btn_items = QPushButton("▶ Get Items")
        btn_items.setStyleSheet("font-weight:bold;")
        btn_items.clicked.connect(self._ogc_get_items)
        lay.addWidget(btn_items)
        lay.addStretch()
        return w

    def _ogc_request(self, path: str):
        base = self._ogc_url.text().rstrip("/")
        url  = f"{base}/{path}?f=json" if path else f"{base}?f=json"
        self._url.setText(url)
        self._send_request(url, "GET")

    def _ogc_get_items(self):
        col = self._ogc_col.text().strip()
        if not col:
            return
        base = self._ogc_url.text().rstrip("/")
        params = [f"f=json", f"limit={self._ogc_flimit.value()}"]
        if self._ogc_bbox.text().strip():
            params.append(f"bbox={self._ogc_bbox.text().strip()}")
        if self._ogc_datetime.text().strip():
            params.append(f"datetime={self._ogc_datetime.text().strip()}")
        url = f"{base}/collections/{col}/items?{'&'.join(params)}"
        self._url.setText(url)
        self._send_request(url, "GET")
