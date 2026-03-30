from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Event, Lock

from .audio import run_microphone_loop
from .config import YOUTUBE_FALLBACK_URL, build_clap_config
from .launcher import launch_target, open_url_foreground
from .media import find_highway_mp3, list_audio_from_folder, pick_next_audio_from_folder, pick_random_audio_from_folder
from .player import Mp3Player
from .realtime_localhost import ensure_realtime_server, stop_realtime_server
from .window_layout import WindowBounds, plan_launch_layout
from .youtube_cache import YouTubeCacheError, ensure_youtube_audio_cached, is_youtube_url


class WakeService:
    def __init__(self, config: dict, project_dir: Path) -> None:
        self.config = config
        self.project_dir = project_dir
        self.logger = logging.getLogger("clap_wake")
        self.player = Mp3Player()
        self._trigger_lock = Lock()
        self._stop_event = Event()
        self._last_action_at = 0.0
        self._cached_url_audio_path: Path | None = None
        self._cached_fallback_audio_path: Path | None = None

    def run_forever(self) -> None:
        self._stop_event.clear()
        self._prepare_realtime_if_needed()
        self._prepare_media_cache_if_needed()
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
            self.play_media_only(
                mp3_path=mp3_path,
                media_url=media_url,
                bounds=media_bounds,
                resolve_when_empty=False,
            )
        finally:
            self._trigger_lock.release()

    def play_media_only(
        self,
        mp3_path: Path | None = None,
        media_url: str | None = None,
        bounds: WindowBounds | None = None,
        resolve_when_empty: bool = True,
    ) -> None:
        if mp3_path is None and media_url is None and resolve_when_empty:
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

    def toggle_media(self) -> None:
        state = self.player.state()
        if state.get("playing"):
            self.player.pause()
            return
        if state.get("paused"):
            self.player.resume()
            return
        self.play_media_only()

    def next_media(self) -> None:
        media = self.config.get("media", {})
        current_path = self.player.state().get("current_path")

        if self.can_skip_media():
            next_path = pick_next_audio_from_folder(media.get("selected_folder_path"), current_path=current_path)
            if next_path is not None:
                self.logger.info("Playing next track: %s", next_path)
                self.player.play(next_path, volume=self.music_volume())
                return

        self.play_media_only()

    def player_state(self) -> dict[str, object]:
        state = self.player.state()
        state["can_skip"] = self.can_skip_media()
        return state

    def can_skip_media(self) -> bool:
        media = self.config.get("media", {})
        folder = media.get("selected_folder_path")
        if not folder:
            return False
        if media.get("mode") not in {"folder_random", "auto_downloads"}:
            return False
        return len(list_audio_from_folder(folder)) > 1

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

    def _prepare_media_cache_if_needed(self) -> None:
        media = self.config.get("media", {})
        self._cached_fallback_audio_path = None
        if media.get("mode") != "url":
            self._cached_url_audio_path = None
        else:
            selected_url = str(media.get("selected_url") or "").strip()
            if not selected_url or not is_youtube_url(selected_url):
                self._cached_url_audio_path = None
            else:
                self._cached_url_audio_path = self._cache_youtube_audio(selected_url, context="selected media")

        if self._should_prefetch_fallback_audio(media):
            fallback_url = self._fallback_media_url()
            if fallback_url and is_youtube_url(fallback_url):
                self._cached_fallback_audio_path = self._cache_youtube_audio(
                    fallback_url,
                    context="fallback media",
                )

    def _cache_youtube_audio(self, url: str, context: str) -> Path | None:
        try:
            cached_path = ensure_youtube_audio_cached(url)
        except YouTubeCacheError as exc:
            self.logger.warning("Unable to prefetch %s cache: %s", context, exc)
            return None
        self.logger.info("YouTube audio cached for %s at %s", context, cached_path)
        return cached_path

    def _should_prefetch_fallback_audio(self, media: dict) -> bool:
        if media.get("mode") == "none":
            return False
        if self._primary_media_path(media) is not None:
            return False
        if media.get("mode") == "url" and str(media.get("selected_url") or "").strip():
            return False
        return True

    def _primary_media_path(self, media: dict) -> Path | None:
        mode = media.get("mode", "auto_downloads")
        if mode == "single_file":
            selected_sound = media.get("selected_sound_path")
            if selected_sound:
                path = Path(selected_sound).expanduser()
                if path.exists():
                    return path
            return None

        if mode == "folder_random":
            folder = media.get("selected_folder_path")
            return pick_random_audio_from_folder(folder) if folder else None

        if mode == "auto_downloads":
            selected_sound = media.get("selected_sound_path")
            if selected_sound:
                path = Path(selected_sound).expanduser()
                if path.exists():
                    return path
            downloads_dir = media.get("selected_folder_path") or media.get("downloads_dir")
            return find_highway_mp3(downloads_dir)

        return None

    def _fallback_media_url(self) -> str:
        return str(self.config.get("media", {}).get("youtube_fallback_url", YOUTUBE_FALLBACK_URL) or "").strip()

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
                if self._cached_url_audio_path and self._cached_url_audio_path.exists():
                    return self._cached_url_audio_path, None
                if is_youtube_url(selected_url):
                    try:
                        self._cached_url_audio_path = ensure_youtube_audio_cached(selected_url)
                    except YouTubeCacheError as exc:
                        self.logger.warning("Unable to use YouTube audio cache for %s: %s", selected_url, exc)
                    else:
                        return self._cached_url_audio_path, None
                return None, selected_url

        if mode == "none":
            return None, None

        selected_sound = media.get("selected_sound_path")
        if selected_sound:
            path = Path(selected_sound).expanduser()
            if path.exists():
                return path, None

        downloads_dir = media.get("selected_folder_path") or media.get("downloads_dir")
        path = find_highway_mp3(downloads_dir)
        if path is not None:
            return path, None

        fallback_url = self._fallback_media_url()
        if self._cached_fallback_audio_path and self._cached_fallback_audio_path.exists():
            return self._cached_fallback_audio_path, None
        if fallback_url and is_youtube_url(fallback_url):
            cached_fallback = self._cache_youtube_audio(fallback_url, context="fallback media")
            if cached_fallback is not None:
                self._cached_fallback_audio_path = cached_fallback
                return cached_fallback, None
        return None, None
