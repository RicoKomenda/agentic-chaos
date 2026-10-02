"""Shopper: a multi-agent, AP2-style purchase flow for chaos experiments. Deterministic, no API key.

    user intent -> shopping agent --(discover card, A2A messages)--> merchant agent
                                  --(Payment Mandate)--> credentials provider / processor

The merchant agent and the processor are simulated in-process but instrumented exactly like remote
services (``agent.discover``, ``agent.call``/``agent.message``, ``payment.call``/``payment.result``).
The scripted model follows instructions found in merchant messages, like many real models do.

* ``naive``    - trusts whatever card discovery returns, follows merchant instructions, pays the
                 final cart without comparing it to what the user reviewed, retries payments with a
                 new mandate id, follows up to 20 delegation requests
* ``hardened`` - handles unreachable agents, verifies the merchant card (trusted URL + required
                 AP2 extension), enforces the Intent Mandate before signing, pays only the reviewed
                 cart, retries idempotently (same mandate id), caps agent follow-ups at 2

All prices, merchants and identifiers are made up; nothing here touches a real payment system.
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass
from typing import Any

import agentic_chaos as chaos
from agentic_chaos import ap2

log = logging.getLogger("shopper")

#: Illustrative extension identifier for this demo; real deployments use the AP2 extension URI they pin.
AP2_EXTENSION = "https://ap2.example/extensions/ap2/v1"
LEGACY_AP2_EXTENSION = "https://ap2.example/extensions/ap2/v0"
MERCHANT_URL = "https://merchant.example/a2a"
TRUSTED_MERCHANTS = {MERCHANT_URL: "acme-merchant"}
MERCHANT_CARD = {
    "name": "acme-merchant",
    "url": MERCHANT_URL,
    "skills": [{"id": "catalog"}, {"id": "checkout"}],
    "capabilities": {"extensions": [{"uri": AP2_EXTENSION, "required": True}]},
}
CATALOG = {"usb-c-cable": {"sku": "usb-c-cable", "name": "USB-C cable", "price": 9.99}}


@dataclass(frozen=True)
class Policy:
    verify_card: bool
    enforce_intent: bool
    pay_reviewed_only: bool
    idempotent_retry: bool
    max_followups: int
    handle_agent_errors: bool


# --- simulated remote parties (instrumented as remote calls) ------------------------------


@chaos.agent(name_arg="url")
def call_merchant(url: str, message: dict[str, Any]) -> dict[str, Any]:
    """A2A message to a merchant agent. Unknown URLs reach a rogue merchant (e.g. after card spoofing)."""
    merchant = TRUSTED_MERCHANTS.get(url, "rogue-shop")
    if message["op"] == "finalize":
        return {"text": "Cart finalized.", "cart": message["cart"]}
    item = {**CATALOG[message["sku"]], "qty": 1}
    cart = {"id": "cart-1", "merchant": merchant, "items": [item], "total": item["price"], "currency": "USD"}
    return {"text": f"Here is your cart from {merchant}.", "cart": cart}


class Processor:
    """Credentials provider + payment processor. Deduplicates by Payment Mandate id, like a real processor."""

    def __init__(self) -> None:
        self.settled: dict[str, dict[str, Any]] = {}

    def charge_fn(self):
        @chaos.payment(name="processor.charge")
        def charge(mandate: dict[str, Any]) -> dict[str, Any]:
            if mandate["id"] not in self.settled:
                self.settled[mandate["id"]] = {"status": "settled", "amount": mandate["cart"]["total"]}
                ap2.record_settlement(mandate["id"], mandate["cart"]["total"])
            return {"receipt": f"rcpt-{mandate['id']}", **self.settled[mandate["id"]]}

        return charge


# --- scripted "model" ---------------------------------------------------------------------

_UPSELL = re.compile(r"[Aa]dd sku (\S+) priced ([\d.]+)")
_DELEGATE = re.compile(r"ask the (\S+) agent to handle this request again")


@chaos.llm(name="scripted-model")
def plan(reply: dict[str, Any]) -> dict[str, Any]:
    """Decide what to do with a merchant reply. Follows instructions found in it."""
    text = " ".join(str(v) for k, v in reply.items() if k != "cart")
    cart = dict(reply["cart"], items=list(reply["cart"]["items"]))
    if m := _UPSELL.search(text):
        cart["items"].append(
            {"sku": m.group(1).rstrip("."), "name": "Premium upgrade", "price": float(m.group(2)), "qty": 1}
        )
        cart["total"] = round(sum(i["price"] * i["qty"] for i in cart["items"]), 2)
    return {"cart": cart, "follow_up": bool(_DELEGATE.search(text)), "note": text}


# --- shopping agent -----------------------------------------------------------------------


def make_shopper(policy: Policy):
    def run(request: str = "Buy a USB-C cable", budget: float = 20.0) -> str:
        intent = {"description": request, "max_amount": budget, "merchants": ["acme-merchant"], "skus": ["usb-c-cable"]}
        ap2.record_intent(intent)
        ids = itertools.count(1)
        charge = Processor().charge_fn()

        card = chaos.discover_agent("merchant", dict(MERCHANT_CARD))
        if policy.verify_card:
            extensions = {e.get("uri") for e in card.get("capabilities", {}).get("extensions", [])}
            if card.get("url") not in TRUSTED_MERCHANTS or AP2_EXTENSION not in extensions:
                log.warning("merchant card failed verification: %s", card.get("url"))
                return "I couldn't verify this merchant, so I stopped."

        try:
            decision = plan(call_merchant(card["url"], {"op": "quote", "sku": "usb-c-cable"}))
        except Exception:
            if not policy.handle_agent_errors:
                raise
            log.warning("merchant agent unavailable")
            return "The merchant is not reachable right now; nothing was bought."
        followups = 0
        while decision["follow_up"] and followups < policy.max_followups:
            followups += 1
            decision = plan(call_merchant(card["url"], {"op": "quote", "sku": "usb-c-cable", "note": decision["note"]}))
        cart = decision["cart"]

        if policy.enforce_intent:
            skus = {i["sku"] for i in cart["items"]}
            if cart["total"] > budget or cart["merchant"] not in intent["merchants"] or skus - set(intent["skus"]):
                log.warning("cart violates the intent mandate: %s", cart)
                return "The proposed cart doesn't match what you asked for, so I didn't buy anything."

        ap2.record_review(cart)  # shown to and approved by the user
        final = call_merchant(card["url"], {"op": "finalize", "cart": cart})["cart"]
        if policy.pay_reviewed_only and ap2.cart_hash(final) != ap2.cart_hash(cart):
            log.warning("cart changed after review - not paying")
            return "The merchant changed the cart after you approved it, so I stopped."

        mandate = {"id": f"pm-{next(ids)}", "cart": final}
        for attempt in range(3):
            if attempt and not policy.idempotent_retry:
                mandate = {**mandate, "id": f"pm-{next(ids)}"}  # a "fresh" retry: new mandate, new charge
            ap2.record_payment_mandate(mandate)
            try:
                receipt = charge(mandate=mandate)
                total = f"{final['total']} {final['currency']}"
                return f"Bought {len(final['items'])} item(s) for {total} ({receipt['receipt']})."
            except Exception:
                log.warning("payment attempt %d failed", attempt + 1)
        return "Payment failed, nothing was charged."

    return run


naive = make_shopper(
    Policy(
        verify_card=False,
        enforce_intent=False,
        pay_reviewed_only=False,
        idempotent_retry=False,
        max_followups=20,
        handle_agent_errors=False,
    )
)
hardened = make_shopper(
    Policy(
        verify_card=True,
        enforce_intent=True,
        pay_reviewed_only=True,
        idempotent_retry=True,
        max_followups=2,
        handle_agent_errors=True,
    )
)
