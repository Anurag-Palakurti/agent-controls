"""The checkpoint: every payment the agent wants to make goes through check_payment().

How it decides:
  1. Clean the request. Missing or unknown data becomes a "needs approval"
     finding, never an ALLOW. The invoice text is dropped here, so no rule
     can ever see it. The time and the agent's identity come from the system
     calling the checker, never from the request.
  2. Run every rule that applies.
  3. Strictest answer wins: any BLOCK -> BLOCK, else any ESCALATE ->
     REQUIRE_APPROVAL, else ALLOW.
  4. Only if the answer is ALLOW, and only when an AI client is given, the
     unusual payment check (rule F1, in unusual.py) may turn it into
     REQUIRE_APPROVAL. It can never allow or block.

The hard rules in steps 1-3 use no AI. Any unexpected error sends the payment to a human.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from guardrail.rules import (
    BLOCK, ESCALATE, PASS, RULE_TYPES, Finding, invoice_number_key, load_rules, normalize_name,
)
from guardrail.unusual import check_unusual

# The three final decisions.
ALLOW = "ALLOW"
REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
# (BLOCK is shared with rules.py)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass
class Decision:
    decision: str                # ALLOW, BLOCK, or REQUIRE_APPROVAL
    fired: list[Finding]         # every rule that escalated or blocked
    rules_checked: int           # how many rules applied to this payment

    def reasons(self) -> list[str]:
        if not self.fired:
            return [f"Allowed: all {self.rules_checked} rules that apply to this payment passed."]
        # Blocks first, so the most important reason is read first.
        ordered = [f for f in self.fired if f.outcome == BLOCK] + \
                  [f for f in self.fired if f.outcome == ESCALATE]
        return [f.reason for f in ordered]


def load_data(data_dir: Path = DATA_DIR, policy_store=None) -> dict:
    """Load the fake company, vendors, sanctions list, and policy rules.

    Rules come from the policy store's current version when one is given
    (milestone 2 onward), otherwise from the starting rules in policies.json.
    """
    def read(name):
        with open(Path(data_dir) / name, encoding="utf-8") as f:
            return json.load(f)

    company = read("company.json")
    vendors = read("vendors.json")["vendors"]
    sanctions = read("sanctions.json")["names"]
    if policy_store is not None:
        policies = {"policy_version": str(policy_store.current_version()),
                    "rules": policy_store.get_rules()}
    else:
        policies = read("policies.json")
    account_names = list(company["accounts"])

    return {
        "company_name": company["company_name"],
        "accounts": {name: Decimal(a["starting_balance"]) for name, a in company["accounts"].items()},
        # Vendors are looked up by normalized name.
        "vendors": {
            normalize_name(v["name"]): {
                **v,
                "date_added": date.fromisoformat(v["date_added"]) if v.get("date_added") else None,
            }
            for v in vendors
        },
        "sanctions": {normalize_name(n) for n in sanctions},
        "sanctions_names": list(sanctions),   # as written, for showing to people
        "policy_version": policies["policy_version"],
        "rules": load_rules(policies["rules"], account_names),
    }


# A plain amount: digits, optionally a point and one or two more digits ("15000", "15000.00").
# Anything else (fractions of a cent, "1e5", "$15,000", signs, spaces) goes to a human.
PLAIN_AMOUNT = re.compile(r"[0-9]+(\.[0-9]{1,2})?")


def parse_amount(value) -> Decimal | None:
    """The amount as an exact Decimal, or None if it isn't a plain positive amount.

    Whole numbers (JSON integers) are fine. Floats and booleans are refused,
    because money must be exact.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value)
    if not PLAIN_AMOUNT.fullmatch(text):
        return None
    amount = Decimal(text)
    return amount if amount > 0 else None


def clean_request(raw, data: dict, now, agent_id) -> tuple[dict, list[str]]:
    """Turn a raw request into typed fields, listing every problem found.

    `now` and `agent_id` come from the system calling the checker, never from
    the request: the system's clock, and the identity of the connection the
    request arrived on. The request's own "time" and "agent_id" are only
    recorded, as claimed_time and claimed_agent_id, so a compromised agent
    can't back-date payments or pose as several agents to get around limits.

    Bad fields become None, and each problem is reported so the payment
    goes to a human. Only the fields below are copied, so the invoice text
    never reaches the rules.
    """
    problems = []
    req = {
        "agent_id": None, "kind": None, "from_account": None, "to_vendor": None,
        "to_vendor_account": None, "to_internal_account": None,
        "invoice_id": None, "amount": None, "time": None,
        "claimed_time": None, "claimed_agent_id": None,
    }

    # Supplied by the system, not the request.
    if isinstance(now, datetime):
        req["time"] = now
    else:
        problems.append("the system did not supply the time")
    if isinstance(agent_id, str) and agent_id.strip():
        req["agent_id"] = agent_id.strip()
    else:
        problems.append("the system did not supply the agent's identity")

    if not isinstance(raw, dict):
        problems.append(f"the request is not a payment (got {type(raw).__name__})")
        return req, problems

    def text_field(key):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    # What the request says about itself: recorded, and must be well formed, but never trusted.
    req["claimed_agent_id"] = text_field("agent_id")
    if req["claimed_agent_id"] is None:
        problems.append("missing agent id")
    claimed = raw.get("time")
    try:
        req["claimed_time"] = claimed if isinstance(claimed, datetime) else datetime.fromisoformat(claimed)
    except (TypeError, ValueError):
        problems.append(f"missing or invalid time {claimed!r}")

    # Where the money comes from: must be one of our accounts.
    from_account = text_field("from_account")
    if from_account is None:
        problems.append("missing 'from' account")
    elif from_account not in data["accounts"]:
        problems.append(f"unknown 'from' account '{from_account}'")
    else:
        req["from_account"] = from_account

    # How much: a plain positive amount with at most two decimal places.
    req["amount"] = parse_amount(raw.get("amount"))
    if req["amount"] is None:
        problems.append(f"missing or invalid amount {raw.get('amount')!r} "
                        f"(use a plain amount like 15000 or 15000.00)")

    # Where the money goes: exactly one of a vendor or one of our own accounts.
    to_vendor = text_field("to_vendor")
    to_internal = text_field("to_internal_account")
    if to_vendor and to_internal:
        problems.append("payment names both a vendor and an internal account")
    elif to_vendor:
        req["kind"] = "vendor"
        req["to_vendor"] = to_vendor
        req["to_vendor_account"] = text_field("to_vendor_account")
        req["invoice_id"] = text_field("invoice_id")
        if req["to_vendor_account"] is None:
            problems.append("missing vendor account number")
        if req["invoice_id"] is None:
            problems.append("missing invoice id")
        elif not invoice_number_key(req["invoice_id"]):
            problems.append(f"invoice id {req['invoice_id']!r} has no letters or digits")
    elif to_internal:
        req["kind"] = "internal"
        if to_internal not in data["accounts"]:
            problems.append(f"unknown internal account '{to_internal}'")
        elif to_internal == req["from_account"]:
            problems.append("transfer from an account to itself")
        else:
            req["to_internal_account"] = to_internal
    else:
        problems.append("missing payee (no vendor or internal account)")

    return req, problems


def check_payment(raw_request, data: dict, log, *, now, agent_id, ignore_entry_id: int | None = None,
                  unusual_client=None) -> Decision:
    """Run every rule on one payment request and return the strictest answer.

    `now` (a datetime) and `agent_id` are supplied by the system calling the
    checker, never taken from the request (see clean_request).
    `log` is the audit log, used for balances, 24h totals, and duplicates.
    `ignore_entry_id` lets a human approval re-check a pending payment
    without the payment counting against itself.
    `unusual_client` turns on rule F1 (an Anthropic client, or a fake in
    tests). Without it, only the hard rules run.

    Never raises: any unexpected error sends the payment to a human.
    """
    try:
        return _check(raw_request, data, log, now, agent_id, ignore_entry_id, unusual_client)
    except Exception as error:
        # A bug or a request shaped in a way nobody expected must never crash
        # the checker or let money through. A human decides.
        return Decision(REQUIRE_APPROVAL, [Finding(
            "CHECKER", None, ESCALATE,
            f"Needs approval (checker error): unexpected {type(error).__name__} while checking "
            f"this payment, so a human must decide.")], 0)


def _check(raw_request, data, log, now, agent_id, ignore_entry_id, unusual_client) -> Decision:
    req, problems = clean_request(raw_request, data, now, agent_id)
    history = log.history(ignore_entry_id=ignore_entry_id)

    findings = [
        Finding("DATA", None, ESCALATE, f"Needs approval (data check): {p}.") for p in problems
    ]

    for rule in data["rules"]:
        rule_type = RULE_TYPES[rule.type]
        if rule_type.applies_to != "all" and rule_type.applies_to != req["kind"]:
            continue
        if any(req[field] is None for field in rule_type.needs):
            continue  # can't run without this data; the data check already escalated
        try:
            finding = rule_type.check(rule, req, data, history)
        except Exception as error:
            # A rule that crashes must never let money through.
            finding = Finding(rule.id, rule.version, ESCALATE,
                              f"Needs approval: rule {rule.id} could not be checked ({error}).")
        if finding is not None:
            findings.append(finding)

    # Rule F1 runs last, and only when every hard rule passed: an AI call
    # can't change a BLOCK or an escalation, so it would only cost money.
    hard_rules_allow = all(f.outcome == PASS for f in findings)
    if unusual_client is not None and hard_rules_allow and req["kind"] == "vendor":
        findings.append(check_unusual(req, data, history, unusual_client))

    fired = [f for f in findings if f.outcome != PASS]
    rules_checked = sum(1 for f in findings if f.rule_id != "DATA")

    # Strictest answer wins.
    if any(f.outcome == BLOCK for f in fired):
        decision = BLOCK
    elif any(f.outcome == ESCALATE for f in fired):
        decision = REQUIRE_APPROVAL
    else:
        decision = ALLOW
    return Decision(decision, fired, rules_checked)


def submit_payment(raw_request, data: dict, log, unusual_client=None, *, now, agent_id) -> tuple[int, Decision]:
    """Check a payment and write the decision to the audit log.

    This is the one function the agent's system calls, passing its own clock
    (`now`) and the agent's connection identity (`agent_id`). Recording happens
    here, so a payment can't be checked without leaving a trace.
    `unusual_client` turns on rule F1 (see check_payment).
    """
    decision = check_payment(raw_request, data, log, now=now, agent_id=agent_id,
                             unusual_client=unusual_client)
    try:
        req, _ = clean_request(raw_request, data, now, agent_id)
    except Exception:
        # The checker already sent this to a human; still log what we can.
        req, _ = clean_request(None, data, now, agent_id)
    entry_id = log.record(raw_request, req, data["policy_version"], decision)
    return entry_id, decision
