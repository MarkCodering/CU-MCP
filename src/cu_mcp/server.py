"""
Computer Use MCP Server

Provides tools to observe and control the computer:
  - Capture screenshots and screen info
  - Move/click/scroll/drag the mouse
  - Type text and press keyboard shortcuts
  - Query the active window
  - Run shell commands

macOS note: You must grant Accessibility and Screen Recording permissions to
the terminal / process running this server the first time you use it.
System Preferences → Privacy & Security → Accessibility / Screen Recording.
"""

from __future__ import annotations

import base64
import functools
import io
import inspect
import json
import os
import subprocess
import sys
import time

import mss
import pyperclip
from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage

# ---------------------------------------------------------------------------
# Lazy pyautogui import – avoids X11 init errors at module load time
# ---------------------------------------------------------------------------

_pyautogui = None


def _get_pyautogui():
    global _pyautogui
    if _pyautogui is None:
        import pyautogui as _pag
        _pag.FAILSAFE = True
        _pag.PAUSE = 0.05
        _pyautogui = _pag
    return _pyautogui


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP("cu-mcp")

_DEFAULT_MAX_SCREENSHOT_EDGE = 1920
_DEFAULT_PNG_COMPRESS_LEVEL = 6


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read an int env var with clamping and safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


_MAX_SCREENSHOT_EDGE = _int_env(
    "CU_MCP_MAX_SCREENSHOT_EDGE", _DEFAULT_MAX_SCREENSHOT_EDGE, 0, 10000
)
_PNG_COMPRESS_LEVEL = _int_env(
    "CU_MCP_SCREENSHOT_PNG_COMPRESS_LEVEL", _DEFAULT_PNG_COMPRESS_LEVEL, 0, 9
)
_LOG_ENABLED = os.getenv("CU_MCP_LOG_TO_STDERR", "1").lower() not in {"0", "false", "no"}
_LOG_MAX_STRING = _int_env("CU_MCP_LOG_MAX_STRING", 300, 32, 10000)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _truncate_string(value: str, limit: int = _LOG_MAX_STRING) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...<truncated {len(value) - limit} chars>"


def _safe_log_value(value, *, depth: int = 0):
    if depth > 3:
        return "<max-depth>"

    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return f"<bytes {len(value)}>"
    if isinstance(value, Image):
        size = len(value.data) if getattr(value, "data", None) else 0
        return {"type": "Image", "format": getattr(value, "format", None), "bytes": size}
    if isinstance(value, (list, tuple)):
        items = [_safe_log_value(v, depth=depth + 1) for v in value[:10]]
        if len(value) > 10:
            items.append(f"...<{len(value) - 10} more>")
        return items if isinstance(value, list) else tuple(items)
    if isinstance(value, dict):
        out = {}
        for idx, (k, v) in enumerate(value.items()):
            if idx >= 20:
                out["..."] = f"<{len(value) - 20} more keys>"
                break
            out[str(k)] = _safe_log_value(v, depth=depth + 1)
        return out
    return repr(value)


def _log_terminal(event: str, tool_name: str, **fields) -> None:
    if not _LOG_ENABLED:
        return
    payload = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "tool": tool_name,
        **fields,
    }
    try:
        line = json.dumps(payload, ensure_ascii=True, default=repr)
    except Exception:
        line = str(payload)
    sys.stderr.write(f"[cu-mcp] {line}\n")
    sys.stderr.flush()


def _logged_tool(fn):
    sig = inspect.signature(fn)
    tool_name = fn.__name__

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            bound = sig.bind_partial(*args, **kwargs)
            call_args = {
                name: _safe_log_value(value) for name, value in bound.arguments.items()
            }
        except Exception:
            call_args = {"args": _safe_log_value(list(args)), "kwargs": _safe_log_value(kwargs)}

        started = time.perf_counter()
        _log_terminal("start", tool_name, args=call_args)
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            _log_terminal(
                "error",
                tool_name,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )
            raise

        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        _log_terminal(
            "end",
            tool_name,
            elapsed_ms=elapsed_ms,
            result=_safe_log_value(result),
        )
        return result

    return wrapper


def _normalize_to_logical_size(
    img: PILImage.Image, logical_width: int, logical_height: int
) -> PILImage.Image:
    """
    Align screenshot dimensions with pyautogui coordinates.

    On macOS Retina, raw screenshots may be 2x the logical coordinate space.
    Resizing avoids coordinate drift and shrinks payload size.
    """
    raw_width, raw_height = img.size
    if logical_width <= 0 or logical_height <= 0:
        return img
    if raw_width == logical_width and raw_height == logical_height:
        return img
    if raw_width <= 0 or raw_height <= 0:
        return img

    raw_ratio = raw_width / raw_height
    logical_ratio = logical_width / logical_height
    if abs(raw_ratio - logical_ratio) > 0.02:
        return img

    if raw_width >= logical_width and raw_height >= logical_height:
        return img.resize((logical_width, logical_height), PILImage.Resampling.LANCZOS)
    return img


def _downscale_for_context(img: PILImage.Image) -> PILImage.Image:
    """
    Cap screenshot dimensions so large screens don't overflow model context windows.
    Set CU_MCP_MAX_SCREENSHOT_EDGE=0 to disable.
    """
    if _MAX_SCREENSHOT_EDGE <= 0:
        return img

    width, height = img.size
    if width <= _MAX_SCREENSHOT_EDGE and height <= _MAX_SCREENSHOT_EDGE:
        return img

    scale = min(_MAX_SCREENSHOT_EDGE / width, _MAX_SCREENSHOT_EDGE / height)
    target_width = max(1, int(width * scale))
    target_height = max(1, int(height * scale))
    return img.resize((target_width, target_height), PILImage.Resampling.LANCZOS)


def _capture_screenshot(screen_size: tuple[int, int] | None = None) -> tuple[bytes, int, int]:
    """Return desktop screenshot PNG bytes plus encoded image width/height."""
    logical_width, logical_height = screen_size if screen_size else _screen_size()

    with mss.mss() as sct:
        # monitor[0] is the combined virtual screen across all displays.
        raw = sct.grab(sct.monitors[0])
        img = PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        img = _normalize_to_logical_size(img, logical_width, logical_height)
        img = _downscale_for_context(img)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True, compress_level=_PNG_COMPRESS_LEVEL)
        return buf.getvalue(), img.width, img.height


def _screenshot_b64(screen_size: tuple[int, int] | None = None) -> tuple[str, int, int]:
    png_bytes, img_width, img_height = _capture_screenshot(screen_size=screen_size)
    return base64.b64encode(png_bytes).decode(), img_width, img_height


def _screen_size() -> tuple[int, int]:
    return _get_pyautogui().size()


# ---------------------------------------------------------------------------
# Tools – Screen observation
# ---------------------------------------------------------------------------


@mcp.tool()
@_logged_tool
def take_screenshot() -> list:
    """
    Capture the current screen and return it as a base64-encoded PNG image.

    Always call this first to understand the current state of the desktop
    before performing any mouse or keyboard actions.

    Returns a dict with:
      - image_base64: PNG image encoded as base64 string
      - mime_type: "image/png"
      - width, height: logical screen dimensions (matches mouse coordinates)
      - image_width, image_height: encoded image dimensions
      - scale_x, scale_y: multiply image coords by these to map to screen coords
    """
    try:
        w, h = _screen_size()
        png_bytes, image_w, image_h = _capture_screenshot(screen_size=(w, h))
        metadata = json.dumps({
            "width": w,
            "height": h,
            "screen_width": w,
            "screen_height": h,
            "image_width": image_w,
            "image_height": image_h,
            "scale_x": (w / image_w) if image_w else 1.0,
            "scale_y": (h / image_h) if image_h else 1.0,
        })
        return [Image(data=png_bytes, format="png"), metadata]
    except Exception as exc:
        return [json.dumps({"success": False, "error": str(exc)})]


@mcp.tool()
@_logged_tool
def get_screen_info() -> dict:
    """
    Return basic information about the screen: dimensions and current cursor position.

    Returns a dict with:
      - screen_width, screen_height: total desktop size in pixels
      - cursor_x, cursor_y: current mouse position
    """
    try:
        w, h = _screen_size()
        cx, cy = _get_pyautogui().position()
        return {
            "success": True,
            "screen_width": w,
            "screen_height": h,
            "cursor_x": cx,
            "cursor_y": cy,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def get_cursor_position() -> dict:
    """
    Return the current (x, y) pixel position of the mouse cursor.
    """
    try:
        x, y = _get_pyautogui().position()
        return {"success": True, "x": x, "y": y}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def get_active_window_info() -> dict:
    """
    Return the name and title of the currently focused window (macOS).

    Uses AppleScript to query System Events. Returns:
      - app_name: the frontmost application name
      - window_title: the title of its front window (may be empty)
    """
    script = """
    tell application "System Events"
        set frontApp to first application process whose frontmost is true
        set appName to name of frontApp
        set winTitle to ""
        try
            set winTitle to name of front window of frontApp
        end try
        return appName & "|" & winTitle
    end tell
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split("|", 1)
            return {
                "success": True,
                "app_name": parts[0] if parts else "",
                "window_title": parts[1] if len(parts) > 1 else "",
            }
        return {"success": False, "error": result.stderr.strip()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Tools – Mouse control
# ---------------------------------------------------------------------------


@mcp.tool()
@_logged_tool
def mouse_move(x: int, y: int, duration: float = 0.25) -> dict:
    """
    Move the mouse cursor to screen coordinates (x, y) without clicking.

    Args:
        x: Target X coordinate (pixels from the left edge of the screen).
        y: Target Y coordinate (pixels from the top edge of the screen).
        duration: Smooth animation time in seconds (default 0.25).
    """
    try:
        _get_pyautogui().moveTo(x, y, duration=duration)
        ax, ay = _get_pyautogui().position()
        return {"success": True, "x": ax, "y": ay}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def mouse_left_click(x: int, y: int, duration: float = 0.2) -> dict:
    """
    Move the mouse to (x, y) and perform a left (primary) click.

    Args:
        x: Target X coordinate.
        y: Target Y coordinate.
        duration: Mouse movement animation time in seconds (default 0.2).
    """
    try:
        _get_pyautogui().click(x, y, button="left", duration=duration)
        return {"success": True, "action": "left_click", "x": x, "y": y}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def mouse_right_click(x: int, y: int, duration: float = 0.2) -> dict:
    """
    Move the mouse to (x, y) and perform a right (secondary / context-menu) click.

    Args:
        x: Target X coordinate.
        y: Target Y coordinate.
        duration: Mouse movement animation time in seconds (default 0.2).
    """
    try:
        _get_pyautogui().click(x, y, button="right", duration=duration)
        return {"success": True, "action": "right_click", "x": x, "y": y}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def mouse_double_click(x: int, y: int, duration: float = 0.2) -> dict:
    """
    Move the mouse to (x, y) and perform a double left-click.

    Args:
        x: Target X coordinate.
        y: Target Y coordinate.
        duration: Mouse movement animation time in seconds (default 0.2).
    """
    try:
        _get_pyautogui().doubleClick(x, y, duration=duration)
        return {"success": True, "action": "double_click", "x": x, "y": y}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def mouse_scroll(x: int, y: int, scroll_y: int = 3, scroll_x: int = 0) -> dict:
    """
    Scroll at screen coordinates (x, y).

    Args:
        x: X coordinate of the scroll target.
        y: Y coordinate of the scroll target.
        scroll_y: Vertical scroll amount.
                  Positive = scroll UP (content moves up / reveals lower content).
                  Negative = scroll DOWN (content moves down / reveals upper content).
                  Typical values: ±3 to ±10. Default: 3 (scroll up).
        scroll_x: Horizontal scroll amount.
                  Positive = scroll RIGHT, negative = scroll LEFT. Default: 0.
    """
    try:
        _get_pyautogui().moveTo(x, y, duration=0.15)
        if scroll_y != 0:
            _get_pyautogui().scroll(scroll_y, x=x, y=y)
        if scroll_x != 0:
            _get_pyautogui().hscroll(scroll_x, x=x, y=y)
        return {
            "success": True,
            "action": "scroll",
            "x": x,
            "y": y,
            "scroll_y": scroll_y,
            "scroll_x": scroll_x,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def mouse_drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float = 0.5,
    button: str = "left",
) -> dict:
    """
    Click and drag the mouse from (start_x, start_y) to (end_x, end_y).

    Useful for dragging files, resizing windows, or selecting text.

    Args:
        start_x: Starting X coordinate.
        start_y: Starting Y coordinate.
        end_x: Ending X coordinate.
        end_y: Ending Y coordinate.
        duration: Duration of the drag animation in seconds (default 0.5).
        button: Mouse button to hold during drag – "left" (default) or "right".
    """
    try:
        _get_pyautogui().moveTo(start_x, start_y, duration=0.2)
        _get_pyautogui().dragTo(end_x, end_y, duration=duration, button=button)
        return {
            "success": True,
            "action": "drag",
            "from": {"x": start_x, "y": start_y},
            "to": {"x": end_x, "y": end_y},
            "button": button,
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Tools – Keyboard control
# ---------------------------------------------------------------------------


@mcp.tool()
@_logged_tool
def keyboard_type(text: str, use_clipboard: bool = True) -> dict:
    """
    Type the given text at the current cursor position.

    By default uses the clipboard (copy-paste) to support Unicode characters,
    emoji, and CJK text that pyautogui's key simulation cannot handle.
    Set use_clipboard=False to simulate individual keystrokes (ASCII only,
    but avoids touching the clipboard).

    Args:
        text: The text to type.
        use_clipboard: If True, paste via clipboard (supports all Unicode).
                       If False, simulate key presses (ASCII only, default interval 30 ms).
    """
    try:
        if use_clipboard:
            previous = pyperclip.paste()
            pyperclip.copy(text)
            _get_pyautogui().hotkey("cmd", "v")  # macOS paste
            # Restore clipboard after a brief delay (best-effort)
            import threading
            def _restore():
                import time; time.sleep(0.4)
                try: pyperclip.copy(previous)
                except Exception: pass
            threading.Thread(target=_restore, daemon=True).start()
        else:
            _get_pyautogui().typewrite(text, interval=0.03)
        return {"success": True, "action": "type", "text": text}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def keyboard_press(key: str) -> dict:
    """
    Press and release a single keyboard key.

    Common key names:
      Navigation : 'up', 'down', 'left', 'right', 'home', 'end', 'pageup', 'pagedown'
      Editing    : 'enter', 'tab', 'backspace', 'delete', 'escape', 'space'
      Function   : 'f1' … 'f12'
      Modifiers  : 'shift', 'ctrl', 'alt', 'cmd' (macOS), 'win' (Windows)
      Letters    : 'a' … 'z'
      Digits     : '0' … '9'

    Args:
        key: The key name string (case-insensitive).
    """
    try:
        _get_pyautogui().press(key)
        return {"success": True, "action": "press", "key": key}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def keyboard_hotkey(keys: list[str]) -> dict:
    """
    Press a keyboard shortcut (multiple keys held simultaneously).

    Pass keys in the order they should be held down.

    Examples:
      ["cmd", "c"]            → Copy (macOS)
      ["cmd", "v"]            → Paste (macOS)
      ["cmd", "z"]            → Undo (macOS)
      ["ctrl", "c"]           → Copy (Windows/Linux)
      ["cmd", "space"]        → Spotlight search (macOS)
      ["ctrl", "shift", "t"]  → Reopen closed tab
      ["alt", "tab"]          → Switch windows

    Args:
        keys: Ordered list of key names to press together.
    """
    try:
        _get_pyautogui().hotkey(*keys)
        return {"success": True, "action": "hotkey", "keys": keys}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def keyboard_key_down(key: str) -> dict:
    """
    Hold a keyboard key down without releasing it.

    Pair with keyboard_key_up() to release. Useful for modifier-held interactions
    (e.g., shift-click, drag with Ctrl held, etc.).

    Args:
        key: Key name to hold (e.g., 'shift', 'ctrl', 'cmd').
    """
    try:
        _get_pyautogui().keyDown(key)
        return {"success": True, "action": "key_down", "key": key}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@mcp.tool()
@_logged_tool
def keyboard_key_up(key: str) -> dict:
    """
    Release a previously held keyboard key.

    Args:
        key: Key name to release (e.g., 'shift', 'ctrl', 'cmd').
    """
    try:
        _get_pyautogui().keyUp(key)
        return {"success": True, "action": "key_up", "key": key}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Tools – Shell
# ---------------------------------------------------------------------------


@mcp.tool()
@_logged_tool
def run_shell_command(command: str, timeout: int = 30) -> dict:
    """
    Execute a shell command and return its stdout, stderr, and return code.

    Use this to inspect system state (e.g., list files, check processes)
    or run non-interactive programs.  Avoid destructive or long-running commands.

    Args:
        command: Shell command string (runs under /bin/zsh on macOS).
        timeout: Maximum allowed runtime in seconds (default 30).
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            executable="/bin/zsh",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": True,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out after {timeout}s"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("screen://screenshot")
def screenshot_resource() -> str:
    """Live screenshot of the entire desktop as a base64-encoded PNG string."""
    return _screenshot_b64()[0]


@mcp.resource("screen://info")
def screen_info_resource() -> str:
    """JSON with screen dimensions and current cursor position."""
    w, h = _screen_size()
    cx, cy = _get_pyautogui().position()
    return json.dumps(
        {"screen_width": w, "screen_height": h, "cursor_x": cx, "cursor_y": cy}
    )


@mcp.resource("info://status")
def status_resource() -> str:
    """Server health check."""
    return "running"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@mcp.prompt()
def describe_screen() -> str:
    """Prompt the LLM to capture and describe the current screen state."""
    return (
        "Use the take_screenshot tool to capture the current screen, then provide "
        "a detailed description of everything visible: open windows, applications, "
        "text content, UI elements, and the overall layout."
    )


@mcp.prompt()
def automate_task(task: str) -> str:
    """
    Prompt to guide the LLM through automating a desktop task end-to-end.

    Args:
        task: Natural-language description of the task to automate.
    """
    return (
        f"Automate the following task on this computer:\n\n{task}\n\n"
        "Steps to follow:\n"
        "1. Take a screenshot to understand the current screen state.\n"
        "2. Identify what needs to be done and plan the required actions.\n"
        "3. Perform each action (mouse clicks, keyboard input, scrolling, etc.).\n"
        "4. After each significant action, take another screenshot to verify progress.\n"
        "5. Repeat until the task is complete.\n"
        "Be careful and methodical — confirm the expected result visually at each step."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
