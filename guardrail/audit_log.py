"""Permanent audit log in SQLite.

Every decision is written here: what the agent asked for, when, which
policy version was in force, which rules fired, the decision, the reasons,
and who approved or rejected it if a human was involved.

The log is also the single source of truth for balances, 24 hour totals,
and duplicate checks. Balances are never stored; they are worked out from
the payments in this log that actually moved money (ALLOWED or APPROVED).
"""

import json
import sqlite3
from datetime import datetime
from decimal import Decimal

from guardrail.checker import BLOCK, REQUIRE_APPROVAL, ALLOW, check_payment

# Decision -> starting status in the log.
STATUS_FOR_DECISION = {ALLOW: "ALLOWED", BLOCK: "BLOCKED", REQUIRE_APPROVAL: "PENDING"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    request_time        TEXT,      -- when the system received the payment (its clock, not the request's)
    decided_at          TEXT,      -- wall-clock time the checker decided
    agent_id            TEXT,      -- the agent's connection, supplied by the system
    claimed_time        TEXT,      -- the time the request claimed (recorded, never trusted)
    claimed_agent_id    TEXT,      -- the agent id the request claimed (recorded, never trusted)
    kind                TEXT,      -- 'vendor' or 'internal'
    from_account        TEXT,
    to_vendor           TEXT,
    to_vendor_account   TEXT,
    to_internal_account TEXT,
    invoice_id          TEXT,
    amount              TEXT,      -- stored as text so Decimal stays exact
    request_json        TEXT,      -- the full original request, as received
    policy_version      TEXT,
    rules_fired         TEXT,      -- JSON list of {rule_id, rule_version, outcome}
    decision            TEXT,      -- ALLOW, BLOCK, or REQUIRE_APPROVAL
    reasons             TEXT,      -- JSON list of plain English reasons
    status              TEXT,      -- ALLOWED, BLOCKED, PENDING, APPROVED, REJECTED
    reviewer            TEXT,      -- who approved or rejected, if a human did
    reviewed_at         TEXT,
    mode                TEXT       -- 'guardrail' (checker ran) or 'baseline' (no checker)
)
"""


class ApprovalBlocked(Exception):
    """Raised when a human tries to approve a payment that would now break a hard rule."""


class AuditLog:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def record(self, raw_request: dict, req: dict, policy_version: str, decision,
               mode: str = "guardrail") -> int:
        """Save one decision. Returns the new entry id.

        mode is "baseline" only for the prompt-only comparison, where payments
        are sent with no checker. Both modes log the same way so they can be compared.
        """
        def text(value):
            return None if value is None else str(value)

        cursor = self.conn.execute(
            """INSERT INTO decisions (request_time, decided_at, agent_id, claimed_time, claimed_agent_id,
                   kind, from_account, to_vendor, to_vendor_account, to_internal_account, invoice_id,
                   amount, request_json, policy_version, rules_fired, decision, reasons, status, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                req["time"].isoformat() if req["time"] else None,
                datetime.now().isoformat(timespec="seconds"),
                req["agent_id"],
                req["claimed_time"].isoformat() if req.get("claimed_time") else None,
                req.get("claimed_agent_id"),
                req["kind"], req["from_account"],
                req["to_vendor"], req["to_vendor_account"], req["to_internal_account"],
                req["invoice_id"], text(req["amount"]),
                json.dumps(raw_request, default=str),
                policy_version,
                json.dumps([{"rule_id": f.rule_id, "rule_version": f.rule_version,
                             "outcome": f.outcome} for f in decision.fired]),
                decision.decision,
                json.dumps(decision.reasons()),
                STATUS_FOR_DECISION[decision.decision],
                mode,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get(self, entry_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM decisions WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(f"No audit entry #{entry_id}")
        entry = dict(row)
        entry["request"] = json.loads(entry["request_json"])
        entry["rules_fired"] = json.loads(entry["rules_fired"])
        entry["reasons"] = json.loads(entry["reasons"])
        return entry

    def all_entries(self) -> list[dict]:
        ids = [r["id"] for r in self.conn.execute("SELECT id FROM decisions ORDER BY id")]
        return [self.get(i) for i in ids]

    def history(self, ignore_entry_id: int | None = None) -> list[dict]:
        """Past entries in the shape the rules use (Decimal amounts, datetime times)."""
        rows = self.conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()
        result = []
        for row in rows:
            if row["id"] == ignore_entry_id:
                continue
            result.append({
                "id": row["id"],
                "status": row["status"],
                "agent_id": row["agent_id"],
                "kind": row["kind"],
                "from_account": row["from_account"],
                "to_vendor": row["to_vendor"],
                "to_internal_account": row["to_internal_account"],
                "invoice_id": row["invoice_id"],
                "amount": Decimal(row["amount"]) if row["amount"] is not None else None,
                "time": datetime.fromisoformat(row["request_time"]) if row["request_time"] else None,
            })
        return result

    def _set_review(self, entry_id: int, status: str, reviewer: str):
        self.conn.execute(
            "UPDATE decisions SET status = ?, reviewer = ?, reviewed_at = ? WHERE id = ?",
            (status, reviewer, datetime.now().isoformat(timespec="seconds"), entry_id),
        )
        self.conn.commit()

    def _require_pending(self, entry_id: int, reviewer: str) -> dict:
        if not reviewer or not reviewer.strip():
            raise ValueError("A reviewer name is required.")
        entry = self.get(entry_id)
        if entry["status"] != "PENDING":
            raise ValueError(f"Entry #{entry_id} is {entry['status']}, not PENDING.")
        return entry

    def approve(self, entry_id: int, reviewer: str, data: dict):
        """A human approves an escalated payment, which moves the money.

        The hard rules are checked again first, because balances may have
        changed since the payment was escalated. A human can approve past an
        ESCALATE, but never past a BLOCK (e.g. an overdraft).
        """
        entry = self._require_pending(entry_id, reviewer)
        # No unusual-payment check (F1) here: it can only escalate, and a human
        # is already reviewing this payment.
        # Same system time and agent as the original check, so the recheck sees
        # the same 24 hour window, not whatever the request claimed.
        recheck = check_payment(
            entry["request"], data, self, ignore_entry_id=entry_id, agent_id=entry["agent_id"],
            now=datetime.fromisoformat(entry["request_time"]) if entry["request_time"] else None)
        if recheck.decision == BLOCK:
            raise ApprovalBlocked(" ".join(recheck.reasons()))
        self._set_review(entry_id, "APPROVED", reviewer.strip())

    def reject(self, entry_id: int, reviewer: str):
        """A human rejects an escalated payment. No money moves."""
        self._require_pending(entry_id, reviewer)
        self._set_review(entry_id, "REJECTED", reviewer.strip())
