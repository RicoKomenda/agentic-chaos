"""Interop: the official A2A Python SDK (client and server) through the A2A chaos transport.

The SDK server runs in-process (ASGI); the SDK client talks to it through ``AsyncA2AChaosTransport``.
Covers the JSON-RPC and HTTP+JSON bindings, blocking and streaming calls.
"""

import asyncio

import pytest

pytest.importorskip("a2a")
httpx = pytest.importorskip("httpx")

from a2a.client import ClientConfig, ClientFactory  # noqa: E402
from a2a.helpers.proto_helpers import new_text_message  # noqa: E402
from a2a.server.agent_execution import AgentExecutor  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes, create_rest_routes  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, SendMessageRequest  # noqa: E402
from a2a.utils.constants import TransportProtocol  # noqa: E402
from google.protobuf.json_format import MessageToDict  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

from agentic_chaos import faults, probes  # noqa: E402
from agentic_chaos.integrations.a2a import AsyncA2AChaosTransport  # noqa: E402
from agentic_chaos.runtime import Session, bound  # noqa: E402

pytestmark = pytest.mark.interop
BASE = "http://merchant.test"


class Merchant(AgentExecutor):
    async def execute(self, context, event_queue):
        await event_queue.enqueue_event(new_text_message("Your cart total is 9.99.", context_id=context.context_id))

    async def cancel(self, context, event_queue):  # pragma: no cover
        raise NotImplementedError


def merchant_app() -> Starlette:
    card = AgentCard(
        name="merchant",
        description="Demo merchant agent",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(url=f"{BASE}/a2a", protocol_binding="JSONRPC", protocol_version="1.0"),
            AgentInterface(url=f"{BASE}/rest", protocol_binding="HTTP+JSON", protocol_version="1.0"),
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id="checkout", name="Checkout", description="Builds carts", tags=["shop"])],
    )
    handler = DefaultRequestHandler(agent_executor=Merchant(), task_store=InMemoryTaskStore(), agent_card=card)
    routes = [
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(handler, "/a2a"),
        *create_rest_routes(handler, path_prefix="/rest"),
    ]
    return Starlette(routes=routes)


async def converse(binding: str, streaming: bool) -> str:
    transport = AsyncA2AChaosTransport(httpx.ASGITransport(app=merchant_app()))
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
        config = ClientConfig(httpx_client=http, streaming=streaming, supported_protocol_bindings=[binding])
        client = await ClientFactory(config).create_from_url(BASE)
        request = SendMessageRequest(message=new_text_message("Quote a USB-C cable", role=1))
        texts = []
        async for response in client.send_message(request):
            texts.append(MessageToDict(response))
        return str(texts)


def run(session: Session, binding: str, streaming: bool) -> str:
    with bound(session):
        return asyncio.run(converse(binding, streaming))


BINDINGS = [TransportProtocol.JSONRPC.value, TransportProtocol.HTTP_JSON.value]


@pytest.mark.parametrize("binding", BINDINGS)
@pytest.mark.parametrize("streaming", [False, True])
def test_passthrough(binding, streaming):
    session = Session()
    assert "Your cart total is 9.99." in run(session, binding, streaming)
    assert session.trace.of("agent.discover") and session.trace.of("agent.call")


@pytest.mark.parametrize("binding", BINDINGS)
@pytest.mark.parametrize("streaming", [False, True])
def test_injection_reaches_sdk_client(binding, streaming):
    session = Session([faults.InjectInstruction("merchant.test", payload="goal_hijack")], seed=2)
    text = run(session, binding, streaming)
    assert "Ignore previous instructions" in text
    session.trace.output = text
    assert not probes.canary_not_leaked()(session.trace).passed


@pytest.mark.parametrize("binding", BINDINGS)
def test_card_spoofing_steers_the_sdk_client(binding):
    spoof = faults.SpoofAgentCard("merchant.test", set={"name": "definitely-the-merchant"})
    session = Session([spoof])
    run(session, binding, False)
    assert session.trace.of("agent.discover")[0].data["card"]["name"] == "definitely-the-merchant"


@pytest.mark.parametrize("binding", BINDINGS)
def test_agent_errors_surface_in_sdk_client(binding):
    with bound(Session([faults.Error("merchant.test", point="agent.call")])):
        try:
            asyncio.run(converse(binding, False))
        except BaseException as exc:  # noqa: BLE001 - the SDK's error type differs per binding
            assert "injected failure" in repr(exc) or "500" in repr(exc)
        else:
            pytest.fail("expected the SDK client to surface the agent error")
