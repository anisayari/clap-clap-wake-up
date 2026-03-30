from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib import error, request

from .config import get_app_home

RUNTIME_STATE_FILENAME = "runtime-state.json"


def get_runtime_state_path() -> Path:
    return get_app_home() / RUNTIME_STATE_FILENAME


def register_runtime(
    mode: str,
    config_path: Path,
    pid: int | None = None,
    dashboard_url: str | None = None,
) -> Path:
    state = {
        "pid": pid or os.getpid(),
        "mode": mode,
        "config_path": str(config_path),
        "dashboard_url": dashboard_url,
        "started_at": time.time(),
    }
    path = get_runtime_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


def load_runtime_state() -> dict[str, Any] | None:
    path = get_runtime_state_path()
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def clear_runtime_state(expected_pid: int | None = None) -> None:
    path = get_runtime_state_path()
    if not path.exists():
        return
    if expected_pid is not None:
        try:
            payload = load_runtime_state()
        except Exception:
            payload = None
        current_pid = _coerce_pid((payload or {}).get("pid"))
        if current_pid not in {None, expected_pid}:
            return
    path.unlink(missing_ok=True)


def request_runtime_stop() -> tuple[bool, str]:
    state = load_runtime_state()
    if state is None:
        return False, "No running Clap Wake Up instance found."

    pid = _coerce_pid(state.get("pid"))
    mode = str(state.get("mode") or "runtime")
    dashboard_url = str(state.get("dashboard_url") or "").strip()

    if pid is not None and not is_process_running(pid):
        clear_runtime_state(expected_pid=pid)
        return False, f"Removed stale {mode} runtime record."

    if mode == "dashboard" and dashboard_url:
        if request_dashboard_shutdown(dashboard_url):
            if pid is None or wait_for_process_exit(pid, timeout_seconds=5.0):
                clear_runtime_state(expected_pid=pid)
                return True, "Dashboard stopped."
            return True, "Dashboard shutdown requested."

    if pid is None:
        clear_runtime_state()
        return False, f"Cannot stop {mode}: missing process id."

    if not terminate_process(pid):
        return False, f"Failed to stop {mode} process {pid}."

    if wait_for_process_exit(pid, timeout_seconds=5.0):
        clear_runtime_state(expected_pid=pid)
        return True, f"{mode.capitalize()} stopped."
    return True, f"Stop requested for {mode} process {pid}."


def request_dashboard_shutdown(dashboard_url: str, timeout_seconds: float = 2.0) -> bool:
    base = dashboard_url.rstrip("/")
    shutdown_url = f"{base}/shutdown"
    payload = b"{}"
    req = request.Request(
        shutdown_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except (error.URLError, TimeoutError, ValueError):
        return False


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def wait_for_process_exit(pid: int, timeout_seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_process_running(pid):
            return True
        time.sleep(0.1)
    return not is_process_running(pid)


def terminate_process(pid: int) -> bool:
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _coerce_pid(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
