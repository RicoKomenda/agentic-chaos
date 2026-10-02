"""The demo MCP server over Streamable HTTP (JSON responses), stdlib only.

python examples/mcp_demo/http_server.py 8000   # serves http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from server import handle  # same tools and resources as the stdio server


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        message = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if "id" not in message or "method" not in message:
            self.send_response(202)
            self.end_headers()
            return
        try:
            reply = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": handle(message["method"], message.get("params", {})),
            }
        except KeyError:
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Method not found"}}
        body = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("mcp-session-id", "demo-session")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8000), Handler).serve_forever()
