from pathlib import Path
import time
import unittest
from unittest.mock import ANY, patch

from clap_wake.config import DEFAULT_CONFIG
from clap_wake.service import WakeService
from clap_wake.youtube_cache import YouTubeCacheError


class WakeServiceTests(unittest.TestCase):
    def build_config(self) -> dict:
        config = {
            "version": DEFAULT_CONFIG["version"],
            "workspace_dir": "/tmp",
            "selected_targets": [],
            "microphone": dict(DEFAULT_CONFIG["microphone"]),
            "media": dict(DEFAULT_CONFIG["media"]),
            "realtime": dict(DEFAULT_CONFIG["realtime"]),
            "window_layout": dict(DEFAULT_CONFIG["window_layout"]),
            "browser": dict(DEFAULT_CONFIG["browser"]),
        }
        return config

    @patch("clap_wake.service.find_highway_mp3", return_value=None)
    @patch("clap_wake.service.ensure_youtube_audio_cached", side_effect=YouTubeCacheError("cache unavailable"))
    @patch("clap_wake.service.open_url_foreground")
    def test_fallback_opens_youtube_when_mp3_missing(
        self,
        open_url_mock,
        cache_mock,
        find_mp3_mock,
    ) -> None:
        del find_mp3_mock
        service = WakeService(config=self.build_config(), project_dir=Path("/tmp"))

        service.handle_trigger()

        open_url_mock.assert_called_once()
        cache_mock.assert_called_once()

    @patch("clap_wake.service.ensure_youtube_audio_cached", return_value=Path("/tmp/fallback-cache.mp3"))
    @patch("clap_wake.service.find_highway_mp3", return_value=None)
    def test_auto_downloads_prefers_cached_youtube_fallback_when_local_mp3_missing(
        self,
        find_mp3_mock,
        cache_mock,
    ) -> None:
        del find_mp3_mock
        config = self.build_config()
        config["media"]["mode"] = "auto_downloads"
        config["media"]["selected_folder_path"] = "/tmp/empty"
        config["media"]["youtube_fallback_url"] = "https://youtube.com/watch?v=abc123XYZ_0"
        service = WakeService(config=config, project_dir=Path("/tmp"))

        mp3_path, media_url = service.resolve_media_action()

        self.assertEqual(mp3_path, Path("/tmp/fallback-cache.mp3"))
        self.assertIsNone(media_url)
        cache_mock.assert_called_once_with("https://youtube.com/watch?v=abc123XYZ_0")

    @patch("clap_wake.service.open_url_foreground")
    def test_direct_media_url_opens_url(self, open_url_mock) -> None:
        config = self.build_config()
        config["media"]["mode"] = "url"
        config["media"]["selected_url"] = "https://example.com/soundtrack"
        service = WakeService(config=config, project_dir=Path("/tmp"))

        service.handle_trigger()

        open_url_mock.assert_called_once_with(
            "https://example.com/soundtrack",
            bounds=ANY,
            browser_preferences=ANY,
        )

    def test_youtube_media_url_opens_selected_url_directly(self) -> None:
        config = self.build_config()
        config["media"]["mode"] = "url"
        config["media"]["selected_url"] = "https://youtube.com/watch?v=abc123XYZ_0"
        service = WakeService(config=config, project_dir=Path("/tmp"))

        mp3_path, media_url = service.resolve_media_action()

        self.assertIsNone(mp3_path)
        self.assertEqual(media_url, "https://youtube.com/watch?v=abc123XYZ_0")

    @patch("clap_wake.service.launch_target")
    @patch("clap_wake.service.time.sleep")
    @patch("clap_wake.service.build_triggered_welcome_url", return_value="http://127.0.0.1:8765/welcome/?autostart=1")
    @patch("clap_wake.service.ensure_realtime_server", return_value="http://127.0.0.1:8765/")
    @patch("clap_wake.service.open_url_foreground")
    def test_localhost_target_opens_in_order(
        self,
        open_url_mock,
        ensure_server_mock,
        build_url_mock,
        sleep_mock,
        launch_target_mock,
    ) -> None:
        config = self.build_config()
        config["realtime"]["launch_on_clap"] = True
        config["selected_targets"] = [{"id": "claude_web", "label": "claude.com", "url": "https://claude.com"}]

        service = WakeService(config=config, project_dir=Path("/tmp"))
        service.launch_selected_targets()

        ensure_server_mock.assert_called_once()
        build_url_mock.assert_called_once_with("http://127.0.0.1:8765/")
        open_url_mock.assert_called_once_with(
            "http://127.0.0.1:8765/welcome/?autostart=1",
            bounds=None,
            browser_preferences=ANY,
        )
        sleep_mock.assert_called_once()
        launch_target_mock.assert_called_once()

    @patch("clap_wake.service.run_microphone_loop")
    @patch("clap_wake.service.ensure_realtime_server", return_value="http://127.0.0.1:8765/")
    def test_run_forever_prewarms_realtime_before_microphone_loop(
        self,
        ensure_server_mock,
        run_loop_mock,
    ) -> None:
        config = self.build_config()
        service = WakeService(config=config, project_dir=Path("/tmp"))

        service.run_forever()

        ensure_server_mock.assert_called_once()
        run_loop_mock.assert_called_once()

    @patch("clap_wake.service.run_microphone_loop")
    @patch("clap_wake.service.ensure_youtube_audio_cached")
    def test_run_forever_does_not_prefetch_selected_url_media(
        self,
        cache_mock,
        run_loop_mock,
    ) -> None:
        config = self.build_config()
        config["media"]["mode"] = "url"
        config["media"]["selected_url"] = "https://youtube.com/watch?v=abc123XYZ_0"
        service = WakeService(config=config, project_dir=Path("/tmp"))

        service.run_forever()

        cache_mock.assert_not_called()
        run_loop_mock.assert_called_once()

    @patch("clap_wake.service.run_microphone_loop")
    @patch("clap_wake.service.find_highway_mp3", return_value=None)
    @patch("clap_wake.service.ensure_youtube_audio_cached", return_value=Path("/tmp/fallback-cache.mp3"))
    def test_run_forever_prefetches_youtube_fallback_for_auto_downloads(
        self,
        cache_mock,
        find_mp3_mock,
        run_loop_mock,
    ) -> None:
        del find_mp3_mock
        config = self.build_config()
        config["media"]["mode"] = "auto_downloads"
        config["media"]["selected_folder_path"] = "/tmp/empty"
        config["media"]["youtube_fallback_url"] = "https://youtube.com/watch?v=abc123XYZ_0"
        service = WakeService(config=config, project_dir=Path("/tmp"))

        service.run_forever()

        cache_mock.assert_called_once_with("https://youtube.com/watch?v=abc123XYZ_0")
        run_loop_mock.assert_called_once()
        self.assertEqual(service._cached_fallback_audio_path, Path("/tmp/fallback-cache.mp3"))

    def test_music_volume_is_lower_when_realtime_is_selected(self) -> None:
        config = self.build_config()
        config["media"]["music_volume"] = 0.6
        service = WakeService(config=config, project_dir=Path("/tmp"))
        self.assertEqual(service.music_volume(), 0.6)

        config["realtime"]["launch_on_clap"] = True
        service = WakeService(config=config, project_dir=Path("/tmp"))
        self.assertEqual(service.music_volume(), 0.24)

    @patch("clap_wake.service.stop_realtime_server")
    def test_stop_also_stops_realtime_server(self, stop_realtime_server_mock) -> None:
        service = WakeService(config=self.build_config(), project_dir=Path("/tmp"))

        service.stop()

        stop_realtime_server_mock.assert_called_once()

    def test_request_stop_sets_stop_event(self) -> None:
        service = WakeService(config=self.build_config(), project_dir=Path("/tmp"))

        service.request_stop()

        self.assertTrue(service._stop_event.is_set())

    @patch("clap_wake.service.capture_launchable_targets", return_value=[])
    @patch("clap_wake.service.capture_visible_windows")
    @patch("clap_wake.service.plan_launch_layout")
    def test_save_current_window_layout_persists_slots_from_visible_windows(
        self,
        plan_mock,
        capture_mock,
        capture_targets_mock,
    ) -> None:
        del capture_targets_mock
        config = self.build_config()
        config["realtime"]["launch_on_clap"] = True
        config["selected_targets"] = [{"id": "claude_web", "label": "claude.com", "url": "https://claude.com"}]
        config["media"]["mode"] = "url"
        config["media"]["selected_url"] = "https://youtube.com/watch?v=abc123XYZ_0"
        service = WakeService(config=config, project_dir=Path("/tmp"))

        from clap_wake.window_layout import VisibleWindow, WindowBounds

        plan_mock.return_value = [
            WindowBounds(left=10, top=10, width=400, height=300),
            WindowBounds(left=420, top=10, width=400, height=300),
            WindowBounds(left=830, top=10, width=400, height=300),
        ]
        capture_mock.return_value = [
            VisibleWindow(owner_name="Google Chrome", bounds=WindowBounds(left=15, top=12, width=410, height=310)),
            VisibleWindow(owner_name="Google Chrome", bounds=WindowBounds(left=425, top=14, width=405, height=305)),
            VisibleWindow(owner_name="Google Chrome", bounds=WindowBounds(left=835, top=16, width=402, height=302)),
        ]

        saved_layout = service.save_current_window_layout()

        self.assertEqual(len(saved_layout["saved_slots"]), 3)
        self.assertEqual(service.config["window_layout"], saved_layout)

    @patch("clap_wake.service.capture_launchable_targets")
    @patch("clap_wake.service.capture_visible_windows")
    @patch("clap_wake.service.plan_launch_layout")
    def test_save_current_window_layout_auto_adds_detected_browser_urls(
        self,
        plan_mock,
        capture_mock,
        capture_targets_mock,
    ) -> None:
        config = self.build_config()
        config["media"]["mode"] = "none"
        service = WakeService(config=config, project_dir=Path("/tmp"))

        from clap_wake.session_capture import CapturedWindowTarget
        from clap_wake.window_layout import VisibleWindow, WindowBounds

        plan_mock.return_value = [WindowBounds(left=10, top=10, width=700, height=500)]
        capture_mock.return_value = [
            VisibleWindow(owner_name="Google Chrome", bounds=WindowBounds(left=15, top=12, width=710, height=510))
        ]
        capture_targets_mock.return_value = [
            CapturedWindowTarget(
                target={"id": "custom_url", "label": "Docs", "url": "https://example.com/docs"},
                bounds=WindowBounds(left=15, top=12, width=710, height=510),
            )
        ]

        saved_layout = service.save_current_window_layout()

        self.assertEqual(len(saved_layout["saved_slots"]), 1)
        self.assertEqual(
            service.config["window_layout"]["captured_targets"],
            [{"id": "custom_url", "label": "Docs", "url": "https://example.com/docs"}],
        )

    @patch("clap_wake.service.launch_target")
    def test_custom_targets_go_through_launcher(self, launch_target_mock) -> None:
        config = self.build_config()
        config["selected_targets"] = [
            {"id": "custom_url", "label": "Docs", "url": "https://example.com"},
            {"id": "custom_shell_command", "label": "Shell", "command": "open /Applications"},
        ]

        service = WakeService(config=config, project_dir=Path("/tmp"))
        service.launch_selected_targets()

        self.assertEqual(launch_target_mock.call_count, 2)

    @patch.object(WakeService, "play_media_only")
    @patch.object(WakeService, "launch_selected_targets")
    def test_second_trigger_is_ignored_inside_guard_window(self, launch_selected_targets_mock, play_media_only_mock) -> None:
        service = WakeService(config=self.build_config(), project_dir=Path("/tmp"))
        service._last_action_at = time.monotonic()

        service.handle_trigger()

        launch_selected_targets_mock.assert_not_called()
        play_media_only_mock.assert_not_called()

    def test_toggle_media_pauses_when_currently_playing(self) -> None:
        service = WakeService(config=self.build_config(), project_dir=Path("/tmp"))
        with patch.object(service.player, "state", return_value={"playing": True, "paused": False}):
            with patch.object(service.player, "pause") as pause_mock:
                service.toggle_media()

        pause_mock.assert_called_once()

    def test_toggle_media_resumes_when_paused(self) -> None:
        service = WakeService(config=self.build_config(), project_dir=Path("/tmp"))
        with patch.object(service.player, "state", return_value={"playing": False, "paused": True}):
            with patch.object(service.player, "resume") as resume_mock:
                service.toggle_media()

        resume_mock.assert_called_once()

    @patch("clap_wake.service.list_audio_from_folder", return_value=[Path("/tmp/a.mp3"), Path("/tmp/b.mp3")])
    def test_player_state_includes_can_skip_for_folder_random(self, list_mock) -> None:
        del list_mock
        config = self.build_config()
        config["media"]["mode"] = "folder_random"
        config["media"]["selected_folder_path"] = "/tmp"
        service = WakeService(config=config, project_dir=Path("/tmp"))

        state = service.player_state()

        self.assertTrue(state["can_skip"])

    @patch("clap_wake.service.pick_next_audio_from_folder", return_value=Path("/tmp/b.mp3"))
    def test_next_media_plays_next_track_when_playlist_available(self, next_mock) -> None:
        config = self.build_config()
        config["media"]["mode"] = "folder_random"
        config["media"]["selected_folder_path"] = "/tmp"
        service = WakeService(config=config, project_dir=Path("/tmp"))

        with patch.object(service, "can_skip_media", return_value=True):
            with patch.object(service.player, "state", return_value={"current_path": "/tmp/a.mp3"}):
                with patch.object(service.player, "play") as play_mock:
                    service.next_media()

        next_mock.assert_called_once()
        play_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
