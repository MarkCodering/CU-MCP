# Contributing

Thanks for contributing to `cu-mcp`.

## Scope

This project is a Model Context Protocol (MCP) server for desktop automation on macOS (screenshots, mouse, keyboard, shell tools).

## Development Setup

1. Create and activate a virtual environment.
2. Install the project and runtime dependencies.

```bash
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install mss pyautogui pyperclip
```

## Run Locally

```bash
.venv/bin/cu-mcp
```

For interactive MCP debugging:

```bash
mcp dev src/cu_mcp/server.py
```

## macOS Permissions

Grant the terminal/process these permissions in **System Settings -> Privacy & Security**:

- Accessibility
- Screen Recording

Without these permissions, mouse/keyboard actions and screenshots will fail.

## Code Guidelines

- Keep changes focused and minimal.
- Preserve MCP `stdio` behavior (do not print tool protocol output to `stdout`).
- Write operational logs to `stderr` only.
- Prefer clear tool return values (`success`, payload, and actionable errors).

## Testing / Validation

Before opening a PR, run at least:

```bash
python3 -m py_compile src/cu_mcp/server.py
```

If your change affects runtime behavior, also smoke-test the affected tool(s) locally.

## Pull Requests

- Use a clear commit message describing the functional change.
- Include a short summary of what changed and how you tested it.
- Call out any macOS-specific behavior or permission impacts.

## Reporting Issues

When reporting bugs, include:

- macOS version
- Python version
- how the server was launched (`cu-mcp`, `mcp dev`, etc.)
- exact tool used and returned error
- whether Accessibility / Screen Recording permissions were granted
