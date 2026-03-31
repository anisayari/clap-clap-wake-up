import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from clap_wake.discovery import cli_path_candidates, detect_cli


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
            with patch("clap_wake.discovery.cli_path_candidates", return_value=[]):
                result = detect_cli(["codex"])

        self.assertEqual(result, {"found": False})

    def test_detect_cli_falls_back_to_windows_npm_candidate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            npm_dir = Path(temp_dir) / "Roaming" / "npm"
            npm_dir.mkdir(parents=True)
            executable = npm_dir / "claude.cmd"
            executable.write_text("@echo off\n", encoding="utf-8")

            with patch.dict(os.environ, {"APPDATA": str(Path(temp_dir) / "Roaming")}, clear=False):
                with patch("clap_wake.discovery.shutil.which", return_value=None):
                    result = detect_cli(["claude"], os_name="nt")

        self.assertTrue(result["found"])
        self.assertEqual(Path(result["command"]), executable)
        self.assertEqual(result["command_name"], "claude")

    def test_cli_path_candidates_include_python_scripts_on_windows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            python_scripts = Path(temp_dir) / "Programs" / "Python" / "Python313" / "Scripts"
            python_scripts.mkdir(parents=True)
            with patch.dict(os.environ, {"LOCALAPPDATA": temp_dir}, clear=False):
                candidates = cli_path_candidates(["codex"], os_name="nt")

        self.assertIn(python_scripts / "codex.exe", candidates)


if __name__ == "__main__":
    unittest.main()
