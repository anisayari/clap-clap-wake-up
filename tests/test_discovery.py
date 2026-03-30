import unittest
from unittest.mock import patch

from clap_wake.discovery import detect_cli


class DiscoveryTests(unittest.TestCase):
    def test_detect_cli_uses_first_match(self) -> None:
        with patch("clap_wake.discovery.shutil.which") as which_mock:
            which_mock.side_effect = [None, "/usr/local/bin/claude"]

            result = detect_cli(["claude-code", "claude"])

        self.assertTrue(result["found"])
        self.assertEqual(result["command"], "/usr/local/bin/claude")
        self.assertEqual(result["command_name"], "claude")

    def test_detect_cli_reports_missing(self) -> None:
        with patch("clap_wake.discovery.shutil.which", return_value=None):
            result = detect_cli(["codex"])

        self.assertEqual(result, {"found": False})


if __name__ == "__main__":
    unittest.main()
