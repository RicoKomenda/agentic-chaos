"""Transport-agnostic MCP chaos logic, shared by the stdio and Streamable HTTP proxies.

The core maps JSON-RPC traffic onto injection points and decides what the transport should do:
forward a request (possibly several times), answer it instead of the server, reject it with an
auth error, or first send server-initiated requests to the client.

Server-initiated requests (sampling, elicitation) are delivered in the style the client speaks:

* handshake-era protocol versions (up to 2025-11-25): standalone JSON-RPC requests sent to the client
  while the call is in flight; the client's JSON-RPC responses are recorded, not forwarded.
* 2026-07-28 and later (SEP-2322 multi-round-trip): the proxy answers the call with an
  ``InputRequiredResult``; the client retries with ``inputResponses`` and the proxy's ``requestState``,
  which the proxy records and strips before forwarding the original call to the server.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from typing import Any

from agentic_chaos.faults import ChaosAuthError, ChaosError, ChaosTimeout, Override, Repeat
from agentic_chaos.runtime import intercept, record

__all__ = [
    "Decision",
    "McpChaosCore",
]

REQUEST_TIMEOUT = -32001  # code used by MCP SDKs for request timeouts
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
HANDSHAKE_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
STATE_PREFIX = "agentic-chaos:"


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
    #: A rewritten request to forward instead of the original (e.g. proxy retry state stripped).
    forward: dict[str, Any] | None = None


class McpChaosCore:
    def __init__(self) -> None:
        self._own: dict[str, str] = {}
        self._states: dict[str, dict[str, str]] = {}  # requestState -> {input key: method}
        self._dropped: set[str] = set()
        self._ids = itertools.count(1)

    # --- client -> server -------------------------------------------------------------------

    def is_own_answer(self, message: dict[str, Any]) -> bool:
        """True if ``message`` answers a request the proxy injected (it is recorded, not forwarded)."""
        if "method" in message or message.get("id") not in self._own:
            return False
        self._record_answer(self._own.pop(message["id"]), message.get("result"), message.get("error"))
        return True

    @staticmethod
    def _record_answer(method: str, result: Any, error: Any = None) -> None:
        if method == "sampling/createMessage":
            record("mcp.sampling.response", method, result=result, error=error)
        elif method == "elicitation/create":
            answer = result if isinstance(result, dict) else {}
            action = answer.get("action", "error")
            record("mcp.elicitation.response", method, action=action, content=answer.get("content"))

    def client_request(self, message: dict[str, Any]) -> Decision:
        record("mcp.request", message["method"], params=message.get("params", {}))
        if message["method"] != "tools/call":
            return Decision()
        params = message.get("params", {})
        state = params.get("requestState")
        if isinstance(state, str) and state in self._states:
            return Decision(forward=self._complete_input_round(message, state))
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
        injected = intercept("mcp.server_request", name, []) or []
        if _is_modern(message):
            notifications = [r for r in injected if r["method"].startswith("notifications/")]
            requests = [r for r in injected if not r["method"].startswith("notifications/")]
            decision.server_requests = [self._server_request(r) for r in notifications]
            if requests:
                decision.reply = self._input_required(message, requests)
                decision.duplicates = []
            return decision
        decision.server_requests = [self._server_request(r) for r in injected]
        return decision

    def _input_required(self, message: dict[str, Any], requests: list[dict[str, Any]]) -> dict[str, Any]:
        state = f"{STATE_PREFIX}{next(self._ids)}"
        keys = {f"agentic-chaos-{next(self._ids)}": r for r in requests}
        self._states[state] = {key: r["method"] for key, r in keys.items()}
        for request in requests:
            record("mcp.server_request", request["method"], params=request.get("params", {}), style="input_required")
        input_requests = {key: {"method": r["method"], "params": r.get("params", {})} for key, r in keys.items()}
        result = {"resultType": "input_required", "inputRequests": input_requests, "requestState": state}
        return _reply(message, result=result)

    def _complete_input_round(self, message: dict[str, Any], state: str) -> dict[str, Any]:
        """Record the client's answers to proxy-injected input requests; return the call without them."""
        methods = self._states.pop(state)
        params = dict(message.get("params", {}))
        responses = params.pop("inputResponses", None) or {}
        params.pop("requestState", None)
        for key, method in methods.items():
            self._record_answer(method, responses.get(key), None if key in responses else "no response")
        return {**message, "params": params}

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


def _is_modern(message: dict[str, Any]) -> bool:
    """True if the request carries a post-handshake (2026-07-28+) protocol version in its ``_meta``."""
    meta = (message.get("params") or {}).get("_meta") or {}
    version = meta.get(PROTOCOL_VERSION_META_KEY)
    return isinstance(version, str) and version not in HANDSHAKE_VERSIONS


def _reply(request: dict[str, Any], **body: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request.get("id"), **body}


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}
