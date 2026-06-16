"""Cross-platform PostgreSQL engine discovery and management.

Supports Windows (Registry + common paths), Linux (pg_lsclusters / paths),
and macOS (Homebrew / Postgres.app / paths).
"""

from __future__ import annotations
import os
import shutil
import subprocess
import platform
from dataclasses import dataclass
from typing import Optional


PLATFORM = platform.system()  # "Windows" | "Linux" | "Darwin"


# ── Data classes ─────────────────────────────────────────────────────────

@dataclass
class PGInstance:
    version:  str
    bin_dir:  str          # directory containing pg_ctl, initdb, psql …
    data_dir: str          # PGDATA
    port:     str = "5432"
    status:   str = "unknown"   # "running" | "stopped" | "unknown"
    source:   str = ""          # "registry" | "homebrew" | "path" | …

    @property
    def pg_ctl(self) -> str:
        exe = "pg_ctl.exe" if PLATFORM == "Windows" else "pg_ctl"
        return os.path.join(self.bin_dir, exe)

    @property
    def initdb(self) -> str:
        exe = "initdb.exe" if PLATFORM == "Windows" else "initdb"
        return os.path.join(self.bin_dir, exe)

    @property
    def psql(self) -> str:
        exe = "psql.exe" if PLATFORM == "Windows" else "psql"
        return os.path.join(self.bin_dir, exe)


# ── Discovery helpers ─────────────────────────────────────────────────────

def _probe_bin_dir(bin_dir: str, data_dir: str = "",
                   port: str = "5432", source: str = "") -> Optional[PGInstance]:
    """Return a PGInstance if bin_dir contains pg_ctl, else None."""
    exe = "pg_ctl.exe" if PLATFORM == "Windows" else "pg_ctl"
    pg_ctl = os.path.join(bin_dir, exe)
    if not os.path.isfile(pg_ctl):
        return None
    # get version
    try:
        out = subprocess.check_output(
            [pg_ctl, "--version"], stderr=subprocess.DEVNULL,
            timeout=5, text=True)
        version = out.strip().split()[-1]
    except Exception:
        version = "?"
    # check status if data_dir known
    status = "unknown"
    if data_dir and os.path.isdir(data_dir):
        try:
            r = subprocess.run(
                [pg_ctl, "status", "-D", data_dir],
                capture_output=True, text=True, timeout=5)
            status = "running" if r.returncode == 0 else "stopped"
        except Exception:
            pass
    return PGInstance(version=version, bin_dir=bin_dir,
                      data_dir=data_dir, port=port,
                      status=status, source=source)


def _discover_windows() -> list[PGInstance]:
    instances = []
    # 1. Windows Registry
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for base in (r"SOFTWARE\PostgreSQL\Installations",
                         r"SOFTWARE\WOW6432Node\PostgreSQL\Installations"):
                try:
                    key = winreg.OpenKey(root, base)
                except FileNotFoundError:
                    continue
                i = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        sub = winreg.OpenKey(key, sub_name)

                        def _v(k, n, d=""):
                            try: return winreg.QueryValueEx(k, n)[0]
                            except FileNotFoundError: return d

                        base_dir  = _v(sub, "Base Directory", "")
                        data_dir  = _v(sub, "Data Directory", "")
                        port      = str(_v(sub, "Port", "5432"))
                        bin_dir   = os.path.join(base_dir, "bin") if base_dir else ""
                        winreg.CloseKey(sub)
                        if bin_dir:
                            inst = _probe_bin_dir(bin_dir, data_dir, port, "registry")
                            if inst:
                                instances.append(inst)
                        i += 1
                    except OSError:
                        break
                winreg.CloseKey(key)
    except ImportError:
        pass

    # 2. Common install paths
    for pf in (os.environ.get("PROGRAMFILES", "C:\\Program Files"),
               os.environ.get("PROGRAMFILES(X86)", "")):
        if not pf:
            continue
        pg_root = os.path.join(pf, "PostgreSQL")
        if not os.path.isdir(pg_root):
            continue
        for ver in sorted(os.listdir(pg_root), reverse=True):
            bin_dir = os.path.join(pg_root, ver, "bin")
            data_dir = os.path.join(pg_root, ver, "data")
            inst = _probe_bin_dir(bin_dir, data_dir, "5432", "program_files")
            if inst and not any(i.bin_dir == inst.bin_dir for i in instances):
                instances.append(inst)
    return instances


def _discover_linux() -> list[PGInstance]:
    instances = []

    # 1. pg_lsclusters (Debian/Ubuntu)
    lsclusters = shutil.which("pg_lsclusters")
    if lsclusters:
        try:
            out = subprocess.check_output(
                [lsclusters, "--no-header"],
                text=True, timeout=10, stderr=subprocess.DEVNULL)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                ver, cluster, port, status, owner = parts[:5]
                data_dir = f"/var/lib/postgresql/{ver}/{cluster}"
                bin_dir  = f"/usr/lib/postgresql/{ver}/bin"
                inst = _probe_bin_dir(bin_dir, data_dir, port, "pg_lsclusters")
                if inst:
                    inst.status = "running" if "online" in status else "stopped"
                    instances.append(inst)
        except Exception:
            pass

    # 2. Glob common paths
    import glob
    search = [
        "/usr/lib/postgresql/*/bin",
        "/usr/local/pgsql/bin",
        "/opt/postgresql/*/bin",
    ]
    for pattern in search:
        for bin_dir in glob.glob(pattern):
            if not any(i.bin_dir == bin_dir for i in instances):
                inst = _probe_bin_dir(bin_dir, "", "5432", "path")
                if inst:
                    instances.append(inst)
    return instances


def _discover_macos() -> list[PGInstance]:
    instances = []
    import glob

    # 1. Postgres.app
    for ver_dir in sorted(
            glob.glob("/Applications/Postgres.app/Contents/Versions/*/bin"),
            reverse=True):
        data_dir = os.path.expanduser(
            f"~/Library/Application Support/Postgres/var-{os.path.basename(os.path.dirname(ver_dir))}")
        inst = _probe_bin_dir(ver_dir, data_dir, "5432", "postgres.app")
        if inst:
            instances.append(inst)

    # 2. Homebrew (Apple Silicon: /opt/homebrew, Intel: /usr/local)
    for prefix in ("/opt/homebrew", "/usr/local"):
        for pattern in (f"{prefix}/opt/postgresql@*/bin",
                        f"{prefix}/opt/postgresql/bin"):
            for bin_dir in sorted(glob.glob(pattern), reverse=True):
                if any(i.bin_dir == bin_dir for i in instances):
                    continue
                # typical Homebrew data dir
                name = os.path.basename(os.path.dirname(bin_dir))
                data_dir = os.path.expanduser(
                    f"~/Library/Application Support/Homebrew/var/{name}")
                if not os.path.isdir(data_dir):
                    data_dir = f"{prefix}/var/{name}"
                inst = _probe_bin_dir(bin_dir, data_dir, "5432", "homebrew")
                if inst:
                    instances.append(inst)

    return instances


def _discover_path() -> list[PGInstance]:
    """Last-resort: find pg_ctl on PATH."""
    found = shutil.which("pg_ctl")
    if not found:
        return []
    bin_dir = os.path.dirname(found)
    inst = _probe_bin_dir(bin_dir, "", "5432", "PATH")
    return [inst] if inst else []


# ── Public: discover all ──────────────────────────────────────────────────

def discover_instances() -> list[PGInstance]:
    """Discover PostgreSQL installations on this machine."""
    if PLATFORM == "Windows":
        instances = _discover_windows()
    elif PLATFORM == "Darwin":
        instances = _discover_macos()
    else:
        instances = _discover_linux()

    # Always try PATH as fallback
    for inst in _discover_path():
        if not any(i.bin_dir == inst.bin_dir for i in instances):
            instances.append(inst)

    # Deduplicate by bin_dir
    seen, result = set(), []
    for i in instances:
        if i.bin_dir not in seen:
            seen.add(i.bin_dir)
            result.append(i)
    return result


# ── Service control ───────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "Timed out"
    except FileNotFoundError as e:
        return -1, str(e)


def start_instance(inst: PGInstance) -> tuple[bool, str]:
    if PLATFORM == "Linux" and shutil.which("systemctl"):
        # try systemd first
        ver = inst.version.split(".")[0]
        unit = f"postgresql@{ver}-main"
        code, out = _run(["systemctl", "start", unit])
        if code == 0:
            return True, out
    if PLATFORM == "Darwin" and shutil.which("brew"):
        name = f"postgresql@{inst.version.split('.')[0]}"
        code, out = _run(["brew", "services", "start", name])
        if code == 0:
            return True, out
    if inst.data_dir and os.path.isfile(inst.pg_ctl):
        log = os.path.join(inst.data_dir, "pg.log")
        code, out = _run([inst.pg_ctl, "start", "-D", inst.data_dir, "-l", log])
        return code == 0, out
    return False, "No valid data_dir or pg_ctl"


def stop_instance(inst: PGInstance) -> tuple[bool, str]:
    if PLATFORM == "Linux" and shutil.which("systemctl"):
        ver = inst.version.split(".")[0]
        code, out = _run(["systemctl", "stop", f"postgresql@{ver}-main"])
        if code == 0:
            return True, out
    if PLATFORM == "Darwin" and shutil.which("brew"):
        name = f"postgresql@{inst.version.split('.')[0]}"
        code, out = _run(["brew", "services", "stop", name])
        if code == 0:
            return True, out
    if inst.data_dir and os.path.isfile(inst.pg_ctl):
        code, out = _run([inst.pg_ctl, "stop", "-D", inst.data_dir, "-m", "fast"])
        return code == 0, out
    return False, "No valid data_dir or pg_ctl"


def reload_instance(inst: PGInstance) -> tuple[bool, str]:
    if inst.data_dir and os.path.isfile(inst.pg_ctl):
        code, out = _run([inst.pg_ctl, "reload", "-D", inst.data_dir])
        return code == 0, out
    return False, "No valid data_dir or pg_ctl"


def init_new_cluster(bin_dir: str, data_dir: str,
                     superuser: str = "postgres",
                     encoding: str = "UTF8") -> tuple[bool, str]:
    """Run initdb to create a new database cluster."""
    exe = "initdb.exe" if PLATFORM == "Windows" else "initdb"
    initdb = os.path.join(bin_dir, exe)
    if not os.path.isfile(initdb):
        return False, f"initdb not found in {bin_dir}"
    os.makedirs(data_dir, exist_ok=True)
    code, out = _run([
        initdb,
        "-D", data_dir,
        "-U", superuser,
        "--encoding", encoding,
        "--auth", "md5",
    ], timeout=60)
    return code == 0, out


def get_status(inst: PGInstance) -> str:
    """Return 'running', 'stopped', or 'unknown'."""
    if not inst.data_dir or not os.path.isfile(inst.pg_ctl):
        return "unknown"
    code, _ = _run([inst.pg_ctl, "status", "-D", inst.data_dir])
    return "running" if code == 0 else "stopped"
