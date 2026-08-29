from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Event, Lock

from .audio import run_microphone_loop
from .config import YOUTUBE_FALLBACK_URL, build_clap_config
from .launcher import detect_default_macos_browser_preferences, launch_target, open_url_foreground
from .media import find_highway_mp3, list_audio_from_folder, pick_next_audio_from_folder, pick_random_audio_from_folder
from .player import Mp3Player
from .realtime_localhost import build_triggered_welcome_url, ensure_realtime_server, stop_realtime_server
from .session_capture import capture_launchable_targets
from .window_layout import (
    WindowBounds,
    build_saved_layout,
    capture_visible_windows,
    match_windows_to_expected_slots,
    plan_launch_layout,
    select_launch_layout,
)
from .youtube_cache import YouTubeCacheError, ensure_youtube_audio_cached, is_youtube_url


class WakeService:
    def __init__(self, config: dict, project_dir: Path, localhost_welcome_url: str | None = None) -> None:
        self.config = config
        self.project_dir = project_dir
        self.localhost_welcome_url = localhost_welcome_url
        self.logger = logging.getLogger("clap_wake")
        self.player = Mp3Player()
        self._trigger_lock = Lock()
        self._stop_event = Event()
        self._last_action_at = 0.0
        self._cached_fallback_audio_path: Path | None = None

    def run_forever(self) -> None:
        self._stop_event.clear()
        self._prepare_realtime_if_needed()
        self._prepare_media_cache_if_needed()
        clap_config = build_clap_config(self.config["microphone"])
        self.logger.info("Listening for double claps...")
        run_microphone_loop(clap_config, self.handle_trigger, stop_event=self._stop_event)

    def request_stop(self) -> None:
        self._stop_event.set()

    def stop(self) -> None:
        self.request_stop()
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
            plan_items = self.build_window_plan(media_url=media_url, resolve_media=False)
            total_windows = len(plan_items)
            layout = select_launch_layout(total_windows, saved_layout=self.config.get("window_layout"))
            target_count = len(self.effective_selected_targets())
            realtime_window_count = 1 if self.should_launch_realtime_on_clap() else 0
            realtime_bounds = layout[0] if realtime_window_count else None
            target_bounds_start = realtime_window_count
            target_bounds_end = target_bounds_start + target_count
            target_bounds = layout[target_bounds_start:target_bounds_end]
            media_bounds = layout[target_bounds_end] if media_url and len(layout) > target_bounds_end else None

            self.launch_selected_targets(target_bounds=target_bounds, realtime_bounds=realtime_bounds)
            self.play_media_only(
                mp3_path=mp3_path,
                media_url=media_url,
                bounds=media_bounds,
                resolve_when_empty=False,
            )
        finally:
            self._trigger_lock.release()

    def build_window_plan(self, media_url: str | None = None, resolve_media: bool = True) -> list[dict[str, object]]:
        if media_url is None and resolve_media:
            _, media_url = self.resolve_media_action()

        browser_hints = self.browser_owner_hints()
        plan: list[dict[str, object]] = []
        if self.should_launch_realtime_on_clap():
            plan.append({"id": "realtime", "owner_hints": browser_hints})

        for target in self.effective_selected_targets():
            plan.append(
                {
                    "id": target.get("id", "target"),
                    "owner_hints": self.owner_hints_for_target(target, browser_hints),
                }
            )

        if media_url:
            plan.append({"id": "media_url", "owner_hints": browser_hints})
        return plan

    def save_current_window_layout(self) -> dict[str, object]:
        windows = capture_visible_windows()
        auto_targets = self.capture_visible_launch_targets(windows)
        self.config.setdefault("window_layout", {})["captured_targets"] = auto_targets

        plan_items = self.build_window_plan()
        count = len(plan_items)
        if count <= 0:
            raise RuntimeError("No windowed targets are configured for the clap launch sequence.")

        if len(windows) < count:
            raise RuntimeError(
                f"Only {len(windows)} visible windows found, but {count} are required to capture the layout."
            )

        displays = None
        expected_slots = plan_launch_layout(count)
        owner_hints = [list(item.get("owner_hints") or []) for item in plan_items]
        matched_slots = match_windows_to_expected_slots(windows, expected_slots, owner_hints=owner_hints)
        saved_layout = build_saved_layout(matched_slots, displays=displays)
        saved_layout["captured_targets"] = auto_targets
        self.config["window_layout"] = saved_layout
        return saved_layout

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
            open_url_foreground(media_url, bounds=bounds, browser_preferences=self.browser_preferences())
            return

        if self.config.get("media", {}).get("mode") == "none":
            self.logger.info("Media mode is none. Nothing to play.")
            return

        fallback_url = self.config["media"].get("youtube_fallback_url", YOUTUBE_FALLBACK_URL)
        self.logger.info("MP3 not found. Opening fallback URL: %s", fallback_url)
        open_url_foreground(fallback_url, bounds=bounds, browser_preferences=self.browser_preferences())

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

    def launch_selected_targets(
        self,
        target_bounds: list[WindowBounds] | None = None,
        realtime_bounds: WindowBounds | None = None,
    ) -> None:
        targets = self.effective_selected_targets()
        target_count = len(targets)
        realtime_window_count = 1 if self.should_launch_realtime_on_clap() else 0
        if self.should_launch_realtime_on_clap():
            base_realtime_url = self.localhost_welcome_url or ensure_realtime_server(self.config)
            realtime_url = build_triggered_welcome_url(base_realtime_url)
            self.logger.info("Opening Realtime welcome first: %s", realtime_url)
            open_url_foreground(
                realtime_url,
                bounds=realtime_bounds,
                browser_preferences=self.browser_preferences(),
            )
            time.sleep(self.realtime_head_start_seconds())
        for index, target in enumerate(targets):
            bounds = target_bounds[index] if target_bounds and index < len(target_bounds) else None
            launch_target(target, cwd=self.project_dir, bounds=bounds)

    def effective_selected_targets(self) -> list[dict]:
        targets = list(self.config.get("selected_targets", []))
        targets.extend(self.config.get("window_layout", {}).get("captured_targets", []))
        return targets

    def capture_visible_launch_targets(self, windows) -> list[dict]:
        captured = capture_launchable_targets(
            browser_preferences=self.browser_preferences(),
            visible_windows=windows,
        )
        signatures = {
            self.target_signature(target)
            for target in self.config.get("selected_targets", [])
            if self.target_signature(target) is not None
        }
        media_url = str(self.config.get("media", {}).get("selected_url") or "").strip()
        if media_url:
            signatures.add(("url", media_url))

        result: list[dict] = []
        for item in captured:
            signature = self.target_signature(item.target)
            if signature is None or signature in signatures:
                continue
            signatures.add(signature)
            result.append(item.target)
        return result

    def target_signature(self, target: dict) -> tuple[str, str] | None:
        target_id = str(target.get("id") or "")
        if target_id in {"claude_web", "chatgpt_web", "custom_url"}:
            url = str(target.get("url") or "").strip()
            return ("url", url) if url else None
        if target_id == "custom_path":
            path = str(target.get("path") or "").strip()
            return ("path", path) if path else None
        if target_id in {"codex_cli", "claude_code", "custom_terminal_command", "custom_shell_command"}:
            command = str(target.get("command") or "").strip()
            return ("command", command) if command else None
        if target_id == "codex_desktop":
            app_path = str(target.get("app_path") or "").strip()
            if app_path:
                return ("app_path", app_path)
            custom_command = str(target.get("custom_command") or "").strip()
            return ("command", custom_command) if custom_command else ("builtin", target_id)
        return ("builtin", target_id) if target_id else None

    def browser_preferences(self) -> dict[str, str | None] | None:
        if self.config.get("browser", {}).get("app_name"):
            return self.config.get("browser")
        if self.config.get("browser", {}).get("profile_directory"):
            return self.config.get("browser")
        if self.config.get("browser", {}).get("profile_name"):
            return self.config.get("browser")
        return detect_default_macos_browser_preferences() if hasattr(detect_default_macos_browser_preferences, "__call__") else None

    def browser_owner_hints(self) -> list[str]:
        prefs = self.browser_preferences() or {}
        app_name = str(prefs.get("app_name") or "").strip()
        return [app_name] if app_name else []

    def owner_hints_for_target(self, target: dict, browser_hints: list[str]) -> list[str]:
        target_id = str(target.get("id") or "")
        if target_id in {"claude_web", "chatgpt_web", "custom_url"}:
            return browser_hints
        if target_id in {"codex_cli", "claude_code", "custom_terminal_command"}:
            return ["Terminal"]
        if target_id == "codex_desktop":
            app_path = str(target.get("app_path") or "").strip()
            if app_path.endswith(".app"):
                return [Path(app_path).stem]
            return ["Codex"]
        if target_id == "custom_path":
            path = str(target.get("path") or "").strip()
            if path.endswith(".app"):
                return [Path(path).stem]
            return ["Finder"]
        return []

    def should_launch_realtime_on_clap(self) -> bool:
        realtime = self.config.get("realtime", {})
        if "launch_on_clap" in realtime:
            return bool(realtime.get("launch_on_clap"))
        return any(target["id"] == "welcome_localhost" for target in self.config["selected_targets"])

    def _prepare_realtime_if_needed(self) -> None:
        if self.localhost_welcome_url:
            self.logger.info("Realtime localhost available on %s", self.localhost_welcome_url)
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
        if self.should_launch_realtime_on_clap():
            volume = min(volume, 0.24)
        return max(0.0, min(1.0, volume))

    def action_guard_seconds(self) -> float:
        guard = 3.0
        media = self.config.get("media", {})
        if self.should_launch_realtime_on_clap():
            guard = max(guard, 7.0)
        if media.get("mode") in {"single_file", "folder_random", "url", "auto_downloads"}:
            guard = max(guard, 6.0)
        return guard

    def realtime_head_start_seconds(self) -> float:
        if not self.should_launch_realtime_on_clap():
            return 0.0
        return 0.45

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
