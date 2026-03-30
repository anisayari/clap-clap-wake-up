from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .window_layout import WindowBounds, place_foreground_window


def launch_targets(targets: Iterable[dict], cwd: Path, bounds_list: list[WindowBounds] | None = None) -> None:
    for index, target in enumerate(targets):
        bounds = bounds_list[index] if bounds_list and index < len(bounds_list) else None
        launch_target(target, cwd=cwd, bounds=bounds)


def launch_target(target: dict, cwd: Path, bounds: WindowBounds | None = None) -> None:
    target_id = target["id"]

    if target_id == "codex_desktop":
        launch_codex_desktop(
            custom_command=target.get("custom_command"),
            app_path=target.get("app_path"),
            bounds=bounds,
        )
        return

    if target_id == "codex_cli":
        launch_terminal_command(target.get("command", "codex"), cwd=cwd, bounds=bounds)
        return

    if target_id == "claude_code":
        launch_terminal_command(target.get("command", "claude"), cwd=cwd, bounds=bounds)
        return

    if target_id in {"claude_web", "chatgpt_web"}:
        open_url_foreground(target["url"], bounds=bounds)
        return

    if target_id == "custom_url":
        open_url_foreground(target["url"], bounds=bounds)
        return

    if target_id == "custom_path":
        open_path_foreground(Path(target["path"]), bounds=bounds)
        return

    if target_id == "custom_terminal_command":
        launch_terminal_command(target["command"], cwd=cwd, bounds=bounds)
        return

    if target_id == "custom_shell_command":
        launch_shell_command(target["command"], bounds=bounds)
        return

    raise ValueError(f"Unsupported target: {target_id}")


def launch_codex_desktop(
    custom_command: str | None = None,
    app_path: str | None = None,
    bounds: WindowBounds | None = None,
) -> None:
    if custom_command:
        launch_shell_command(custom_command, bounds=bounds)
        return

    if app_path:
        open_path_foreground(Path(app_path), bounds=bounds)
        return

    if sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Codex"])
        place_foreground_window(bounds)
        return

    if os.name == "nt":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Codex" / "Codex.exe",
            Path(os.environ.get("ProgramFiles", "")) / "Codex" / "Codex.exe",
            Path(os.environ.get("ProgramFiles(x86)", "")) / "Codex" / "Codex.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                subprocess.Popen([str(candidate)])
                place_foreground_window(bounds)
                return

        launch_shell_command('start "" "Codex"', bounds=bounds)
        return

    raise RuntimeError("Codex Desktop is only wired for macOS and Windows.")


def open_url_foreground(url: str, bounds: WindowBounds | None = None) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", url])
        place_foreground_window(bounds)
        return

    if os.name == "nt":
        launch_shell_command(f'start "" "{url}"', bounds=bounds)
        return

    subprocess.Popen(["xdg-open", url])


def launch_terminal_command(command: str, cwd: Path, bounds: WindowBounds | None = None) -> None:
    if sys.platform == "darwin":
        shell_line = f"cd {shlex.quote(str(cwd))} && {command}"
        apple_script = "\n".join(
            [
                'tell application "Terminal"',
                "activate",
                f'do script "{escape_for_applescript(shell_line)}"',
                "end tell",
            ]
        )
        subprocess.Popen(["osascript", "-e", apple_script])
        place_foreground_window(bounds)
        return

    if os.name == "nt":
        full_command = f'start "" cmd /k "cd /d {quote_for_cmd(str(cwd))} && {command}"'
        launch_shell_command(full_command, bounds=bounds)
        return

    subprocess.Popen(
        ["x-terminal-emulator", "-e", f"cd {shlex.quote(str(cwd))} && {command}"],
        cwd=str(cwd),
    )


def open_file_background(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return

    if os.name == "nt":
        launch_shell_command(f'start "" "{path}"')
        return

    subprocess.Popen(["xdg-open", str(path)])


def open_directory_background(path: Path) -> None:
    open_file_background(path)


def open_path_foreground(path: Path, bounds: WindowBounds | None = None) -> None:
    expanded = path.expanduser()
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(expanded)])
        place_foreground_window(bounds)
        return

    if os.name == "nt":
        launch_shell_command(f'start "" "{expanded}"', bounds=bounds)
        return

    subprocess.Popen(["xdg-open", str(expanded)])


def launch_shell_command(command: str, bounds: WindowBounds | None = None) -> None:
    subprocess.Popen(command, shell=True)
    place_foreground_window(bounds)


def build_dashboard_command(config_path: Path) -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" dashboard --config "{config_path}"'
    return f'"{sys.executable}" -m clap_wake dashboard --config "{config_path}"'


def launch_dashboard_terminal(config_path: Path, cwd: Path) -> None:
    launch_terminal_command(build_dashboard_command(config_path), cwd=cwd)


def escape_for_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def quote_for_cmd(value: str) -> str:
    return f'"{value}"'
