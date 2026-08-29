from pathlib import Path
import unittest
from unittest.mock import patch

from clap_wake.launcher import (
    detect_default_macos_browser_preferences,
    detect_windows_browser,
    detect_macos_browser_profiles,
    get_default_macos_browser_app_name,
    open_url_foreground,
    open_url_new_window_macos,
)
from clap_wake.window_layout import WindowBounds


class LauncherTests(unittest.TestCase):
    @patch("clap_wake.launcher.subprocess.Popen")
    @patch("clap_wake.launcher.detect_default_macos_browser_preferences", return_value={"app_name": "Google Chrome", "profile_directory": "Profile 3", "profile_name": "Primary"})
    @patch("clap_wake.launcher.macos_app_exists", side_effect=lambda app: app == "Google Chrome")
    @patch("clap_wake.launcher.get_macos_browser_executable", return_value=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    def test_open_url_new_window_macos_prefers_chrome_like_browser(self, executable_mock, app_exists_mock, default_browser_mock, popen_mock) -> None:
        del executable_mock
        del default_browser_mock
        del app_exists_mock
        result = open_url_new_window_macos("https://chatgpt.com")

        self.assertTrue(result)
        popen_mock.assert_called_once_with(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "--profile-directory=Profile 3",
                "--new-window",
                "https://chatgpt.com",
            ]
        )

    def test_get_default_macos_browser_app_name_maps_chrome_bundle_id(self) -> None:
        import plistlib

        fake_plist = {
            "LSHandlers": [
                {"LSHandlerURLScheme": "http", "LSHandlerRoleAll": "com.google.chrome"},
            ]
        }
        with patch("clap_wake.launcher.Path.exists", return_value=True):
            with patch("clap_wake.launcher.Path.read_bytes", return_value=plistlib.dumps(fake_plist)):
                self.assertEqual(get_default_macos_browser_app_name(), "Google Chrome")

    def test_detect_macos_browser_profiles_prefers_last_used_profile(self) -> None:
        fake_state = {
            "profile": {
                "last_used": "Profile 3",
                "info_cache": {
                    "Profile 2": {"name": "Other", "user_name": "other@example.com"},
                    "Profile 3": {"name": "Primary", "user_name": "primary@example.com"},
                },
            }
        }
        with patch("clap_wake.launcher.get_macos_browser_local_state_path", return_value=Path("/tmp/Local State")):
            with patch("clap_wake.launcher.Path.exists", return_value=True):
                with patch("clap_wake.launcher.Path.read_text", return_value=__import__("json").dumps(fake_state)):
                    profiles = detect_macos_browser_profiles("Google Chrome")

        self.assertEqual(profiles[0]["directory"], "Profile 3")
        self.assertEqual(profiles[0]["name"], "Primary")

    @patch("clap_wake.launcher.detect_macos_browser_profiles", return_value=[{"directory": "Profile 3", "name": "Primary", "email": "primary@example.com"}])
    @patch("clap_wake.launcher.get_default_macos_browser_app_name", return_value="Google Chrome")
    def test_detect_default_macos_browser_preferences_uses_last_used_profile(self, browser_mock, profiles_mock) -> None:
        del browser_mock, profiles_mock
        prefs = detect_default_macos_browser_preferences()

        self.assertEqual(prefs["app_name"], "Google Chrome")
        self.assertEqual(prefs["profile_directory"], "Profile 3")
        self.assertEqual(prefs["profile_name"], "Primary")

    @patch("clap_wake.launcher.subprocess.Popen")
    @patch("clap_wake.launcher.macos_app_exists", side_effect=lambda app: app == "Safari")
    def test_open_url_new_window_macos_falls_back_to_safari_script(self, app_exists_mock, popen_mock) -> None:
        del app_exists_mock
        result = open_url_new_window_macos("https://claude.com")

        self.assertTrue(result)
        command = popen_mock.call_args.args[0]
        self.assertEqual(command[0], "osascript")
        self.assertIn("Safari", command[2])

    @patch("clap_wake.launcher.place_foreground_window")
    @patch("clap_wake.launcher.open_url_new_window_macos", return_value=True)
    def test_open_url_foreground_uses_separate_window_on_macos(self, open_new_window_mock, place_mock) -> None:
        bounds = WindowBounds(left=0, top=0, width=800, height=600)
        with patch("clap_wake.launcher.sys.platform", "darwin"):
            open_url_foreground("https://youtube.com/watch?v=l482T0yNkeo", bounds=bounds)

        open_new_window_mock.assert_called_once_with(
            "https://youtube.com/watch?v=l482T0yNkeo",
            browser_preferences=None,
        )
        place_mock.assert_called_once_with(bounds)

    @patch("clap_wake.launcher.which", side_effect=lambda command: {"chrome": "/tmp/chrome"}.get(command))
    def test_detect_windows_browser_prefers_path_browser(self, which_mock) -> None:
        del which_mock
        browser = detect_windows_browser()

        self.assertEqual(browser, ("/tmp/chrome", ["--new-window"]))

    @patch("clap_wake.launcher.which", return_value=None)
    def test_detect_windows_browser_falls_back_to_file_candidates(self, which_mock) -> None:
        del which_mock
        candidate = Path("/Program Files/Google/Chrome/Application/chrome.exe")

        def fake_exists(path_self):
            return path_self == candidate

        with patch("clap_wake.launcher.Path.exists", fake_exists):
            with patch.dict("clap_wake.launcher.os.environ", {"ProgramFiles": "/Program Files"}, clear=False):
                browser = detect_windows_browser()

        self.assertEqual(browser, (str(candidate), ["--new-window"]))


if __name__ == "__main__":
    unittest.main()
