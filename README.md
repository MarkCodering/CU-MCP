# Computer Use MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets an AI observe and control your desktop — take screenshots, move the mouse, click, scroll, type, and run shell commands.

## Tools

### Screen observation

| Tool | Description |
|---|---|
| `take_screenshot` | Capture desktop as a base64 PNG (auto-scaled for large screens) |
| `get_screen_info` | Screen dimensions and current cursor position |
| `get_cursor_position` | Current mouse (x, y) coordinates |
| `get_active_window_info` | Frontmost application name and window title (macOS) |

### Mouse control

| Tool | Description |
|---|---|
| `mouse_move` | Move cursor to (x, y) |
| `mouse_left_click` | Left-click at (x, y) |
| `mouse_right_click` | Right-click at (x, y) |
| `mouse_double_click` | Double-click at (x, y) |
| `mouse_scroll` | Scroll vertically and/or horizontally at (x, y) |
| `mouse_drag` | Click-and-drag from one position to another |

### Keyboard control

| Tool | Description |
|---|---|
| `keyboard_type` | Type text (Unicode-safe via clipboard by default) |
| `keyboard_press` | Press a single key (`enter`, `tab`, `escape`, arrow keys, etc.) |
| `keyboard_hotkey` | Press a shortcut, e.g. `["cmd", "c"]` |
| `keyboard_key_down` | Hold a key down |
| `keyboard_key_up` | Release a held key |

### Shell

| Tool | Description |
|---|---|
| `run_shell_command` | Run a zsh command, returns stdout/stderr/return code |

## Resources

| URI | Description |
|---|---|
| `screen://screenshot` | Live desktop screenshot as a base64 PNG string (auto-scaled for large screens) |
| `screen://info` | JSON with screen dimensions and cursor position |
| `info://status` | Server health check |

## Screenshot sizing

To avoid context-window bloat on very large or Retina displays, screenshots are automatically normalized/downscaled before encoding.

- `take_screenshot.width` / `take_screenshot.height` remain logical screen coordinates for mouse actions.
- `take_screenshot.image_width` / `take_screenshot.image_height` are the actual encoded image dimensions.
- `take_screenshot.scale_x` / `take_screenshot.scale_y` map image coordinates back to screen coordinates.

Optional environment variables:

- `CU_MCP_MAX_SCREENSHOT_EDGE` (default `1920`): max width/height of the encoded screenshot. Set `0` to disable downscaling.
- `CU_MCP_SCREENSHOT_PNG_COMPRESS_LEVEL` (default `6`, range `0-9`): PNG compression level.

## Prompts

| Prompt | Description |
|---|---|
| `describe_screen` | Captures and describes the current screen state |
| `automate_task(task)` | Step-by-step guide for automating a desktop task |

## Setup

### Prerequisites

- Python 3.10+
- macOS (mouse/keyboard automation and screen capture use macOS APIs)

### Install

```bash
# Create a virtual environment using the Homebrew Python (recommended)
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
source .venv/bin/activate

# Install the server and all dependencies
pip install -e .
pip install mss pyautogui pyperclip
```

### macOS permissions

The first time the server performs mouse/keyboard actions or takes a screenshot, macOS will prompt for permissions. Grant both in **System Settings → Privacy & Security**:

- **Accessibility** — required for mouse and keyboard control
- **Screen Recording** — required for screenshots

## Running

```bash
# Run directly
.venv/bin/cu-mcp

# Or with the MCP CLI inspector (useful for debugging)
mcp dev src/cu_mcp/server.py
```

### Terminal execution logs

The server now prints live tool execution logs (start/end with timing and sanitized arguments/results) to the terminal via `stderr` while it runs. This does not interfere with MCP `stdio` responses.

Optional environment variables:

- `CU_MCP_LOG_TO_STDERR` (default `1`): set to `0` to disable terminal logs
- `CU_MCP_LOG_MAX_STRING` (default `300`): max logged string length before truncation

## Adding to Claude Code

```bash
claude mcp add cu-mcp -- /path/to/CU-MCP/.venv/bin/cu-mcp
```

## Adding to Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cu-mcp": {
      "command": "/path/to/CU-MCP/.venv/bin/cu-mcp"
    }
  }
}
```

## Project structure

```
src/cu_mcp/
  server.py   # All tools, resources, and prompts
```
