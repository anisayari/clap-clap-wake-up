from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def detect_known_targets() -> dict[str, dict]:
    return {
        "codex_desktop": detect_codex_desktop(),
        "codex_cli": detect_codex_cli(),
        "claude_code": detect_claude_code(),
    }


def detect_codex_desktop() -> dict:
    if sys.platform == "darwin":
        for candidate in macos_app_candidates(["Codex.app"]):
            if candidate.exists():
                return {"found": True, "method": "app_path", "app_path": str(candidate)}
        return {"found": False}

    if os.name == "nt":
        for candidate in windows_file_candidates(
            [
                ("LOCALAPPDATA", Path("Programs") / "Codex" / "Codex.exe"),
                ("ProgramFiles", Path("Codex") / "Codex.exe"),
                ("ProgramFiles(x86)", Path("Codex") / "Codex.exe"),
            ]
        ):
            if candidate.exists():
                return {"found": True, "method": "app_path", "app_path": str(candidate)}
        return {"found": False}

    return {"found": False}


def detect_codex_cli() -> dict:
    return detect_cli(["codex"])


def detect_claude_code() -> dict:
    return detect_cli(["claude", "claude-code"])


def detect_cli(commands: list[str]) -> dict:
    for command in commands:
        resolved = shutil.which(command)
        if resolved:
            return {
                "found": True,
                "method": "command",
                "command": resolved,
                "command_name": command,
            }
    return {"found": False}


def macos_app_candidates(names: list[str]) -> list[Path]:
    candidates: list[Path] = []
    roots = [
        Path("/Applications"),
        Path.home() / "Applications",
    ]
    for root in roots:
        for name in names:
            candidates.append(root / name)
    return candidates


def windows_file_candidates(items: list[tuple[str, Path]]) -> list[Path]:
    candidates: list[Path] = []
    for env_name, suffix in items:
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / suffix)
    return candidates
