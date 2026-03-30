from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from clap_wake.runtime_control import (
    clear_runtime_state,
    get_runtime_state_path,
    load_runtime_state,
    register_runtime,
    request_runtime_stop,
)


class RuntimeControlTests(unittest.TestCase):
    def test_register_runtime_round_trip(self) -> None:
        with TemporaryDirectory() as temp_dir:
            app_home = Path(temp_dir)
            with patch("clap_wake.runtime_control.get_app_home", return_value=app_home):
                register_runtime("run", Path("/tmp/config.json"), pid=321)
                state = load_runtime_state()

                self.assertIsNotNone(state)
                self.assertEqual(state["pid"], 321)
                self.assertEqual(state["mode"], "run")
                self.assertEqual(get_runtime_state_path(), app_home / "runtime-state.json")

                clear_runtime_state(expected_pid=321)
                self.assertIsNone(load_runtime_state())

    def test_request_runtime_stop_prefers_dashboard_shutdown(self) -> None:
        state = {
            "pid": 456,
            "mode": "dashboard",
            "dashboard_url": "http://127.0.0.1:8766/",
        }
        with patch("clap_wake.runtime_control.load_runtime_state", return_value=state):
            with patch("clap_wake.runtime_control.is_process_running", return_value=True):
                with patch("clap_wake.runtime_control.request_dashboard_shutdown", return_value=True) as shutdown_mock:
                    with patch("clap_wake.runtime_control.wait_for_process_exit", return_value=True):
                        with patch("clap_wake.runtime_control.clear_runtime_state") as clear_mock:
                            stopped, message = request_runtime_stop()

        self.assertTrue(stopped)
        self.assertEqual(message, "Dashboard stopped.")
        shutdown_mock.assert_called_once_with("http://127.0.0.1:8766/")
        clear_mock.assert_called_once_with(expected_pid=456)

    def test_request_runtime_stop_terminates_non_dashboard_process(self) -> None:
        state = {
            "pid": 789,
            "mode": "run",
            "dashboard_url": None,
        }
        with patch("clap_wake.runtime_control.load_runtime_state", return_value=state):
            with patch("clap_wake.runtime_control.is_process_running", return_value=True):
                with patch("clap_wake.runtime_control.terminate_process", return_value=True) as terminate_mock:
                    with patch("clap_wake.runtime_control.wait_for_process_exit", return_value=True):
                        with patch("clap_wake.runtime_control.clear_runtime_state") as clear_mock:
                            stopped, message = request_runtime_stop()

        self.assertTrue(stopped)
        self.assertEqual(message, "Run stopped.")
        terminate_mock.assert_called_once_with(789)
        clear_mock.assert_called_once_with(expected_pid=789)


if __name__ == "__main__":
    unittest.main()
