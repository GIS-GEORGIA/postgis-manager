"""AI Assistant panel — natural language to SQL/PostGIS via Anthropic API or Ollama."""

from __future__ import annotations
import json
import urllib.request
import urllib.error
import psycopg2
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QLineEdit, QComboBox, QGroupBox, QFormLayout,
    QMessageBox, QTabWidget, QCheckBox,
)

from ...utils import config
from ...utils.workers import launch


# ── Schema fetcher ────────────────────────────────────────────────────────

def _fetch_schema_context(conn_params: dict, max_tables: int = 40) -> str:
    """Return a compact schema summary for the AI prompt."""
    if not conn_params:
        return "(no database connected)"
    try:
        conn = psycopg2.connect(**conn_params)
        cur = conn.cursor()
        cur.execute("""
            SELECT c.table_schema, c.table_name,
                   STRING_AGG(c.column_name || ' ' || c.udt_name, ', '
                              ORDER BY c.ordinal_position) AS cols
            FROM information_schema.columns c
            WHERE c.table_schema NOT IN ('pg_catalog','information_schema')
            GROUP BY c.table_schema, c.table_name
            ORDER BY c.table_schema, c.table_name
            LIMIT %s
        """, (max_tables,))
        lines = []
        for schema, table, cols in cur.fetchall():
            lines.append(f'  "{schema}"."{table}" ({cols})')
        cur.close(); conn.close()
        return "\n".join(lines) if lines else "(schema empty)"
    except Exception as e:
        return f"(schema fetch failed: {e})"


# ── AI worker (Anthropic or Ollama) ──────────────────────────────────────

class AIWorker(QThread):
    chunk    = pyqtSignal(str)   # streaming token
    finished = pyqtSignal(str)   # full response
    error    = pyqtSignal(str)

    def __init__(self, prompt: str, system: str,
                 provider: str, model: str, api_key: str,
                 ollama_url: str = "http://localhost:11434"):
        super().__init__()
        self.prompt = prompt
        self.system = system
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.ollama_url = ollama_url

    def run(self):
        try:
            if self.provider == "anthropic":
                result = self._call_anthropic()
            else:
                result = self._call_ollama()
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def _call_anthropic(self) -> str:
        url = "https://api.anthropic.com/v1/messages"
        body = json.dumps({
            "model": self.model,
            "max_tokens": 2048,
            "system": self.system,
            "messages": [{"role": "user", "content": self.prompt}],
        }).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return data["content"][0]["text"]

    def _call_ollama(self) -> str:
        url = f"{self.ollama_url}/api/generate"
        body = json.dumps({
            "model": self.model,
            "prompt": f"<system>{self.system}</system>\n\n{self.prompt}",
            "stream": False,
        }).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        return data.get("response", "")


# ── Panel ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a PostGIS SQL expert assistant embedded in a GIS application.
The user's database schema is:
{schema}

Rules:
- Always return valid PostgreSQL / PostGIS SQL.
- Use the exact table and column names from the schema above.
- Prefer ST_* PostGIS functions when spatial operations are involved.
- Return ONLY the SQL query, wrapped in ```sql ... ``` fences — no prose before or after.
- If the user asks something that is not SQL-related, answer briefly then still produce the SQL.
- If no suitable SQL can be generated, say so clearly inside a SQL comment.
"""


class AIPanel(QWidget):
    sql_ready = pyqtSignal(str)   # emitted when user clicks "Send to SQL Editor"

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._conn_params: dict = {}
        self._schema_ctx = ""
        self._worker: AIWorker | None = None
        self._history: list[dict] = []
        self._setup_ui()
        self._load_settings()

    def set_connection(self, params: dict):
        self._conn_params = params
        self._schema_ctx = _fetch_schema_context(params)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        tabs = QTabWidget()
        tabs.addTab(self._build_chat_tab(),     "💬  Chat")
        tabs.addTab(self._build_settings_tab(), "⚙  Settings")
        root.addWidget(tabs, 1)

    # ── Chat tab ──────────────────────────────────────────────────────────

    def _build_chat_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)

        # Context pill
        ctx_row = QHBoxLayout()
        self._ctx_label = QLabel("🔴  No DB connection")
        self._ctx_label.setStyleSheet("font-size:11px; color:gray;")
        btn_refresh_ctx = QPushButton("↺ Refresh schema")
        btn_refresh_ctx.setFixedHeight(24)
        btn_refresh_ctx.clicked.connect(self._refresh_schema)
        self._include_schema = QCheckBox("Include schema context")
        self._include_schema.setChecked(True)
        ctx_row.addWidget(self._ctx_label)
        ctx_row.addWidget(self._include_schema)
        ctx_row.addStretch()
        ctx_row.addWidget(btn_refresh_ctx)
        lay.addLayout(ctx_row)

        # Conversation
        self._chat = QTextEdit()
        self._chat.setReadOnly(True)
        self._chat.setFont(QFont("Segoe UI", 11))
        self._chat.setPlaceholderText(
            "Ask anything about your spatial data…\n\n"
            "Examples:\n"
            "• Show all buildings within 500m of a river\n"
            "• Count features per municipality\n"
            "• Find duplicate geometries in parcels table\n"
            "• Create a buffer of 100m around roads and dissolve")
        lay.addWidget(self._chat, 1)

        # Last SQL block preview
        self._sql_preview = QTextEdit()
        self._sql_preview.setReadOnly(True)
        self._sql_preview.setFont(QFont("Courier New", 10))
        self._sql_preview.setMaximumHeight(110)
        self._sql_preview.setPlaceholderText("Generated SQL appears here…")
        lay.addWidget(self._sql_preview)

        btn_row2 = QHBoxLayout()
        self._send_to_editor_btn = QPushButton("📤 Send to SQL Editor")
        self._send_to_editor_btn.setEnabled(False)
        self._send_to_editor_btn.clicked.connect(self._send_to_editor)
        btn_copy = QPushButton("📋 Copy SQL")
        btn_copy.clicked.connect(self._copy_sql)
        btn_clear = QPushButton("🗑 Clear chat")
        btn_clear.clicked.connect(self._clear_chat)
        btn_row2.addWidget(self._send_to_editor_btn)
        btn_row2.addWidget(btn_copy)
        btn_row2.addStretch()
        btn_row2.addWidget(btn_clear)
        lay.addLayout(btn_row2)

        # Input
        input_row = QHBoxLayout()
        self._input = QTextEdit()
        self._input.setMaximumHeight(70)
        self._input.setFont(QFont("Segoe UI", 11))
        self._input.setPlaceholderText(
            "Type your question in Georgian or English… (Ctrl+Enter to send)")
        self._ask_btn = QPushButton("▶ Ask")
        self._ask_btn.setStyleSheet("font-weight:bold; min-width:70px;")
        self._ask_btn.clicked.connect(self._ask)
        self._stop_btn = QPushButton("⏹")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        input_row.addWidget(self._input)
        vbtn = QVBoxLayout()
        vbtn.addWidget(self._ask_btn)
        vbtn.addWidget(self._stop_btn)
        input_row.addLayout(vbtn)
        lay.addLayout(input_row)

        # Quick prompts
        quick_row = QHBoxLayout()
        for label, prompt in [
            ("Count by schema",   "Count the number of features in each geometry table"),
            ("Invalid geoms",     "Find all invalid geometries in the database"),
            ("Biggest tables",    "List the 10 largest tables by disk size"),
            ("Spatial index?",    "Which geometry tables are missing a spatial index?"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setStyleSheet("font-size:11px;")
            b.clicked.connect(lambda _, p=prompt: self._quick_ask(p))
            quick_row.addWidget(b)
        quick_row.addStretch()
        lay.addLayout(quick_row)
        return w

    # ── Settings tab ──────────────────────────────────────────────────────

    def _build_settings_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 10, 10, 10)

        prov_box = QGroupBox("Provider")
        pfl = QFormLayout(prov_box)
        self._provider = QComboBox()
        self._provider.addItem("Anthropic Claude (cloud, fast, cheap)", "anthropic")
        self._provider.addItem("Ollama (local, free, no internet needed)", "ollama")
        self._provider.currentIndexChanged.connect(self._on_provider_change)

        self._anthropic_model = QComboBox()
        self._anthropic_model.addItems([
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
            "claude-opus-4-8",
        ])
        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("sk-ant-…  (from console.anthropic.com)")
        btn_show = QPushButton("👁")
        btn_show.setFixedWidth(28)
        btn_show.setCheckable(True)
        btn_show.toggled.connect(lambda on: self._api_key.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        key_row = QHBoxLayout()
        key_row.addWidget(self._api_key)
        key_row.addWidget(btn_show)
        key_w = QWidget(); key_w.setLayout(key_row)

        self._ollama_url = QLineEdit("http://localhost:11434")
        self._ollama_model = QLineEdit("llama3")
        self._ollama_model.setPlaceholderText("e.g. llama3, codellama, mistral")
        pfl.addRow("Provider:", self._provider)
        pfl.addRow("Claude model:", self._anthropic_model)
        pfl.addRow("API Key:", key_w)
        pfl.addRow("Ollama URL:", self._ollama_url)
        pfl.addRow("Ollama model:", self._ollama_model)
        lay.addWidget(prov_box)

        cost_box = QGroupBox("Cost reference (Anthropic)")
        cfl = QFormLayout(cost_box)
        cfl.addRow(QLabel("Haiku 4.5:"),   QLabel("$0.80 / M input · $4 / M output — ~5000 SQL queries = $1"))
        cfl.addRow(QLabel("Sonnet 4.6:"),  QLabel("$3 / M input · $15 / M output — smarter, more expensive"))
        cfl.addRow(QLabel("Ollama:"),       QLabel("FREE — runs on your machine, no API key needed"))
        cfl.addRow(QLabel("API Keys:"),     QLabel("console.anthropic.com → API Keys"))
        lay.addWidget(cost_box)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 Save settings")
        btn_save.clicked.connect(self._save_settings)
        btn_test = QPushButton("🔌 Test connection")
        btn_test.clicked.connect(self._test_connection)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_test)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        lay.addStretch()
        return w

    def _on_provider_change(self, _):
        is_anthropic = self._provider.currentData() == "anthropic"
        self._anthropic_model.setEnabled(is_anthropic)
        self._api_key.setEnabled(is_anthropic)
        self._ollama_url.setEnabled(not is_anthropic)
        self._ollama_model.setEnabled(not is_anthropic)

    def _save_settings(self):
        config.set("ai_provider",      self._provider.currentData())
        config.set("ai_anthropic_model", self._anthropic_model.currentText())
        config.set("ai_api_key",       self._api_key.text())
        config.set("ai_ollama_url",    self._ollama_url.text())
        config.set("ai_ollama_model",  self._ollama_model.text())
        QMessageBox.information(self, "Saved", "AI settings saved.")

    def _load_settings(self):
        provider = config.get("ai_provider", "anthropic")
        idx = self._provider.findData(provider)
        if idx >= 0:
            self._provider.setCurrentIndex(idx)
        model = config.get("ai_anthropic_model", "claude-haiku-4-5-20251001")
        idx2 = self._anthropic_model.findText(model)
        if idx2 >= 0:
            self._anthropic_model.setCurrentIndex(idx2)
        self._api_key.setText(config.get("ai_api_key", ""))
        self._ollama_url.setText(
            config.get("ai_ollama_url", "http://localhost:11434"))
        self._ollama_model.setText(config.get("ai_ollama_model", "llama3"))
        self._on_provider_change(0)

    def _test_connection(self):
        provider = self._provider.currentData()
        try:
            if provider == "anthropic":
                key = self._api_key.text().strip()
                if not key:
                    raise ValueError("Enter API key first.")
                w = AIWorker("Say 'OK' in one word.", "You are a test.",
                             "anthropic", self._anthropic_model.currentText(), key)
            else:
                w = AIWorker("Say 'OK' in one word.", "You are a test.",
                             "ollama", self._ollama_model.text(),
                             "", self._ollama_url.text())
            w.finished.connect(lambda r: QMessageBox.information(
                self, "✓ Connected", f"Response: {r[:80]}"))
            w.error.connect(lambda e: QMessageBox.critical(
                self, "Connection failed", e))
            launch(w)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ── Actions ───────────────────────────────────────────────────────────

    def _refresh_schema(self):
        if self._conn_params:
            self._schema_ctx = _fetch_schema_context(self._conn_params)
            n = self._schema_ctx.count("\n") + 1
            self._ctx_label.setText(
                f"🟢  {n} table(s) loaded into context")
            self._ctx_label.setStyleSheet("font-size:11px; color:#3fb950;")
        else:
            self._ctx_label.setText("🔴  No DB connection")

    def _quick_ask(self, prompt: str):
        self._input.setPlainText(prompt)
        self._ask()

    def _ask(self):
        text = self._input.toPlainText().strip()
        if not text:
            return
        provider = self._provider.currentData()
        api_key  = self._api_key.text().strip()
        if provider == "anthropic" and not api_key:
            QMessageBox.warning(self, "No API key",
                                "Enter your Anthropic API key in Settings tab.")
            return

        system = SYSTEM_PROMPT.format(schema=self._schema_ctx)

        self._append_chat("user", text)
        self._input.clear()
        self._ask_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._append_chat("assistant", "⏳ Thinking…")

        model = (self._anthropic_model.currentText()
                 if provider == "anthropic"
                 else self._ollama_model.text())
        ollama_url = self._ollama_url.text()

        self._worker = AIWorker(
            text, system, provider, model, api_key, ollama_url)
        self._worker.finished.connect(self._on_response)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
        self._ask_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    def _on_response(self, response: str):
        self._ask_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        # Remove the "Thinking" placeholder
        html = self._chat.toHtml()
        html = html.replace("⏳ Thinking…", "")
        self._chat.setHtml(html)
        self._append_chat("assistant", response)
        # Extract SQL
        sql = self._extract_sql(response)
        if sql:
            self._sql_preview.setPlainText(sql)
            self._send_to_editor_btn.setEnabled(True)
        self._history.append({"role": "user",
                               "content": self._input.toPlainText()})
        self._history.append({"role": "assistant", "content": response})

    def _on_error(self, error: str):
        self._ask_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._append_chat("error", f"Error: {error}")

    def _append_chat(self, role: str, text: str):
        colors = {
            "user":      ("#1a73e8", "You"),
            "assistant": ("#2E7D32", "AI"),
            "error":     ("#c62828", "Error"),
        }
        color, label = colors.get(role, ("#666", role))
        escaped = (text.replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace(">", "&gt;")
                       .replace("\n", "<br>"))
        # Highlight SQL block
        import re
        def highlight_sql(m):
            code = m.group(1).replace("&lt;", "<").replace("&gt;", ">")
            code_escaped = (code.replace("&", "&amp;")
                                .replace("<", "&lt;")
                                .replace(">", "&gt;"))
            return (f'<pre style="background:#1e1e1e;color:#d4d4d4;'
                    f'padding:8px;border-radius:4px;'
                    f'font-family:Courier New,monospace;font-size:10px;">'
                    f'{code_escaped}</pre>')
        escaped = re.sub(r'```sql<br>(.*?)<br>```',
                         highlight_sql, escaped, flags=re.DOTALL)

        self._chat.append(
            f'<p style="margin:4px 0;">'
            f'<b style="color:{color};">{label}:</b><br>{escaped}</p>'
            f'<hr style="border:none;border-top:0.5px solid #ccc;">')

    @staticmethod
    def _extract_sql(text: str) -> str:
        import re
        m = re.search(r'```sql\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m = re.search(r'```\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|WITH).*?```',
                      text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(0).strip("`").strip()
        return ""

    def _send_to_editor(self):
        sql = self._sql_preview.toPlainText().strip()
        if sql:
            self.sql_ready.emit(sql)

    def _copy_sql(self):
        from PyQt6.QtWidgets import QApplication
        sql = self._sql_preview.toPlainText().strip()
        if sql:
            QApplication.clipboard().setText(sql)

    def _clear_chat(self):
        self._chat.clear()
        self._sql_preview.clear()
        self._send_to_editor_btn.setEnabled(False)
        self._history.clear()
