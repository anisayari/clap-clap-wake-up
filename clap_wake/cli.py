from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

from .autostart import install_autostart, uninstall_autostart
from .config import (
    build_clap_config,
    get_config_path,
    get_log_path,
    load_config,
    prompt_setup,
    run_clap_calibration,
    save_config,
)
from .discovery import detect_known_targets
from .launcher import launch_dashboard_terminal
from .service import WakeService


def build_parser(default_config: Path) -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        type=Path,
        default=default_config,
        help="Path to the JSON config file",
    )
    parser = argparse.ArgumentParser(description="Double-clap wake-up daemon", parents=[common])

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("setup", help="Interactive setup", parents=[common])
    subparsers.add_parser("status", help="Print current config", parents=[common])
    subparsers.add_parser("detect-targets", help="Scan local paths/commands for known targets", parents=[common])
    subparsers.add_parser("calibrate", help="Calibrate clap signature", parents=[common])
    subparsers.add_parser("run", help="Run the background listener", parents=[common])
    subparsers.add_parser("dashboard", help="Run the listener with the local dashboard", parents=[common])
    subparsers.add_parser("tray", help="Run the tray app", parents=[common])
    subparsers.add_parser("install-autostart", help="Install login auto-start", parents=[common])
    subparsers.add_parser("uninstall-autostart", help="Remove login auto-start", parents=[common])
    return parser


def configure_logging() -> None:
    log_path = get_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path, default=get_config_path())
    known_args, _ = pre_parser.parse_known_args(argv)

    parser = build_parser(default_config=known_args.config)
    args = parser.parse_args(argv)

    if args.command == "setup":
        path = prompt_setup(config_path=args.config)
        config = load_config(path)
        language = config.get("language", "fr")
        maybe_offer_post_setup_actions(config, path)
        if language == "en":
            print(f"Configuration written to: {path}")
        else:
            print(f"Configuration ecrite dans: {path}")
        return 0

    if args.command == "status":
        try:
            config = load_config(args.config)
        except FileNotFoundError as exc:
            print(str(exc))
            print("Lance `clap-wake setup` pour creer la configuration.")
            return 1
        print_config(config, args.config)
        return 0

    if args.command == "detect-targets":
        print_detected_targets()
        return 0

    if args.command == "calibrate":
        try:
            config = load_config(args.config)
        except FileNotFoundError as exc:
            print(str(exc))
            print("Lance `clap-wake setup` pour creer la configuration.")
            return 1
        run_clap_calibration(config)
        save_config(config, args.config)
        if config.get("language", "fr") == "en":
            print(f"Calibration saved in: {args.config}")
        else:
            print(f"Calibration sauvee dans: {args.config}")
        return 0

    if args.command == "tray":
        from .tray import run_tray

        configure_logging()
        return run_tray(config_path=args.config)

    if args.command == "dashboard":
        from .dashboard import run_dashboard

        configure_logging()
        try:
            load_config(args.config)
        except FileNotFoundError as exc:
            print(str(exc))
            print("Lance `clap-wake setup` pour creer la configuration.")
            return 1
        return run_dashboard(config_path=args.config)

    if args.command == "install-autostart":
        config = load_config(args.config) if args.config.exists() else {"workspace_dir": get_runtime_root()}
        path = install_autostart(
            project_dir=Path(config.get("workspace_dir") or get_runtime_root()),
            config_path=args.config,
        )
        print(f"Autostart installe: {path}")
        return 0

    if args.command == "uninstall-autostart":
        path = uninstall_autostart()
        print(f"Autostart supprime: {path}")
        return 0

    if args.command == "run":
        configure_logging()
        try:
            config = load_config(args.config)
        except FileNotFoundError as exc:
            print(str(exc))
            print("Lance `clap-wake setup` pour creer la configuration.")
            return 1
        workspace_dir = Path(config.get("workspace_dir") or Path.cwd())
        service = WakeService(config=config, project_dir=workspace_dir)
        interrupted = False
        previous_sigint = signal.getsignal(signal.SIGINT)
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def shutdown_handler(signum, frame) -> None:
            del frame
            logging.getLogger("clap_wake").info("Shutdown signal received: %s", signum)
            service.stop()
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, shutdown_handler)
        signal.signal(signal.SIGTERM, shutdown_handler)
        try:
            service.run_forever()
        except KeyboardInterrupt:
            interrupted = True
        finally:
            service.stop()
            signal.signal(signal.SIGINT, previous_sigint)
            signal.signal(signal.SIGTERM, previous_sigterm)

        if interrupted:
            print("Arret demande. Clap Wake Up est stoppe.")
            return 130
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def print_config(config: dict, config_path: Path) -> None:
    language = config.get("language", "fr")
    clap_config = build_clap_config(config["microphone"])
    if language == "en":
        print(f"Config file: {config_path}")
        print(f"Language: {language}")
        print(f"Workspace dir: {config.get('workspace_dir')}")
        print("Targets:")
    else:
        print(f"Fichier config: {config_path}")
        print(f"Langue: {language}")
        print(f"Dossier de travail: {config.get('workspace_dir')}")
        print("Cibles:")
    for target in config.get("selected_targets", []):
        label = target.get("label", target["id"])
        print(f"  - {label}")
    if language == "en":
        print(f"Media mode: {config['media'].get('mode')}")
        print(f"Selected sound: {config['media'].get('selected_sound_path')}")
        print(f"Selected folder: {config['media'].get('selected_folder_path')}")
        print(f"Selected URL: {config['media'].get('selected_url')}")
        print("Microphone:")
    else:
        print(f"Mode media: {config['media'].get('mode')}")
        print(f"Son selectionne: {config['media'].get('selected_sound_path')}")
        print(f"Dossier selectionne: {config['media'].get('selected_folder_path')}")
        print(f"URL selectionnee: {config['media'].get('selected_url')}")
        print("Microphone:")
    for key, value in config["microphone"].items():
        if key == "profile":
            if language == "en":
                print(f"  - profile: {'yes' if value else 'no'}")
            else:
                print(f"  - profile: {'oui' if value else 'non'}")
            continue
        if key == "trigger_cooldown_seconds":
            if language == "en":
                print(f"  - trigger_cooldown_seconds: auto ({clap_config.trigger_cooldown_seconds})")
            else:
                print(f"  - trigger_cooldown_seconds: auto ({clap_config.trigger_cooldown_seconds})")
            continue
        print(f"  - {key}: {value}")
    print("Realtime:")
    for key in ["model", "voice", "port", "assistant_name", "welcome_name"]:
        print(f"  - {key}: {config['realtime'].get(key)}")


def get_runtime_root() -> Path:
    return Path(__file__).resolve().parent.parent


def print_detected_targets() -> None:
    targets = detect_known_targets()
    for target_id, payload in targets.items():
        print(f"{target_id}:")
        if not payload.get("found"):
            print("  - found: no")
            continue
        print("  - found: yes")
        for key, value in payload.items():
            if key == "found":
                continue
            print(f"  - {key}: {value}")


def maybe_offer_post_setup_actions(config: dict, config_path: Path) -> None:
    language = config.get("language", "fr")
    workspace_dir = Path(config.get("workspace_dir") or get_runtime_root())
    launch_prompt = (
        "🚀 Launch now in a new terminal? [Y/n] : "
        if language == "en"
        else "🚀 Lancer maintenant dans un nouveau terminal ? [Y/n] : "
    )
    autostart_prompt = (
        "🖥️  Start automatically at login in a visible terminal? [y/N] : "
        if language == "en"
        else "🖥️  Demarrer automatiquement a l'ouverture de session dans un terminal visible ? [y/N] : "
    )
    yes_no_retry = "Answer with y or n." if language == "en" else "Reponds par y ou n."
    launched_text = (
        "Dashboard launched in a new terminal."
        if language == "en"
        else "Dashboard lance dans un nouveau terminal."
    )
    autostart_text = (
        "Autostart installed."
        if language == "en"
        else "Demarrage automatique installe."
    )

    if ask_yes_no(launch_prompt, default=True, retry_text=yes_no_retry):
        launch_dashboard_terminal(config_path=config_path, cwd=workspace_dir)
        print(launched_text)

    if ask_yes_no(autostart_prompt, default=False, retry_text=yes_no_retry):
        path = install_autostart(project_dir=workspace_dir, config_path=config_path)
        print(f"{autostart_text} {path}")


def ask_yes_no(prompt: str, default: bool, retry_text: str) -> bool:
    default_token = "y" if default else "n"
    while True:
        raw = input(prompt).strip().casefold()
        if not raw:
            return default
        if raw in {"y", "yes", "oui", "o"}:
            return True
        if raw in {"n", "no", "non"}:
            return False
        print(retry_text)
