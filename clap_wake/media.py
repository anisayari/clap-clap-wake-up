from __future__ import annotations

import random
from pathlib import Path

PREFERRED_KEYWORDS = [
    ("highway to hell",),
    ("highway", "acdc"),
    ("highway", "ac-dc"),
]


def find_highway_mp3(downloads_dir: str | Path | None) -> Path | None:
    if not downloads_dir:
        return None

    base = Path(downloads_dir).expanduser()
    if not base.exists():
        return None

    mp3_files = list(base.glob("*.mp3"))
    if not mp3_files:
        mp3_files = list(base.rglob("*.mp3"))

    for keyword_group in PREFERRED_KEYWORDS:
        match = next(
            (
                path
                for path in mp3_files
                if all(keyword in normalize_name(path.name) for keyword in keyword_group)
            ),
            None,
        )
        if match:
            return match

    fallback = next((path for path in mp3_files if "highway" in normalize_name(path.name)), None)
    if fallback:
        return fallback

    return None


def pick_random_audio_from_folder(folder: str | Path | None) -> Path | None:
    matches = list_audio_from_folder(folder)
    if not matches:
        return None
    return random.choice(matches)


def pick_next_audio_from_folder(
    folder: str | Path | None,
    current_path: str | Path | None = None,
) -> Path | None:
    matches = list_audio_from_folder(folder)
    if not matches:
        return None

    if current_path:
        current = str(Path(current_path).expanduser())
        filtered = [path for path in matches if str(path) != current]
        if filtered:
            return random.choice(filtered)
    return random.choice(matches)


def list_audio_from_folder(folder: str | Path | None) -> list[Path]:
    if not folder:
        return []

    base = Path(folder).expanduser()
    if not base.exists() or not base.is_dir():
        return []

    return [
        path
        for path in base.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".mp3", ".wav", ".ogg"}
    ]


def normalize_name(name: str) -> str:
    return name.casefold().replace("_", " ").replace("-", " ")
