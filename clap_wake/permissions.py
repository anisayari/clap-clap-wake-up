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
    device_index = _configured_device_index(microphone_config)
    try:
        _open_input_stream(sd, sample_rate=sample_rate, blocksize=blocksize, device_index=device_index)
        return PermissionResult(
            key="microphone",
            label="Microphone",
            granted=True,
            message="Microphone input is available.",
            can_open_settings=False,
        )
    except Exception as exc:
        fallback_rates = _fallback_sample_rates(sd, device_index=device_index, current_rate=sample_rate)
        for fallback_rate in fallback_rates:
            try:
                _open_input_stream(sd, sample_rate=fallback_rate, blocksize=blocksize, device_index=device_index)
            except Exception:
                continue

            microphone_config["sample_rate"] = fallback_rate
            return PermissionResult(
                key="microphone",
                label="Microphone",
                granted=True,
                message=f"Microphone input is available with sample rate {fallback_rate} Hz.",
                can_open_settings=False,
            )

        is_config_error = _looks_like_stream_format_error(str(exc))
        return PermissionResult(
            key="microphone",
            label="Microphone",
            granted=False,
            message=str(exc) or "Microphone access failed.",
            can_open_settings=(not is_config_error) and settings_supported("microphone"),
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
        can_open_settings=settings_supported("accessibility", platform=resolved_platform),
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


def _configured_device_index(microphone_config: dict[str, Any]) -> int | None:
    raw = microphone_config.get("input_device")
    if raw in {None, ""}:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _open_input_stream(sd: Any, sample_rate: int, blocksize: int, device_index: int | None) -> None:
    stream_kwargs = {
        "samplerate": sample_rate,
        "channels": 1,
        "dtype": "float32",
        "blocksize": blocksize,
    }
    if device_index is not None:
        stream_kwargs["device"] = device_index
    with sd.InputStream(**stream_kwargs):
        time.sleep(0.12)


def _fallback_sample_rates(sd: Any, device_index: int | None, current_rate: int) -> list[int]:
    candidates: list[int] = []
    try:
        device_info = sd.query_devices(device_index) if device_index is not None else sd.query_devices()
    except Exception:
        device_info = None

    default_rate = None
    if isinstance(device_info, dict):
        raw_default = device_info.get("default_samplerate")
        try:
            default_rate = int(float(raw_default)) if raw_default else None
        except (TypeError, ValueError):
            default_rate = None

    for rate in [default_rate, 48000, 44100, 32000, 22050, 16000]:
        if rate and rate != current_rate and rate not in candidates:
            candidates.append(rate)
    return candidates


def _looks_like_stream_format_error(message: str) -> bool:
    lowered = message.casefold()
    return (
        "invalid sample rate" in lowered
        or "paerrorcode -9997" in lowered
        or "invalid number of channels" in lowered
        or "device unavailable" in lowered
        or "error opening inputstream" in lowered
    )
