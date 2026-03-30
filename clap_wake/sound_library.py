from __future__ import annotations

import os
import shutil
import shlex
from pathlib import Path

APP_NAME = "ClapWakeUp"

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg"}


def get_media_library_dir() -> Path:
    return get_app_home() / "media"


def get_app_home() -> Path:
    if os.sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / APP_NAME
    return Path.home() / ".config" / APP_NAME.lower()


def list_audio_files(directory: Path) -> list[Path]:
    matches = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS
    ]
    return sorted(matches, key=lambda item: item.name.casefold())


def normalize_user_path(raw: str) -> Path:
    text = raw.strip()
    if text.startswith("file://"):
        text = text[7:]
    try:
        pieces = shlex.split(text)
        if len(pieces) == 1:
            text = pieces[0]
    except ValueError:
        pass
    text = text.replace("\\ ", " ")
    return Path(text).expanduser()


def copy_audio_to_library(source: Path) -> Path:
    library_dir = get_media_library_dir()
    library_dir.mkdir(parents=True, exist_ok=True)

    safe_name = sanitize_filename(source.stem) + source.suffix.casefold()
    destination = unique_destination(library_dir / safe_name)
    shutil.copy2(source, destination)
    return destination


def sanitize_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", " "} else "_" for ch in value)
    collapsed = " ".join(cleaned.split()).strip()
    return collapsed or "sound"


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Unable to find a free destination for {path}")


def choose_audio_file_dialog() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    root = tk.Tk()
    root.withdraw()
    root.update()
    filename = filedialog.askopenfilename(
        title=f"{APP_NAME} - Choisir un son",
        filetypes=[("Audio files", "*.mp3 *.wav *.ogg"), ("MP3", "*.mp3"), ("All files", "*.*")],
    )
    root.destroy()
    return Path(filename) if filename else None


def choose_directory_dialog() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    root = tk.Tk()
    root.withdraw()
    root.update()
    selected = filedialog.askdirectory(title=f"{APP_NAME} - Choisir un dossier audio")
    root.destroy()
    return Path(selected) if selected else None


def describe_existing_sound(path_value: str | None) -> str | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    if path.exists():
        return path.name
    return f"{path.name} (introuvable)"
