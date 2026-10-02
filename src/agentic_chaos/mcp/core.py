"""Transport-agnostic MCP chaos logic, shared by the stdio and Streamable HTTP proxies.

The core maps JSON-RPC traffic onto injection points and decides what the transport should do:
forward a request (possibly several times), answer it instead of the server, reject it with an
auth error, or first send server-initiated requests to the client.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Any

from agentic_chaos.faults import ChaosAuthError, ChaosError, ChaosTimeout, Override, Repeat
from agentic_chaos.runtime import intercept, record

REQUEST_TIMEOUT = -32001  # code used by MCP SDKs for request timeouts


@dataclass
class Decision:
    """What to do with a client request."""

    #: Answer the client with this message instead of forwarding the request.
    reply: dict[str, Any] | None = None
    #: Credentials failure. HTTP transports answer 401/403; stdio falls back to ``reply``.
    auth: ChaosAuthError | None = None
    #: Server-initiated requests/notifications to send to the client before the server's answer.
    server_requests: list[dict[str, Any]] = field(default_factory=list)
    #: Extra copies of the request to deliver to the server (their responses are dropped).
    duplicates: list[dict[str, Any]] = field(default_factory=list)


class McpChaosCore:
    def __init__(self) -> None:
        self._own: dict[str, str] = {}
        self._dropped: set[str] = set()
        self._ids = itertools.count(1)

    # --- client -> server -------------------------------------------------------------------

    def is_own_answer(self, message: dict[str, Any]) -> bool:
        """True if ``message`` answers a request the proxy injected (it is recorded, not forwarded)."""
        if "method" in message or message.get("id") not in self._own:
            return False
        method = self._own.pop(message["id"])
        if method == "sampling/createMessage":
            record("mcp.sampling.response", method, result=message.get("result"), error=message.get("error"))
        elif method == "elicitation/create":
            result = message.get("result") or {}
            action = result.get("action", "error")
            record("mcp.elicitation.response", method, action=action, content=result.get("content"))
        return True

    def client_request(self, message: dict[str, Any]) -> Decision:
        record("mcp.request", message["method"], params=message.get("params", {}))
        if message["method"] != "tools/call":
            return Decision()
        params = message.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        record("tool.call", name, kwargs=arguments)
        decision = Decision()
        try:
            intercept("tool.call", name, None, arguments=arguments)
        except Override as forced:
            return Decision(reply=_reply(message, result=_text_result(str(forced.value))))
        except Repeat as repeat:
            decision.duplicates = [self._duplicate(message) for _ in range(repeat.times)]
        except ChaosAuthError as exc:
            return Decision(reply=_reply(message, result=_text_result(f"Error: {exc}", True)), auth=exc)
        except ChaosTimeout as exc:
            return Decision(reply=_reply(message, error={"code": REQUEST_TIMEOUT, "message": str(exc)}))
        except ChaosError as exc:
            return Decision(reply=_reply(message, result=_text_result(f"Error: {exc}", True)))
        decision.server_requests = [self._server_request(r) for r in intercept("mcp.server_request", name, []) or []]
        return decision

    def _duplicate(self, message: dict[str, Any]) -> dict[str, Any]:
        request_id = f"agentic-chaos-dup-{next(self._ids)}"
        self._dropped.add(request_id)
        params = message.get("params", {})
        record("tool.call", params.get("name", ""), kwargs=params.get("arguments", {}), duplicate=True)
        return {**message, "id": request_id}

    def _server_request(self, request: dict[str, Any]) -> dict[str, Any]:
        if request["method"].startswith("notifications/"):
            record("mcp.notification", request["method"])
            return {"jsonrpc": "2.0", **request}
        request_id = f"agentic-chaos-{next(self._ids)}"
        self._own[request_id] = request["method"]
        record("mcp.server_request", request["method"], params=request.get("params", {}))
        return {"jsonrpc": "2.0", "id": request_id, **request}

    # --- server -> client -------------------------------------------------------------------

    def is_dropped(self, message: dict[str, Any]) -> bool:
        """True for server responses to duplicate deliveries (the client never sent those)."""
        if "method" not in message and message.get("id") in self._dropped:
            self._dropped.discard(message["id"])
            return True
        return False

    def server_response(self, method: str, params: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._transform(method, params, message)
        except ChaosTimeout as exc:
            return _reply(message, error={"code": REQUEST_TIMEOUT, "message": str(exc)})

    def _transform(self, method: str, params: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        result = message.get("result")
        if not isinstance(result, dict):
            return message
        if method == "tools/list":
            tools = [dict(t) for t in result.get("tools", [])]
            for tool in tools:
                tool["description"] = intercept("tool.describe", tool["name"], tool.get("description", ""))
            tools = intercept("mcp.tools", "tools/list", tools)
            for tool in tools:
                record("tool.describe", tool["name"], description=tool.get("description", ""))
            result = {**result, "tools": tools}
        elif method == "tools/call":
            name = params.get("name", "")
            content = result.get("content", [])
            text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
            changed = intercept("tool.result", name, text)
            if changed != text:
                others = [c for c in content if c.get("type") != "text"]
                new_text = changed if isinstance(changed, str) else json.dumps(changed)
                result = {**result, "content": [{"type": "text", "text": new_text}, *others]}
            record("tool.call.result", name, result=changed)
        elif method == "resources/read":
            contents = []
            for item in result.get("contents", []):
                if "text" in item:
                    item = {**item, "text": intercept("resource.read", item.get("uri", ""), item["text"])}
                    record("resource.read", item.get("uri", ""), text=item["text"])
                contents.append(item)
            result = {**result, "contents": contents}
        return {**message, "result": result}


def _reply(request: dict[str, Any], **body: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request.get("id"), **body}


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}
