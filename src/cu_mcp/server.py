from mcp.server.fastmcp import FastMCP

mcp = FastMCP("CU MCP Server - A computer-use MCP")


# --- Tools ---
# Tools let the LLM perform actions (side effects, computations, API calls).


@mcp.tool()
def hello(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"


# --- Resources ---
# Resources expose read-only data the LLM can retrieve for context.


@mcp.resource("info://status")
def get_status() -> str:
    """Return the current server status."""
    return "running"


# --- Prompts ---
# Prompts are reusable templates the LLM can invoke.


@mcp.prompt()
def summarize(text: str) -> str:
    """Create a prompt asking the LLM to summarize the given text."""
    return f"Please summarize the following text:\n\n{text}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
