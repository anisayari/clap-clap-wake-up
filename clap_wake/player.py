from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


class Mp3Player:
    def __init__(self) -> None:
        self._lock = Lock()
        self._initialized = False
        self._mixer = None
        self._current_path: str | None = None
        self._paused = False

    def play(self, path: str | Path, volume: float = 0.24) -> None:
        with self._lock:
            mixer = self._ensure_mixer()
            mixer.music.stop()
            mixer.music.load(str(Path(path).expanduser()))
            mixer.music.set_volume(max(0.0, min(1.0, float(volume))))
            mixer.music.play()
            self._current_path = str(Path(path).expanduser())
            self._paused = False

    def pause(self) -> None:
        with self._lock:
            if not self._initialized or self._mixer is None:
                return
            self._mixer.music.pause()
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            if not self._initialized or self._mixer is None:
                return
            self._mixer.music.unpause()
            self._paused = False

    def stop(self) -> None:
        with self._lock:
            if not self._initialized or self._mixer is None:
                return
            self._mixer.music.stop()
            self._paused = False

    def state(self) -> dict[str, object]:
        with self._lock:
            busy = False
            if self._initialized and self._mixer is not None:
                busy = bool(self._mixer.music.get_busy())
            return {
                "loaded": bool(self._current_path),
                "playing": busy and not self._paused,
                "paused": self._paused,
                "current_path": self._current_path,
                "volume": float(self._mixer.music.get_volume()) if self._initialized and self._mixer is not None else 0.0,
            }

    def _ensure_mixer(self):
        if self._initialized and self._mixer is not None:
            return self._mixer

        try:
            import pygame
        except ImportError as exc:
            raise RuntimeError(
                "pygame is required for MP3 playback. Install project dependencies first."
            ) from exc

        pygame.mixer.init()
        self._mixer = pygame.mixer
        self._initialized = True
        return self._mixer
