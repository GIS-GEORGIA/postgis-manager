"""Docker / Podman container utilities for PostgreSQL instance management."""

from __future__ import annotations
import subprocess
import json
from typing import Optional


RUNTIMES = ["docker", "podman"]


def _run(runtime: str, *args, timeout: int = 10) -> tuple[int, str, str]:
    """Run a docker/podman command. Returns (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            [runtime, *args],
            capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return -1, "", f"'{runtime}' not found in PATH"
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)


def runtime_available(runtime: str) -> bool:
    code, _, _ = _run(runtime, "version", "--format", "{{.Server.Version}}")
    return code == 0


def available_runtimes() -> list[str]:
    return [r for r in RUNTIMES if runtime_available(r)]


def list_postgres_containers(runtime: str) -> list[dict]:
    """List all containers (running + stopped) that look like PostgreSQL."""
    code, out, err = _run(
        runtime, "ps", "-a",
        "--format", "{{json .}}",
        timeout=15,
    )
    if code != 0:
        return []
    results = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            c = json.loads(line)
        except Exception:
            continue
        # Accept containers whose image or name looks postgresql-related
        image = (c.get("Image") or c.get("image") or "").lower()
        name  = (c.get("Names") or c.get("names") or c.get("Name") or "").lower()
        if any(kw in image or kw in name for kw in
               ["postgres", "postgis", "pg", "pgsql"]):
            results.append(_normalise(c, runtime))
    return results


def _normalise(c: dict, runtime: str) -> dict:
    """Normalise docker vs podman JSON field names."""
    name   = c.get("Names") or c.get("names") or c.get("Name") or ""
    if isinstance(name, list):
        name = name[0] if name else ""
    name = name.lstrip("/")
    status = (c.get("Status") or c.get("status") or c.get("State") or "").lower()
    ports  = c.get("Ports") or c.get("ports") or ""
    image  = c.get("Image") or c.get("image") or ""
    cid    = c.get("ID") or c.get("Id") or c.get("id") or ""
    return {
        "id":      cid[:12],
        "name":    name,
        "image":   image,
        "status":  status,
        "ports":   str(ports),
        "runtime": runtime,
        "running": "up" in status or "running" in status,
    }


def container_info(runtime: str, name_or_id: str) -> Optional[dict]:
    code, out, _ = _run(runtime, "inspect",
                         "--format", "{{json .}}", name_or_id, timeout=10)
    if code != 0 or not out:
        return None
    try:
        data = json.loads(out)
        if isinstance(data, list):
            data = data[0]
    except Exception:
        return None
    # Extract mapped host port for 5432
    port = _extract_host_port(data)
    state = data.get("State", {})
    running = state.get("Running", False) if isinstance(state, dict) else False
    return {
        "id":      data.get("Id", "")[:12],
        "name":    (data.get("Name") or "").lstrip("/"),
        "image":   (data.get("Config") or {}).get("Image", ""),
        "running": running,
        "host_port": port,
        "runtime": runtime,
    }


def _extract_host_port(inspect_data: dict) -> Optional[str]:
    try:
        bindings = (inspect_data.get("HostConfig", {})
                    .get("PortBindings", {}))
        for key, val in bindings.items():
            if "5432" in key and val:
                return val[0].get("HostPort")
    except Exception:
        pass
    return None


def start_container(runtime: str, name_or_id: str) -> tuple[bool, str]:
    code, _, err = _run(runtime, "start", name_or_id, timeout=30)
    return code == 0, err


def stop_container(runtime: str, name_or_id: str) -> tuple[bool, str]:
    code, _, err = _run(runtime, "stop", name_or_id, timeout=30)
    return code == 0, err


def restart_container(runtime: str, name_or_id: str) -> tuple[bool, str]:
    code, _, err = _run(runtime, "restart", name_or_id, timeout=30)
    return code == 0, err


def get_container_env(runtime: str, name_or_id: str) -> dict[str, str]:
    """Extract POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB from container env."""
    code, out, _ = _run(runtime, "inspect",
                         "--format", "{{json .Config.Env}}", name_or_id)
    result: dict[str, str] = {}
    if code != 0 or not out:
        return result
    try:
        env_list = json.loads(out)
        for entry in (env_list or []):
            if "=" in entry:
                k, v = entry.split("=", 1)
                result[k] = v
    except Exception:
        pass
    return result


def suggest_connection_from_container(runtime: str, name_or_id: str) -> dict:
    """Build a connection profile dict from a container's metadata."""
    info = container_info(runtime, name_or_id) or {}
    env  = get_container_env(runtime, name_or_id)
    return {
        "name":          info.get("name") or name_or_id,
        "host":          "localhost",
        "port":          info.get("host_port") or "5432",
        "dbname":        env.get("POSTGRES_DB") or env.get("PGDATABASE") or "postgres",
        "user":          env.get("POSTGRES_USER") or env.get("PGUSER") or "postgres",
        "password":      env.get("POSTGRES_PASSWORD") or env.get("PGPASSWORD") or "",
        "ssl_mode":      "disable",
        "timeout":       10,
        "instance_type": runtime,
        "container_name": info.get("name") or name_or_id,
    }
