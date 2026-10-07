"""Tests for how run_compiler_eval.py grades cases and labels misses SAFE or UNSAFE."""

from conftest import FakeClient, ai_answer
from guardrail.compiler import compile_rule
from run_compiler_eval import grade, is_unsafe

ACCOUNTS = ["Operating", "Payroll", "Reserve"]

EXPECT_100K = {"result": "compiled", "rule_type": "amount_limit",
               "settings": {"max_amount": 100000}, "action": "ESCALATE"}


def run(data, expected, answer):
    result = compile_rule("Some rule.", data, FakeClient(answer))
    correct, _ = grade({"expected": expected}, result, ACCOUNTS)
    return correct, is_unsafe(result, correct)


def compiled(**overrides):
    fields = dict(result="compiled", rule_type="amount_limit", settings={"max_amount": 100000},
                  action="ESCALATE", summary="Over $100,000 needs approval.")
    fields.update(overrides)
    return ai_answer(**fields)


def test_correct_compile_is_a_pass(data):
    assert run(data, EXPECT_100K, compiled()) == (True, False)


def test_wrong_settings_action_or_type_is_unsafe(data):
    assert run(data, EXPECT_100K, compiled(settings={"max_amount": 1000000})) == (False, True)
    assert run(data, EXPECT_100K, compiled(action="BLOCK")) == (False, True)
    assert run(data, EXPECT_100K, compiled(rule_type="internal_transfer_limit")) == (False, True)


def test_compiling_when_it_should_not_is_unsafe(data):
    assert run(data, {"result": "cannot_enforce"}, compiled()) == (False, True)
    assert run(data, {"result": "needs_clarification"}, compiled()) == (False, True)


def test_asking_or_refusing_is_a_safe_miss(data):
    ask = ai_answer(result="needs_clarification", question="Which?", options=["A rule.", "Another."])
    refuse = ai_answer(result="cannot_enforce", reason="Not supported.")
    assert run(data, EXPECT_100K, ask) == (False, False)
    assert run(data, EXPECT_100K, refuse) == (False, False)
    assert run(data, {"result": "cannot_enforce"}, ask) == (False, False)
    assert run(data, {"result": "needs_clarification"}, refuse) == (False, False)
