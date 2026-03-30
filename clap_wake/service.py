from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Event, Lock

from .audio import run_microphone_loop
from .config import YOUTUBE_FALLBACK_URL, build_clap_config
from .launcher import launch_target, open_url_foreground
from .media import find_highway_mp3, pick_random_audio_from_folder
from .player import Mp3Player
from .realtime_localhost import ensure_realtime_server, stop_realtime_server
from .window_layout import WindowBounds, plan_launch_layout


class WakeService:
    def __init__(self, config: dict, project_dir: Path) -> None:
        self.config = config
        self.project_dir = project_dir
        self.logger = logging.getLogger("clap_wake")
        self.player = Mp3Player()
        self._trigger_lock = Lock()
        self._stop_event = Event()
        self._last_action_at = 0.0

    def run_forever(self) -> None:
        self._stop_event.clear()
        self._prepare_realtime_if_needed()
        clap_config = build_clap_config(self.config["microphone"])
        self.logger.info("Listening for double claps...")
        run_microphone_loop(clap_config, self.handle_trigger, stop_event=self._stop_event)

    def stop(self) -> None:
        self._stop_event.set()
        self.player.stop()
        stop_realtime_server()

    def handle_trigger(self) -> None:
        now = time.monotonic()
        if now - self._last_action_at < self.action_guard_seconds():
            self.logger.info("Double clap ignored during action guard window.")
            return

        if not self._trigger_lock.acquire(blocking=False):
            return

        try:
            self._last_action_at = now
            self.logger.info("Double clap detected. Launching targets.")
            mp3_path, media_url = self.resolve_media_action()
            target_count = len(self.config["selected_targets"])
            total_windows = target_count + (1 if media_url else 0)
            layout = plan_launch_layout(total_windows)
            target_bounds = layout[:target_count]
            media_bounds = layout[target_count] if media_url and len(layout) > target_count else None

            self.launch_selected_targets(target_bounds=target_bounds)
            self.play_media_only(mp3_path=mp3_path, media_url=media_url, bounds=media_bounds)
        finally:
            self._trigger_lock.release()

    def play_media_only(
        self,
        mp3_path: Path | None = None,
        media_url: str | None = None,
        bounds: WindowBounds | None = None,
    ) -> None:
        if mp3_path is None and media_url is None:
            mp3_path, media_url = self.resolve_media_action()

        if mp3_path:
            self.logger.info("Playing MP3: %s", mp3_path)
            self.player.play(mp3_path, volume=self.music_volume())
            return

        if media_url:
            self.logger.info("Opening media URL: %s", media_url)
            open_url_foreground(media_url, bounds=bounds)
            return

        if self.config.get("media", {}).get("mode") == "none":
            self.logger.info("Media mode is none. Nothing to play.")
            return

        fallback_url = self.config["media"].get("youtube_fallback_url", YOUTUBE_FALLBACK_URL)
        self.logger.info("MP3 not found. Opening fallback URL: %s", fallback_url)
        open_url_foreground(fallback_url, bounds=bounds)

    def pause_media(self) -> None:
        self.player.pause()

    def resume_media(self) -> None:
        self.player.resume()

    def stop_media(self) -> None:
        self.player.stop()

    def player_state(self) -> dict[str, object]:
        return self.player.state()

    def launch_selected_targets(self, target_bounds: list[WindowBounds] | None = None) -> None:
        realtime_index = next(
            (index for index, target in enumerate(self.config["selected_targets"]) if target["id"] == "welcome_localhost"),
            None,
        )
        if realtime_index is not None:
            realtime_url = ensure_realtime_server(self.config)
            realtime_bounds = target_bounds[realtime_index] if target_bounds and realtime_index < len(target_bounds) else None
            open_url_foreground(realtime_url, bounds=realtime_bounds)

        for index, target in enumerate(self.config["selected_targets"]):
            if target["id"] == "welcome_localhost":
                continue
            bounds_index = index
            bounds = target_bounds[bounds_index] if target_bounds and bounds_index < len(target_bounds) else None
            launch_target(target, cwd=self.project_dir, bounds=bounds)

    def has_realtime_target(self) -> bool:
        return any(target["id"] == "welcome_localhost" for target in self.config["selected_targets"])

    def _prepare_realtime_if_needed(self) -> None:
        if not self.has_realtime_target():
            return
        try:
            url = ensure_realtime_server(self.config)
        except Exception:
            self.logger.exception("Unable to prewarm Realtime localhost")
            return
        self.logger.info("Realtime localhost prewarmed on %s", url)

    def music_volume(self) -> float:
        media = self.config.get("media", {})
        default = 0.24
        volume = float(media.get("music_volume", default))
        if self.has_realtime_target():
            volume = min(volume, 0.24)
        return max(0.0, min(1.0, volume))

    def action_guard_seconds(self) -> float:
        guard = 3.0
        media = self.config.get("media", {})
        if self.has_realtime_target():
            guard = max(guard, 7.0)
        if media.get("mode") in {"single_file", "folder_random", "url", "auto_downloads"}:
            guard = max(guard, 6.0)
        return guard

    def resolve_media_action(self):
        media = self.config.get("media", {})
        mode = media.get("mode", "auto_downloads")

        if mode == "single_file":
            selected_sound = media.get("selected_sound_path")
            if selected_sound:
                path = Path(selected_sound).expanduser()
                if path.exists():
                    return path, None

        if mode == "folder_random":
            folder = media.get("selected_folder_path")
            if folder:
                path = pick_random_audio_from_folder(folder)
                if path:
                    return path, None

        if mode == "url":
            selected_url = media.get("selected_url")
            if selected_url:
                return None, selected_url

        if mode == "none":
            return None, None

        selected_sound = media.get("selected_sound_path")
        if selected_sound:
            path = Path(selected_sound).expanduser()
            if path.exists():
                return path, None

        downloads_dir = media.get("selected_folder_path") or media.get("downloads_dir")
        return find_highway_mp3(downloads_dir), None
