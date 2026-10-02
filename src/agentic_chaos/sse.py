"""Minimal Server-Sent Events parsing and formatting, shared by the MCP HTTP proxy and the A2A transport."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    data: str = ""
    event: str | None = None
    id: str | None = None
    extra: list[str] = field(default_factory=list)  # comments, retry: and unknown fields, kept verbatim

    def json(self) -> Any:
        return json.loads(self.data)

    def encode(self) -> bytes:
        lines = list(self.extra)
        if self.event is not None:
            lines.append(f"event: {self.event}")
        if self.id is not None:
            lines.append(f"id: {self.id}")
        lines.extend(f"data: {line}" for line in self.data.split("\n"))
        return ("\n".join(lines) + "\n\n").encode()


def encode_json(message: Any, event: str | None = None, id: str | None = None) -> bytes:
    return Event(json.dumps(message, separators=(",", ":")), event, id).encode()


class Parser:
    """Incremental parser: feed bytes, get complete events."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: bytes) -> Iterator[Event]:
        self._buffer += chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")
        while "\n\n" in self._buffer:
            block, self._buffer = self._buffer.split("\n\n", 1)
            if block.strip():
                yield _parse(block)

    def flush(self) -> Iterator[Event]:
        if self._buffer.strip():
            yield _parse(self._buffer)
        self._buffer = ""


def _parse(block: str) -> Event:
    event = Event()
    data: list[str] = []
    for line in block.split("\n"):
        key, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if key == "data":
            data.append(value)
        elif key == "event":
            event.event = value
        elif key == "id":
            event.id = value
        else:
            event.extra.append(line)
    event.data = "\n".join(data)
    return event


def transform_json(event: Event, fn: Callable[[Any], Any]) -> Event:
    """Apply ``fn`` to an event's JSON payload; non-JSON events pass through unchanged."""
    try:
        payload = event.json()
    except ValueError:
        return event
    return Event(json.dumps(fn(payload), separators=(",", ":")), event.event, event.id, event.extra)
