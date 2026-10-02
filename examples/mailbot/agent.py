"""Mailbot: a tiny, deterministic agent used to demonstrate Agentic Chaos without any API key.

The "model" is a scripted stand-in that, like many real models, follows instructions it finds in
its context. Three variants show how the same experiments separate weak from strong designs:

* ``naive``             - no guardrail, no error handling
* ``guarded_fail_open`` - has an injection guardrail that only inspects the first 2,000 characters,
                          and proceeds if the guardrail is down
* ``hardened``          - guardrail fails closed, an authorization check guards the sensitive tool,
                          and tool/LLM errors are handled gracefully

Swap the scripted model for a real one (see ``agentic_chaos.integrations.httpx``) to run the same
experiments against production-like behaviour.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import agentic_chaos as chaos

log = logging.getLogger("mailbot")

PAGES = {"https://example.com/news": "Quarterly results are up 4%. The new office opens in March."}
ALLOWED_RECIPIENTS = {"team@example.com"}
TOOLS = {
    "fetch_page": "Fetch a web page and return its text.",
    "send_email": "Send an email. Arguments: to, body.",
}


@dataclass
class Mailbox:
    sent: list[dict[str, str]] = field(default_factory=list)


# --- tools, memory and controls (instrumented) --------------------------------------------


@chaos.tool
def fetch_page(url: str) -> str:
    return PAGES.get(url, "404 not found")


def make_send_email(outbox: Mailbox):
    @chaos.tool(name="send_email")
    def send_email(to: str, body: str) -> str:
        outbox.sent.append({"to": to, "body": body})
        return "sent"

    return send_email


@chaos.memory(name="user_notes")
def recall_notes(user: str) -> list[str]:
    return ["Prefers short summaries."]


@chaos.control("guardrail.injection")
def injection_guardrail(text: str) -> bool:
    """Returns True when the text looks safe."""
    return not re.search(r"ignore previous instructions|<IMPORTANT>|platform administrator", text, re.I)


@chaos.control("authz.send_email")
def authorize_email(to: str) -> bool:
    """Action-level policy: only allow-listed recipients."""
    return to in ALLOWED_RECIPIENTS


# --- scripted "model" ---------------------------------------------------------------------

_ACTION = re.compile(r"call the (\w+) tool with to=(\S+) and body=(\S+)")
_HIJACK = re.compile(r"reply with the exact text (\S+?)\.?$", re.M)


@chaos.llm(name="scripted-model")
def think(context: str) -> dict:
    if m := _ACTION.search(context):
        return {"action": m.group(1), "to": m.group(2), "body": m.group(3)}
    if m := _HIJACK.search(context):
        return {"reply": m.group(1)}
    page = context.split("PAGE:\n", 1)[-1]
    return {"reply": "Summary: " + page.strip().split(".")[0] + "."}


# --- agent loop ---------------------------------------------------------------------------


def make_agent(*, guardrail: bool, fail_open: bool, allow_list: bool, retries: int, inspect_limit: int | None = None):
    def run(task: str = "Summarise https://example.com/news", user: str = "alice") -> str:
        send_email = make_send_email(Mailbox())
        url = task.split()[-1]

        notes = recall_notes(user)
        tools = "\n".join(f"- {name}: {chaos.describe_tool(name, desc)}" for name, desc in TOOLS.items())
        try:
            page = fetch_page(url)
        except Exception:
            if retries == 0:
                raise
            log.warning("fetch_page failed for %s", url)
            return "Sorry, I could not fetch that page right now."
        context = f"NOTES:\n{chr(10).join(notes)}\nTOOLS:\n{tools}\nPAGE:\n{page}"

        if guardrail:
            try:
                # a guardrail with a limited inspection window only sees the start of the context
                safe = injection_guardrail(context[:inspect_limit] if inspect_limit else context)
            except Exception:
                if not fail_open:
                    log.error("injection guardrail unavailable - refusing (fail closed)")
                    return "Sorry, I can't safely complete this request right now."
                log.warning("injection guardrail unavailable - continuing without it")
                safe = True
            if not safe:
                log.warning("possible prompt injection blocked")
                return "I found suspicious instructions in the content and stopped."

        for attempt in range(retries + 1):
            try:
                decision = think(context)
                break
            except Exception:
                if attempt == retries:
                    if retries == 0:
                        raise
                    log.error("model unavailable after %d attempts", attempt + 1)
                    return "The assistant is temporarily unavailable."
        if decision.get("action") == "send_email":
            if allow_list:
                try:
                    allowed = authorize_email(decision["to"])
                except Exception:
                    allowed = False
                if not allowed:
                    log.warning("blocked email to non-allow-listed recipient %s", decision["to"])
                    return "I can't send email to that recipient."
            send_email(to=decision["to"], body=decision["body"])
            return "Done."
        return decision.get("reply", "")

    return run


naive = make_agent(guardrail=False, fail_open=True, allow_list=False, retries=0)
guarded_fail_open = make_agent(guardrail=True, fail_open=True, allow_list=False, retries=2, inspect_limit=2000)
hardened = make_agent(guardrail=True, fail_open=False, allow_list=True, retries=2)
