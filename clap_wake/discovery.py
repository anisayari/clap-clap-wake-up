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


def detect_cli(commands: list[str], os_name: str | None = None) -> dict:
    for command in commands:
        resolved = shutil.which(command)
        if resolved:
            return {
                "found": True,
                "method": "command",
                "command": resolved,
                "command_name": command,
            }

    for candidate in cli_path_candidates(commands, os_name=os_name):
        if candidate.exists():
            return {
                "found": True,
                "method": "command",
                "command": str(candidate),
                "command_name": candidate.stem,
            }
    return {"found": False}


def cli_path_candidates(commands: list[str], os_name: str | None = None) -> list[Path]:
    resolved_os_name = os_name or os.name
    if resolved_os_name == "nt":
        return windows_cli_candidates(commands)
    return unix_cli_candidates(commands)


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


def windows_cli_candidates(commands: list[str]) -> list[Path]:
    variants: list[str] = []
    for command in commands:
        variants.extend([command, f"{command}.cmd", f"{command}.exe", f"{command}.bat", f"{command}.ps1"])

    candidates: list[Path] = []
    candidate_dirs: list[Path] = []

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidate_dirs.append(Path(appdata) / "npm")

    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        local_root = Path(localappdata)
        candidate_dirs.extend(
            [
                local_root / "npm",
                local_root / "Programs" / "Python",
                local_root / "Microsoft" / "WinGet" / "Links",
            ]
        )

    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        home = Path(userprofile)
        candidate_dirs.extend([home / "AppData" / "Roaming" / "npm", home / "scoop" / "shims"])

    seen: set[Path] = set()
    for directory in candidate_dirs:
        if not directory.exists():
            continue
        if directory.name == "Python":
            for python_dir in directory.glob("Python*"):
                script_dir = python_dir / "Scripts"
                if script_dir.exists():
                    for variant in variants:
                        candidate = script_dir / variant
                        if candidate not in seen:
                            candidates.append(candidate)
                            seen.add(candidate)
            continue
        for variant in variants:
            candidate = directory / variant
            if candidate not in seen:
                candidates.append(candidate)
                seen.add(candidate)
    return candidates


def unix_cli_candidates(commands: list[str]) -> list[Path]:
    candidate_dirs = [
        Path.home() / ".local" / "bin",
        Path.home() / ".npm-global" / "bin",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    ]
    candidates: list[Path] = []
    seen: set[Path] = set()
    for directory in candidate_dirs:
        if not directory.exists():
            continue
        for command in commands:
            candidate = directory / command
            if candidate not in seen:
                candidates.append(candidate)
                seen.add(candidate)
    return candidates
