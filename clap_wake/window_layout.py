from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class WindowBounds:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class VisibleWindow:
    owner_name: str
    bounds: WindowBounds
    title: str = ""


def plan_launch_layout(count: int, displays: list[WindowBounds] | None = None) -> list[WindowBounds]:
    if count <= 0:
        return []

    display_bounds = displays or get_display_bounds()
    if not display_bounds:
        return []

    display_count = len(display_bounds)
    base = count // display_count
    remainder = count % display_count
    per_display = [base + (1 if index < remainder else 0) for index in range(display_count)]
    if count < display_count:
        per_display = [1 if index < count else 0 for index in range(display_count)]

    slots: list[WindowBounds] = []
    for display, slot_count in zip(display_bounds, per_display):
        if slot_count <= 0:
            continue
        slots.extend(split_display(display, slot_count))
    return slots[:count]


def select_launch_layout(
    count: int,
    saved_layout: dict | None = None,
    displays: list[WindowBounds] | None = None,
) -> list[WindowBounds]:
    display_bounds = displays or get_display_bounds()
    restored = restore_saved_layout(saved_layout, count=count, displays=display_bounds)
    if restored is not None:
        return restored
    return plan_launch_layout(count, displays=display_bounds)


def build_saved_layout(bounds_list: Iterable[WindowBounds], displays: list[WindowBounds] | None = None) -> dict:
    display_bounds = displays or get_display_bounds()
    slots = [normalize_bounds_for_display(bounds, display_bounds) for bounds in bounds_list]
    return {
        "saved_slots": slots,
        "display_count": len(display_bounds),
        "saved_at": time.time(),
    }


def restore_saved_layout(
    saved_layout: dict | None,
    count: int,
    displays: list[WindowBounds] | None = None,
) -> list[WindowBounds] | None:
    if not saved_layout:
        return None
    slots = list(saved_layout.get("saved_slots") or [])
    if count <= 0:
        return []
    if len(slots) < count:
        return None

    display_bounds = displays or get_display_bounds()
    if not display_bounds:
        return None

    saved_display_count = int(saved_layout.get("display_count") or 0)
    if saved_display_count and saved_display_count != len(display_bounds):
        return None

    restored: list[WindowBounds] = []
    for slot in slots[:count]:
        display_index = int(slot.get("display_index", 0))
        if display_index < 0 or display_index >= len(display_bounds):
            return None
        display = display_bounds[display_index]
        restored.append(denormalize_bounds_for_display(slot, display))
    return restored


def normalize_bounds_for_display(bounds: WindowBounds, displays: list[WindowBounds]) -> dict[str, float | int]:
    display_index = find_display_index_for_bounds(bounds, displays)
    display = displays[display_index]
    return {
        "display_index": display_index,
        "left_ratio": round((bounds.left - display.left) / max(display.width, 1), 6),
        "top_ratio": round((bounds.top - display.top) / max(display.height, 1), 6),
        "width_ratio": round(bounds.width / max(display.width, 1), 6),
        "height_ratio": round(bounds.height / max(display.height, 1), 6),
    }


def denormalize_bounds_for_display(slot: dict, display: WindowBounds) -> WindowBounds:
    left_ratio = float(slot.get("left_ratio", 0.0))
    top_ratio = float(slot.get("top_ratio", 0.0))
    width_ratio = float(slot.get("width_ratio", 1.0))
    height_ratio = float(slot.get("height_ratio", 1.0))
    return WindowBounds(
        left=display.left + int(round(display.width * left_ratio)),
        top=display.top + int(round(display.height * top_ratio)),
        width=max(320, int(round(display.width * width_ratio))),
        height=max(220, int(round(display.height * height_ratio))),
    )


def find_display_index_for_bounds(bounds: WindowBounds, displays: list[WindowBounds]) -> int:
    center_x = bounds.left + (bounds.width / 2)
    center_y = bounds.top + (bounds.height / 2)
    for index, display in enumerate(displays):
        if (
            display.left <= center_x <= display.left + display.width
            and display.top <= center_y <= display.top + display.height
        ):
            return index

    closest_index = 0
    closest_distance = float("inf")
    for index, display in enumerate(displays):
        display_center_x = display.left + (display.width / 2)
        display_center_y = display.top + (display.height / 2)
        distance = math.hypot(center_x - display_center_x, center_y - display_center_y)
        if distance < closest_distance:
            closest_distance = distance
            closest_index = index
    return closest_index


def capture_visible_windows() -> list[VisibleWindow]:
    if sys.platform == "darwin":
        return capture_visible_windows_macos()
    return []


def capture_visible_windows_macos() -> list[VisibleWindow]:
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGNullWindowID,
            kCGWindowListOptionOnScreenOnly,
        )
    except Exception:
        return []

    try:
        raw_windows = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
    except Exception:
        return []

    windows: list[VisibleWindow] = []
    for item in raw_windows:
        if int(item.get("kCGWindowLayer", 1)) != 0:
            continue
        if float(item.get("kCGWindowAlpha", 1.0)) <= 0.0:
            continue

        owner_name = str(item.get("kCGWindowOwnerName") or "").strip()
        if not owner_name:
            continue

        bounds_info = item.get("kCGWindowBounds") or {}
        width = int(round(float(bounds_info.get("Width", 0))))
        height = int(round(float(bounds_info.get("Height", 0))))
        if width < 220 or height < 120:
            continue

        bounds = WindowBounds(
            left=int(round(float(bounds_info.get("X", 0)))),
            top=int(round(float(bounds_info.get("Y", 0)))),
            width=width,
            height=height,
        )
        windows.append(
            VisibleWindow(
                owner_name=owner_name,
                bounds=bounds,
                title=str(item.get("kCGWindowName") or "").strip(),
            )
        )
    return windows


def match_windows_to_expected_slots(
    windows: list[VisibleWindow],
    expected_slots: list[WindowBounds],
    owner_hints: list[list[str]] | None = None,
) -> list[WindowBounds]:
    remaining = list(windows)
    hints = owner_hints or [[] for _ in expected_slots]
    matched: list[WindowBounds] = []

    for index, slot in enumerate(expected_slots):
        preferred = [hint for hint in hints[index] if hint]
        candidates = [
            window
            for window in remaining
            if not preferred or owner_matches_any_hint(window.owner_name, preferred)
        ]
        if not candidates:
            candidates = remaining
        if not candidates:
            raise RuntimeError("Not enough visible windows to capture the current layout.")

        best = min(candidates, key=lambda window: bounds_distance(window.bounds, slot))
        matched.append(best.bounds)
        remaining.remove(best)

    return matched


def owner_matches_any_hint(owner_name: str, hints: list[str]) -> bool:
    owner = owner_name.casefold()
    return any(hint.casefold() in owner or owner in hint.casefold() for hint in hints)


def bounds_distance(left: WindowBounds, right: WindowBounds) -> float:
    left_center_x = left.left + (left.width / 2)
    left_center_y = left.top + (left.height / 2)
    right_center_x = right.left + (right.width / 2)
    right_center_y = right.top + (right.height / 2)
    return math.hypot(left_center_x - right_center_x, left_center_y - right_center_y)


def get_display_bounds() -> list[WindowBounds]:
    if sys.platform == "darwin":
        return get_macos_display_bounds()
    if os.name == "nt":
        return get_windows_display_bounds()
    return []


def split_display(display: WindowBounds, count: int) -> list[WindowBounds]:
    if count <= 1:
        return [inset_bounds(display)]

    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    gap = 14
    cell_width = display.width / cols
    cell_height = display.height / rows

    slots: list[WindowBounds] = []
    for index in range(count):
        row = index // cols
        col = index % cols
        left = display.left + int(round(col * cell_width))
        top = display.top + int(round(row * cell_height))
        right = display.left + int(round((col + 1) * cell_width))
        bottom = display.top + int(round((row + 1) * cell_height))
        slots.append(
            WindowBounds(
                left=left + gap,
                top=top + gap,
                width=max(320, right - left - (gap * 2)),
                height=max(220, bottom - top - (gap * 2)),
            )
        )
    return slots


def inset_bounds(display: WindowBounds) -> WindowBounds:
    gap = 18
    return WindowBounds(
        left=display.left + gap,
        top=display.top + gap,
        width=max(320, display.width - (gap * 2)),
        height=max(220, display.height - (gap * 2)),
    )


def place_foreground_window(bounds: WindowBounds | None, wait_seconds: float = 2.2) -> None:
    if bounds is None:
        return
    if sys.platform == "darwin":
        place_foreground_window_macos(bounds, wait_seconds=wait_seconds)
        return
    if os.name == "nt":
        place_foreground_window_windows(bounds, wait_seconds=wait_seconds)


def get_macos_display_bounds() -> list[WindowBounds]:
    try:
        from AppKit import NSScreen
    except Exception:
        return []

    screens = list(NSScreen.screens())
    if not screens:
        return []

    frames = [screen.visibleFrame() for screen in screens]
    max_y = max(float(frame.origin.y + frame.size.height) for frame in frames)
    bounds = []
    for frame in frames:
        left = int(round(float(frame.origin.x)))
        width = int(round(float(frame.size.width)))
        height = int(round(float(frame.size.height)))
        top = int(round(max_y - float(frame.origin.y + frame.size.height)))
        bounds.append(WindowBounds(left=left, top=top, width=width, height=height))
    bounds.sort(key=lambda item: (item.top, item.left))
    return bounds


def place_foreground_window_macos(bounds: WindowBounds, wait_seconds: float = 2.2) -> None:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        script = f"""
tell application "System Events"
  set frontProc to first application process whose frontmost is true
  if (count of windows of frontProc) is 0 then error "No window"
  tell front window of frontProc
    set position to {{{bounds.left}, {bounds.top}}}
    set size to {{{bounds.width}, {bounds.height}}}
  end tell
end tell
"""
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(0.18)


def get_windows_display_bounds() -> list[WindowBounds]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    monitors: list[WindowBounds] = []

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )

    def callback(hmonitor, hdc, rect_ptr, lparam):
        del hmonitor, hdc, lparam
        rect = rect_ptr.contents
        monitors.append(
            WindowBounds(
                left=int(rect.left),
                top=int(rect.top),
                width=int(rect.right - rect.left),
                height=int(rect.bottom - rect.top),
            )
        )
        return 1

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(callback), 0)
    monitors.sort(key=lambda item: (item.top, item.left))
    return monitors


def place_foreground_window_windows(bounds: WindowBounds, wait_seconds: float = 2.2) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            user32.MoveWindow(hwnd, bounds.left, bounds.top, bounds.width, bounds.height, True)
            return
        time.sleep(0.18)
