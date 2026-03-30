from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from .config import get_app_home, load_config, run_clap_calibration, save_config
from .launcher import launch_terminal_command, open_directory_background, open_file_background
from .service import WakeService
from .sound_library import get_media_library_dir


class TrayApplication:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.logger = logging.getLogger("clap_wake.tray")
        self.icon = None
        self.listener_thread: threading.Thread | None = None
        self.service: WakeService | None = None
        self.status_text = "Initialisation"
        self._shutdown = False

    def run(self) -> int:
        pystray = import_pystray()
        self.icon = pystray.Icon("clap-wake-up", create_tray_image(), "Clap Wake Up", self._build_menu(pystray))
        self._load_and_start()
        self.icon.run()
        return 0

    def _build_menu(self, pystray_module):
        Menu = pystray_module.Menu
        MenuItem = pystray_module.MenuItem
        return Menu(
            MenuItem(lambda _item: f"Etat: {self.status_text}", None, enabled=False),
            MenuItem("Declencher maintenant", self._trigger_now),
            MenuItem("Calibrer le clap", self._calibrate_clap),
            MenuItem("Recharger la config", self._reload_config),
            MenuItem("Lancer le setup", self._launch_setup),
            MenuItem("Ouvrir le dossier config", self._open_app_home),
            MenuItem("Ouvrir la bibliotheque audio", self._open_audio_library),
            MenuItem("Ouvrir le fichier config", self._open_config),
            MenuItem("Ouvrir le log", self._open_log),
            MenuItem("Quitter", self._quit),
        )

    def _load_and_start(self) -> None:
        try:
            config = load_config(self.config_path)
        except FileNotFoundError:
            self.status_text = "Config absente"
            self.logger.warning("Config file not found: %s", self.config_path)
            self._notify("Clap Wake Up", "Config absente. Lance le setup depuis le menu.")
            return
        except Exception as exc:
            self.status_text = f"Erreur config: {exc}"
            self.logger.exception("Unable to load config")
            self._notify("Clap Wake Up", f"Erreur de configuration: {exc}")
            return

        self._stop_listener()
        workspace_dir = Path(config.get("workspace_dir") or Path.cwd())
        self.service = WakeService(config=config, project_dir=workspace_dir)
        self.listener_thread = threading.Thread(target=self._run_listener, daemon=True)
        self.listener_thread.start()
        self.status_text = "En ecoute"
        self._refresh_menu()

    def _run_listener(self) -> None:
        try:
            assert self.service is not None
            self.service.run_forever()
        except Exception as exc:
            self.status_text = f"Erreur audio: {exc}"
            self.logger.exception("Listener failed")
            self._notify("Clap Wake Up", f"Erreur audio: {exc}")
            self._refresh_menu()

    def _stop_listener(self) -> None:
        if self.service is not None:
            self.service.stop()
        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=1.5)
        self.listener_thread = None
        self.service = None

    def _trigger_now(self, icon, item) -> None:
        del icon, item
        if self.service is None:
            self._notify("Clap Wake Up", "Config absente ou listener indisponible.")
            return
        threading.Thread(target=self.service.handle_trigger, daemon=True).start()

    def _reload_config(self, icon, item) -> None:
        del icon, item
        self.status_text = "Rechargement..."
        self._refresh_menu()
        self._load_and_start()

    def _calibrate_clap(self, icon, item) -> None:
        del icon, item
        threading.Thread(target=self._calibrate_clap_worker, daemon=True).start()

    def _calibrate_clap_worker(self) -> None:
        try:
            config = load_config(self.config_path)
            self._stop_listener()
            self.status_text = "Calibration clap..."
            self._refresh_menu()
            self._notify("Clap Wake Up", "Calibration: fais 4 doubles claquements.")
            run_clap_calibration(config)
            save_config(config, self.config_path)
            self._notify("Clap Wake Up", "Calibration du clap terminee.")
            self._load_and_start()
        except Exception as exc:
            self.logger.exception("Clap calibration failed")
            self.status_text = f"Erreur calibration: {exc}"
            self._notify("Clap Wake Up", f"Calibration impossible: {exc}")
            self._refresh_menu()

    def _launch_setup(self, icon, item) -> None:
        del icon, item
        launch_setup_terminal(self.config_path)

    def _open_app_home(self, icon, item) -> None:
        del icon, item
        app_home = get_app_home()
        app_home.mkdir(parents=True, exist_ok=True)
        open_directory_background(app_home)

    def _open_config(self, icon, item) -> None:
        del icon, item
        app_home = get_app_home()
        app_home.mkdir(parents=True, exist_ok=True)
        if self.config_path.exists():
            open_file_background(self.config_path)
        else:
            open_directory_background(app_home)

    def _open_audio_library(self, icon, item) -> None:
        del icon, item
        library_dir = get_media_library_dir()
        library_dir.mkdir(parents=True, exist_ok=True)
        open_directory_background(library_dir)

    def _open_log(self, icon, item) -> None:
        del icon, item
        from .config import get_log_path

        log_path = get_log_path()
        if log_path.exists():
            open_file_background(log_path)
        else:
            open_directory_background(log_path.parent)

    def _quit(self, icon, item) -> None:
        del item
        self._shutdown = True
        self.status_text = "Arret..."
        self._stop_listener()
        icon.stop()

    def _notify(self, title: str, message: str) -> None:
        if self.icon is None:
            return
        try:
            self.icon.notify(message, title=title)
        except Exception:
            self.logger.debug("Tray notification unavailable", exc_info=True)

    def _refresh_menu(self) -> None:
        if self.icon is not None:
            self.icon.update_menu()


def run_tray(config_path: Path) -> int:
    app = TrayApplication(config_path=config_path)
    return app.run()


def create_tray_image():
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (17, 24, 39, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 56, 56), radius=14, fill=(17, 24, 39, 255))
    draw.ellipse((18, 14, 30, 26), fill=(248, 250, 252, 255))
    draw.ellipse((34, 14, 46, 26), fill=(248, 250, 252, 255))
    draw.rectangle((28, 28, 36, 46), fill=(248, 250, 252, 255))
    return image


def import_pystray():
    try:
        import pystray
    except ImportError as exc:
        raise RuntimeError(
            "pystray is required for tray mode. Reinstall with the project dependencies."
        ) from exc
    return pystray


def launch_setup_terminal(config_path: Path) -> None:
    if getattr(sys, "frozen", False):
        command = f'"{sys.executable}" setup --config "{config_path}"'
    else:
        command = f'"{sys.executable}" -m clap_wake setup --config "{config_path}"'
    launch_terminal_command(command, cwd=Path.cwd())
