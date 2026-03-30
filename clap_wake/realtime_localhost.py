from __future__ import annotations

import json
import logging
import os
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import DEFAULT_LANGUAGE, get_default_welcome_prompt
from .env_utils import load_env_value

LOGGER = logging.getLogger("clap_wake.realtime")
_SERVER_LOCK = threading.Lock()
_SERVER_INSTANCE: "RealtimeWelcomeServer | None" = None


def ensure_realtime_server(config: dict[str, Any]) -> str:
    global _SERVER_INSTANCE

    realtime_config = config.get("realtime", {})
    preferred_port = int(realtime_config.get("port", 8765))

    with _SERVER_LOCK:
        if _SERVER_INSTANCE is not None and _SERVER_INSTANCE.port == preferred_port:
            _SERVER_INSTANCE.update_config(config)
            return _SERVER_INSTANCE.url

        if _SERVER_INSTANCE is not None:
            _SERVER_INSTANCE.stop()

        port = preferred_port if is_port_free(preferred_port) else find_free_port(preferred_port + 1)
        server = RealtimeWelcomeServer(config=config, port=port)
        server.start()
        _SERVER_INSTANCE = server
        return server.url


def stop_realtime_server() -> None:
    global _SERVER_INSTANCE

    with _SERVER_LOCK:
        if _SERVER_INSTANCE is None:
            return
        _SERVER_INSTANCE.stop()
        _SERVER_INSTANCE = None


class RealtimeWelcomeServer:
    def __init__(self, config: dict[str, Any], port: int) -> None:
        self.config = config
        self.port = port
        self.url = f"http://127.0.0.1:{self.port}/"
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if not is_port_free(self.port):
            LOGGER.info("Realtime localhost already bound on port %s, reusing existing URL.", self.port)
            return

        handler = self._make_handler()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        LOGGER.info("Realtime localhost started on %s", self.url)

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)
        self.thread = None
        self.httpd = None

    def update_config(self, config: dict[str, Any]) -> None:
        self.config = config

    def _make_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path in {"/", "/index.html"}:
                    self._send_html(build_index_html(server.public_config()))
                    return

                if self.path == "/app.js":
                    self._send_js(build_app_js(server))
                    return

                if self.path == "/styles.css":
                    self._send_css(build_styles_css())
                    return

                if self.path == "/config":
                    self._send_json(server.public_config())
                    return

                if self.path == "/health":
                    self._send_json({"ok": True})
                    return

                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

            def do_POST(self) -> None:
                if self.path != "/token":
                    self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
                    return

                try:
                    payload = mint_ephemeral_token(server.config)
                except RuntimeError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                except Exception as exc:
                    LOGGER.exception("Unable to mint ephemeral token")
                    self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return

                self._send_json(payload)

            def log_message(self, format: str, *args) -> None:
                LOGGER.debug("localhost %s - %s", self.address_string(), format % args)

            def _send_html(self, body: str) -> None:
                self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8")

            def _send_js(self, body: str) -> None:
                self._send_bytes(body.encode("utf-8"), "application/javascript; charset=utf-8")

            def _send_css(self, body: str) -> None:
                self._send_bytes(body.encode("utf-8"), "text/css; charset=utf-8")

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

    def public_config(self) -> dict[str, Any]:
        realtime = self.config.get("realtime", {})
        language = self.config.get("language", DEFAULT_LANGUAGE)
        return {
            "language": language,
            "model": realtime.get("model", "gpt-realtime"),
            "voice": realtime.get("voice", "marin"),
            "assistant_name": realtime.get("assistant_name", "Jarvis"),
            "welcome_name": realtime.get("welcome_name", ""),
            "welcome_prompt": realtime.get(
                "welcome_prompt",
                get_default_welcome_prompt(language),
            ),
        }


def mint_ephemeral_token(config: dict[str, Any]) -> dict[str, Any]:
    realtime = config.get("realtime", {})
    api_key = (
        realtime.get("api_key")
        or load_workspace_openai_key(config)
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError(
            "Aucune cle API OpenAI configuree. Renseigne-la dans le setup ou via OPENAI_API_KEY."
        )

    payload = {
        "session": {
            "type": "realtime",
            "model": realtime.get("model", "gpt-realtime"),
            "audio": {
                "output": {
                    "voice": realtime.get("voice", "marin"),
                }
            },
        }
    }

    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url="https://api.openai.com/v1/realtime/client_secrets",
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        data=data,
    )

    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI token error ({exc.code}): {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI token error: {exc}") from exc


def load_workspace_openai_key(config: dict[str, Any]) -> str | None:
    workspace_dir = config.get("workspace_dir")
    if not workspace_dir:
        return None
    return load_env_value(Path(workspace_dir) / ".env", "OPENAI_API_KEY")


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def find_free_port(start_port: int) -> int:
    for port in range(start_port, start_port + 200):
        if is_port_free(port):
            return port
    raise RuntimeError("Aucun port libre trouve pour le localhost welcome.")


def build_index_html(public_config: dict[str, Any]) -> str:
    language = public_config.get("language", DEFAULT_LANGUAGE)
    assistant_name = public_config.get("assistant_name", "Jarvis")
    model = public_config.get("model", "gpt-realtime")
    voice = public_config.get("voice", "marin")
    welcome_name = public_config.get("welcome_name", "")
    prompt_preview = public_config.get("welcome_prompt", "")
    copy = {
        "fr": {
            "title": "Clap Wake Up Voice",
            "eyebrow": "Realtime Voice",
            "headline": "Bienvenue, on se reveille.",
            "lede": "Le double claquement a ouvert ce localhost. Clique si besoin sur reconnecter, puis parle.",
            "connect": "Connecter",
            "disconnect": "Couper",
            "status": "Preparation...",
            "log": "Journal",
            "overview": "Voice Interface",
            "assistant": "Assistant",
            "voice": "Voice",
            "model": "Model",
            "user": "Wake Name",
            "prompt": "Welcome Prompt",
            "session": "Session Status",
            "events": "Realtime Log",
            "hint": "This is the clap-triggered Realtime page. It should open fast, speak first, and stay usable while the session is live.",
        },
        "en": {
            "title": "Clap Wake Up Voice",
            "eyebrow": "Realtime Voice",
            "headline": "Welcome back. Wake up.",
            "lede": "The double clap opened this localhost page. Reconnect if needed, then talk.",
            "connect": "Connect",
            "disconnect": "Disconnect",
            "status": "Preparing...",
            "log": "Log",
            "overview": "Voice Interface",
            "assistant": "Assistant",
            "voice": "Voice",
            "model": "Model",
            "user": "Wake Name",
            "prompt": "Welcome Prompt",
            "session": "Session Status",
            "events": "Realtime Log",
            "hint": "This is the clap-triggered Realtime page. It should open fast, speak first, and stay usable while the session is live.",
        },
    }[language]
    return f"""<!doctype html>
<html lang="{language}">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{copy["title"]}</title>
    <script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Inter:wght@300;400;600&display=swap" rel="stylesheet"/>
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
    <link rel="stylesheet" href="/styles.css" />
  </head>
  <body class="bg-background text-on-background font-body overflow-hidden">
    <div class="fixed inset-0 grid-bg pointer-events-none"></div>
    <div class="fixed inset-0 scanline pointer-events-none"></div>

    <header class="fixed top-0 w-full z-50 h-16 px-6 bg-[#0a0e14]/82 backdrop-blur-xl border-b border-cyan-500/18 shadow-[0_0_18px_rgba(129,236,255,0.12)]">
      <div class="h-full flex items-center justify-between gap-6">
        <div class="flex items-center gap-4 min-w-0">
          <div class="w-10 h-10 flex items-center justify-center border border-primary/20 bg-primary/5">
            <span class="material-symbols-outlined text-primary">neurology</span>
          </div>
          <div class="min-w-0">
            <div class="flex items-center gap-3 flex-wrap">
              <span class="text-cyan-400 font-bold tracking-[0.26em] font-headline text-sm">JARVIS_OS</span>
              <span class="text-[10px] text-primary/60 tracking-[0.18em] font-label">{copy["overview"].upper()}</span>
            </div>
            <p class="text-[11px] text-on-surface-variant truncate">{copy["hint"]}</p>
          </div>
        </div>
        <div class="hud-chip px-3 py-2 flex items-center gap-2 text-[10px] font-headline tracking-[0.18em]">
          <span class="w-2 h-2 rounded-full bg-primary shadow-[0_0_8px_rgba(129,236,255,0.7)]"></span>
          <span id="status">{copy["status"]}</span>
        </div>
      </div>
    </header>

    <aside class="fixed left-0 top-16 h-[calc(100vh-64px)] z-40 bg-[#0a0e14]/40 backdrop-blur-md w-24 border-r border-cyan-500/10 px-3 py-6">
      <div class="flex flex-col items-center gap-2 mb-8">
        <div class="w-12 h-12 flex items-center justify-center border border-primary/20 bg-primary/5">
          <span class="material-symbols-outlined text-primary" style="font-variation-settings: 'FILL' 1;">graphic_eq</span>
        </div>
        <span class="text-cyan-500 font-black text-[10px] font-headline tracking-[0.16em] text-center">VOICE_CORE</span>
      </div>
      <div class="flex flex-col gap-4">
        <button id="connectButton" class="w-full flex flex-col items-center py-4 text-cyan-400 bg-cyan-500/20 border-l-4 border-cyan-400 transition-transform">
          <span class="material-symbols-outlined mb-1">power</span>
          <span class="font-['Space_Grotesk'] uppercase text-[10px] tracking-tighter">{copy["connect"].upper()}</span>
        </button>
        <button id="disconnectButton" class="w-full flex flex-col items-center py-4 text-cyan-900/60 hover:bg-cyan-500/5 hover:text-cyan-200 transition-all">
          <span class="material-symbols-outlined mb-1">power_off</span>
          <span class="font-['Space_Grotesk'] uppercase text-[10px] tracking-tighter">{copy["disconnect"].upper()}</span>
        </button>
      </div>
    </aside>

    <main class="ml-24 mt-16 min-h-[calc(100vh-64px)] p-6 md:p-8">
      <div class="max-w-[1600px] mx-auto grid grid-cols-1 xl:grid-cols-[1.05fr_0.95fr] gap-6">
        <section class="hud-panel clip-path-chamfer-lg p-6 md:p-8 min-h-[40rem] relative overflow-hidden">
          <div class="absolute inset-0 pointer-events-none opacity-50">
            <div class="absolute -top-12 left-10 w-48 h-48 rounded-full bg-primary/10 blur-3xl"></div>
            <div class="absolute bottom-10 right-8 w-56 h-56 rounded-full bg-secondary/8 blur-3xl"></div>
          </div>
          <div class="relative h-full flex flex-col justify-between gap-8">
            <div class="max-w-xl">
              <p class="text-[11px] font-label tracking-[0.22em] text-primary/70 mb-3">{copy["eyebrow"].upper()}</p>
              <h1 class="text-4xl md:text-5xl font-headline font-semibold tracking-[0.08em] text-on-background">{copy["headline"]}</h1>
              <p class="mt-4 text-sm text-on-surface-variant leading-6">{copy["lede"]}</p>
            </div>

            <div class="relative mx-auto flex items-center justify-center min-w-[18rem] min-h-[18rem]">
              <div class="absolute w-80 h-80 border border-primary/12 rounded-full animate-pulse-ring"></div>
              <div class="absolute w-64 h-64 border border-primary/18 rounded-full animate-pulse-ring" style="animation-delay: 0.8s"></div>
              <div class="absolute w-[21rem] h-[21rem] border-t-2 border-b-2 border-primary/25 rounded-full animate-[spin_12s_linear_infinite]"></div>
              <div class="absolute w-[18rem] h-[18rem] border-l border-r border-secondary/20 rounded-full animate-[spin_18s_linear_infinite_reverse]"></div>
              <div class="w-52 h-52 rounded-full border border-primary/40 bg-gradient-to-br from-primary/28 via-surface-container to-surface-container-lowest backdrop-blur-xl shadow-[0_0_70px_rgba(129,236,255,0.18)] flex flex-col items-center justify-center relative overflow-hidden">
                <div class="absolute inset-0 bg-radial-gradient from-primary/20 via-transparent to-transparent animate-pulse"></div>
                <span class="material-symbols-outlined text-7xl text-primary drop-shadow-[0_0_12px_rgba(129,236,255,0.7)]" style="font-variation-settings: 'FILL' 1;">neurology</span>
                <p class="mt-3 text-[11px] font-label tracking-[0.28em] text-primary/70">REALTIME_CORE</p>
                <p id="assistantName" class="mt-2 text-sm font-headline tracking-[0.2em] text-on-background">{assistant_name.upper()}</p>
              </div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div class="hud-panel-soft clip-path-chamfer-lg p-5">
                <p class="text-[10px] font-label tracking-[0.2em] text-primary/60">{copy["assistant"].upper()}</p>
                <p class="mt-2 text-lg font-headline tracking-[0.12em] text-on-background">{assistant_name}</p>
                <p class="mt-2 text-sm text-on-surface-variant">Voice-first assistant launched by a double clap trigger.</p>
              </div>
              <div class="hud-panel-soft clip-path-chamfer-lg p-5">
                <p class="text-[10px] font-label tracking-[0.2em] text-primary/60">{copy["session"].upper()}</p>
                <p id="connectionState" class="mt-2 text-lg font-headline tracking-[0.12em] text-on-background">BOOTING</p>
                <p class="mt-2 text-sm text-on-surface-variant">WebRTC audio session and Realtime control channel state.</p>
              </div>
              <div class="hud-panel-soft clip-path-chamfer-lg p-5">
                <p class="text-[10px] font-label tracking-[0.2em] text-primary/60">{copy["model"].upper()}</p>
                <p class="mt-2 text-lg font-headline tracking-[0.12em] text-on-background">{model}</p>
                <p class="mt-2 text-sm text-on-surface-variant">Realtime model used for the local wake-up voice session.</p>
              </div>
              <div class="hud-panel-soft clip-path-chamfer-lg p-5">
                <p class="text-[10px] font-label tracking-[0.2em] text-primary/60">{copy["voice"].upper()}</p>
                <p class="mt-2 text-lg font-headline tracking-[0.12em] text-on-background">{voice}</p>
                <p class="mt-2 text-sm text-on-surface-variant">Configured output voice for the current session.</p>
              </div>
            </div>
          </div>
        </section>

        <section class="flex flex-col gap-6">
          <div class="hud-panel clip-path-chamfer-lg p-5">
            <div class="flex items-center justify-between gap-4 mb-5">
              <div>
                <p class="text-[10px] font-label tracking-[0.2em] text-primary/60">SESSION_CONTEXT</p>
                <h2 class="mt-1 text-xl font-headline tracking-[0.12em] text-on-background">{copy["prompt"]}</h2>
              </div>
              <span class="material-symbols-outlined text-primary/70 text-3xl">notes</span>
            </div>
            <div class="space-y-4">
              <div>
                <p class="text-[10px] font-label tracking-[0.18em] text-primary/60">{copy["user"].upper()}</p>
                <p class="mt-1 text-sm font-headline tracking-[0.14em] text-on-background">{welcome_name or "-"}</p>
              </div>
              <div>
                <p class="text-[10px] font-label tracking-[0.18em] text-primary/60">{copy["prompt"].upper()}</p>
                <p class="mt-1 text-sm text-on-surface-variant leading-6">{prompt_preview}</p>
              </div>
            </div>
          </div>

          <div class="hud-panel clip-path-chamfer-lg p-5 min-h-[22rem] flex flex-col">
            <div class="flex items-center justify-between gap-4 mb-5">
              <div>
                <p class="text-[10px] font-label tracking-[0.2em] text-primary/60">EVENT_STREAM</p>
                <h2 class="mt-1 text-xl font-headline tracking-[0.12em] text-on-background">{copy["events"]}</h2>
              </div>
              <span class="material-symbols-outlined text-primary/70 text-3xl">terminal</span>
            </div>
            <pre id="log" class="hud-log flex-1"></pre>
          </div>
        </section>
      </div>
    </main>
    <script src="/app.js" type="module"></script>
  </body>
</html>
"""


def build_styles_css() -> str:
    return """
:root {
  color-scheme: dark;
  --surface: #0a0e14;
  --surface-container: #151a21;
  --surface-container-low: #0f141a;
  --surface-container-highest: #20262f;
  --surface-variant: #20262f;
  --outline-variant: #44484f;
  --primary: #81ecff;
  --secondary: #ff7350;
  --tertiary: #c2ff99;
  --on-background: #f1f3fc;
  --on-surface-variant: #a8abb3;
  --error: #ff716c;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }
body {
  font-family: Inter, sans-serif;
  background: var(--surface);
  color: var(--on-background);
}
.font-headline { font-family: "Space Grotesk", sans-serif; }
.font-body { font-family: Inter, sans-serif; }
.font-label { font-family: "Space Grotesk", sans-serif; }
.clip-path-chamfer-lg {
  clip-path: polygon(0 0, 95% 0, 100% 5%, 100% 100%, 5% 100%, 0 95%);
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
.bg-background { background-color: var(--surface); }
.text-on-background { color: var(--on-background); }
.text-on-surface-variant { color: var(--on-surface-variant); }
.text-primary { color: var(--primary); }
.text-error { color: var(--error); }
.border-primary { border-color: rgba(129,236,255,0.2); }
.hud-chip {
  background: rgba(129,236,255,0.08);
  border: 1px solid rgba(129,236,255,0.12);
}
.hud-panel {
  background:
    linear-gradient(180deg, rgba(129,236,255,0.10), rgba(129,236,255,0.00) 24%),
    rgba(15, 20, 26, 0.72);
  backdrop-filter: blur(18px);
  border: 1px solid rgba(129,236,255,0.14);
}
.hud-panel-soft {
  background:
    linear-gradient(180deg, rgba(129,236,255,0.06), rgba(129,236,255,0.00) 22%),
    rgba(15, 20, 26, 0.58);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(129,236,255,0.10);
}
.hud-log {
  margin: 0;
  overflow: auto;
  white-space: pre-wrap;
  font-family: "SF Mono", "Menlo", monospace;
  font-size: 12px;
  line-height: 1.6;
  color: var(--on-background);
  background: rgba(0,0,0,0.18);
  border: 1px solid rgba(129,236,255,0.08);
  padding: 16px;
}
.hud-log::-webkit-scrollbar { width: 8px; height: 8px; }
.hud-log::-webkit-scrollbar-thumb { background: rgba(129,236,255,0.18); }
    """


def build_app_js(server: RealtimeWelcomeServer) -> str:
    public_config = json.dumps(server.public_config(), ensure_ascii=True)
    return f"""
const PUBLIC_CONFIG = {public_config};
const COPY = PUBLIC_CONFIG.language === "en"
  ? {{
      sessionAlreadyActive: "Session already active.",
      gettingToken: "Getting token...",
      tokenFailed: "Could not get Realtime token",
      channelOpened: "Realtime channel opened.",
      webrtcState: "WebRTC state",
      sdpFailed: "SDP exchange failed",
      connected: "Connected. The model will greet you.",
      error: "Error",
      assistant: "Assistant",
      responseDone: "Response complete.",
      openaiError: "OpenAI error",
      event: "Event",
      disconnected: "Disconnected.",
      assistantIsNamed: "Your name is",
      personIsNamed: "The person's name is",
      stayBrief: "Stay brief, warm, energetic, and end with one useful question.",
      wokeUpNamed: "I just woke up. Introduce yourself as",
      wokeUpNamedMiddle: "then welcome",
      wokeUpNamedSuffix: "in English.",
      wokeUpAnon: "I just woke up. Introduce yourself with your configured AI name, then welcome me in English.",
    }}
  : {{
      sessionAlreadyActive: "Session deja active.",
      gettingToken: "Recuperation du token...",
      tokenFailed: "Impossible de recuperer le token Realtime",
      channelOpened: "Canal Realtime ouvert.",
      webrtcState: "Etat WebRTC",
      sdpFailed: "SDP exchange failed",
      connected: "Connecte. Le model va saluer.",
      error: "Erreur",
      assistant: "Assistant",
      responseDone: "Reponse terminee.",
      openaiError: "Erreur OpenAI",
      event: "Event",
      disconnected: "Deconnecte.",
      assistantIsNamed: "Tu t'appelles",
      personIsNamed: "La personne s'appelle",
      stayBrief: "Reste bref, chaleureux, energique, et termine par une question utile.",
      wokeUpNamed: "Je viens de me reveiller. Presente-toi sous le nom de",
      wokeUpNamedMiddle: "puis souhaite la bienvenue a",
      wokeUpNamedSuffix: "en francais.",
      wokeUpAnon: "Je viens de me reveiller. Presente-toi avec ton nom d'IA configure, puis souhaite-moi la bienvenue en francais.",
    }};

const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const connectButton = document.getElementById("connectButton");
const disconnectButton = document.getElementById("disconnectButton");

let pc = null;
let dc = null;
let stream = null;
let audioEl = null;

function setStatus(text) {{
  statusEl.textContent = text;
}}

function log(text) {{
  const stamp = new Date().toLocaleTimeString();
  logEl.textContent += `[${{stamp}}] ${{text}}\\n`;
  logEl.scrollTop = logEl.scrollHeight;
}}

async function connect() {{
  if (pc) {{
    log(COPY.sessionAlreadyActive);
    return;
  }}

  try {{
    setStatus(COPY.gettingToken);
    const tokenResponse = await fetch("/token", {{ method: "POST" }});
    const tokenData = await tokenResponse.json();
    if (!tokenResponse.ok) {{
      throw new Error(tokenData.error || COPY.tokenFailed);
    }}

    const ephemeralKey = tokenData.value;
    pc = new RTCPeerConnection();
    audioEl = document.createElement("audio");
    audioEl.autoplay = true;
    pc.ontrack = (event) => {{
      audioEl.srcObject = event.streams[0];
    }};
    pc.onconnectionstatechange = () => {{
      setStatus(`${{COPY.webrtcState}}: ${{pc.connectionState}}`);
      document.getElementById("connectionState").textContent = String(pc.connectionState || "booting").toUpperCase();
      log(`${{COPY.webrtcState}} -> ${{pc.connectionState}}`);
    }};

    stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
    stream.getTracks().forEach((track) => pc.addTrack(track, stream));

    dc = pc.createDataChannel("oai-events");
    dc.addEventListener("open", () => {{
      log(COPY.channelOpened);
      sendWelcomeBootstrap();
    }});
    dc.addEventListener("message", (event) => {{
      try {{
        const payload = JSON.parse(event.data);
        handleRealtimeEvent(payload);
      }} catch (error) {{
        log(`Event non parse: ${{event.data}}`);
      }}
    }});

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const sdpResponse = await fetch("https://api.openai.com/v1/realtime/calls", {{
      method: "POST",
      body: offer.sdp,
      headers: {{
        Authorization: `Bearer ${{ephemeralKey}}`,
        "Content-Type": "application/sdp",
      }},
    }});
    if (!sdpResponse.ok) {{
      const errorText = await sdpResponse.text();
      throw new Error(`${{COPY.sdpFailed}}: ${{errorText}}`);
    }}

    const answer = {{
      type: "answer",
      sdp: await sdpResponse.text(),
    }};
    await pc.setRemoteDescription(answer);
    setStatus(COPY.connected);
  }} catch (error) {{
    log(`${{COPY.error}}: ${{error.message}}`);
    setStatus(`${{COPY.error}}: ${{error.message}}`);
    await disconnect();
  }}
}}

function sendWelcomeBootstrap() {{
  if (!dc || dc.readyState !== "open") {{
    return;
  }}

  dc.send(JSON.stringify({{
    type: "session.update",
    session: {{
      type: "realtime",
      model: PUBLIC_CONFIG.model,
      output_modalities: ["audio"],
      audio: {{
        input: {{
          turn_detection: {{
            type: "server_vad",
            threshold: 0.5,
            prefix_padding_ms: 300,
            silence_duration_ms: 500
          }}
        }},
        output: {{
          voice: PUBLIC_CONFIG.voice,
        }}
      }},
      instructions: buildInstructions(),
    }},
  }}));

  dc.send(JSON.stringify({{
    type: "conversation.item.create",
    item: {{
      type: "message",
      role: "user",
      content: [
        {{
          type: "input_text",
          text: buildWelcomeMessage(),
        }}
      ],
    }},
  }}));

  dc.send(JSON.stringify({{
    type: "response.create",
    response: {{
      output_modalities: ["audio"],
    }},
  }}));
}}

function buildInstructions() {{
  const whoAi = PUBLIC_CONFIG.assistant_name ? `${{COPY.assistantIsNamed}} ${{PUBLIC_CONFIG.assistant_name}}.` : "";
  const who = PUBLIC_CONFIG.welcome_name ? `${{COPY.personIsNamed}} ${{PUBLIC_CONFIG.welcome_name}}.` : "";
  return `${{PUBLIC_CONFIG.welcome_prompt}} ${{whoAi}} ${{who}} ${{COPY.stayBrief}}`;
}}

function buildWelcomeMessage() {{
  if (PUBLIC_CONFIG.welcome_name) {{
    return `${{COPY.wokeUpNamed}} ${{PUBLIC_CONFIG.assistant_name}} ${{COPY.wokeUpNamedMiddle}} ${{PUBLIC_CONFIG.welcome_name}} ${{COPY.wokeUpNamedSuffix}}`;
  }}
  return COPY.wokeUpAnon;
}}

function handleRealtimeEvent(event) {{
  if (event.type === "response.audio_transcript.delta" && event.delta) {{
    log(`${{COPY.assistant}}: ${{event.delta}}`);
    return;
  }}

  if (event.type === "response.output_audio_transcript.delta" && event.delta) {{
    log(`${{COPY.assistant}}: ${{event.delta}}`);
    return;
  }}

  if (event.type === "response.text.delta" && event.delta) {{
    log(`${{COPY.assistant}}: ${{event.delta}}`);
    return;
  }}

  if (event.type === "response.done") {{
    log(COPY.responseDone);
    return;
  }}

  if (event.type === "error") {{
    log(`${{COPY.openaiError}}: ${{JSON.stringify(event)}}`);
    return;
  }}

  log(`${{COPY.event}}: ${{event.type}}`);
}}

async function disconnect() {{
  if (dc) {{
    try {{
      dc.close();
    }} catch (error) {{
      console.warn(error);
    }}
  }}
  dc = null;

  if (pc) {{
    try {{
      pc.close();
    }} catch (error) {{
      console.warn(error);
    }}
  }}
  pc = null;

  if (stream) {{
    stream.getTracks().forEach((track) => track.stop());
  }}
  stream = null;
  setStatus(COPY.disconnected);
}}

connectButton.addEventListener("click", () => {{
  connect();
}});
disconnectButton.addEventListener("click", () => {{
  disconnect();
}});

connect();
"""
