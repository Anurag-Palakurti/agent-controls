"""Shared test setup: fresh company data and a fresh audit log for every test."""

import copy
import json
from datetime import datetime
from types import SimpleNamespace

import anthropic
import pytest

from guardrail.audit_log import AuditLog
from guardrail.checker import check_payment, load_data, submit_payment

_DATA = load_data()


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    """Tests must never call the real API: building a real client fails loudly."""
    def refuse(*args, **kwargs):
        raise RuntimeError("Tests must not create a real Anthropic client. Pass a FakeClient.")
    monkeypatch.setattr(anthropic, "Anthropic", refuse)


class FakeClient:
    """Stands in for anthropic.Anthropic. Returns a canned answer and records each call.

    `answer` can be a dict (sent back as JSON text), a raw string (for bad JSON),
    or an exception instance (raised, like a failed API call).
    """
    def __init__(self, answer, stop_reason="end_turn"):
        self.answer = answer
        self.stop_reason = stop_reason
        self.calls = []
        self.messages = self   # so client.messages.create(...) works
        self.options = {}

    def with_options(self, **options):
        # Like the real client: per-call settings such as timeout. Recorded for tests.
        self.options = options
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.answer, Exception):
            raise self.answer
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=text)])


def ai_answer(**fields):
    """A full AI answer in the schema's shape: every field present, unused ones null."""
    answer = {"result": None, "rule_type": None, "settings": None, "action": None,
              "summary": None, "question": None, "options": None, "reason": None,
              "heads_up": None}
    settings = {"max_amount": None, "max_total": None, "minimum": None, "days": None, "account": None}
    if "settings" in fields:
        settings.update(fields.pop("settings"))
        answer["settings"] = settings
    answer.update(fields)
    return answer


@pytest.fixture
def data():
    # A deep copy, so a test that changes a balance can't affect other tests.
    return copy.deepcopy(_DATA)


@pytest.fixture
def log(tmp_path):
    audit = AuditLog(str(tmp_path / "audit.db"))
    yield audit
    audit.close()


def vendor_payment(**overrides):
    """A valid $15K payment to a long-time approved vendor. Tests change one thing at a time."""
    request = {
        "agent_id": "agent-1",
        "from_account": "Operating",
        "to_vendor": "Three Rivers Freight",
        "to_vendor_account": "SIM-1003-3392",
        "amount": "15000.00",
        "invoice_id": "TRF-1000",
        "invoice_text": "Freight services.",
        "time": "2026-10-04T09:00:00",
    }
    request.update(overrides)
    return request


def transfer(**overrides):
    """A valid internal transfer from Reserve to Operating."""
    request = {
        "agent_id": "agent-1",
        "from_account": "Reserve",
        "to_internal_account": "Operating",
        "amount": "300000.00",
        "time": "2026-10-04T09:00:00",
    }
    request.update(overrides)
    return request


# ---------- the system around the checker ----------
# The checker takes the time and the agent's identity from the system calling it,
# never from the request. Most tests send honest requests, so these helpers act as
# that system: they use the request's own time and agent id when those are usable.

TEST_NOW = datetime(2026, 10, 4, 9, 0)


def system_for(request) -> dict:
    """The clock and connection an honest system would supply for this request."""
    now, agent = TEST_NOW, "agent-1"
    if isinstance(request, dict):
        try:
            parsed = datetime.fromisoformat(request.get("time"))
            if parsed.tzinfo is None:
                now = parsed
        except (TypeError, ValueError):
            pass
        if isinstance(request.get("agent_id"), str) and request["agent_id"].strip():
            agent = request["agent_id"]
    return {"now": now, "agent_id": agent}


def check(request, data, log, **kwargs):
    """check_payment, with the system's time and connection taken from an honest request."""
    return check_payment(request, data, log, **system_for(request), **kwargs)


def submit(request, data, log, unusual_client=None):
    """submit_payment, with the system's time and connection taken from an honest request."""
    return submit_payment(request, data, log, unusual_client, **system_for(request))
