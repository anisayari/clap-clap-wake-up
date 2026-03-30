from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowBounds:
    left: int
    top: int
    width: int
    height: int


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
