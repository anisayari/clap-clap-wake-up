import unittest
from unittest.mock import patch

from clap_wake.session_capture import (
    CapturedWindowTarget,
    capture_launchable_targets,
    is_captureworthy_url,
)
from clap_wake.window_layout import VisibleWindow, WindowBounds


class SessionCaptureTests(unittest.TestCase):
    def test_is_captureworthy_url_rejects_localhost(self) -> None:
        self.assertFalse(is_captureworthy_url("http://127.0.0.1:8766/welcome/"))
        self.assertFalse(is_captureworthy_url("https://localhost/foo"))
        self.assertTrue(is_captureworthy_url("https://chatgpt.com"))

    @patch("clap_wake.session_capture.sys.platform", "darwin")
    @patch("clap_wake.session_capture.list_browser_windows_with_urls_macos")
    def test_capture_launchable_targets_matches_browser_windows_by_title(self, list_mock) -> None:
        list_mock.return_value = [
            {"title": "ChatGPT - Projects", "url": "https://chatgpt.com/c/example"},
            {"title": "YouTube", "url": "https://www.youtube.com/watch?v=abc"},
        ]
        windows = [
            VisibleWindow(
                owner_name="Google Chrome",
                title="YouTube",
                bounds=WindowBounds(left=900, top=50, width=800, height=600),
            ),
            VisibleWindow(
                owner_name="Google Chrome",
                title="ChatGPT - Projects",
                bounds=WindowBounds(left=20, top=50, width=800, height=600),
            ),
        ]

        targets = capture_launchable_targets(
            browser_preferences={"app_name": "Google Chrome"},
            visible_windows=windows,
        )

        self.assertEqual(
            targets,
            [
                CapturedWindowTarget(
                    target={
                        "id": "custom_url",
                        "label": "ChatGPT - Projects",
                        "url": "https://chatgpt.com/c/example",
                    },
                    bounds=WindowBounds(left=20, top=50, width=800, height=600),
                ),
                CapturedWindowTarget(
                    target={
                        "id": "custom_url",
                        "label": "YouTube",
                        "url": "https://www.youtube.com/watch?v=abc",
                    },
                    bounds=WindowBounds(left=900, top=50, width=800, height=600),
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
