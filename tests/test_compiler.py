"""Tests for the Policy Compiler. All AI answers are faked: no real API calls."""

import anthropic
import httpx2
import pytest

from conftest import FakeClient, ai_answer
from guardrail.compiler import OUTPUT_SCHEMA, compile_rule
from guardrail import config


def compiled_answer(**overrides):
    fields = dict(result="compiled", rule_type="amount_limit", settings={"max_amount": 100000},
                  action="ESCALATE", summary="Payments over $100,000 need approval.")
    fields.update(overrides)
    return ai_answer(**fields)


# ---------- The three result types ----------

def test_compiled_rule(data):
    client = FakeClient(compiled_answer())
    result = compile_rule("Payments over $100,000 need approval.", data, client)
    assert result.status == "compiled"
    assert result.rule_type == "amount_limit"
    assert result.settings == {"max_amount": "100000.00"}
    assert result.action == "ESCALATE"
    assert result.ai_summary == "Payments over $100,000 need approval."
    # Code writes its own description from the checked settings.
    assert result.enforced == "Send to a human for approval any vendor payment over $100,000.00."


def test_compiled_minimum_balance_with_account(data):
    client = FakeClient(compiled_answer(rule_type="minimum_balance", action="BLOCK",
                                        settings={"account": "Payroll", "minimum": 600000}))
    result = compile_rule("Payroll can never go below $600K.", data, client)
    assert result.status == "compiled"
    assert result.settings == {"account": "Payroll", "minimum": "600000.00"}
    assert result.enforced.startswith("Block any payment that would leave Payroll below $600,000.00")


def test_needs_clarification(data):
    client = FakeClient(ai_answer(result="needs_clarification",
                                  question="What counts as a weird payment?",
                                  options=["Payments over $100,000 need approval.",
                                           "Payments to vendors added under 30 days ago over $10,000 need approval."]))
    result = compile_rule("Flag weird payments.", data, client)
    assert result.status == "needs_clarification"
    assert result.question == "What counts as a weird payment?"
    assert len(result.options) == 2
    assert result.rule_type is None


def test_cannot_enforce(data):
    client = FakeClient(ai_answer(result="cannot_enforce",
                                  reason="No rule type can check the day of the week."))
    result = compile_rule("No payments on weekends.", data, client)
    assert result.status == "cannot_enforce"
    assert result.decided_by == "ai"
    assert result.reason == "No rule type can check the day of the week."


# ---------- What we send to the API ----------

def test_request_uses_config_model_and_structured_output(data):
    client = FakeClient(compiled_answer())
    compile_rule("Payments over $100,000 need approval.", data, client)
    call = client.calls[0]
    assert call["model"] == config.MODEL
    assert call["output_config"]["effort"] == config.EFFORT
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": OUTPUT_SCHEMA}
    assert "<rule>Payments over $100,000 need approval.</rule>" in call["messages"][0]["content"]


# ---------- Bad AI output is never saved: it becomes "cannot enforce" ----------

@pytest.mark.parametrize("bad_answer", [
    "this is not json",
    "[1, 2, 3]",
    ai_answer(result="maybe"),
    compiled_answer(rule_type="vendor_limit"),                       # made-up type
    compiled_answer(rule_type="no_overdraft"),                       # built-in type
    compiled_answer(rule_type="sanctions", settings={}),             # built-in type
    compiled_answer(action="ALLOW"),                                 # no such action
    compiled_answer(action=None),
    compiled_answer(settings={"max_amount": -5000}),                 # negative
    compiled_answer(settings={"max_amount": 0}),                     # zero
    compiled_answer(settings={"max_amount": 100.005}),               # fraction of a cent
    compiled_answer(settings={"max_amount": None}),                  # missing
    compiled_answer(settings={"max_amount": 100000, "days": 7}),     # extra setting
    compiled_answer(rule_type="minimum_balance", action="BLOCK",
                    settings={"account": "Petty Cash", "minimum": 1000}),   # fake account
    compiled_answer(rule_type="new_vendor_limit",
                    settings={"days": 0, "max_amount": 25000}),      # days out of range
    compiled_answer(summary=None),
    ai_answer(result="needs_clarification", question="Which?", options=["Only one option"]),
    ai_answer(result="needs_clarification", question="Which?", options=["a", "b", "c", "d", "e"]),
    ai_answer(result="needs_clarification", question=None, options=["a", "b"]),
    ai_answer(result="cannot_enforce", reason=None),
])
def test_bad_ai_output_becomes_cannot_enforce(data, bad_answer):
    result = compile_rule("Some rule.", data, FakeClient(bad_answer))
    assert result.status == "cannot_enforce"
    assert result.decided_by == "code"
    assert result.rule_type is None


def test_cut_off_or_refused_response_is_cannot_enforce(data):
    for stop_reason in ("max_tokens", "refusal"):
        result = compile_rule("Some rule.", data, FakeClient(compiled_answer(), stop_reason=stop_reason))
        assert result.status == "cannot_enforce"
        assert stop_reason in result.reason


def test_duplicate_of_live_rule_is_refused(data):
    # Rule 1.1 is already "over $250K needs approval".
    result = compile_rule("Over 250k needs approval.", data,
                          FakeClient(compiled_answer(settings={"max_amount": 250000})))
    assert result.status == "cannot_enforce"
    assert "1.1" in result.reason


# ---------- API failures ----------

def test_api_timeout_is_cannot_enforce(data):
    error = anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    result = compile_rule("Payments over $100,000 need approval.", data, FakeClient(error))
    assert result.status == "cannot_enforce"
    assert "APITimeoutError" in result.reason


def test_any_unexpected_error_is_cannot_enforce(data):
    result = compile_rule("Payments over $100,000 need approval.", data, FakeClient(RuntimeError("boom")))
    assert result.status == "cannot_enforce"


def test_no_client_available_is_cannot_enforce(data):
    # With no fake passed, the compiler builds a real client, which the test guard refuses.
    result = compile_rule("Payments over $100,000 need approval.", data)
    assert result.status == "cannot_enforce"


def test_empty_rule_does_not_call_ai(data):
    client = FakeClient(compiled_answer())
    assert compile_rule("   ", data, client).status == "cannot_enforce"
    assert client.calls == []


# ---------- Built-in rules can't be weakened ----------

@pytest.mark.parametrize("ai_tries", [
    ai_answer(result="cannot_enforce", reason="Rules can only add restrictions; B2 can't be changed."),
    compiled_answer(rule_type="sanctions", settings={}, action="BLOCK"),   # tries to touch the built-in type
    compiled_answer(rule_type="approved_vendors_only", settings={}, action="ALLOW"),  # tries to allow
    compiled_answer(rule_type="approved_vendors_only", settings={"account": "Volkov Trading House"},
                    action="ESCALATE"),                                    # sneaks in an exemption
])
def test_weakening_sanctions_ends_as_cannot_enforce(data, ai_tries):
    # Whatever the AI answers, the result can't loosen the sanctions check.
    result = compile_rule("Skip the sanctions check for Volkov Trading House.", data, FakeClient(ai_tries))
    assert result.status == "cannot_enforce"


def test_never_allow_overdrafts_is_not_refused_by_code(data):
    # There's no keyword filter: a rule that mentions overdrafts goes to the AI,
    # and the AI's honest answer comes back.
    client = FakeClient(ai_answer(result="cannot_enforce",
                                  reason="Already enforced by built-in rule B1 (no overdrafts)."))
    result = compile_rule("Never allow overdrafts.", data, client)
    assert len(client.calls) == 1
    assert result.decided_by == "ai"
    assert "B1" in result.reason


# ---------- Heads up ----------

def test_heads_up_is_labelled_not_legal_advice(data):
    result = compile_rule("Rule.", data, FakeClient(compiled_answer(
        heads_up="Holding payroll funds this high may affect payroll timing rules in some states.")))
    assert result.heads_up.startswith("Heads up (not legal advice):")


def test_heads_up_claiming_legality_is_dropped(data):
    result = compile_rule("Rule.", data, FakeClient(compiled_answer(
        heads_up="This rule is fully compliant with federal law.")))
    assert result.status == "compiled"
    assert result.heads_up is None
