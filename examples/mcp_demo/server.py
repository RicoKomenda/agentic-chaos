"""A minimal, dependency-free MCP server (stdio transport) used as the upstream in MCP chaos experiments.

Tools: ``fetch_page`` (read-only) and ``send_email`` (side effect). Resource: ``notes://alice``.
"""

from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2025-11-25"
PAGES = {"https://example.com/news": "Quarterly results are up 4%. The new office opens in March."}
TOOLS = [
    {
        "name": "fetch_page",
        "description": "Fetch a web page and return its text.",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "send_email",
        "description": "Send an email.",
        "inputSchema": {
            "type": "object",
            "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
            "required": ["to", "body"],
        },
    },
]


def handle(method: str, params: dict) -> dict:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": True}, "resources": {}},
            "serverInfo": {"name": "demo-server", "version": "0.1.0"},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        args = params.get("arguments", {})
        if params["name"] == "fetch_page":
            text = PAGES.get(args.get("url", ""), "404 not found")
        elif params["name"] == "send_email":
            text = f"sent to {args.get('to')}"
        else:
            return {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}
        return {"content": [{"type": "text", "text": text}], "isError": False}
    if method == "resources/read":
        return {"contents": [{"uri": params["uri"], "mimeType": "text/plain", "text": "Prefers short summaries."}]}
    if method == "ping":
        return {}
    raise KeyError(method)


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        message = json.loads(line)
        if "id" not in message or "method" not in message:
            continue  # notifications and responses
        try:
            reply = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": handle(message["method"], message.get("params", {})),
            }
        except KeyError:
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Method not found"}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
