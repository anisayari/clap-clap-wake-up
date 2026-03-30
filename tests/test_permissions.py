import unittest
from unittest.mock import patch

from clap_wake.permissions import (
    get_required_permission_keys,
    probe_accessibility_permission,
    settings_command_for,
)


class PermissionTests(unittest.TestCase):
    def test_required_permissions_include_accessibility_on_macos_with_targets(self) -> None:
        keys = get_required_permission_keys("darwin", [{"id": "codex_desktop"}])
        self.assertEqual(keys, ["microphone", "accessibility"])

    def test_required_permissions_only_include_microphone_without_targets(self) -> None:
        keys = get_required_permission_keys("linux", [])
        self.assertEqual(keys, ["microphone"])

    def test_settings_command_for_macos_microphone(self) -> None:
        self.assertEqual(
            settings_command_for("microphone", platform="darwin"),
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"],
        )

    @patch("clap_wake.permissions.subprocess.run")
    def test_probe_accessibility_permission_accepts_true(self, run_mock) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "true\n"
        run_mock.return_value.stderr = ""

        result = probe_accessibility_permission(platform="darwin")

        self.assertTrue(result.granted)
        self.assertEqual(result.label, "Accessibility")

    @patch("clap_wake.permissions.subprocess.run")
    def test_probe_accessibility_permission_returns_blocked_on_false(self, run_mock) -> None:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "false\n"
        run_mock.return_value.stderr = ""

        result = probe_accessibility_permission(platform="darwin")

        self.assertFalse(result.granted)
        self.assertTrue(result.can_open_settings)


if __name__ == "__main__":
    unittest.main()
