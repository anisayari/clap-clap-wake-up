import unittest
from unittest.mock import patch

from clap_wake.invocation import build_module_command, detect_python_launcher


class InvocationTests(unittest.TestCase):
    def test_detect_python_launcher_prefers_python_on_windows(self) -> None:
        with patch("clap_wake.invocation.shutil.which", side_effect=lambda name: "python.exe" if name == "python" else None):
            self.assertEqual(detect_python_launcher(platform="nt"), ["python"])

    def test_detect_python_launcher_falls_back_to_py_on_windows(self) -> None:
        with patch("clap_wake.invocation.shutil.which", side_effect=lambda name: "py.exe" if name == "py" else None):
            self.assertEqual(detect_python_launcher(platform="nt"), ["py", "-3"])

    def test_detect_python_launcher_uses_sys_executable_when_nothing_is_found(self) -> None:
        with patch("clap_wake.invocation.shutil.which", return_value=None):
            with patch("clap_wake.invocation.sys.executable", "C:\\Python313\\python.exe"):
                self.assertEqual(detect_python_launcher(platform="nt"), ["C:\\Python313\\python.exe"])

    def test_build_module_command_uses_detected_launcher(self) -> None:
        with patch("clap_wake.invocation.shutil.which", side_effect=lambda name: "python.exe" if name == "python" else None):
            self.assertEqual(build_module_command("setup", platform="nt"), "python -m clap_wake setup")


if __name__ == "__main__":
    unittest.main()
