"""Push the sample invoices through the checker and print every decision.

There is no AI agent yet (that's milestone 3). This script plays the agent:
it turns each invoice into a payment request exactly as written, with no
judgment, and lets the checker decide. A fake human reviewer approves one
escalated payment to show money only moves after approval.

Run:  python run_demo.py
"""

import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from guardrail.audit_log import AuditLog
from guardrail.checker import DATA_DIR, REQUIRE_APPROVAL, load_data, submit_payment
from guardrail.rules import current_balance, money

DEMO_DB = Path(__file__).resolve().parent / "demo_audit.db"
AGENT = "treasury-agent-1"
REVIEWER = "Dana Whitfield (Treasury Manager)"   # made-up person
APPROVE_INVOICES = {"ASS-5520"}                  # escalations the reviewer approves in this demo


def invoice_to_request(invoice: dict) -> dict:
    return {
        "agent_id": AGENT,
        "from_account": "Operating",
        "to_vendor": invoice["vendor"],
        "to_vendor_account": invoice["account_number"],
        "amount": invoice["amount"],
        "invoice_id": invoice["invoice_id"],
        "invoice_text": invoice["text"],
        "time": invoice["received_at"],
    }


# Two internal transfers, to show rule 7.1.
TRANSFERS = [
    {"agent_id": AGENT, "from_account": "Reserve", "to_internal_account": "Operating",
     "amount": "300000.00", "time": "2026-10-04T12:00:00"},
    {"agent_id": AGENT, "from_account": "Operating", "to_internal_account": "Payroll",
     "amount": "600000.00", "time": "2026-10-04T12:30:00"},
]


def describe(request: dict) -> str:
    amount = money(Decimal(request["amount"]))
    if "to_vendor" in request:
        return f"{amount} to {request['to_vendor']} (invoice {request['invoice_id']}, {request['time']})"
    return f"{amount} transfer {request['from_account']} -> {request['to_internal_account']} ({request['time']})"


def main():
    # Fresh log every run, so the demo always tells the same story.
    if DEMO_DB.exists():
        DEMO_DB.unlink()
    data = load_data()
    log = AuditLog(str(DEMO_DB))

    with open(DATA_DIR / "invoices.json", encoding="utf-8") as f:
        invoices = json.load(f)["invoices"]
    requests = [invoice_to_request(inv) for inv in invoices] + TRANSFERS

    print(f"{data['company_name']} - payment checks under policy version {data['policy_version']}")
    print("=" * 78)

    for n, request in enumerate(requests, start=1):
        # In this demo the system's clock is the time each invoice arrived, and each
        # request's agent id stands in for the connection it came on.
        entry_id, decision = submit_payment(request, data, log, now=datetime.fromisoformat(request["time"]),
                                            agent_id=request["agent_id"])
        print(f"\n#{n} {describe(request)}")
        print(f"   DECISION: {decision.decision}")
        for reason in decision.reasons():
            print(f"   - {reason}")
        if decision.decision == REQUIRE_APPROVAL:
            if request.get("invoice_id") in APPROVE_INVOICES:
                log.approve(entry_id, REVIEWER, data)
                print(f"   >> Approved by {REVIEWER}. Money moves now.")
            else:
                print("   >> Waiting in the approval queue. No money has moved.")

    # Summary
    statuses = Counter(e["status"] for e in log.all_entries())
    print("\n" + "=" * 78)
    print("Audit log status counts: " + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())))
    print("Ending balances (only ALLOWED and APPROVED payments moved money):")
    history = log.history()
    for account in data["accounts"]:
        start = data["accounts"][account]
        end = current_balance(account, data, history)
        print(f"   {account:<10} {money(start):>16} -> {money(end):>16}")
    print(f"\nFull audit log saved to {DEMO_DB.name}")
    log.close()


if __name__ == "__main__":
    main()
