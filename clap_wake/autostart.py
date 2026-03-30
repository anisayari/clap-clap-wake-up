from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import get_app_home, get_config_path, get_log_path
from .launcher import build_dashboard_command

MAC_LABEL = "com.clapwakeup.agent"
WINDOWS_SCRIPT_NAME = "clap-wake-up.vbs"


def install_autostart(project_dir: Path, config_path: Path | None = None) -> Path:
    if sys.platform == "darwin":
        return install_launch_agent(project_dir, config_path=config_path)
    if os.name == "nt":
        return install_windows_startup(project_dir, config_path=config_path)
    raise RuntimeError("Autostart is only implemented for macOS and Windows.")


def uninstall_autostart() -> Path:
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
        path.unlink(missing_ok=True)
        return path
    if os.name == "nt":
        path = get_windows_startup_dir() / WINDOWS_SCRIPT_NAME
        path.unlink(missing_ok=True)
        return path
    raise RuntimeError("Autostart is only implemented for macOS and Windows.")


def install_launch_agent(project_dir: Path, config_path: Path | None = None) -> Path:
    agent_path = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    app_home = get_app_home()
    app_home.mkdir(parents=True, exist_ok=True)
    log_path = get_log_path()
    resolved_config_path = config_path or get_config_path()
    shell_line = f"cd {escape_shell(str(project_dir))} && {build_dashboard_command(resolved_config_path)}"
    program_arguments = f"""
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/osascript</string>
    <string>-e</string>
    <string>tell application "Terminal"</string>
    <string>-e</string>
    <string>activate</string>
    <string>-e</string>
    <string>do script "{escape_for_applescript(shell_line)}"</string>
    <string>-e</string>
    <string>end tell</string>
  </array>"""

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{MAC_LABEL}</string>
{program_arguments}
  <key>WorkingDirectory</key>
  <string>{project_dir}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>{log_path}</string>
  <key>StandardErrorPath</key>
  <string>{log_path}</string>
</dict>
</plist>
"""
    agent_path.write_text(plist, encoding="utf-8")
    return agent_path


def install_windows_startup(project_dir: Path, config_path: Path | None = None) -> Path:
    startup_dir = get_windows_startup_dir()
    startup_dir.mkdir(parents=True, exist_ok=True)
    app_home = get_app_home()
    app_home.mkdir(parents=True, exist_ok=True)
    resolved_config_path = config_path or get_config_path()
    command = build_dashboard_command(resolved_config_path)

    script_path = startup_dir / WINDOWS_SCRIPT_NAME
    script = "\n".join(
        [
            'Set WshShell = CreateObject("WScript.Shell")',
            f'WshShell.CurrentDirectory = "{project_dir}"',
            f'WshShell.Run "cmd /k ""cd /d {escape_vbs(str(project_dir))} && {escape_vbs(command)}""", 1, False',
        ]
    )
    script_path.write_text(script, encoding="utf-8")
    return script_path


def get_windows_startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not defined.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def escape_vbs(value: str) -> str:
    return value.replace('"', '""')


def escape_for_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def escape_shell(value: str) -> str:
    return f'"{value}"'
