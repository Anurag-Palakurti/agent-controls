"""A sample morning of payments, so the Overview looks like a real company's day.

Twelve payment requests go straight through the real checker (submit_payment),
exactly as the agent's would, but with no AI calls at all. The unusual payment
check (Rule F1) runs the same way as in Crash Lab Layer 1: its plain-code
sanctions near-match check is on, and its AI is replaced by NeverFlagsClient,
which never flags anything. The decisions come from the checker, not from this
file. Under the starting policy they come out as:
  7 routine vendor payments                         allowed
  1 routine Allegheny Steel Supply payment          needs approval: name close to "Alleghany
                                                    Steel Supply" on the sanctions list
  1 bank-change scam (Allegheny, new account)       blocked: account not on file
  1 payment to a sanctioned name (Volkov)           blocked: sanctions list
  1 large payment to a vendor added yesterday       needs approval: new vendor limit
  1 $600,000 transfer from Reserve to Operating     needs approval: transfer limit

The vendor payments add up to about $162,000, under the $250,000 daily total, so
the Simulator's normal inbox still goes through afterwards. Internal transfers
don't count toward that total.
"""

from datetime import datetime

from guardrail.agent import AGENT_ID
from guardrail.checker import submit_payment
from guardrail.crash_lab import NeverFlagsClient


def vendor(time, invoice_id, name, account, amount):
    return {"time": time, "agent_id": AGENT_ID, "from_account": "Operating", "to_vendor": name,
            "to_vendor_account": account, "amount": amount, "invoice_id": invoice_id}


SAMPLE_MORNING = [
    vendor("2026-10-04T07:12:00", "KIG-2288", "Keystone Industrial Gases", "SIM-1002-7710", "8320.00"),
    vendor("2026-10-04T07:25:00", "PVE-1088", "Penn Valley Electric", "SIM-1005-1267", "12140.00"),
    vendor("2026-10-04T07:41:00", "LRP-4471", "Laurel Ridge Packaging", "SIM-1006-5530", "9650.00"),
    vendor("2026-10-04T07:58:00", "TRF-7981", "Three Rivers Freight", "SIM-1003-3392", "14750.00"),
    vendor("2026-10-04T08:06:00", "BSE-688", "Brightline Safety Equipment", "SIM-1007-9014", "5980.00"),
    vendor("2026-10-04T08:14:00", "ASS-5590", "Allegheny Steel Supply", "SIM-1001-4521", "43200.00"),
    vendor("2026-10-04T08:22:00", "ST-8790", "Susquehanna Tooling", "SIM-1004-8845", "19750.00"),
    # Bank-change scam: a real vendor, but a new account number from the invoice.
    vendor("2026-10-04T08:31:00", "ASS-5602", "Allegheny Steel Supply", "SIM-4410-2290", "46900.00"),
    # A name on the sanctions list.
    vendor("2026-10-04T08:37:00", "VTH-221", "Volkov Trading House", "SIM-9900-1177", "38500.00"),
    vendor("2026-10-04T08:44:00", "TRF-7998", "Three Rivers Freight", "SIM-1003-3392", "16200.00"),
    # Added to our vendor list yesterday, and over the $25,000 new vendor limit.
    vendor("2026-10-04T08:52:00", "NMW-0001", "Northgate Machine Works", "SIM-1008-2276", "32000.00"),
    # Moving cash between our own accounts, over the $500,000 transfer limit.
    {"time": "2026-10-04T08:58:00", "agent_id": AGENT_ID, "from_account": "Reserve",
     "to_internal_account": "Operating", "amount": "600000.00"},
]


def load_sample_morning(data: dict, log) -> list:
    """Send every sample payment through the checker, in order. Returns the decisions.

    The log should be empty first (the Simulator resets it), so the morning starts clean.
    """
    decisions = []
    unusual_client = NeverFlagsClient()   # F1's plain-code checks run; its AI never flags, and is never called
    for request in SAMPLE_MORNING:
        _, decision = submit_payment(dict(request), data, log, unusual_client,
                                     now=datetime.fromisoformat(request["time"]), agent_id=AGENT_ID)
        decisions.append(decision.decision)
    return decisions
