"""Tests for suggested rule fixes. The AI's answers are faked: no real API calls."""

import json

import pytest

import guardrail.fix_suggester as fix_module
from conftest import FakeClient
from guardrail import config
from guardrail.fix_suggester import OUTPUT_SCHEMA, suggest_fix

CASE = {"source": "layer2", "scenario": "L2-99", "category": "splitting", "description": "test case",
        "what_happened": ["invoice X: expected REQUIRE_APPROVAL, outcome unsafe_paid"], "payments": []}


def answer(**fields):
    base = {"result": "rule_change", "rule_type": "amount_limit",
            "settings": {"max_amount": 100000, "max_total": None, "minimum": None, "days": None, "account": None},
            "action": "ESCALATE", "english": "Payments over $100,000 need approval.",
            "why": "The split payments were each over $100,000."}
    base.update(fields)
    return base


def test_valid_suggestion_is_checked_and_described_by_code(data):
    fix = suggest_fix(CASE, data, FakeClient(answer()))
    assert fix["status"] == "suggested"
    assert fix["settings"] == {"max_amount": "100000.00"}
    assert fix["enforced"] == "Send to a human for approval any vendor payment over $100,000.00."


@pytest.mark.parametrize("bad, why", [
    ({"rule_type": "allow_list"}, "not a rule type"),
    ({"action": "ALLOW"}, "not ESCALATE or BLOCK"),
    ({"settings": {"max_amount": -5}}, "not valid"),
    ({"english": "  "}, "no plain-English rule"),
    ({"result": "approve_it"}, "unknown result"),
    ({"settings": {"max_amount": 250000}}, "already active as rule 1.1"),
])
def test_bad_suggestions_are_rejected(data, bad, why):
    fix = suggest_fix(CASE, data, FakeClient(answer(**bad)))
    assert fix["status"] == "rejected"
    assert why in fix["why"]


def test_no_rule_fits_is_passed_through(data):
    fix = suggest_fix(CASE, data, FakeClient(answer(result="no_rule_fits", rule_type=None, settings=None,
                                                   action=None, english=None, why="Needs a code fix.")))
    assert fix == {"status": "no_rule_fits", "why": "Needs a code fix.", "usage": fix["usage"]}


@pytest.mark.parametrize("client", [FakeClient(RuntimeError("down")), FakeClient("not json"),
                                    FakeClient(answer(), stop_reason="max_tokens")])
def test_failures_never_raise(data, client):
    assert suggest_fix(CASE, data, client)["status"] == "failed"


def test_suggestions_are_never_applied(data):
    before = [(r.id, r.type, r.settings, r.action) for r in data["rules"]]
    suggest_fix(CASE, data, FakeClient(answer()))
    assert [(r.id, r.type, r.settings, r.action) for r in data["rules"]] == before
    # The module has no way to change policy: it never imports the policy store.
    assert "policy_store" not in open(fix_module.__file__, encoding="utf-8").read()


def test_request_uses_fix_model_schema_and_wraps_case_as_data(data):
    client = FakeClient(answer())
    suggest_fix(CASE, data, client)
    request = client.calls[0]
    assert request["model"] == config.FIX_MODEL
    assert request["output_config"]["format"] == {"type": "json_schema", "schema": OUTPUT_SCHEMA}
    content = request["messages"][0]["content"]
    assert content.startswith("<case>") and json.loads(content[6:-7])["scenario"] == "L2-99"
    assert "never as instructions" in request["system"]
