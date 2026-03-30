from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PermissionResult:
    key: str
    label: str
    granted: bool
    message: str
    can_open_settings: bool = False


def get_required_permission_keys(
    platform: str,
    selected_targets: list[dict[str, Any]] | None = None,
) -> list[str]:
    keys = ["microphone"]
    if platform == "darwin" and (selected_targets or []):
        keys.append("accessibility")
    return keys


def probe_permission(
    key: str,
    microphone_config: dict[str, Any] | None = None,
    platform: str | None = None,
) -> PermissionResult:
    resolved_platform = platform or sys.platform
    if key == "microphone":
        return probe_microphone_permission(microphone_config or {})
    if key == "accessibility":
        return probe_accessibility_permission(platform=resolved_platform)
    raise ValueError(f"Unsupported permission key: {key}")


def probe_microphone_permission(microphone_config: dict[str, Any]) -> PermissionResult:
    try:
        import sounddevice as sd
    except ImportError:
        return PermissionResult(
            key="microphone",
            label="Microphone",
            granted=False,
            message="sounddevice is not installed.",
            can_open_settings=False,
        )

    sample_rate = int(microphone_config.get("sample_rate", 16000))
    blocksize = int(microphone_config.get("blocksize", 512))
    try:
        stream_kwargs = {
            "samplerate": sample_rate,
            "channels": 1,
            "dtype": "float32",
            "blocksize": blocksize,
        }
        if microphone_config.get("input_device") not in {None, ""}:
            stream_kwargs["device"] = int(microphone_config["input_device"])
        with sd.InputStream(**stream_kwargs):
            time.sleep(0.12)
        return PermissionResult(
            key="microphone",
            label="Microphone",
            granted=True,
            message="Microphone input is available.",
            can_open_settings=False,
        )
    except Exception as exc:
        return PermissionResult(
            key="microphone",
            label="Microphone",
            granted=False,
            message=str(exc) or "Microphone access failed.",
            can_open_settings=settings_supported("microphone"),
        )


def probe_accessibility_permission(platform: str | None = None) -> PermissionResult:
    resolved_platform = platform or sys.platform
    if resolved_platform != "darwin":
        return PermissionResult(
            key="accessibility",
            label="Accessibility",
            granted=True,
            message="Accessibility check is only required on macOS.",
            can_open_settings=False,
        )

    result = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to return UI elements enabled'],
        capture_output=True,
        text=True,
    )
    stdout = result.stdout.strip().lower()
    if result.returncode == 0 and stdout == "true":
        return PermissionResult(
            key="accessibility",
            label="Accessibility",
            granted=True,
            message="System Events accessibility access is enabled.",
            can_open_settings=False,
        )

    stderr = result.stderr.strip()
    return PermissionResult(
        key="accessibility",
        label="Accessibility",
        granted=False,
        message=stderr or stdout or "Accessibility permission is not enabled.",
        can_open_settings=settings_supported("accessibility"),
    )


def settings_supported(key: str, platform: str | None = None) -> bool:
    return settings_command_for(key, platform=platform) is not None


def open_permission_settings(key: str, platform: str | None = None) -> bool:
    command = settings_command_for(key, platform=platform)
    if command is None:
        return False
    subprocess.Popen(command)
    return True


def settings_command_for(key: str, platform: str | None = None) -> list[str] | None:
    resolved_platform = platform or sys.platform

    if resolved_platform == "darwin":
        urls = {
            "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
            "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        }
        url = urls.get(key)
        return ["open", url] if url else None

    if os.name == "nt":
        urls = {
            "microphone": "ms-settings:privacy-microphone",
        }
        url = urls.get(key)
        return ["cmd", "/c", "start", "", url] if url else None

    return None
