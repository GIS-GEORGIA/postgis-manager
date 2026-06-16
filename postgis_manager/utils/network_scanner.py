"""
Network scanner for PostgreSQL / PostGIS instances.

Strategy (fastest to slowest, all optional):
  1. mDNS/Zeroconf — discovers services announcing _postgresql._tcp
  2. TCP port probe  — concurrent socket connect, configurable timeout
  3. PG banner check — anonymous connect to read server_version
  4. PostGIS check   — connect with supplied credentials, run postgis_version()
"""

from __future__ import annotations
import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional


# ── Helpers ──────────────────────────────────────────────────────────────────

def parse_targets(cidr_or_range: str, extra_ports: list[int] | None = None) -> list[tuple[str, int]]:
    """
    Parse user input into a list of (host, port) pairs.
    Accepted formats:
      192.168.1.0/24            → all hosts in subnet, port 5432
      192.168.1.1-192.168.1.50 → host range, port 5432
      192.168.1.10              → single host, port 5432
      192.168.1.10:5433         → single host, specific port
    extra_ports: additional ports to probe each host (e.g. [5433, 5434])
    """
    ports = [5432] + (extra_ports or [])
    hosts: list[str] = []
    text = cidr_or_range.strip()

    if ":" in text and "/" not in text and "-" not in text:
        # single host:port
        host, port_s = text.rsplit(":", 1)
        return [(host.strip(), int(port_s))]

    if "/" in text:
        net = ipaddress.ip_network(text, strict=False)
        hosts = [str(h) for h in net.hosts()]
    elif "-" in text:
        parts = text.split("-")
        start = ipaddress.ip_address(parts[0].strip())
        end   = ipaddress.ip_address(parts[1].strip())
        hosts = [str(ipaddress.ip_address(i))
                 for i in range(int(start), int(end) + 1)]
    else:
        hosts = [text]

    return [(h, p) for h in hosts for p in set(ports)]


def tcp_probe(host: str, port: int, timeout: float = 0.5) -> Optional[float]:
    """
    Try TCP connect. Returns latency in ms on success, None on failure.
    """
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.monotonic() - t0) * 1000
    except (OSError, socket.timeout):
        return None


def pg_banner(host: str, port: int, timeout: float = 2.0) -> Optional[str]:
    """
    Send the PostgreSQL startup packet and read back the first response byte.
    If the server responds with 'N' or 'R' or server data, it's PostgreSQL.
    Returns a short label like 'PostgreSQL' or None.
    """
    # Minimal startup message: asking for SSL (byte 'S') — server replies N/S
    ssl_request = b"\x00\x00\x00\x08\x04\xd2\x16\x2f"
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(ssl_request)
            resp = s.recv(1)
            if resp in (b"N", b"S"):
                return "PostgreSQL"
            return None
    except Exception:
        return None


def pg_version_check(host: str, port: int, user: str = "postgres",
                     password: str = "", dbname: str = "postgres",
                     timeout: float = 3.0) -> dict:
    """
    Full psycopg2 connect to retrieve server version and PostGIS presence.
    Returns dict with keys: pg_version, postgis_version, error.
    """
    result: dict = {"pg_version": None, "postgis_version": None, "error": None}
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=host, port=port, user=user, password=password,
            dbname=dbname, connect_timeout=int(timeout),
            options="-c statement_timeout=3000",
        )
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            result["pg_version"] = (cur.fetchone() or [""])[0].split(",")[0]
            try:
                cur.execute("SELECT postgis_version()")
                result["postgis_version"] = (cur.fetchone() or [""])[0]
            except Exception:
                pass
        conn.close()
    except Exception as e:
        result["error"] = str(e)
    return result


# ── mDNS discovery ───────────────────────────────────────────────────────────

def mdns_discover(timeout: float = 3.0) -> list[dict]:
    """
    Use zeroconf to find _postgresql._tcp services on the local network.
    Returns list of dicts: {name, host, port}.
    Falls back silently if zeroconf is not installed.
    """
    results: list[dict] = []
    try:
        from zeroconf import Zeroconf, ServiceBrowser  # type: ignore

        found: list[dict] = []

        class Handler:
            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name)
                if info:
                    host = socket.inet_ntoa(info.addresses[0]) if info.addresses else None
                    if host:
                        found.append({
                            "name": name.split(".")[0],
                            "host": host,
                            "port": info.port or 5432,
                            "source": "mDNS",
                        })
            def remove_service(self, *_): pass
            def update_service(self, *_): pass

        zc = Zeroconf()
        handler = Handler()
        _browser = ServiceBrowser(zc, "_postgresql._tcp.local.", handler)
        time.sleep(timeout)
        zc.close()
        results = found
    except ImportError:
        pass
    except Exception:
        pass
    return results


# ── Main scanner ─────────────────────────────────────────────────────────────

def scan_network(
    cidr_or_range: str,
    extra_ports: list[int] | None = None,
    tcp_timeout: float = 0.4,
    deep_check: bool = False,
    deep_user: str = "postgres",
    deep_password: str = "",
    deep_dbname: str = "postgres",
    workers: int = 128,
    on_found: Callable[[dict], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    stop_flag: Callable[[], bool] | None = None,
) -> list[dict]:
    """
    Scan a network range for PostgreSQL instances.

    on_found(result_dict)    — called immediately when a host is found (for live UI updates)
    on_progress(done, total) — called after each probe
    stop_flag()              — if returns True, scanning stops early

    Result dict keys:
      host, port, latency_ms, banner, pg_version, postgis_version,
      source ("scan"|"mDNS"), error
    """
    targets = parse_targets(cidr_or_range, extra_ports)
    total = len(targets)
    found: list[dict] = []
    done_count = 0

    def probe(host: str, port: int) -> Optional[dict]:
        if stop_flag and stop_flag():
            return None
        latency = tcp_probe(host, port, timeout=tcp_timeout)
        if latency is None:
            return None
        result: dict = {
            "host": host, "port": port,
            "latency_ms": round(latency, 1),
            "banner": None,
            "pg_version": None,
            "postgis_version": None,
            "source": "scan",
            "error": None,
        }
        # Verify it's actually PostgreSQL (cheap)
        result["banner"] = pg_banner(host, port, timeout=2.0)
        if result["banner"] is None:
            return None  # Port open but not PG
        # Deep check (optional, needs credentials)
        if deep_check:
            info = pg_version_check(host, port, deep_user, deep_password,
                                    deep_dbname, timeout=3.0)
            result.update(info)
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe, h, p): (h, p) for h, p in targets}
        for future in as_completed(futures):
            done_count += 1
            if on_progress:
                on_progress(done_count, total)
            if stop_flag and stop_flag():
                break
            try:
                r = future.result()
            except Exception:
                r = None
            if r:
                found.append(r)
                if on_found:
                    on_found(r)

    return found
