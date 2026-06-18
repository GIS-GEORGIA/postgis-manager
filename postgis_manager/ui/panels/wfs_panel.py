"""WFS Connector — load OGC WFS layers into Map Viewer."""

from __future__ import annotations
import urllib.request
import urllib.parse
import json
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
    QMessageBox, QComboBox, QSplitter, QTextEdit, QSpinBox,
    QGroupBox, QFormLayout,
)


# ── WFS worker ────────────────────────────────────────────────────────────────

class _WFSCapWorker(QThread):
    """Fetch WFS GetCapabilities and parse layer list."""
    done  = pyqtSignal(list)   # [(title, name, abstract, crs), ...]
    error = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        try:
            from xml.etree import ElementTree as ET
            params = {
                "SERVICE": "WFS",
                "VERSION": "2.0.0",
                "REQUEST": "GetCapabilities",
            }
            full_url = self._url.rstrip("?") + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(full_url, timeout=15) as resp:
                xml = resp.read()
            root = ET.fromstring(xml)
            ns = {
                "wfs": "http://www.opengis.net/wfs/2.0",
                "ows": "http://www.opengis.net/ows/1.1",
            }
            layers = []
            for ft in root.findall(".//wfs:FeatureType", ns):
                name  = ft.findtext("wfs:Name",     "", ns)
                title = ft.findtext("wfs:Title",    "", ns)
                abstr = ft.findtext("wfs:Abstract", "", ns)
                crs_el = ft.find("wfs:DefaultCRS", ns) or ft.find("wfs:SRS", ns)
                crs   = crs_el.text if crs_el is not None else ""
                layers.append((title or name, name, abstr, crs))
            self.done.emit(layers)
        except Exception as e:
            self.error.emit(str(e))


class _WFSFetchWorker(QThread):
    """Fetch WFS features as GeoJSON."""
    done  = pyqtSignal(dict)   # GeoJSON FeatureCollection
    error = pyqtSignal(str)

    def __init__(self, url: str, layer: str, max_features: int, cql_filter: str):
        super().__init__()
        self._url         = url
        self._layer       = layer
        self._max_features = max_features
        self._cql_filter  = cql_filter

    def run(self):
        try:
            params = {
                "SERVICE":      "WFS",
                "VERSION":      "2.0.0",
                "REQUEST":      "GetFeature",
                "TYPENAMES":    self._layer,
                "OUTPUTFORMAT": "application/json",
                "COUNT":        str(self._max_features),
            }
            if self._cql_filter:
                params["CQL_FILTER"] = self._cql_filter
            full_url = self._url.rstrip("?") + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(full_url, timeout=30) as resp:
                data = json.loads(resp.read())
            self.done.emit(data)
        except Exception as e:
            self.error.emit(str(e))


# ── panel ─────────────────────────────────────────────────────────────────────

_SAMPLE_URLS = [
    "https://demo.pygeoapi.io/master/wfs",
    "https://geo.vliz.be/geoserver/wfs",
    "https://ahocevar.com/geoserver/wfs",
]


class WFSPanel(QWidget):
    """Load WFS layers from remote services into the Map Viewer."""

    # emit (geojson_dict, layer_name) so MapViewerPanel can display it
    layer_loaded = pyqtSignal(dict, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layers: list = []
        self._cap_worker  = None
        self._fetch_worker = None
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # ── Connection bar ────────────────────────────────────────────────
        conn_box = QGroupBox("WFS Service")
        cv = QVBoxLayout(conn_box)

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://example.com/geoserver/wfs")
        url_row.addWidget(self._url_edit)
        self._sample_combo = QComboBox()
        self._sample_combo.addItem("— Sample services —")
        for s in _SAMPLE_URLS:
            self._sample_combo.addItem(s)
        self._sample_combo.currentTextChanged.connect(
            lambda t: self._url_edit.setText(t) if t.startswith("http") else None)
        url_row.addWidget(self._sample_combo)
        self._cap_btn = QPushButton("📡 Get Capabilities")
        self._cap_btn.clicked.connect(self._get_capabilities)
        url_row.addWidget(self._cap_btn)
        cv.addLayout(url_row)

        root.addWidget(conn_box)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setMaximumHeight(4)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Layer list ────────────────────────────────────────────────────
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        self._status = QLabel("Enter a WFS URL and click Get Capabilities")
        lv.addWidget(self._status)
        self._layer_list = QListWidget()
        self._layer_list.currentRowChanged.connect(self._on_layer_selected)
        lv.addWidget(self._layer_list)
        splitter.addWidget(left)

        # ── Layer options + preview ───────────────────────────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)

        opt_form = QFormLayout()
        self._max_spin = QSpinBox()
        self._max_spin.setRange(1, 50000)
        self._max_spin.setValue(1000)
        opt_form.addRow("Max features:", self._max_spin)
        self._cql_edit = QLineEdit()
        self._cql_edit.setPlaceholderText("CQL filter (optional), e.g.  name = 'Paris'")
        opt_form.addRow("CQL Filter:", self._cql_edit)
        rv.addLayout(opt_form)

        self._load_btn = QPushButton("⬇ Load Layer into Map")
        self._load_btn.setEnabled(False)
        self._load_btn.clicked.connect(self._load_layer)
        rv.addWidget(self._load_btn)

        rv.addWidget(QLabel("Layer info:"))
        self._info_view = QTextEdit()
        self._info_view.setReadOnly(True)
        rv.addWidget(self._info_view)

        splitter.addWidget(right)
        splitter.setSizes([380, 420])
        root.addWidget(splitter)

    # ── capabilities ─────────────────────────────────────────────────────

    def _get_capabilities(self):
        url = self._url_edit.text().strip()
        if not url:
            return
        self._cap_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText("Fetching capabilities…")
        self._cap_worker = _WFSCapWorker(url)
        self._cap_worker.done.connect(self._on_cap_done)
        self._cap_worker.error.connect(self._on_error)
        self._cap_worker.start()

    def _on_cap_done(self, layers: list):
        self._layers = layers
        self._progress.setVisible(False)
        self._cap_btn.setEnabled(True)
        self._layer_list.clear()
        for title, name, abstr, crs in layers:
            item = QListWidgetItem(f"{title}  ({name})")
            item.setData(Qt.ItemDataRole.UserRole, (title, name, abstr, crs))
            self._layer_list.addItem(item)
        self._status.setText(f"{len(layers)} layers")

    def _on_layer_selected(self, row: int):
        if row < 0 or row >= len(self._layers):
            self._load_btn.setEnabled(False)
            return
        title, name, abstr, crs = self._layers[row]
        self._info_view.setPlainText(
            f"Name: {name}\nTitle: {title}\nCRS: {crs}\n\n{abstr}")
        self._load_btn.setEnabled(True)

    # ── fetch + emit ─────────────────────────────────────────────────────

    def _load_layer(self):
        row = self._layer_list.currentRow()
        if row < 0 or row >= len(self._layers):
            return
        title, name, abstr, crs = self._layers[row]
        url = self._url_edit.text().strip()
        self._load_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._status.setText(f"Loading {name}…")
        self._fetch_worker = _WFSFetchWorker(
            url, name, self._max_spin.value(), self._cql_edit.text().strip())
        self._fetch_worker.done.connect(lambda gj: self._on_fetched(gj, title or name))
        self._fetch_worker.error.connect(self._on_error)
        self._fetch_worker.start()

    def _on_fetched(self, geojson: dict, name: str):
        self._progress.setVisible(False)
        self._load_btn.setEnabled(True)
        n = len(geojson.get("features", []))
        self._status.setText(f"Loaded {n} features from {name}")
        self.layer_loaded.emit(geojson, name)

    def _on_error(self, msg: str):
        self._progress.setVisible(False)
        self._cap_btn.setEnabled(True)
        self._load_btn.setEnabled(True)
        self._status.setText(f"Error: {msg[:80]}")
        QMessageBox.critical(self, "WFS error", msg)
