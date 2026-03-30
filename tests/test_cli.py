from pathlib import Path
import unittest
from unittest.mock import patch

from clap_wake.cli import main
from clap_wake.config import DEFAULT_CONFIG


class CliTests(unittest.TestCase):
    def test_run_returns_130_on_keyboard_interrupt(self) -> None:
        config = {
            "version": DEFAULT_CONFIG["version"],
            "language": "fr",
            "workspace_dir": "/tmp",
            "selected_targets": [],
            "microphone": dict(DEFAULT_CONFIG["microphone"]),
            "media": dict(DEFAULT_CONFIG["media"]),
            "realtime": dict(DEFAULT_CONFIG["realtime"]),
            "dashboard": dict(DEFAULT_CONFIG["dashboard"]),
        }
        with patch("clap_wake.cli.load_config", return_value=config):
            with patch("clap_wake.cli.configure_logging"):
                with patch("clap_wake.cli.WakeService") as service_cls:
                    service = service_cls.return_value
                    service.run_forever.side_effect = KeyboardInterrupt

                    rc = main(["run", "--config", str(Path("/tmp/config.json"))])

        self.assertEqual(rc, 130)
        self.assertEqual(service.stop.call_count, 1)


if __name__ == "__main__":
    unittest.main()
