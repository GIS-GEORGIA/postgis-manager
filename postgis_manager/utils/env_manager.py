"""Cross-platform persistent environment variable management.

Windows : HKCU\\Environment (broadcast WM_SETTINGCHANGE so shells pick it up)
Linux   : ~/.bashrc  and ~/.profile (if they exist) / creates ~/.profile
macOS   : ~/.zshrc   and ~/.bash_profile
"""

from __future__ import annotations
import os
import platform
import re
import shutil
from pathlib import Path
from typing import Optional

PLATFORM = platform.system()


# ── Read current env vars ─────────────────────────────────────────────────

def get_env_var(name: str) -> str:
    """Return the *process-level* value of an environment variable, or ''."""
    return os.environ.get(name, "")


# ── Windows ───────────────────────────────────────────────────────────────

def _win_get(name: str) -> str:
    """Read from HKCU\\Environment."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
        val, _ = winreg.QueryValueEx(key, name)
        winreg.CloseKey(key)
        return val
    except Exception:
        return ""


def _win_set(name: str, value: str) -> None:
    """Write to HKCU\\Environment and broadcast the change."""
    import winreg, ctypes
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment",
                         0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)
    winreg.CloseKey(key)
    # Broadcast so Explorer / new consoles pick it up
    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 1000, None)


def _win_delete(name: str) -> None:
    try:
        import winreg, ctypes
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment",
                             0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, name)
        winreg.CloseKey(key)
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 1000, None)
    except Exception:
        pass


# ── Unix shell file helpers ───────────────────────────────────────────────

_MARKER = "# postgis_manager_env"


def _shell_files() -> list[Path]:
    """Return shell rc files to write to on this OS."""
    home = Path.home()
    if PLATFORM == "Darwin":
        candidates = [home / ".zshrc", home / ".bash_profile"]
    else:  # Linux
        candidates = [home / ".bashrc", home / ".profile"]
    # ensure at least one exists
    existing = [p for p in candidates if p.exists()]
    if not existing:
        candidates[0].touch()
        return [candidates[0]]
    return existing


def _read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _write_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _unix_get(name: str) -> str:
    """Scan shell files for the most recent export NAME=... line we wrote."""
    pattern = re.compile(
        rf'^export\s+{re.escape(name)}=(.+?)\s*{re.escape(_MARKER)}',
        re.MULTILINE)
    for path in _shell_files():
        text = _read_file(path)
        m = pattern.search(text)
        if m:
            return m.group(1).strip('"\'')
    return ""


def _unix_set(name: str, value: str) -> None:
    """Write or update export NAME=VALUE in each shell file."""
    line = f'export {name}="{value}" {_MARKER}'
    pattern = re.compile(
        rf'^export\s+{re.escape(name)}=.*?{re.escape(_MARKER)}.*$',
        re.MULTILINE)
    for path in _shell_files():
        text = _read_file(path)
        if pattern.search(text):
            new_text = pattern.sub(line, text)
        else:
            sep = "\n" if text and not text.endswith("\n") else ""
            new_text = text + sep + line + "\n"
        _write_file(path, new_text)


def _unix_delete(name: str) -> None:
    """Remove lines we wrote for this variable."""
    pattern = re.compile(
        rf'^export\s+{re.escape(name)}=.*?{re.escape(_MARKER)}.*\n?',
        re.MULTILINE)
    for path in _shell_files():
        text = _read_file(path)
        _write_file(path, pattern.sub("", text))


# ── Public API ────────────────────────────────────────────────────────────

def get_persistent(name: str) -> str:
    """Read the persistently stored value for *name* (not current process)."""
    if PLATFORM == "Windows":
        return _win_get(name)
    return _unix_get(name)


def set_persistent(name: str, value: str, also_set_process: bool = True) -> None:
    """Persist *name=value* so new shells inherit it."""
    if PLATFORM == "Windows":
        _win_set(name, value)
    else:
        _unix_set(name, value)
    if also_set_process:
        os.environ[name] = value


def delete_persistent(name: str, also_unset_process: bool = True) -> None:
    """Remove the persistent setting for *name*."""
    if PLATFORM == "Windows":
        _win_delete(name)
    else:
        _unix_delete(name)
    if also_unset_process:
        os.environ.pop(name, None)


# ── Candidate auto-detection ──────────────────────────────────────────────

def _find_dir(*candidates: str) -> str:
    for c in candidates:
        c = os.path.expandvars(os.path.expanduser(c))
        if os.path.isdir(c):
            return c
    return ""


def suggest_proj_lib() -> str:
    """Return the best candidate PROJ_LIB directory or ''."""
    candidates = []
    if PLATFORM == "Windows":
        for pf in (os.environ.get("PROGRAMFILES", ""),
                   os.environ.get("PROGRAMFILES(X86)", "")):
            if pf:
                for pg in ("PostgreSQL", "OSGeo4W64", "OSGeo4W"):
                    candidates.extend([
                        os.path.join(pf, pg, v, "share", "proj")
                        for v in ("17", "16", "15", "14", "13", "12")
                    ])
        for osgeo in (r"C:\OSGeo4W64", r"C:\OSGeo4W"):
            candidates.append(os.path.join(osgeo, "share", "proj"))
    elif PLATFORM == "Darwin":
        for prefix in ("/opt/homebrew", "/usr/local"):
            candidates.append(f"{prefix}/share/proj")
    else:
        candidates.extend(["/usr/share/proj", "/usr/local/share/proj"])
    # also check QGIS bundled PROJ
    if PLATFORM == "Windows":
        qgis_proj = os.path.join(
            os.environ.get("PROGRAMFILES", ""),
            "QGIS 3.34", "apps", "proj", "share", "proj")
        candidates.insert(0, qgis_proj)
    return _find_dir(*candidates)


def suggest_gdal_data() -> str:
    """Return the best candidate GDAL_DATA directory or ''."""
    candidates = []
    if PLATFORM == "Windows":
        for pf in (os.environ.get("PROGRAMFILES", ""),):
            if pf:
                for pg in ("QGIS 3.34", "OSGeo4W64", "OSGeo4W"):
                    candidates.extend([
                        os.path.join(pf, pg, "apps", "gdal", "share", "gdal"),
                        os.path.join(pf, pg, "share", "gdal"),
                    ])
        for osgeo in (r"C:\OSGeo4W64", r"C:\OSGeo4W"):
            candidates.append(os.path.join(osgeo, "share", "gdal"))
    elif PLATFORM == "Darwin":
        for prefix in ("/opt/homebrew", "/usr/local"):
            candidates.append(f"{prefix}/share/gdal")
    else:
        candidates.extend(["/usr/share/gdal", "/usr/local/share/gdal"])
    return _find_dir(*candidates)


def suggest_ssl_cert() -> str:
    """Return a CA-bundle path or ''."""
    candidates = [
        "/etc/ssl/certs/ca-certificates.crt",        # Debian/Ubuntu
        "/etc/pki/tls/certs/ca-bundle.crt",           # RHEL/Fedora
        "/etc/ssl/cert.pem",                           # macOS / Alpine
        "/usr/local/etc/openssl/cert.pem",             # macOS Homebrew
        r"C:\Program Files\Common Files\ssl\cert.pem",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    # certifi bundled with Python
    try:
        import certifi
        return certifi.where()
    except ImportError:
        pass
    return ""


def all_suggestions() -> dict[str, str]:
    """Return {VAR_NAME: suggested_path} for common PostGIS env vars."""
    return {
        "PROJ_LIB":    suggest_proj_lib(),
        "GDAL_DATA":   suggest_gdal_data(),
        "SSL_CERT_FILE": suggest_ssl_cert(),
    }
