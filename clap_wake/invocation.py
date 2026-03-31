from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence


def detect_python_launcher(platform: str | None = None) -> list[str]:
    current_platform = platform or os.name
    candidates = ["python", "py", "python3"] if current_platform == "nt" else ["python3", "python", "py"]

    for candidate in candidates:
        if shutil.which(candidate):
            if candidate == "py":
                return ["py", "-3"]
            return [candidate]

    return [sys.executable]


def format_shell_command(parts: Sequence[str], platform: str | None = None) -> str:
    current_platform = platform or os.name
    if current_platform == "nt":
        return subprocess.list2cmdline(list(parts))
    return shlex.join(parts)


def build_module_command(command: str, *extra_args: str, platform: str | None = None) -> str:
    current_platform = platform or os.name
    if getattr(sys, "frozen", False):
        return format_shell_command([sys.executable, command, *extra_args], platform=current_platform)

    launcher = detect_python_launcher(platform=current_platform)
    return format_shell_command([*launcher, "-m", "clap_wake", command, *extra_args], platform=current_platform)
