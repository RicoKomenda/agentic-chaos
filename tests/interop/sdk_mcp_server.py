"""The demo tools served by the official MCP Python SDK (stdio, or Streamable HTTP with a port argument)."""

import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("sdk-demo")


@server.tool()
def fetch_page(url: str) -> str:
    """Fetch a web page and return its text."""
    return "Quarterly results are up 4%. The new office opens in March."


@server.tool()
def send_email(to: str, body: str) -> str:
    """Send an email."""
    return f"sent to {to}"


@server.resource("notes://alice")
def notes() -> str:
    return "Prefers short summaries."


if __name__ == "__main__":
    if len(sys.argv) > 1:
        server.run("streamable-http", host="127.0.0.1", port=int(sys.argv[1]))
    else:
        server.run()
