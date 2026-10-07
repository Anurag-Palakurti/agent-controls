"""Tests for the audit log, balances, and human approve/reject."""

from decimal import Decimal

import pytest

from conftest import check, submit, transfer, vendor_payment
from guardrail.audit_log import ApprovalBlocked
from guardrail.checker import ALLOW, REQUIRE_APPROVAL
from guardrail.rules import current_balance


def balance(account, data, log):
    return current_balance(account, data, log.history())


def test_every_decision_is_logged_with_full_details(data, log):
    entry_id, _ = submit(vendor_payment(amount="430000.00"), data, log)
    entry = log.get(entry_id)
    assert entry["decision"] == REQUIRE_APPROVAL
    assert entry["status"] == "PENDING"
    assert entry["policy_version"] == data["policy_version"]
    assert entry["request_time"] == "2026-10-04T09:00:00"
    assert entry["request"]["invoice_text"] == "Freight services."  # full request kept for auditors
    assert {"rule_id": "1.1", "rule_version": 1, "outcome": "ESCALATE"} in entry["rules_fired"]
    assert any("Policy 1.1" in r for r in entry["reasons"])
    assert entry["reviewer"] is None


def test_blocked_and_escalated_payments_do_not_move_money(data, log):
    submit(vendor_payment(amount="430000.00"), data, log)                   # escalated
    submit(vendor_payment(to_vendor_account="SIM-0000-0000", invoice_id="X"), data, log)  # blocked
    assert balance("Operating", data, log) == Decimal("3500000.00")


def test_allowed_payment_moves_money(data, log):
    submit(vendor_payment(amount="15000.00"), data, log)
    assert balance("Operating", data, log) == Decimal("3485000.00")


def test_internal_transfer_moves_money_between_accounts(data, log):
    submit(transfer(amount="300000.00"), data, log)
    assert balance("Reserve", data, log) == Decimal("4700000.00")
    assert balance("Operating", data, log) == Decimal("3800000.00")


def test_approve_moves_money_and_records_reviewer(data, log):
    entry_id, _ = submit(vendor_payment(amount="430000.00"), data, log)
    log.approve(entry_id, "Dana Reviewer", data)
    entry = log.get(entry_id)
    assert entry["status"] == "APPROVED"
    assert entry["reviewer"] == "Dana Reviewer"
    assert entry["reviewed_at"] is not None
    assert balance("Operating", data, log) == Decimal("3070000.00")


def test_reject_does_not_move_money(data, log):
    entry_id, _ = submit(vendor_payment(amount="430000.00"), data, log)
    log.reject(entry_id, "Dana Reviewer")
    entry = log.get(entry_id)
    assert entry["status"] == "REJECTED"
    assert entry["reviewer"] == "Dana Reviewer"
    assert balance("Operating", data, log) == Decimal("3500000.00")


def test_approval_rechecks_hard_rules(data, log):
    # Two big payments are escalated while Operating has room for either one, but not both.
    first, _ = submit(vendor_payment(amount="900000.00", invoice_id="A"), data, log)
    second, _ = submit(vendor_payment(amount="900000.00", invoice_id="B"), data, log)
    log.approve(first, "Dana Reviewer", data)      # Operating: 3.5M -> 2.6M
    with pytest.raises(ApprovalBlocked, match="Policy 2.1"):
        log.approve(second, "Dana Reviewer", data)  # would leave 1.7M, below the 2M minimum
    assert log.get(second)["status"] == "PENDING"
    assert balance("Operating", data, log) == Decimal("2600000.00")


def test_only_pending_entries_can_be_reviewed(data, log):
    entry_id, decision = submit(vendor_payment(), data, log)
    assert decision.decision == ALLOW
    with pytest.raises(ValueError):
        log.approve(entry_id, "Dana Reviewer", data)
    with pytest.raises(ValueError):
        log.reject(entry_id, "Dana Reviewer")


def test_review_needs_a_reviewer_name(data, log):
    entry_id, _ = submit(vendor_payment(amount="430000.00"), data, log)
    with pytest.raises(ValueError):
        log.approve(entry_id, "  ", data)


def test_bad_request_is_still_logged(data, log):
    entry_id, decision = submit({"agent_id": "agent-1", "amount": "nonsense"}, data, log)
    assert decision.decision == REQUIRE_APPROVAL
    entry = log.get(entry_id)
    assert entry["amount"] is None
    assert entry["request"]["amount"] == "nonsense"
