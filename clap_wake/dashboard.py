from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG, load_config, merge_dict, migrate_config, save_config
from .env_utils import load_env_value, save_env_value
from .launcher import open_url_foreground
from .runtime_control import clear_runtime_state, register_runtime
from .service import WakeService

LOGGER = logging.getLogger("clap_wake.dashboard")


class DashboardRuntime:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
        self.service: WakeService | None = None
        self.listener_thread: threading.Thread | None = None
        self.httpd: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._shutdown = threading.Event()
        self._status = "Starting"
        self.port = 0
        self.url = ""

    def start(self) -> str:
        self.restart_listener()
        self._start_server()
        return self.url

    def wait(self) -> None:
        while not self._shutdown.is_set():
            time.sleep(0.2)

    def shutdown(self) -> None:
        self._shutdown.set()
        self.stop_listener()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=1.5)
        self.server_thread = None
        self.httpd = None
        self._status = "Stopped"

    def restart_listener(self) -> None:
        with self._lock:
            self.config = load_config(self.config_path)
        self.stop_listener()
        with self._lock:
            workspace_dir = Path(self.config.get("workspace_dir") or Path.cwd())
            self.service = WakeService(config=self.config, project_dir=workspace_dir)
            self.listener_thread = threading.Thread(target=self._run_listener, daemon=True)
            self.listener_thread.start()
            self._status = "Listening"

    def start_listener(self) -> None:
        if self.listener_thread and self.listener_thread.is_alive():
            return
        self.restart_listener()

    def stop_listener(self) -> None:
        service = self.service
        if service is not None:
            service.stop()
        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=1.5)
        self.listener_thread = None
        self.service = None
        if not self._shutdown.is_set():
            self._status = "Stopped"

    def trigger_now(self) -> None:
        if self.service is None:
            raise RuntimeError("Listener not running.")
        threading.Thread(target=self.service.handle_trigger, daemon=True).start()

    def play_media(self) -> None:
        if self.service is None:
            raise RuntimeError("Listener not running.")
        threading.Thread(target=self.service.play_media_only, daemon=True).start()

    def pause_music(self) -> None:
        if self.service is None:
            return
        self.service.pause_media()

    def toggle_music(self) -> None:
        if self.service is None:
            return
        self.service.toggle_media()

    def next_music(self) -> None:
        if self.service is None:
            return
        self.service.next_media()

    def resume_music(self) -> None:
        if self.service is None:
            return
        self.service.resume_media()

    def stop_music(self) -> None:
        if self.service is None:
            return
        self.service.stop_media()

    def save_dashboard_config(self, payload: dict[str, Any]) -> None:
        config_payload = payload.get("config")
        if not isinstance(config_payload, dict):
            raise ValueError("Invalid config payload.")

        config = deepcopy(DEFAULT_CONFIG)
        merge_dict(config, config_payload)
        migrate_config(config)
        config["realtime"]["api_key"] = None

        openai_key = str(payload.get("openai_key") or "").strip()
        workspace_dir = Path(config.get("workspace_dir") or Path.cwd())
        env_path = workspace_dir / ".env"
        if openai_key:
            save_env_value(env_path, "OPENAI_API_KEY", openai_key)

        save_config(config, self.config_path)
        with self._lock:
            self.config = config
        self.restart_listener()

    def state(self) -> dict[str, Any]:
        with self._lock:
            config = deepcopy(self.config)
        config["realtime"]["api_key"] = None
        workspace_dir = Path(config.get("workspace_dir") or Path.cwd())
        env_path = workspace_dir / ".env"
        player_state = self.service.player_state() if self.service is not None else {
            "loaded": False,
            "playing": False,
            "paused": False,
            "current_path": None,
            "can_skip": False,
        }
        return {
            "status": self._status,
            "listener_running": bool(self.listener_thread and self.listener_thread.is_alive()),
            "dashboard_url": self.url,
            "config_path": str(self.config_path),
            "openai_key_present": bool(load_env_value(env_path, "OPENAI_API_KEY")),
            "config": config,
            "player": player_state,
        }

    def request_shutdown(self) -> None:
        threading.Thread(target=self.shutdown, daemon=True).start()

    def _run_listener(self) -> None:
        try:
            assert self.service is not None
            self.service.run_forever()
        except Exception as exc:
            LOGGER.exception("Dashboard listener failed")
            self._status = f"Audio error: {exc}"
        else:
            if not self._shutdown.is_set():
                self._status = "Stopped"

    def _start_server(self) -> None:
        preferred_port = int(self.config.get("dashboard", {}).get("port", 8766))
        self.port = preferred_port if is_port_free(preferred_port) else find_free_port(preferred_port + 1)
        self.url = f"http://127.0.0.1:{self.port}/"
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), self._make_handler())
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()
        self._status = "Listening"

    def _make_handler(self):
        runtime = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path in {"/", "/index.html"}:
                    self._send_html(build_dashboard_html())
                    return
                if self.path == "/settings":
                    self._send_html(build_dashboard_settings_html())
                    return
                if self.path == "/styles.css":
                    self._send_css(build_dashboard_css())
                    return
                if self.path == "/app.js":
                    self._send_js(build_dashboard_js())
                    return
                if self.path == "/settings.js":
                    self._send_js(build_dashboard_settings_js())
                    return
                if self.path == "/state":
                    self._send_json(runtime.state())
                    return
                if self.path == "/health":
                    self._send_json({"ok": True})
                    return
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

            def do_POST(self) -> None:
                try:
                    if self.path == "/trigger":
                        runtime.trigger_now()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/player/play":
                        runtime.play_media()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/player/toggle":
                        runtime.toggle_music()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/player/next":
                        runtime.next_music()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/player/pause":
                        runtime.pause_music()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/player/resume":
                        runtime.resume_music()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/player/stop":
                        runtime.stop_music()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/listener/restart":
                        runtime.restart_listener()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/listener/start":
                        runtime.start_listener()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/listener/stop":
                        runtime.stop_listener()
                        self._send_json({"ok": True})
                        return
                    if self.path == "/config":
                        payload = self._read_json()
                        runtime.save_dashboard_config(payload)
                        self._send_json({"ok": True})
                        return
                    if self.path == "/shutdown":
                        self._send_json({"ok": True})
                        runtime.request_shutdown()
                        return
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

            def log_message(self, format: str, *args) -> None:
                LOGGER.debug("dashboard %s - %s", self.address_string(), format % args)

            def _read_json(self) -> dict[str, Any]:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(content_length) if content_length else b"{}"
                return json.loads(raw.decode("utf-8"))

            def _send_html(self, body: str) -> None:
                self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8")

            def _send_css(self, body: str) -> None:
                self._send_bytes(body.encode("utf-8"), "text/css; charset=utf-8")

            def _send_js(self, body: str) -> None:
                self._send_bytes(body.encode("utf-8"), "application/javascript; charset=utf-8")

            def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
                self._send_bytes(
                    json.dumps(payload).encode("utf-8"),
                    "application/json; charset=utf-8",
                    status=status,
                )

            def _send_bytes(self, payload: bytes, content_type: str, status: int = HTTPStatus.OK) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler


def run_dashboard(config_path: Path, open_browser: bool = True) -> int:
    runtime = DashboardRuntime(config_path=config_path)
    url = runtime.start()
    register_runtime("dashboard", config_path=config_path, pid=os.getpid(), dashboard_url=url)
    LOGGER.info("Dashboard started on %s", url)
    if open_browser:
        open_url_foreground(url)
    try:
        runtime.wait()
    except KeyboardInterrupt:
        runtime.shutdown()
    finally:
        clear_runtime_state(expected_pid=os.getpid())
    return 0


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def find_free_port(start_port: int) -> int:
    for port in range(start_port, start_port + 200):
        if is_port_free(port):
            return port
    raise RuntimeError("No free port found for dashboard.")


def build_dashboard_html() -> str:
    return """<!DOCTYPE html>
<html class="dark" lang="en">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>JARVIS_OS: CORE_INTERFACE</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&amp;family=Inter:wght@300;400;600&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<script id="tailwind-config">
      tailwind.config = {
        darkMode: "class",
        theme: {
          extend: {
            colors: {
              "secondary-fixed": "#ffc4b6",
              "outline": "#72757d",
              "surface-container": "#151a21",
              "inverse-surface": "#f8f9ff",
              "surface-dim": "#0a0e14",
              "on-tertiary": "#2b6500",
              "on-secondary-fixed-variant": "#9b2200",
              "on-primary": "#005762",
              "tertiary-dim": "#6ded00",
              "on-surface-variant": "#a8abb3",
              "secondary-fixed-dim": "#ffb09d",
              "error": "#ff716c",
              "inverse-primary": "#006976",
              "secondary-container": "#b42800",
              "surface-bright": "#262c36",
              "primary": "#81ecff",
              "tertiary-fixed-dim": "#6ded00",
              "surface-container-low": "#0f141a",
              "secondary": "#ff7350",
              "surface-container-high": "#1b2028",
              "primary-fixed": "#00e3fd",
              "inverse-on-surface": "#51555d",
              "primary-fixed-dim": "#00d4ec",
              "on-background": "#f1f3fc",
              "on-secondary": "#440900",
              "outline-variant": "#44484f",
              "on-secondary-fixed": "#681300",
              "on-tertiary-fixed": "#1c4700",
              "on-primary-container": "#004d57",
              "tertiary": "#c2ff99",
              "on-secondary-container": "#fff6f4",
              "primary-dim": "#00d4ec",
              "on-primary-fixed-variant": "#005762",
              "on-surface": "#f1f3fc",
              "tertiary-container": "#75fd00",
              "error-container": "#9f0519",
              "on-error": "#490006",
              "tertiary-fixed": "#75fd00",
              "primary-container": "#00e3fd",
              "on-tertiary-fixed-variant": "#2b6600",
              "on-error-container": "#ffa8a3",
              "surface-container-lowest": "#000000",
              "surface-tint": "#81ecff",
              "on-primary-fixed": "#003840",
              "on-tertiary-container": "#265c00",
              "surface-variant": "#20262f",
              "surface": "#0a0e14",
              "error-dim": "#d7383b",
              "surface-container-highest": "#20262f",
              "background": "#0a0e14",
              "secondary-dim": "#dc3300"
            },
            fontSize: {
              "2xs": ["0.625rem", { lineHeight: "1rem" }],
            },
            fontFamily: {
              "headline": ["Space Grotesk"],
              "body": ["Inter"],
              "label": ["Space Grotesk"]
            },
            borderRadius: {"DEFAULT": "0px", "lg": "0px", "xl": "0px", "full": "9999px"},
          },
        },
      }
    </script>
<style>
        .clip-path-chamfer {
            clip-path: polygon(10% 0, 90% 0, 100% 15%, 100% 85%, 90% 100%, 10% 100%, 0 85%, 0 15%);
        }
        .clip-path-chamfer-lg {
            clip-path: polygon(0 0, 97% 0, 100% 3%, 100% 100%, 3% 100%, 0 97%);
        }
        .grid-bg {
            background-image:
                linear-gradient(to right, rgba(129, 236, 255, 0.05) 1px, transparent 1px),
                linear-gradient(to bottom, rgba(129, 236, 255, 0.05) 1px, transparent 1px);
            background-size: 40px 40px;
        }
        .scanline {
            background: linear-gradient(to bottom, transparent 50%, rgba(129, 236, 255, 0.02) 50%);
            background-size: 100% 4px;
        }
        @keyframes pulse-ring {
            0% { transform: scale(0.8); opacity: 0.5; }
            100% { transform: scale(1.5); opacity: 0; }
        }
        .animate-pulse-ring {
            animation: pulse-ring 3s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }
        .radial-vignette {
            background: radial-gradient(ellipse at center, rgba(129,236,255,0.05) 0%, transparent 70%);
        }
        .radial-glow {
            background: radial-gradient(circle, rgba(129,236,255,0.20) 0%, transparent 70%);
        }
        .hud-input {
            background: rgba(15, 20, 26, 0.75);
            border: none;
            border-bottom: 1px solid rgba(68,72,79,0.30);
            color: #f1f3fc;
            font-family: Inter, sans-serif;
        }
        .hud-input:focus {
            outline: none;
            border-bottom-color: rgba(129,236,255,0.7);
            box-shadow: 0 2px 12px rgba(129,236,255,0.12);
        }
        .hud-scroll::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        .hud-scroll::-webkit-scrollbar-thumb {
            background: rgba(129,236,255,0.18);
        }
        .hud-panel {
            background:
                linear-gradient(180deg, rgba(129,236,255,0.10), rgba(129,236,255,0.00) 24%),
                rgba(15, 20, 26, 0.72);
            backdrop-filter: blur(18px);
            border: 1px solid rgba(68,72,79,0.22);
        }
        .hud-panel-soft {
            background:
                linear-gradient(180deg, rgba(129,236,255,0.06), rgba(129,236,255,0.00) 22%),
                rgba(15, 20, 26, 0.58);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(68,72,79,0.18);
        }
        .hud-chip {
            background: rgba(129,236,255,0.08);
            border: 1px solid rgba(68,72,79,0.20);
        }
        .hud-action {
            background: linear-gradient(180deg, rgba(129,236,255,0.16), rgba(129,236,255,0.04));
            border: 1px solid rgba(68,72,79,0.24);
        }
    </style>
</head>
<body class="bg-background text-on-background font-body selection:bg-primary/30 overflow-hidden min-h-screen">
<div class="fixed inset-0 grid-bg pointer-events-none"></div>
<div class="fixed inset-0 scanline pointer-events-none"></div>
<div class="fixed inset-0 radial-vignette pointer-events-none"></div>

<header class="fixed top-0 w-full z-50 h-14 px-5 bg-[#0a0e14]/82 backdrop-blur-xl shadow-[0_0_18px_rgba(129,236,255,0.12)]">
<div class="h-full flex items-center justify-between gap-4">
<div class="flex items-center gap-4 min-w-0">
<div class="w-8 h-8 flex items-center justify-center border border-primary/20 bg-primary/5">
<span class="material-symbols-outlined text-primary text-lg">graphic_eq</span>
</div>
<div class="min-w-0">
<div class="flex items-center gap-3 flex-wrap">
<span class="text-cyan-400 font-bold tracking-widest font-headline text-sm">CLAP WAKE UP</span>
<span class="text-2xs text-primary/60 tracking-widest font-label">DOUBLE_CLAP_CONTROL_PANEL</span>
</div>
<p class="text-xs text-on-surface-variant truncate">Audio-triggered launcher, local media player, and realtime welcome assistant.</p>
</div>
</div>
<div class="flex items-center gap-3 text-primary">
<div class="hidden xl:flex items-center gap-2">
<div class="hud-chip px-2 py-1 flex items-center gap-2 text-2xs font-headline tracking-widest max-w-[18rem]">
<span class="material-symbols-outlined text-base">music_note</span>
<span id="headerPlayerState" class="truncate">PLAYER_IDLE</span>
</div>
<button id="headerToggleButton" class="material-symbols-outlined text-xl hover:text-cyan-300 transition-colors" title="Play or pause media">play_circle</button>
<button id="headerNextButton" class="material-symbols-outlined text-xl hover:text-cyan-300 transition-colors hidden" title="Next track">skip_next</button>
</div>
<div class="hud-chip px-2 py-1 flex items-center gap-2 text-2xs font-headline tracking-widest">
<span class="w-2 h-2 rounded-full bg-primary shadow-[0_0_8px_rgba(129,236,255,0.7)]"></span>
<span id="headerStatus">LISTENER_BOOTING</span>
</div>
<button id="reloadButton" class="material-symbols-outlined text-xl hover:text-cyan-300 transition-colors" title="Open config page">settings</button>
<button id="triggerButton" class="material-symbols-outlined text-xl hover:text-cyan-300 transition-colors" title="Replay trigger">play_circle</button>
</div>
</div>
</header>

<aside class="hidden md:flex md:flex-col fixed left-0 top-14 h-[calc(100vh-3.5rem)] z-40 bg-[#0a0e14]/40 backdrop-blur-md w-20 px-2 py-4">
<div class="flex flex-col items-center gap-2 mb-4">
<div class="w-9 h-9 flex items-center justify-center border border-primary/20 bg-primary/5">
<span class="material-symbols-outlined text-primary" style="font-variation-settings: 'FILL' 1;">neurology</span>
</div>
<span class="text-cyan-500 font-black text-2xs font-headline tracking-widest text-center">WAKE_CORE</span>
</div>
<div class="flex flex-col w-full gap-2">
<button id="startListenerButton" class="w-full flex flex-col items-center py-3 text-cyan-400 bg-cyan-500/20 border-l border-cyan-400/60 transition-transform">
<span class="material-symbols-outlined mb-1">rocket_launch</span>
<span class="font-headline uppercase text-2xs tracking-widest">LISTEN</span>
</button>
<button id="restartListenerButton" class="w-full flex flex-col items-center py-3 text-cyan-900/60 hover:bg-cyan-500/5 hover:text-cyan-200 transition-all">
<span class="material-symbols-outlined mb-1">ads_click</span>
<span class="font-headline uppercase text-2xs tracking-widest">RELOAD</span>
</button>
<button id="playButton" class="w-full flex flex-col items-center py-3 text-cyan-900/60 hover:bg-cyan-500/5 hover:text-cyan-200 transition-all">
<span class="material-symbols-outlined mb-1">sensors</span>
<span class="font-headline uppercase text-2xs tracking-widest">PLAY</span>
</button>
<button id="saveButton" class="w-full flex flex-col items-center py-3 text-cyan-900/60 hover:bg-cyan-500/5 hover:text-cyan-200 transition-all">
<span class="material-symbols-outlined mb-1">memory</span>
<span class="font-headline uppercase text-2xs tracking-widest">CONFIG</span>
</button>
<button id="stopListenerButton" class="w-full flex flex-col items-center py-3 text-cyan-900/60 hover:bg-cyan-500/5 hover:text-cyan-200 transition-all">
<span class="material-symbols-outlined mb-1">explore</span>
<span class="font-headline uppercase text-2xs tracking-widest">STOP</span>
</button>
</div>
<button id="killButton" class="mt-auto flex flex-col items-center text-error hover:text-error-dim transition-colors group">
<span class="material-symbols-outlined mb-1">cancel</span>
<span class="font-headline uppercase text-2xs tracking-widest">KILL</span>
</button>
</aside>

<main class="md:ml-20 mt-14 min-h-[calc(100vh-3.5rem)] p-4 md:p-6">
<div class="max-w-[1600px] mx-auto flex flex-col gap-4">
<section class="grid grid-cols-1 xl:grid-cols-[1.15fr_0.85fr] gap-4 items-stretch">
<div class="hud-panel clip-path-chamfer-lg p-4 relative overflow-hidden min-h-[18rem]">
<div class="absolute inset-0 pointer-events-none opacity-50">
<div class="absolute -top-12 left-10 w-48 h-48 rounded-full bg-primary/10 blur-3xl"></div>
<div class="absolute bottom-10 right-8 w-56 h-56 rounded-full bg-secondary/8 blur-3xl"></div>
</div>
<div class="relative h-full flex flex-col justify-between gap-4">
<div class="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
<div class="max-w-lg">
<p class="text-xs font-label tracking-widest text-primary/70 mb-1.5">WAKE_ENGINE / LIVE OVERVIEW</p>
<h1 class="text-2xl md:text-3xl font-headline font-semibold tracking-wide text-on-background">Double clap, then launch everything.</h1>
<p class="mt-2 text-sm text-on-surface-variant leading-relaxed">Compact control panel for the microphone listener, clap profile, selected targets, local media, and the OpenAI realtime wake flow.</p>
<div class="mt-3 flex flex-wrap gap-2">
<div class="hud-chip px-2 py-1">
<span class="text-2xs font-label tracking-widest text-primary/60">ASSISTANT</span>
<p id="assistantNameDisplay" class="text-sm font-headline tracking-widest text-primary mt-1">JARVIS</p>
</div>
<div class="hud-chip px-2 py-1">
<span class="text-2xs font-label tracking-widest text-primary/60">LISTENER</span>
<p id="statusPill" class="text-sm font-headline tracking-widest text-on-background mt-1">LISTENER_BOOTING</p>
</div>
<div class="hud-chip px-2 py-1">
<span class="text-2xs font-label tracking-widest text-primary/60">PLAYER</span>
<p id="playerPill" class="text-sm font-headline tracking-widest text-on-background mt-1">PLAYER_IDLE</p>
</div>
</div>
</div>
<div class="relative mx-auto xl:mx-0 flex items-center justify-center min-w-[14rem] min-h-[14rem]">
<div class="absolute w-60 h-60 border border-primary/12 rounded-full animate-pulse-ring"></div>
<div class="absolute w-48 h-48 border border-primary/18 rounded-full animate-pulse-ring" style="animation-delay: 0.8s"></div>
<div class="absolute w-[16rem] h-[16rem] border-t-2 border-b-2 border-primary/25 rounded-full animate-[spin_12s_linear_infinite]"></div>
<div class="absolute w-[13rem] h-[13rem] border-l border-r border-secondary/20 rounded-full animate-[spin_18s_linear_infinite_reverse]"></div>
<div class="w-40 h-40 rounded-full border border-primary/40 bg-gradient-to-br from-primary/28 via-surface-container to-surface-container-lowest backdrop-blur-xl shadow-[0_0_70px_rgba(129,236,255,0.18)] flex flex-col items-center justify-center relative overflow-hidden">
<div class="absolute inset-0 radial-glow animate-pulse"></div>
<span class="material-symbols-outlined text-5xl text-primary drop-shadow-[0_0_12px_rgba(129,236,255,0.7)]" style="font-variation-settings: 'FILL' 1;">neurology</span>
<p class="mt-2 text-xs font-label tracking-widest text-primary/70">REALTIME_CORE</p>
<div class="mt-2 flex gap-1">
<div class="w-1 h-4 bg-primary animate-[bounce_1s_infinite]"></div>
<div class="w-1 h-6 bg-primary animate-[bounce_1.1s_infinite]"></div>
<div class="w-1 h-3 bg-primary animate-[bounce_0.85s_infinite]"></div>
<div class="w-1 h-5 bg-primary animate-[bounce_1.2s_infinite]"></div>
<div class="w-1 h-4 bg-primary animate-[bounce_0.95s_infinite]"></div>
</div>
</div>
</div>
</div>
<div class="grid grid-cols-1 gap-3">
<button id="engageButton" class="hud-action clip-path-chamfer-lg px-3 py-2.5 text-left hover:bg-primary/10 transition-colors">
<p class="text-2xs font-label tracking-widest text-primary/60">DOUBLE_CLAP_TEST</p>
<p class="mt-2 text-sm font-headline tracking-widest text-on-background">Replay full trigger</p>
<p class="mt-1 text-xs text-on-surface-variant">Relaunch targets and replay media now.</p>
</button>
</div>
</div>
</div>

<div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
<div class="hud-panel-soft clip-path-chamfer-lg p-4">
<div class="flex items-start justify-between gap-4">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">MICROPHONE</p>
<h2 class="mt-1.5 text-base font-headline tracking-wide text-on-background">Listener status</h2>
<p id="listenerCardValue" class="mt-2 text-sm text-on-surface-variant">Listener standing by</p>
</div>
<span class="material-symbols-outlined text-primary/70 text-2xl">mic</span>
</div>
</div>
<div class="hud-panel-soft clip-path-chamfer-lg p-4">
<div class="flex items-start justify-between gap-4">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">OPENAI REALTIME</p>
<h2 class="mt-1.5 text-base font-headline tracking-wide text-on-background">Assistant link</h2>
<p id="realtimeCardValue" class="mt-2 text-sm text-on-surface-variant">Realtime link idle</p>
</div>
<span class="material-symbols-outlined text-primary/70 text-2xl">hub</span>
</div>
</div>
<div class="hud-panel-soft clip-path-chamfer-lg p-4">
<div class="flex items-start justify-between gap-4">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">MEDIA ROUTER</p>
<h2 class="mt-1.5 text-base font-headline tracking-wide text-on-background">Current playback</h2>
<p id="mediaCardValue" class="mt-2 text-sm text-on-surface-variant">Media bus calm</p>
</div>
<span class="material-symbols-outlined text-primary/70 text-2xl">music_note</span>
</div>
</div>
<div class="hud-panel-soft clip-path-chamfer-lg p-4">
<div class="flex items-start justify-between gap-4">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">LAST EVENT</p>
<h2 class="mt-1.5 text-base font-headline tracking-wide text-on-background">System message</h2>
<p id="message" class="mt-2 text-sm text-on-surface-variant">No pending system event.</p>
</div>
<span class="material-symbols-outlined text-error/70 text-2xl">notification_important</span>
</div>
</div>
</section>

<section class="grid grid-cols-1 lg:grid-cols-[1.15fr_0.78fr_0.78fr] gap-3">
<div class="hud-panel clip-path-chamfer-lg p-4">
<div class="flex items-center justify-between gap-4 mb-3">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">OPEN_TARGETS</p>
<h2 class="mt-1.5 text-base font-headline tracking-wide text-on-background">Launch plan</h2>
</div>
<div class="hud-chip px-2 py-1">
<span class="text-2xs font-label tracking-widest text-primary/60">COUNT</span>
<p id="targetsCountValue" class="text-sm font-headline text-primary mt-1">0</p>
</div>
</div>
<div id="targetsList" class="space-y-3"></div>
</div>

<div class="hud-panel clip-path-chamfer-lg p-4">
<div class="flex items-center justify-between gap-4 mb-3">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">DOUBLE_CLAP_PROFILE</p>
<h2 class="mt-1.5 text-base font-headline tracking-wide text-on-background">Calibration</h2>
</div>
<span class="material-symbols-outlined text-primary/70 text-2xl">graphic_eq</span>
</div>
<div class="space-y-3">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">PAIR_COUNT</p>
<p id="pairCountValue" class="mt-1 text-base font-headline tracking-wider text-on-background">0</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">AVG_GAP</p>
<p id="clapGapValue" class="mt-1 text-base font-headline tracking-wider text-on-background">0.00s</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">AVG_SCORE</p>
<p id="clapScoreValue" class="mt-1 text-base font-headline tracking-wider text-on-background">0.00</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">MATCH_TOLERANCE</p>
<p id="clapToleranceValue" class="mt-1 text-base font-headline tracking-wider text-on-background">0.00</p>
</div>
</div>
</div>

<div class="hud-panel clip-path-chamfer-lg p-4">
<div class="flex items-center justify-between gap-4 mb-3">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">MEDIA_AND_AI</p>
<h2 class="mt-1.5 text-base font-headline tracking-wide text-on-background">Runtime sources</h2>
</div>
<span class="material-symbols-outlined text-primary/70 text-2xl">tune</span>
</div>
<div class="space-y-3">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">MEDIA_MODE</p>
<p id="mediaModeValue" class="mt-1 text-sm font-headline tracking-wider text-on-background">single_file</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">MEDIA_SOURCE</p>
<p id="mediaSourceValue" class="mt-1 text-sm text-on-surface-variant break-all">unknown</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">VOICE</p>
<p id="voiceValue" class="mt-1 text-sm font-headline tracking-wider text-on-background">marin</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">LANGUAGE</p>
<p id="languageValue" class="mt-1 text-sm font-headline tracking-wider text-on-background">fr</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">WORKSPACE</p>
<p id="workspaceValue" class="mt-1 text-sm text-on-surface-variant break-all">unknown</p>
</div>
</div>
</div>
</section>

<section class="grid grid-cols-1 gap-3">
<div class="hud-panel clip-path-chamfer-lg p-4">
<div class="flex items-center justify-between gap-4 mb-3">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">SYSTEM_PATHS</p>
<h2 class="mt-1.5 text-base font-headline tracking-wide text-on-background">Runtime metadata</h2>
</div>
<div class="flex flex-col items-end gap-3">
<span id="satelliteStatus" class="text-2xs font-headline tracking-widest text-primary">ENCRYPTED_99%</span>
<div class="flex items-end gap-1">
<div id="signalBar1" class="w-1 bg-primary h-2"></div>
<div id="signalBar2" class="w-1 bg-primary h-4"></div>
<div id="signalBar3" class="w-1 bg-primary h-6"></div>
<div id="signalBar4" class="w-1 bg-primary h-3"></div>
<div id="signalBar5" class="w-1 bg-surface-variant h-5"></div>
</div>
</div>
</div>
<div class="space-y-3">
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">LISTENER_META</p>
<p id="listenerMeta" class="mt-1 text-sm font-headline tracking-wider text-on-background">LISTENER: ONLINE</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">CONFIG_PATH</p>
<p id="configPathMeta" class="mt-1 text-sm text-on-surface-variant break-all">CONFIG: /UNKNOWN</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">DASHBOARD_URL</p>
<p id="dashboardUrlMeta" class="mt-1 text-sm text-on-surface-variant break-all">URL: LOCALHOST</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">OPENAI_LINK</p>
<div id="keyMeta" class="mt-1 text-sm font-headline text-primary/70 flex items-center gap-2">
<span class="w-2 h-2 rounded-full bg-tertiary shadow-[0_0_5px_#c2ff99]"></span>
<span>OPENAI_LINK: UNKNOWN</span>
</div>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/60">KEY_STATUS</p>
<p id="openaiHint" class="mt-1 text-sm text-on-surface-variant leading-relaxed">KEY_STATUS: UNKNOWN</p>
</div>
</div>
</div>
</section>
</div>

</main>
<script src="/app.js" type="module"></script>
</body></html>
"""


def build_dashboard_css() -> str:
    return ""


def build_dashboard_settings_html() -> str:
    return """<!DOCTYPE html>
<html class="dark" lang="en">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>CLAP WAKE UP: CONFIG</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&amp;family=Inter:wght@300;400;600&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<script id="tailwind-config">
tailwind.config = {
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        primary: "#81ecff",
        "primary-dim": "#00d4ec",
        secondary: "#ff7350",
        tertiary: "#c2ff99",
        background: "#0a0e14",
        surface: "#0a0e14",
        "surface-bright": "#262c36",
        "surface-container": "#151a21",
        "surface-container-low": "#0f141a",
        "surface-container-high": "#1b2028",
        "surface-variant": "#20262f",
        "on-background": "#f1f3fc",
        "on-surface": "#f1f3fc",
        "on-surface-variant": "#a8abb3",
        "on-primary": "#005762",
        "on-secondary": "#440900",
        "outline-variant": "#44484f",
        error: "#ff716c",
        "error-dim": "#d7383b"
      },
      fontSize: {
        "2xs": ["0.625rem", { lineHeight: "1rem" }]
      },
      fontFamily: {
        headline: ["Space Grotesk"],
        body: ["Inter"],
        label: ["Space Grotesk"]
      },
      borderRadius: {"DEFAULT": "0px", "lg": "0px", "xl": "0px", "full": "9999px"}
    }
  }
}
</script>
<style>
.grid-bg {
  background-image:
    linear-gradient(to right, rgba(129, 236, 255, 0.05) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(129, 236, 255, 0.05) 1px, transparent 1px);
  background-size: 40px 40px;
}
.scanline {
  background: linear-gradient(to bottom, transparent 50%, rgba(129, 236, 255, 0.02) 50%);
  background-size: 100% 4px;
}
.hud-panel {
  background:
    linear-gradient(180deg, rgba(129,236,255,0.10), rgba(129,236,255,0.00) 24%),
    rgba(15, 20, 26, 0.72);
  backdrop-filter: blur(18px);
  border: 1px solid rgba(68,72,79,0.22);
}
.hud-input {
  background: rgba(15, 20, 26, 0.75);
  border: none;
  border-bottom: 1px solid rgba(68,72,79,0.30);
  color: #f1f3fc;
}
.hud-input:focus {
  outline: none;
  border-bottom-color: rgba(129,236,255,0.7);
  box-shadow: 0 2px 12px rgba(129,236,255,0.12);
}
.clip-path-chamfer-lg {
  clip-path: polygon(0 0, 97% 0, 100% 3%, 100% 100%, 3% 100%, 0 97%);
}
.hud-scroll::-webkit-scrollbar {
  width: 8px;
}
.hud-scroll::-webkit-scrollbar-thumb {
  background: rgba(129,236,255,0.18);
}
</style>
</head>
<body class="bg-background text-on-background font-body min-h-screen overflow-x-hidden">
<div class="fixed inset-0 grid-bg pointer-events-none"></div>
<div class="fixed inset-0 scanline pointer-events-none"></div>
<main class="relative min-h-screen p-4 md:p-6">
  <div class="max-w-[1200px] mx-auto flex flex-col gap-4">
    <header class="hud-panel clip-path-chamfer-lg p-4 flex flex-wrap items-center justify-between gap-3">
      <div>
        <p class="text-2xs font-label tracking-widest text-primary/70">CONFIG_CONSOLE</p>
        <h1 class="mt-2 text-xl font-headline tracking-wide">Prompt, key, and runtime config.</h1>
        <p id="settingsMeta" class="mt-2 text-sm text-on-surface-variant">Loading runtime metadata...</p>
      </div>
      <div class="flex items-center gap-3">
        <a href="/" class="px-4 py-2 text-2xs font-headline tracking-widest border border-cyan-400/20 text-primary hover:bg-cyan-400/10 transition-colors">BACK</a>
        <button id="settingsReloadButton" class="px-4 py-2 text-2xs font-headline tracking-widest border border-cyan-400/20 text-primary hover:bg-cyan-400/10 transition-colors">RELOAD</button>
        <button id="settingsSaveButton" class="px-4 py-2 text-2xs font-headline tracking-widest bg-primary text-black hover:opacity-90 transition-opacity">SAVE_CONFIG</button>
      </div>
    </header>

    <section class="grid grid-cols-1 xl:grid-cols-[0.9fr_1.1fr] gap-4">
      <div class="hud-panel clip-path-chamfer-lg p-4 flex flex-col gap-4">
        <div>
          <p class="text-2xs font-label tracking-widest text-primary/60">OPENAI_KEY</p>
          <input id="openaiKeyInput" type="password" placeholder="Leave blank to keep current key" class="hud-input w-full text-xs px-3 py-3 mt-3"/>
          <p id="openaiHint" class="mt-2 text-sm text-on-surface-variant">KEY_STATUS: UNKNOWN</p>
        </div>
        <div>
          <p class="text-2xs font-label tracking-widest text-primary/60">WELCOME_PROMPT</p>
          <textarea id="promptInput" rows="8" class="hud-input hud-scroll w-full text-xs px-3 py-3 mt-3 resize-none"></textarea>
        </div>
        <div>
          <p class="text-2xs font-label tracking-widest text-primary/60">STATUS</p>
          <p id="message" class="mt-2 text-sm text-on-surface-variant">Ready.</p>
        </div>
      </div>

      <div class="hud-panel clip-path-chamfer-lg p-4">
        <p class="text-2xs font-label tracking-widest text-primary/60">CONFIG_JSON</p>
        <textarea id="configEditor" rows="24" class="hud-input hud-scroll w-full h-[70vh] text-xs px-3 py-3 mt-3 resize-none font-mono leading-relaxed"></textarea>
      </div>
    </section>
  </div>
</main>
<script src="/settings.js" type="module"></script>
</body></html>
"""


def build_dashboard_js() -> str:
    return """
const statusPill = document.getElementById("statusPill");
const playerPill = document.getElementById("playerPill");
const messageEl = document.getElementById("message");
const listenerMeta = document.getElementById("listenerMeta");
const configPathMeta = document.getElementById("configPathMeta");
const dashboardUrlMeta = document.getElementById("dashboardUrlMeta");
const keyMeta = document.getElementById("keyMeta");
const assistantNameDisplay = document.getElementById("assistantNameDisplay");
const headerStatus = document.getElementById("headerStatus");
const headerPlayerState = document.getElementById("headerPlayerState");
const headerToggleButton = document.getElementById("headerToggleButton");
const headerNextButton = document.getElementById("headerNextButton");
const listenerCardValue = document.getElementById("listenerCardValue");
const realtimeCardValue = document.getElementById("realtimeCardValue");
const mediaCardValue = document.getElementById("mediaCardValue");
const targetsList = document.getElementById("targetsList");
const satelliteStatus = document.getElementById("satelliteStatus");
const targetsCountValue = document.getElementById("targetsCountValue");
const pairCountValue = document.getElementById("pairCountValue");
const clapGapValue = document.getElementById("clapGapValue");
const clapScoreValue = document.getElementById("clapScoreValue");
const clapToleranceValue = document.getElementById("clapToleranceValue");
const mediaModeValue = document.getElementById("mediaModeValue");
const mediaSourceValue = document.getElementById("mediaSourceValue");
const voiceValue = document.getElementById("voiceValue");
const languageValue = document.getElementById("languageValue");
const workspaceValue = document.getElementById("workspaceValue");

let currentState = null;

function compactPath(path) {
  if (!path) return "UNKNOWN";
  if (path.length <= 42) return path;
  return `...${path.slice(-39)}`;
}

function setMessage(text, isError = false) {
  messageEl.textContent = text;
  messageEl.className = isError
    ? "mt-2 text-sm text-error"
    : "mt-2 text-sm text-on-surface-variant";
}

function openSettingsPage() {
  window.open("/settings", "_blank", "noopener");
}

function setSignalBars(level) {
  const bars = [
    document.getElementById("signalBar1"),
    document.getElementById("signalBar2"),
    document.getElementById("signalBar3"),
    document.getElementById("signalBar4"),
    document.getElementById("signalBar5"),
  ];
  bars.forEach((bar, index) => {
    bar.className = `${index < level ? "w-1 bg-primary" : "w-1 bg-surface-variant"} ${bar.className
      .split(" ")
      .filter((part) => part.startsWith("h-"))
      .join(" ")}`;
  });
}

function renderTargets(targets) {
  targetsList.innerHTML = "";
  const items = Array.isArray(targets) ? targets : [];
  targetsCountValue.textContent = String(items.length);
  if (!items.length) {
    const row = document.createElement("div");
    row.className = "hud-panel-soft clip-path-chamfer-lg px-3 py-2.5 flex items-center gap-3";
    const emptyDot = document.createElement("div");
    emptyDot.className = "w-2 h-2 bg-surface-variant";
    const emptyLabel = document.createElement("span");
    emptyLabel.className = "text-2xs font-headline text-on-surface-variant";
    emptyLabel.textContent = "NO_TARGETS_CONFIGURED";
    const emptyState = document.createElement("span");
    emptyState.className = "ml-auto text-2xs text-on-surface-variant";
    emptyState.textContent = "IDLE";
    row.append(emptyDot, emptyLabel, emptyState);
    targetsList.appendChild(row);
    return;
  }

  items.slice(0, 6).forEach((target, index) => {
    const row = document.createElement("div");
    row.className = "hud-panel-soft clip-path-chamfer-lg px-3 py-2.5 flex items-center gap-3";

    const dot = document.createElement("div");
    dot.className = index === 0 ? "w-2 h-2 bg-primary animate-pulse" : "w-2 h-2 bg-surface-variant";

    const label = document.createElement("span");
    label.className = index === 0 ? "text-2xs font-headline" : "text-2xs font-headline text-on-surface-variant";
    label.textContent = (target.label || target.id || "TARGET").toUpperCase();

    const state = document.createElement("span");
    state.className = index === 0 ? "ml-auto text-2xs text-primary/40" : "ml-auto text-2xs text-on-surface-variant";
    state.textContent = target.id === "welcome_localhost" ? "REALTIME" : index === 0 ? "FIRST" : "READY";

    row.append(dot, label, state);
    targetsList.appendChild(row);
  });
}

function mediaSourceLabel(media) {
  if (!media) return "unknown";
  if (media.mode === "single_file") return media.selected_sound_path || "single file not set";
  if (media.mode === "folder_random") return media.selected_folder_path || "folder not set";
  if (media.mode === "folder_choice") return media.selected_folder_path || "folder not set";
  if (media.mode === "url") return media.selected_url || "url not set";
  if (media.mode === "auto_downloads") return media.selected_folder_path || media.downloads_dir || "downloads not set";
  return media.youtube_fallback_url || "no media source";
}

async function fetchState() {
  const response = await fetch("/state");
  const payload = await response.json();
  currentState = payload;
  renderState(payload);
}

function renderState(state) {
  const realtime = state.config?.realtime || {};
  const player = state.player || {};
  const media = state.config?.media || {};
  const microphone = state.config?.microphone || {};
  const profile = microphone.profile || {};
  const assistantName = realtime.assistant_name || "JARVIS";
  const statusLabel = state.listener_running
    ? `LISTENER_${String(state.status || "ONLINE").toUpperCase().replaceAll(" ", "_")}`
    : `LISTENER_${String(state.status || "OFFLINE").toUpperCase().replaceAll(" ", "_")}`;

  assistantNameDisplay.textContent = String(assistantName).toUpperCase();
  statusPill.textContent = statusLabel;
  headerStatus.textContent = statusLabel;

  if (player.playing) {
    playerPill.textContent = `PLAYING_${compactPath(player.current_path || "TRACK").replaceAll(" ", "_")}`;
  } else if (player.paused) {
    playerPill.textContent = `PAUSED_${compactPath(player.current_path || "TRACK").replaceAll(" ", "_")}`;
  } else {
    playerPill.textContent = "PLAYER_IDLE";
  }
  headerPlayerState.textContent = playerPill.textContent;
  headerToggleButton.textContent = player.playing ? "pause_circle" : "play_circle";
  headerToggleButton.title = player.playing ? "Pause media" : player.paused ? "Resume media" : "Play media";
  headerNextButton.classList.toggle("hidden", !player.can_skip);
  headerNextButton.title = player.can_skip ? "Next track" : "No next track available";

  listenerCardValue.textContent = state.listener_running ? "Microphone stream online" : "Listener offline";
  realtimeCardValue.textContent = state.openai_key_present ? "Realtime credentials armed" : "OpenAI key required";
  mediaCardValue.textContent = player.playing
    ? "Soundtrack flowing through bus"
    : player.paused
      ? "Soundtrack paused in buffer"
      : "Media bus calm";
  listenerMeta.textContent = state.listener_running
    ? `LISTENER: ${String(state.status || "ONLINE").toUpperCase()}`
    : `LISTENER: ${String(state.status || "OFFLINE").toUpperCase()}`;
  configPathMeta.textContent = `CONFIG: ${compactPath(state.config_path)}`;
  dashboardUrlMeta.textContent = `URL: ${compactPath(state.dashboard_url || "LOCALHOST")}`;
  while (keyMeta.firstChild) keyMeta.removeChild(keyMeta.firstChild);
  const keyDot = document.createElement("span");
  keyDot.className = state.openai_key_present
    ? "w-2 h-2 rounded-full bg-tertiary shadow-[0_0_5px_#c2ff99]"
    : "w-2 h-2 rounded-full bg-error shadow-[0_0_5px_#ff716c]";
  const keyLabel = document.createElement("span");
  keyLabel.textContent = state.openai_key_present ? "OPENAI_LINK: VERIFIED" : "OPENAI_LINK: REQUIRED";
  keyMeta.append(keyDot, keyLabel);
  renderTargets(state.config?.selected_targets || []);
  setSignalBars(state.openai_key_present ? 5 : state.listener_running ? 3 : 1);
  satelliteStatus.textContent = state.openai_key_present ? "CONTROL_LINK_READY" : "AUTH_REQUIRED";
  pairCountValue.textContent = String(profile.pair_count || 0);
  clapGapValue.textContent = `${Number(profile.average_gap || 0).toFixed(3)}s`;
  clapScoreValue.textContent = Number(profile.average_score || 0).toFixed(3);
  clapToleranceValue.textContent = Number(profile.match_tolerance || 0).toFixed(3);
  mediaModeValue.textContent = String(media.mode || "none").toUpperCase();
  mediaSourceValue.textContent = mediaSourceLabel(media);
  voiceValue.textContent = String(realtime.voice || "marin");
  languageValue.textContent = String(state.config?.language || "fr").toUpperCase();
  workspaceValue.textContent = String(state.config?.workspace_dir || "unknown");
}

async function post(path, payload = null) {
  const response = await fetch(path, {
    method: "POST",
    headers: payload ? { "Content-Type": "application/json" } : {},
    body: payload ? JSON.stringify(payload) : null,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed: ${path}`);
  }
  return data;
}

async function callAndRefresh(path, successMessage) {
  try {
    await post(path);
    if (successMessage) {
      setMessage(successMessage);
    }
    setTimeout(fetchState, 180);
  } catch (error) {
    setMessage(error.message, true);
  }
}

document.getElementById("triggerButton").addEventListener("click", () => callAndRefresh("/trigger", "Trigger replayed."));
document.getElementById("playButton").addEventListener("click", () => callAndRefresh("/player/toggle", "Player toggled."));
headerToggleButton.addEventListener("click", () => callAndRefresh("/player/toggle", "Player toggled."));
headerNextButton.addEventListener("click", () => callAndRefresh("/player/next", "Next track requested."));
document.getElementById("restartListenerButton").addEventListener("click", () => callAndRefresh("/listener/restart", "Listener restarted."));
document.getElementById("stopListenerButton").addEventListener("click", () => callAndRefresh("/listener/stop", "Listener stopped."));
document.getElementById("startListenerButton").addEventListener("click", () => callAndRefresh("/listener/start", "Listener started."));
document.getElementById("reloadButton").addEventListener("click", openSettingsPage);
document.getElementById("engageButton").addEventListener("click", () => callAndRefresh("/trigger", "System engaged."));
document.getElementById("saveButton").addEventListener("click", openSettingsPage);
document.getElementById("killButton").addEventListener("click", async () => {
  try {
    await post("/shutdown");
    setMessage("Shutting everything down...");
  } catch (error) {
    setMessage(error.message, true);
  }
});

fetchState().catch((error) => setMessage(error.message, true));
setInterval(() => {
  fetchState().catch(() => {});
}, 2000);
"""


def build_dashboard_settings_js() -> str:
    return """
const promptInput = document.getElementById("promptInput");
const openaiKeyInput = document.getElementById("openaiKeyInput");
const openaiHint = document.getElementById("openaiHint");
const configEditor = document.getElementById("configEditor");
const settingsMeta = document.getElementById("settingsMeta");
const messageEl = document.getElementById("message");

let editorDirty = false;
let promptDirty = false;

function setMessage(text, isError = false) {
  messageEl.textContent = text;
  messageEl.className = isError ? "mt-2 text-sm text-error" : "mt-2 text-sm text-on-surface-variant";
}

async function fetchState() {
  const response = await fetch("/state");
  const state = await response.json();
  renderState(state);
}

function renderState(state) {
  if (!promptDirty) {
    promptInput.value = state.config?.realtime?.welcome_prompt || "";
  }
  if (!editorDirty) {
    configEditor.value = JSON.stringify(state.config, null, 2);
  }
  openaiHint.textContent = state.openai_key_present
    ? "KEY_STATUS: STORED_IN_ENV"
    : "KEY_STATUS: MISSING";
  settingsMeta.textContent = `Config: ${state.config_path} • Dashboard: ${state.dashboard_url}`;
}

async function post(path, payload = null) {
  const response = await fetch(path, {
    method: "POST",
    headers: payload ? { "Content-Type": "application/json" } : {},
    body: payload ? JSON.stringify(payload) : null,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed: ${path}`);
  }
  return data;
}

async function saveConfig() {
  try {
    const config = JSON.parse(configEditor.value);
    config.realtime = config.realtime || {};
    config.realtime.welcome_prompt = promptInput.value.trim();
    await post("/config", {
      config,
      openai_key: openaiKeyInput.value.trim(),
    });
    editorDirty = false;
    promptDirty = false;
    openaiKeyInput.value = "";
    setMessage("Config saved and listener restarted.");
    await fetchState();
  } catch (error) {
    setMessage(error.message, true);
  }
}

document.getElementById("settingsReloadButton").addEventListener("click", async () => {
  editorDirty = false;
  promptDirty = false;
  await fetchState();
  setMessage("Config reloaded from disk.");
});

document.getElementById("settingsSaveButton").addEventListener("click", saveConfig);
configEditor.addEventListener("input", () => {
  editorDirty = true;
});
promptInput.addEventListener("input", () => {
  promptDirty = true;
});

fetchState().catch((error) => setMessage(error.message, true));
"""
