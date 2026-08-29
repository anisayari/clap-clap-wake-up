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

    def test_stop_returns_zero_when_runtime_is_stopped(self) -> None:
        with patch("clap_wake.cli.request_runtime_stop", return_value=(True, "Stopped.")):
            with patch("builtins.print") as print_mock:
                rc = main(["stop", "--config", str(Path("/tmp/config.json"))])

        self.assertEqual(rc, 0)
        print_mock.assert_called_once_with("Stopped.")

    def test_stop_returns_one_when_no_runtime_is_found(self) -> None:
        with patch("clap_wake.cli.request_runtime_stop", return_value=(False, "No running instance found.")):
            with patch("builtins.print") as print_mock:
                rc = main(["stop", "--config", str(Path("/tmp/config.json"))])

        self.assertEqual(rc, 1)
        print_mock.assert_called_once_with("No running instance found.")

    def test_status_missing_config_prints_adaptive_setup_hint(self) -> None:
        with patch("clap_wake.cli.load_config", side_effect=FileNotFoundError("missing config")):
            with patch("clap_wake.cli.build_module_command", return_value="python -m clap_wake setup"):
                with patch("builtins.print") as print_mock:
                    rc = main(["status", "--config", str(Path("/tmp/config.json"))])

        self.assertEqual(rc, 1)
        self.assertEqual(
            print_mock.call_args_list,
            [
                unittest.mock.call("missing config"),
                unittest.mock.call("Lance `python -m clap_wake setup` pour creer la configuration."),
            ],
        )

    def test_dashboard_prints_runtime_banner_before_starting(self) -> None:
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
            with patch("clap_wake.cli.print_setup_banner") as banner_mock:
                with patch("clap_wake.cli.configure_logging"):
                    with patch("clap_wake.dashboard.run_dashboard", return_value=0):
                        rc = main(["dashboard", "--config", str(Path("/tmp/config.json"))])

        self.assertEqual(rc, 0)
        banner_mock.assert_called_once()

    def test_run_returns_130_when_signal_handler_requests_stop(self) -> None:
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
        handlers: dict[int, object] = {}

        def fake_signal(sig, handler):
            previous = handlers.get(sig)
            handlers[sig] = handler
            return previous

        def fake_run_forever():
            handlers[__import__("signal").SIGINT](__import__("signal").SIGINT, None)

        with patch("clap_wake.cli.load_config", return_value=config):
            with patch("clap_wake.cli.configure_logging"):
                with patch("clap_wake.cli.signal.signal", side_effect=fake_signal):
                    with patch("clap_wake.cli.WakeService") as service_cls:
                        service = service_cls.return_value
                        service.run_forever.side_effect = fake_run_forever

                        rc = main(["run", "--config", str(Path("/tmp/config.json"))])

        self.assertEqual(rc, 130)
        service.request_stop.assert_called_once()
        service.stop.assert_called_once()

    def test_save_layout_persists_captured_layout(self) -> None:
        config = {
            "version": DEFAULT_CONFIG["version"],
            "language": "fr",
            "workspace_dir": "/tmp",
            "selected_targets": [],
            "microphone": dict(DEFAULT_CONFIG["microphone"]),
            "media": dict(DEFAULT_CONFIG["media"]),
            "realtime": dict(DEFAULT_CONFIG["realtime"]),
            "dashboard": dict(DEFAULT_CONFIG["dashboard"]),
            "window_layout": dict(DEFAULT_CONFIG["window_layout"]),
            "browser": dict(DEFAULT_CONFIG["browser"]),
        }
        with patch("clap_wake.cli.load_config", return_value=config):
            with patch("clap_wake.cli.save_config") as save_mock:
                with patch("clap_wake.cli.WakeService") as service_cls:
                    service_cls.return_value.save_current_window_layout.return_value = {
                        "saved_slots": [{"display_index": 0}],
                        "display_count": 1,
                        "saved_at": 1.0,
                    }
                    with patch("builtins.print") as print_mock:
                        rc = main(["save-layout", "--config", str(Path("/tmp/config.json"))])

        self.assertEqual(rc, 0)
        save_mock.assert_called_once()
        print_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
