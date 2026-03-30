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
            "transcript": "Live Voice",
            "transcript_waiting": "En attente de la premiere phrase de l'assistant...",
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
            "transcript": "Live Voice",
            "transcript_waiting": "Waiting for the assistant's first sentence...",
            "hint": "This is the clap-triggered Realtime page. It should open fast, speak first, and stay usable while the session is live.",
        },
    }[language]
    return f"""<!doctype html>
<html lang="{language}">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{copy["title"]}</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Inter:wght@300;400;600&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
<script>
tailwind.config = {{
  darkMode: "class",
  theme: {{
    extend: {{
      colors: {{
        primary:"#81ecff","primary-dim":"#00d4ec",secondary:"#ff7350",tertiary:"#c2ff99",
        background:"#0a0e14",surface:"#0a0e14","surface-bright":"#262c36",
        "surface-container":"#151a21","surface-container-low":"#0f141a",
        "surface-container-high":"#1b2028","surface-variant":"#20262f",
        "surface-container-lowest":"#000000",
        "on-background":"#f1f3fc","on-surface":"#f1f3fc","on-surface-variant":"#a8abb3",
        "on-primary":"#005762","outline-variant":"#44484f",
        error:"#ff716c","error-dim":"#d7383b"
      }},
      fontSize: {{ "2xs": ["0.625rem", {{ lineHeight: "1rem" }}] }},
      fontFamily: {{ headline:["Space Grotesk"],body:["Inter"],label:["Space Grotesk"] }},
      borderRadius: {{"DEFAULT":"0px","lg":"0px","xl":"0px","full":"9999px"}},
    }},
  }},
}}
</script>
<link rel="stylesheet" href="/styles.css"/>
</head>
<body class="dark bg-background text-on-background font-body overflow-x-hidden min-h-screen">
<div class="fixed inset-0 grid-bg pointer-events-none"></div>
<div class="fixed inset-0 scanline pointer-events-none"></div>

<header class="fixed top-0 w-full z-50 h-12 px-4 bg-surface/85 backdrop-blur-xl shadow-[0_0_16px_rgba(129,236,255,0.08)]">
<div class="h-full flex items-center justify-between gap-3">
<div class="flex items-center gap-2.5 min-w-0">
<div class="w-7 h-7 flex items-center justify-center border border-primary/15 bg-primary/5">
<span class="material-symbols-outlined text-primary text-base" style="font-variation-settings:'FILL' 1;">graphic_eq</span>
</div>
<div class="min-w-0">
<span class="text-primary font-bold tracking-widest font-headline text-xs">{copy["title"].upper()}</span>
<p class="text-2xs text-on-surface-variant truncate hidden sm:block">{copy["overview"]}</p>
</div>
</div>
<div class="hud-chip px-2 py-0.5 flex items-center gap-1.5 text-2xs font-headline tracking-widest text-primary">
<span class="w-1.5 h-1.5 rounded-full bg-primary shadow-[0_0_6px_rgba(129,236,255,0.6)]"></span>
<span id="status">{copy["status"]}</span>
</div>
</div>
</header>

<main class="mt-12 min-h-[calc(100vh-3rem)] pb-24 overflow-y-auto">
<div class="max-w-5xl mx-auto px-4 md:px-6 flex flex-col gap-5">

<section class="relative flex flex-col items-center text-center pt-6 pb-2">
<div class="absolute inset-0 pointer-events-none">
<div class="absolute top-0 left-1/2 -translate-x-1/2 w-96 h-96 rounded-full bg-primary/5 blur-3xl"></div>
</div>
<p class="text-2xs font-label tracking-widest text-primary/50 relative">{copy["eyebrow"].upper()}</p>
<h1 class="text-3xl md:text-4xl font-headline font-semibold tracking-wide text-on-background mt-1.5 relative">{copy["headline"]}</h1>
<p class="text-xs text-on-surface-variant mt-1 relative">{copy["lede"]}</p>

<div class="relative flex items-center justify-center mt-6 w-64 h-64 md:w-72 md:h-72">
<div class="absolute w-56 h-56 md:w-64 md:h-64 border border-primary/8 rounded-full animate-pulse-ring"></div>
<div class="absolute w-44 h-44 md:w-52 md:h-52 border border-primary/12 rounded-full animate-pulse-ring" style="animation-delay:0.9s"></div>
<div class="absolute w-64 h-64 md:w-72 md:h-72 border-t border-b border-primary/15 rounded-full animate-[spin_16s_linear_infinite]"></div>
<div class="absolute w-52 h-52 md:w-60 md:h-60 border-l border-r border-secondary/10 rounded-full animate-[spin_22s_linear_infinite_reverse]"></div>
<div class="w-40 h-40 md:w-44 md:h-44 rounded-full border border-primary/25 bg-gradient-to-br from-primary/15 via-surface-container to-surface-container-lowest backdrop-blur-xl shadow-[0_0_80px_rgba(129,236,255,0.12)] flex flex-col items-center justify-center relative overflow-hidden">
<div class="absolute inset-0 radial-glow animate-pulse"></div>
<span class="material-symbols-outlined text-5xl md:text-6xl text-primary drop-shadow-[0_0_14px_rgba(129,236,255,0.5)]" style="font-variation-settings:'FILL' 1;">neurology</span>
<p class="mt-1 text-2xs font-label tracking-widest text-primary/50">REALTIME_CORE</p>
<p class="mt-0.5 text-xs font-headline tracking-widest text-on-background">{assistant_name.upper()}</p>
</div>
</div>

<div class="hud-panel clip-path-chamfer-lg p-4 mt-4 w-full max-w-2xl text-left">
<p class="text-2xs font-label tracking-widest text-primary/40">{copy["transcript"].upper()}</p>
<p id="liveTranscript" class="mt-2 text-sm md:text-base font-headline tracking-wide text-on-background min-h-[3rem] leading-relaxed">{copy["transcript_waiting"]}</p>
</div>
</section>

<section class="grid grid-cols-2 md:grid-cols-4 gap-2">
<div class="hud-panel clip-path-chamfer-lg p-3">
<p class="text-2xs font-label tracking-widest text-primary/40">{copy["assistant"].upper()}</p>
<p class="mt-1 text-sm font-headline tracking-wider text-on-background">{assistant_name}</p>
</div>
<div class="hud-panel clip-path-chamfer-lg p-3">
<p class="text-2xs font-label tracking-widest text-primary/40">{copy["session"].upper()}</p>
<p id="connectionState" class="mt-1 text-sm font-headline tracking-wider text-on-background">BOOTING</p>
</div>
<div class="hud-panel clip-path-chamfer-lg p-3">
<p class="text-2xs font-label tracking-widest text-primary/40">{copy["model"].upper()}</p>
<p class="mt-1 text-sm font-headline tracking-wider text-on-background">{model}</p>
</div>
<div class="hud-panel clip-path-chamfer-lg p-3">
<p class="text-2xs font-label tracking-widest text-primary/40">{copy["voice"].upper()}</p>
<p class="mt-1 text-sm font-headline tracking-wider text-on-background">{voice}</p>
</div>
</section>

<section class="grid grid-cols-1 lg:grid-cols-2 gap-3">
<div class="hud-panel clip-path-chamfer-lg p-4">
<div class="flex items-center justify-between gap-3 mb-3">
<div>
<p class="text-2xs font-label tracking-widest text-primary/40">SESSION_CONTEXT</p>
<h2 class="mt-1 text-sm font-headline tracking-wide text-on-background">{copy["prompt"]}</h2>
</div>
<span class="material-symbols-outlined text-primary/50 text-lg">notes</span>
</div>
<div class="space-y-2.5">
<div>
<p class="text-2xs font-label tracking-widest text-primary/40">{copy["user"].upper()}</p>
<p class="mt-0.5 text-xs font-headline tracking-wider text-on-background">{welcome_name or "-"}</p>
</div>
<div>
<p class="text-2xs font-label tracking-widest text-primary/40">{copy["prompt"].upper()}</p>
<p class="mt-0.5 text-xs text-on-surface-variant leading-relaxed">{prompt_preview}</p>
</div>
</div>
</div>

<div class="hud-panel clip-path-chamfer-lg p-4 min-h-[18rem] flex flex-col">
<div class="flex items-center justify-between gap-3 mb-3">
<div>
<p class="text-2xs font-label tracking-widest text-primary/40">EVENT_STREAM</p>
<h2 class="mt-1 text-sm font-headline tracking-wide text-on-background">{copy["events"]}</h2>
</div>
<span class="material-symbols-outlined text-primary/50 text-lg">terminal</span>
</div>
<div class="flex-1 min-h-0 overflow-hidden">
<pre id="log" class="hud-log h-full"></pre>
</div>
</div>
</section>

</div>
</main>

<nav class="fixed bottom-3 left-1/2 -translate-x-1/2 z-50 hud-dock clip-path-chamfer-lg px-3 py-1.5 flex items-center gap-1">
<button id="connectButton" class="flex flex-col items-center px-4 py-1 text-primary/40 hover:text-primary hover:bg-primary/5 transition-colors" title="{copy["connect"]}">
<span class="material-symbols-outlined text-lg">power</span>
<span class="text-2xs font-headline tracking-widest mt-0.5">{copy["connect"].upper()}</span>
</button>
<div class="w-px h-8 bg-outline-variant/20 mx-1"></div>
<button id="disconnectButton" class="flex flex-col items-center px-4 py-1 text-error/40 hover:text-error hover:bg-error/5 transition-colors" title="{copy["disconnect"]}">
<span class="material-symbols-outlined text-lg">power_off</span>
<span class="text-2xs font-headline tracking-widest mt-0.5">{copy["disconnect"].upper()}</span>
</button>
</nav>

<script src="/app.js" type="module"></script>
</body>
</html>
"""


def build_styles_css() -> str:
    return """
:root {
  color-scheme: dark;
}
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; }

.clip-path-chamfer-lg {
  clip-path: polygon(0 0, 97% 0, 100% 3%, 100% 100%, 3% 100%, 0 97%);
}
.grid-bg {
  background-image:
    linear-gradient(to right, rgba(129,236,255,0.04) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(129,236,255,0.04) 1px, transparent 1px);
  background-size: 40px 40px;
}
.scanline {
  background: linear-gradient(to bottom, transparent 50%, rgba(129,236,255,0.015) 50%);
  background-size: 100% 4px;
}
@keyframes pulse-ring {
  0%   { transform: scale(0.85); opacity: 0.4; }
  100% { transform: scale(1.6); opacity: 0; }
}
.animate-pulse-ring {
  animation: pulse-ring 3.5s cubic-bezier(0.4,0,0.6,1) infinite;
}
.radial-glow {
  background: radial-gradient(circle, rgba(129,236,255,0.18) 0%, transparent 70%);
}
.hud-chip {
  background: rgba(129,236,255,0.06);
  border: 1px solid rgba(68,72,79,0.18);
}
.hud-panel {
  background:
    linear-gradient(180deg, rgba(129,236,255,0.08), rgba(129,236,255,0.00) 22%),
    rgba(15,20,26,0.72);
  backdrop-filter: blur(18px);
  border: 1px solid rgba(68,72,79,0.20);
}
.hud-dock {
  background: linear-gradient(0deg, rgba(10,14,20,0.92), rgba(129,236,255,0.06));
  backdrop-filter: blur(24px);
  border: 1px solid rgba(68,72,79,0.22);
  box-shadow: 0 -2px 30px rgba(129,236,255,0.05);
}
.hud-log {
  margin: 0;
  overflow: auto;
  white-space: pre-wrap;
  font-family: "SF Mono", "Menlo", monospace;
  font-size: 0.75rem;
  line-height: 1.6;
  color: #f1f3fc;
  background: rgba(0,0,0,0.18);
  border: 1px solid rgba(68,72,79,0.15);
  padding: 0.75rem;
  height: 100%;
  max-height: 22rem;
}
.hud-log::-webkit-scrollbar       { width: 6px; height: 6px; }
.hud-log::-webkit-scrollbar-thumb { background: rgba(129,236,255,0.15); }
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
      transcriptWaiting: "Waiting for the assistant's first sentence...",
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
      transcriptWaiting: "En attente de la premiere phrase de l'assistant...",
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
const transcriptEl = document.getElementById("liveTranscript");
const connectButton = document.getElementById("connectButton");
const disconnectButton = document.getElementById("disconnectButton");

let pc = null;
let dc = null;
let stream = null;
let audioEl = null;
let liveTranscript = "";
let logLines = [];
const MAX_LOG_LINES = 160;

function setStatus(text) {{
  statusEl.textContent = text;
}}

function log(text) {{
  const stamp = new Date().toLocaleTimeString();
  logLines.push(`[${{stamp}}] ${{text}}`);
  if (logLines.length > MAX_LOG_LINES) {{
    logLines = logLines.slice(-MAX_LOG_LINES);
  }}
  logEl.textContent = logLines.join("\\n");
  logEl.scrollTop = logEl.scrollHeight;
}}

function resetTranscript() {{
  liveTranscript = "";
  transcriptEl.textContent = COPY.transcriptWaiting || "";
}}

function appendTranscript(delta) {{
  liveTranscript += delta;
  transcriptEl.textContent = liveTranscript.trim() || (COPY.transcriptWaiting || "");
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
      resetTranscript();
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
  if (
    event.type === "response.created" ||
    event.type === "response.output_item.added"
  ) {{
    resetTranscript();
    return;
  }}

  if (event.type === "response.audio_transcript.delta" && event.delta) {{
    appendTranscript(event.delta);
    log(`${{COPY.assistant}}: ${{event.delta}}`);
    return;
  }}

  if (event.type === "response.output_audio_transcript.delta" && event.delta) {{
    appendTranscript(event.delta);
    log(`${{COPY.assistant}}: ${{event.delta}}`);
    return;
  }}

  if (event.type === "response.text.delta" && event.delta) {{
    appendTranscript(event.delta);
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

resetTranscript();
connect();
"""
