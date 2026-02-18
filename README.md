# CU MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server built with the Python MCP SDK.

## Setup

```bash
# Create a virtual environment and install
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Running

```bash
# Run directly
cu-mcp

# Or with the MCP CLI inspector (useful for debugging)
mcp dev src/cu_mcp/server.py
```

## Adding to Claude Code

```bash
claude mcp add cu-mcp -- cu-mcp
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

## Project Structure

```
src/cu_mcp/
  server.py   # Server definition — tools, resources, and prompts go here
```
