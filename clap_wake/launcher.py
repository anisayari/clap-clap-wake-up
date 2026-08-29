from __future__ import annotations

import os
import json
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path
from shutil import which
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


def open_url_foreground(
    url: str,
    bounds: WindowBounds | None = None,
    browser_preferences: dict[str, str | None] | None = None,
) -> None:
    if sys.platform == "darwin":
        if open_url_new_window_macos(url, browser_preferences=browser_preferences):
            place_foreground_window(bounds)
            return
        subprocess.Popen(["open", url])
        place_foreground_window(bounds)
        return

    if os.name == "nt":
        if open_url_new_window_windows(url):
            place_foreground_window(bounds)
            return
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


def open_url_new_window_macos(
    url: str,
    browser_preferences: dict[str, str | None] | None = None,
) -> bool:
    preferred_browser = browser_preferences or detect_default_macos_browser_preferences()
    app_name = str((preferred_browser or {}).get("app_name") or "").strip()
    profile_directory = str((preferred_browser or {}).get("profile_directory") or "").strip() or None
    if app_name and open_url_in_named_macos_browser(app_name, url, profile_directory=profile_directory):
        return True

    chrome_like_apps = [
        "Google Chrome",
        "Google Chrome Canary",
        "Brave Browser",
        "Microsoft Edge",
        "Arc",
    ]
    for app_name in chrome_like_apps:
        if open_url_in_named_macos_browser(app_name, url):
            return True

    if open_url_in_named_macos_browser("Safari", url):
        return True

    return False


def open_url_in_named_macos_browser(app_name: str, url: str, profile_directory: str | None = None) -> bool:
    if not macos_app_exists(app_name):
        return False

    if app_name in {
        "Google Chrome",
        "Google Chrome Canary",
        "Brave Browser",
        "Microsoft Edge",
        "Arc",
    }:
        executable = get_macos_browser_executable(app_name)
        if executable is None:
            return False
        command = [str(executable)]
        if profile_directory:
            command.append(f"--profile-directory={profile_directory}")
        command.extend(["--new-window", url])
        subprocess.Popen(command)
        return True

    if app_name == "Safari":
        script = "\n".join(
            [
                'tell application "Safari"',
                "activate",
                "make new document",
                f'set URL of front document to "{escape_for_applescript(url)}"',
                "end tell",
            ]
        )
        subprocess.Popen(["osascript", "-e", script])
        return True

    return False


def open_url_new_window_windows(url: str) -> bool:
    browser = detect_windows_browser()
    if browser is None:
        return False

    executable, args = browser
    subprocess.Popen([executable, *args, url])
    return True


def detect_windows_browser() -> tuple[str, list[str]] | None:
    candidates = [
        ("msedge", ["--new-window"]),
        ("chrome", ["--new-window"]),
        ("brave", ["--new-window"]),
        ("firefox", ["-new-window"]),
    ]
    for command, args in candidates:
        path = which(command)
        if path:
            return path, args

    file_candidates = [
        (Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe", ["--new-window"]),
        (Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe", ["--new-window"]),
        (Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe", ["--new-window"]),
        (Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe", ["--new-window"]),
        (Path(os.environ.get("ProgramFiles", "")) / "Mozilla Firefox" / "firefox.exe", ["-new-window"]),
        (Path(os.environ.get("ProgramFiles(x86)", "")) / "Mozilla Firefox" / "firefox.exe", ["-new-window"]),
    ]
    for candidate, args in file_candidates:
        if candidate.exists():
            return str(candidate), args
    return None


def macos_app_exists(app_name: str) -> bool:
    return (
        Path("/Applications", f"{app_name}.app").exists()
        or (Path.home() / "Applications" / f"{app_name}.app").exists()
    )


def get_default_macos_browser_app_name() -> str | None:
    handlers_path = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    if not handlers_path.exists():
        return None

    try:
        data = plistlib.loads(handlers_path.read_bytes())
    except Exception:
        return None

    for item in data.get("LSHandlers", []):
        if item.get("LSHandlerURLScheme") != "http":
            continue
        bundle_id = item.get("LSHandlerRoleAll")
        if not bundle_id:
            continue
        return {
            "com.apple.Safari": "Safari",
            "com.google.chrome": "Google Chrome",
            "com.google.Chrome.canary": "Google Chrome Canary",
            "com.brave.Browser": "Brave Browser",
            "com.microsoft.edgemac": "Microsoft Edge",
            "company.thebrowser.Browser": "Arc",
        }.get(bundle_id)
    return None


def detect_default_macos_browser_preferences() -> dict[str, str | None]:
    app_name = get_default_macos_browser_app_name()
    preferences = {
        "app_name": app_name,
        "profile_directory": None,
        "profile_name": None,
    }
    if not app_name:
        return preferences

    profiles = detect_macos_browser_profiles(app_name)
    if profiles:
        preferences["profile_directory"] = profiles[0]["directory"]
        preferences["profile_name"] = profiles[0]["name"]
    return preferences


def detect_macos_browser_profiles(app_name: str) -> list[dict[str, str]]:
    local_state_path = get_macos_browser_local_state_path(app_name)
    if local_state_path is None or not local_state_path.exists():
        return []

    try:
        data = json.loads(local_state_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    profile_payload = data.get("profile", {})
    last_used = str(profile_payload.get("last_used") or "").strip()
    info_cache = profile_payload.get("info_cache", {})
    profiles: list[dict[str, str]] = []
    for directory, payload in info_cache.items():
        name = str(
            payload.get("name")
            or payload.get("gaia_name")
            or payload.get("gaia_given_name")
            or directory
        ).strip()
        email = str(payload.get("user_name") or "").strip()
        profiles.append(
            {
                "directory": str(directory),
                "name": name,
                "email": email,
            }
        )

    profiles.sort(
        key=lambda item: (
            0 if item["directory"] == last_used else 1,
            item["name"].casefold(),
            item["directory"].casefold(),
        )
    )
    return profiles


def get_macos_browser_local_state_path(app_name: str) -> Path | None:
    user_data_dir = get_macos_browser_user_data_dir(app_name)
    if user_data_dir is None:
        return None
    return user_data_dir / "Local State"


def get_macos_browser_user_data_dir(app_name: str) -> Path | None:
    browser_dirs = {
        "Google Chrome": Path.home() / "Library/Application Support/Google/Chrome",
        "Google Chrome Canary": Path.home() / "Library/Application Support/Google/Chrome Canary",
        "Brave Browser": Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser",
        "Microsoft Edge": Path.home() / "Library/Application Support/Microsoft Edge",
        "Arc": Path.home() / "Library/Application Support/Arc/User Data",
    }
    return browser_dirs.get(app_name)


def get_macos_browser_executable(app_name: str) -> Path | None:
    candidates = [
        Path("/Applications") / f"{app_name}.app" / "Contents/MacOS" / app_name,
        Path.home() / "Applications" / f"{app_name}.app" / "Contents/MacOS" / app_name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


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
