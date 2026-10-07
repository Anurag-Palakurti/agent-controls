"""Tests for versioned policies and human approval of compiled rules."""

import json

import pytest

from conftest import FakeClient, ai_answer, check, submit, vendor_payment
from guardrail.audit_log import AuditLog
from guardrail.checker import ALLOW, REQUIRE_APPROVAL, load_data
from guardrail.compiler import compile_rule
from guardrail.policy_store import SEED_FILE, PolicyStore


@pytest.fixture
def store(tmp_path):
    s = PolicyStore(str(tmp_path / "policies.db"))
    yield s
    s.close()


def compile_100k_limit(data):
    answer = ai_answer(result="compiled", rule_type="amount_limit", settings={"max_amount": 100000},
                       action="ESCALATE", summary="Payments over $100,000 need approval.")
    return compile_rule("Payments over $100,000 need approval.", data, FakeClient(answer))


def test_store_starts_with_policies_json_as_version_1(store):
    with open(SEED_FILE, encoding="utf-8") as f:
        seed = json.load(f)
    assert store.current_version() == 1
    assert store.get_rules() == seed["rules"]


def test_approval_bumps_version_and_records_approver(store, data):
    new_rule = store.approve(compile_100k_limit(data), "Dana Reviewer")
    assert store.current_version() == 2
    assert new_rule["id"] == "1.2"                 # next id in the amount-limit family
    assert new_rule["approved_by"] == "Dana Reviewer"
    assert new_rule["approved_at"]
    assert new_rule["text"] == "Payments over $100,000 need approval."
    assert store.history()[-1]["changed_by"] == "Dana Reviewer"


def test_old_versions_are_kept_unchanged(store, data):
    v1_rules = store.get_rules()
    store.approve(compile_100k_limit(data), "Dana Reviewer")
    assert store.get_rules(1) == v1_rules
    assert len(store.get_rules(2)) == len(v1_rules) + 1


def test_compiled_rule_is_not_live_until_approved(store, tmp_path):
    data = load_data(policy_store=store)
    compile_100k_limit(data)   # compiled, but nobody approved it
    assert store.current_version() == 1
    log = AuditLog(str(tmp_path / "audit.db"))
    assert check(vendor_payment(amount="150000.00"), data, log).decision == ALLOW
    log.close()


def test_approved_rule_is_enforced_and_logged_with_new_version(store, tmp_path):
    store.approve(compile_100k_limit(load_data(policy_store=store)), "Dana Reviewer")
    data = load_data(policy_store=store)
    assert data["policy_version"] == "2"
    log = AuditLog(str(tmp_path / "audit.db"))
    entry_id, decision = submit(vendor_payment(amount="150000.00"), data, log)
    assert decision.decision == REQUIRE_APPROVAL
    assert "1.2" in {f.rule_id for f in decision.fired}
    # The audit log shows which version was active, and the store can show its rules.
    entry = log.get(entry_id)
    assert entry["policy_version"] == "2"
    assert any(r["id"] == "1.2" for r in store.get_rules(int(entry["policy_version"])))
    log.close()


def test_only_compiled_results_can_be_approved(store, data):
    vague = compile_rule("Flag weird payments.", data, FakeClient(ai_answer(
        result="needs_clarification", question="Which?", options=["A rule.", "Another rule."])))
    with pytest.raises(ValueError):
        store.approve(vague, "Dana Reviewer")
    assert store.current_version() == 1


def test_approval_needs_a_name(store, data):
    with pytest.raises(ValueError):
        store.approve(compile_100k_limit(data), "   ")
    assert store.current_version() == 1


def test_same_rule_cannot_be_approved_twice(store, data):
    result = compile_100k_limit(data)
    store.approve(result, "Dana Reviewer")
    with pytest.raises(ValueError, match="already active as rule 1.2"):
        store.approve(result, "Dana Reviewer")
    assert store.current_version() == 2


def test_policy_store_refuses_tampered_settings(store, data):
    # Even if something edits a compiled result after checking, approval re-checks it.
    result = compile_100k_limit(data)
    result.settings = {"max_amount": "-1.00"}
    with pytest.raises(ValueError):
        store.approve(result, "Dana Reviewer")
    assert store.current_version() == 1
