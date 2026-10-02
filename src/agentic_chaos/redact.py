"""Redact secrets from traces and reports before they leave the process.

Traces record prompts, tool arguments, HTTP headers and model output, so they can contain credentials.
Reports and trace files are redacted by default; probes always see the unredacted in-memory trace.

Two mechanisms apply:

* **keys**: the value of a mapping entry whose key names a secret (``authorization``, ``api_key``,
  ``x-api-key``, ``password``, ``cookie``, ``mcp-session-id``, ...) is replaced entirely.
* **patterns**: well-known credential formats inside any string (bearer tokens, OpenAI/Anthropic-style
  ``sk-`` keys, AWS access keys, GitHub and Slack tokens, Google API keys, JWTs, PEM private keys).

Canary tokens planted by faults are never redacted.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

__all__ = ["DEFAULT_KEY_PATTERN", "DEFAULT_PATTERNS", "REDACTED", "Redactor", "redact"]

REDACTED = "[REDACTED]"
DEFAULT_KEY_PATTERN = (
    r"^(?:.*[-_])?(?:authorization|proxy-authorization|api[-_]?key|apikey|access[-_]?token|refresh[-_]?token|"
    r"id[-_]?token|bearer|token|secret|client[-_]?secret|password|passwd|cookie|set-cookie|session[-_]?id|"
    r"credentials?|private[-_]?key)$"
)
DEFAULT_PATTERNS: dict[str, str] = {
    "private-key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    "bearer": r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}",
    "jwt": r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    "api-key": r"\bsk-[A-Za-z0-9_-]{16,}",
    "aws-access-key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "github-token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})",
    "slack-token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "google-api-key": r"\bAIza[0-9A-Za-z_-]{35}\b",
}


class Redactor:
    def __init__(
        self,
        *,
        key_pattern: str = DEFAULT_KEY_PATTERN,
        patterns: dict[str, str] | None = None,
        extra_keys: Iterable[str] = (),
        extra_patterns: Iterable[str] = (),
    ) -> None:
        extra = "|".join(re.escape(k) for k in extra_keys)
        self.key_re = re.compile(f"{key_pattern}|^(?:{extra})$" if extra else key_pattern, re.I)
        merged = dict(DEFAULT_PATTERNS if patterns is None else patterns)
        merged.update({f"custom-{i}": p for i, p in enumerate(extra_patterns)})
        self.patterns = [(name, re.compile(p)) for name, p in merged.items()]

    def text(self, value: str) -> str:
        for name, pattern in self.patterns:
            value = pattern.sub(f"[REDACTED:{name}]", value)
        return value

    def __call__(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {
                k: (REDACTED if isinstance(k, str) and self.key_re.search(k) and v not in (None, "") else self(v))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self(v) for v in value]
        return value


_default = Redactor()


def redact(value: Any, redactor: Redactor | None = None) -> Any:
    """Return a redacted copy of a JSON-like value using the default (or given) redactor."""
    return (redactor or _default)(value)
