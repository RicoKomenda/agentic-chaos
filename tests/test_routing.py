"""Reroute fault and the residency / model / control / approval probes."""

import json

import httpx

import agentic_chaos as chaos
from agentic_chaos import faults, probes
from agentic_chaos.integrations.httpx import ChaosTransport
from agentic_chaos.runtime import Session, Trace, bound


def provider(request: httpx.Request) -> httpx.Response:
    model = json.loads(request.content)["model"]
    return httpx.Response(200, json={"model": model, "host": request.url.host, "content": "ok"})


def ask(model: str = "primary-model") -> dict:
    client = httpx.Client(transport=ChaosTransport(httpx.MockTransport(provider)))
    return client.post("https://llm.eu.example/v1/chat", json={"model": model}).json()


def test_reroute_changes_host_and_model_and_is_recorded():
    session = Session([faults.Reroute("llm.eu.example", host="llm.us.example", model="fallback-model")])
    with bound(session):
        answer = ask()
    assert answer["host"] == "llm.us.example" and answer["model"] == "fallback-model"
    assert not probes.hosts_within(["*.eu.example"])(session.trace).passed
    assert not probes.models_within(["primary-*"])(session.trace).passed


def test_probes_pass_without_reroute():
    session = Session()
    with bound(session):
        ask()
    assert probes.hosts_within(["*.eu.example"])(session.trace).passed
    assert probes.models_within(["primary-*"])(session.trace).passed


def test_reroute_requires_a_destination():
    import pytest

    with pytest.raises(ValueError):
        faults.Reroute("x")


def test_control_invoked_before_each_matching_call():
    @chaos.control("guardrail.input")
    def guardrail(text):
        return True

    @chaos.tool(name="send_report")
    def send_report():
        return "sent"

    def guarded_twice():
        guardrail("a")
        send_report()
        guardrail("b")
        send_report()

    def guarded_once():
        guardrail("a")
        send_report()
        send_report()

    probe = probes.control_invoked("guardrail.*", before="send_*")
    for target, expected in ((guarded_twice, True), (guarded_once, False)):
        session = Session()
        with bound(session):
            target()
        assert probe(session.trace).passed is expected


def test_approved_before():
    @chaos.control("approval.purchase")
    def approve(item):
        return item != "unreviewed"

    @chaos.payment(name="processor.charge")
    def charge():
        return "ok"

    def flow(item):
        def run():
            approve(item)
            charge()

        return run

    probe = probes.approved_before("processor.*")
    for item, expected in (("reviewed", True), ("unreviewed", False)):
        session = Session()
        with bound(session):
            flow(item)()
        assert probe(session.trace).passed is expected

    session = Session([faults.ForceVerdict("approval.*", verdict=True)])
    with bound(session):
        flow("unreviewed")()
    assert probe(session.trace).passed  # a forced approval counts: pair with control-outage experiments


def test_control_invoked_reports_when_never_run():
    assert not probes.control_invoked("guardrail.*")(Trace()).passed
