from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit

from .launcher import detect_default_macos_browser_preferences
from .window_layout import VisibleWindow, WindowBounds


@dataclass(frozen=True)
class CapturedWindowTarget:
    target: dict[str, str]
    bounds: WindowBounds


def capture_launchable_targets(
    browser_preferences: dict[str, str | None] | None = None,
    visible_windows: Iterable[VisibleWindow] | None = None,
) -> list[CapturedWindowTarget]:
    if sys.platform != "darwin":
        return []

    preferences = browser_preferences or detect_default_macos_browser_preferences()
    app_name = str((preferences or {}).get("app_name") or "").strip()
    if not app_name:
        return []

    visible = list(visible_windows or [])
    browser_windows = sorted(
        [window for window in visible if window_belongs_to_browser(window, app_name)],
        key=lambda window: (window.bounds.left, window.bounds.top, window.title.casefold()),
    )
    if not browser_windows:
        return []

    browser_records = list_browser_windows_with_urls_macos(app_name)
    if not browser_records:
        return []

    matched: list[CapturedWindowTarget] = []
    unused_records = list(browser_records)
    for window in browser_windows:
        record = pop_best_matching_record(window, unused_records)
        if record is None:
            continue
        url = str(record.get("url") or "").strip()
        if not is_captureworthy_url(url):
            continue
        title = str(record.get("title") or "").strip()
        matched.append(
            CapturedWindowTarget(
                target={
                    "id": "custom_url",
                    "label": build_target_label(title, url),
                    "url": url,
                },
                bounds=window.bounds,
            )
        )
    return matched


def window_belongs_to_browser(window: VisibleWindow, app_name: str) -> bool:
    owner = window.owner_name.casefold()
    browser = app_name.casefold()
    return browser in owner or owner in browser


def list_browser_windows_with_urls_macos(app_name: str) -> list[dict[str, str]]:
    if app_name in {
        "Google Chrome",
        "Google Chrome Canary",
        "Brave Browser",
        "Microsoft Edge",
        "Arc",
    }:
        script = build_chrome_like_jxa(app_name)
    elif app_name == "Safari":
        script = build_safari_jxa()
    else:
        return []

    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return []

    raw = (result.stdout or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    records: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if url:
            records.append({"title": title, "url": url})
    return records


def build_chrome_like_jxa(app_name: str) -> str:
    return f"""
var app = Application({json.dumps(app_name)});
var result = [];
try {{
  var windows = app.windows();
  for (var i = 0; i < windows.length; i++) {{
    try {{
      var tab = windows[i].activeTab();
      result.push({{title: tab.title(), url: tab.url()}});
    }} catch (e) {{}}
  }}
}} catch (e) {{}}
JSON.stringify(result);
""".strip()


def build_safari_jxa() -> str:
    return """
var app = Application("Safari");
var result = [];
try {
  var windows = app.windows();
  for (var i = 0; i < windows.length; i++) {
    try {
      var tab = windows[i].currentTab();
      result.push({title: tab.name(), url: tab.url()});
    } catch (e) {}
  }
} catch (e) {}
JSON.stringify(result);
""".strip()


def pop_best_matching_record(window: VisibleWindow, records: list[dict[str, str]]) -> dict[str, str] | None:
    if not records:
        return None
    window_title = normalize_title(window.title)
    if window_title:
        for index, record in enumerate(records):
            record_title = normalize_title(str(record.get("title") or ""))
            if not record_title:
                continue
            if record_title == window_title or record_title in window_title or window_title in record_title:
                return records.pop(index)
    return records.pop(0)


def normalize_title(value: str) -> str:
    return " ".join(value.casefold().split())


def is_captureworthy_url(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").casefold()
    if not host:
        return False
    if host in {"127.0.0.1", "localhost"}:
        return False
    return True


def build_target_label(title: str, url: str) -> str:
    cleaned_title = " ".join(title.split())
    if cleaned_title:
        return cleaned_title[:80]
    parsed = urlsplit(url)
    return parsed.netloc or url
