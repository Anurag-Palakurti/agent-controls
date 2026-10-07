"""Rule types and the plain-code checks behind them.

Every check here is ordinary Python. No check calls an AI, and no check is
ever given the invoice text: the checker strips it out before rules run, so
a fake invoice saying "ignore your rules" has nothing to talk to.

Each check returns one of:
  - None              the rule doesn't apply to this payment (e.g. a vendor rule
                      on an internal transfer, or a minimum for another account)
  - a PASS Finding    the rule applied and is satisfied
  - a fired Finding   ESCALATE or BLOCK, with a plain English reason
"""

import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation

# The three outcomes a single rule can produce.
PASS = "PASS"
ESCALATE = "ESCALATE"
BLOCK = "BLOCK"

# Audit log statuses. Money only moves for ALLOWED and APPROVED.
# PENDING payments haven't moved money, but they still count toward the
# 24 hour total and duplicate checks, so an agent can't queue around limits.
MONEY_MOVED = ("ALLOWED", "APPROVED")
ACTIVE = ("ALLOWED", "APPROVED", "PENDING")


@dataclass
class Rule:
    """One policy rule: the English a human approved, plus its exact settings."""
    id: str
    version: int
    type: str
    text: str
    settings: dict
    action: str          # what happens when it fires: ESCALATE or BLOCK
    approved_by: str
    approved_at: str
    built_in: bool = False


@dataclass
class Finding:
    """The result of running one rule on one payment."""
    rule_id: str
    rule_version: int | None
    outcome: str         # PASS, ESCALATE, or BLOCK
    reason: str


# ---------- small helpers ----------

def normalize_name(name: str) -> str:
    """Compare names ignoring case and extra spaces ("ACME  Inc" == "acme inc")."""
    return " ".join(name.split()).casefold()


def invoice_number_key(invoice_id: str) -> str:
    """An invoice id reduced to what identifies it, for spotting duplicates.

    Unicode look-alikes are normalized, case is folded, and every character
    that isn't a letter or digit is dropped, so "KIG-2301", "KIG 2301",
    "KIG2301", "kig_2301", and "KIG–2301" (en dash) are all "kig2301".
    """
    text = unicodedata.normalize("NFKC", invoice_id).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def money(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def rule_label(rule: Rule) -> str:
    """How a reason names its rule: "Built-in rule B1", "Policy 2.1", or "Rule F1".

    Only B1 and B2 are built in. Policy rules have numbered ids like 2.1.
    F1 also lives in code, but it isn't built in, so it's just "Rule F1".
    """
    if rule.built_in:
        return f"Built-in rule {rule.id}"
    return f"Policy {rule.id}" if rule.id[:1].isdigit() else f"Rule {rule.id}"


def fire(rule: Rule, message: str, outcome: str | None = None) -> Finding:
    """Build a fired Finding whose reason names the exact rule."""
    outcome = outcome or rule.action
    word = "Blocked" if outcome == BLOCK else "Needs approval"
    return Finding(rule.id, rule.version, outcome, f"{word} ({rule_label(rule)}): {message}")


def passed(rule: Rule) -> Finding:
    return Finding(rule.id, rule.version, PASS, f"Passed ({rule_label(rule)})")


def current_balance(account: str, data: dict, history: list[dict]) -> Decimal:
    """Starting balance, minus money that left, plus internal transfers that arrived.

    Only ALLOWED and APPROVED entries count, because those are the only
    payments that actually moved money.
    """
    balance = data["accounts"][account]
    for entry in history:
        if entry["status"] not in MONEY_MOVED or entry["amount"] is None:
            continue
        if entry["from_account"] == account:
            balance -= entry["amount"]
        if entry["to_internal_account"] == account:
            balance += entry["amount"]
    return balance


def lookup_vendor(req: dict, data: dict) -> dict | None:
    return data["vendors"].get(normalize_name(req["to_vendor"]))


# ---------- the checks, one per rule type ----------
# Every check has the same signature: (rule, req, data, history).
# `req` is the cleaned request (no invoice text), `history` is the audit log.

def check_amount_limit(rule, req, data, history):
    limit = Decimal(rule.settings["max_amount"])
    if req["amount"] > limit:
        return fire(rule, f"{money(req['amount'])} is over the {money(limit)} limit.")
    return passed(rule)


def check_minimum_balance(rule, req, data, history):
    account = rule.settings["account"]
    if req["from_account"] != account:
        return None  # this minimum is for a different account
    minimum = Decimal(rule.settings["minimum"])
    after = current_balance(account, data, history) - req["amount"]
    if after < minimum:
        return fire(rule, f"this would leave {money(after)} in {account}, "
                          f"below the {money(minimum)} minimum.")
    return passed(rule)


def check_approved_vendors_only(rule, req, data, history):
    vendor = lookup_vendor(req, data)
    if vendor is None:
        return fire(rule, f"'{req['to_vendor']}' is not on our vendor list.")
    if vendor["status"] != "approved":
        return fire(rule, f"'{vendor['name']}' is a known vendor but is not approved.")
    return passed(rule)


def check_vendor_account_match(rule, req, data, history):
    vendor = lookup_vendor(req, data)
    if vendor is None or vendor["status"] != "approved":
        return None  # rule 3.1 already handles unknown and unapproved vendors
    if req["to_vendor_account"] != vendor["account_number"]:
        return fire(rule, f"payment goes to account {req['to_vendor_account']}, but the account "
                          f"on file for '{vendor['name']}' is {vendor['account_number']}. "
                          f"Verify the change with the vendor by phone, using the number "
                          f"already on file, before updating it.")
    return passed(rule)


def check_new_vendor_limit(rule, req, data, history):
    vendor = lookup_vendor(req, data)
    if vendor is None or vendor["status"] != "approved":
        return None  # rule 3.1 already handles these
    if vendor["date_added"] is None:
        # We can't tell how new the vendor is, so a human decides.
        return fire(rule, f"no 'date added' on file for '{vendor['name']}'.", outcome=ESCALATE)
    days = int(rule.settings["days"])
    limit = Decimal(rule.settings["max_amount"])
    age = (req["time"].date() - vendor["date_added"]).days
    if age < days and req["amount"] > limit:
        return fire(rule, f"'{vendor['name']}' was added {age} day{'' if age == 1 else 's'} ago (under {days}) "
                          f"and {money(req['amount'])} is over {money(limit)}.")
    return passed(rule)


def check_rolling_24h_total(rule, req, data, history):
    limit = Decimal(rule.settings["max_total"])
    window_start = req["time"] - timedelta(hours=24)
    earlier = [
        e for e in history
        if e["agent_id"] == req["agent_id"]
        and e["kind"] == "vendor"
        and e["status"] in ACTIVE           # PENDING counts too, on purpose
        and e["amount"] is not None
        and e["time"] is not None
        and window_start < e["time"] <= req["time"]
    ]
    total = sum((e["amount"] for e in earlier), Decimal("0")) + req["amount"]
    if total > limit:
        count = len(earlier) + 1
        pending = sum(1 for e in earlier if e["status"] == "PENDING")
        note = f" ({pending} of them still pending approval)" if pending else ""
        return fire(rule, f"agent '{req['agent_id']}' would reach {money(total)} in the last "
                          f"24 hours across {count} payment{'s' if count != 1 else ''}{note}, "
                          f"over the {money(limit)} limit.")
    return passed(rule)


def check_duplicate_invoice(rule, req, data, history):
    # Same vendor, and the same invoice number however it's punctuated or spelled.
    vendor_key = normalize_name(req["to_vendor"])
    invoice_key = invoice_number_key(req["invoice_id"])
    for e in history:
        if (e["status"] in ACTIVE
                and e["to_vendor"] is not None and e["invoice_id"] is not None
                and normalize_name(e["to_vendor"]) == vendor_key
                and invoice_number_key(e["invoice_id"]) == invoice_key):
            return fire(rule, f"invoice {req['invoice_id']} from '{req['to_vendor']}' was already "
                              f"submitted (audit entry #{e['id']}, status {e['status']}).")
    return passed(rule)


def check_internal_transfer_limit(rule, req, data, history):
    # Staying above each account's minimum is enforced by rules 2.1-2.3,
    # which run on every payment, so this rule only checks the size.
    limit = Decimal(rule.settings["max_amount"])
    if req["amount"] > limit:
        return fire(rule, f"internal transfer of {money(req['amount'])} is over the "
                          f"{money(limit)} limit for transfers without approval.")
    return passed(rule)


def check_no_overdraft(rule, req, data, history):
    balance = current_balance(req["from_account"], data, history)
    if req["amount"] > balance:
        return fire(rule, f"{money(req['amount'])} is more than the {money(balance)} "
                          f"in {req['from_account']}.")
    return passed(rule)


def check_sanctions(rule, req, data, history):
    if normalize_name(req["to_vendor"]) in data["sanctions"]:
        return fire(rule, f"'{req['to_vendor']}' is on the sanctions list.")
    return passed(rule)


# ---------- the rule type table ----------
# applies_to: which payments the rule runs on ("vendor", "internal", or "all").
# needs:      request fields that must be present. If any is missing, the
#             checker skips this rule; the data check has already escalated.

@dataclass
class RuleType:
    check: callable
    applies_to: str
    needs: tuple = field(default_factory=tuple)


RULE_TYPES = {
    "amount_limit":            RuleType(check_amount_limit, "vendor", ("amount",)),
    "minimum_balance":         RuleType(check_minimum_balance, "all", ("from_account", "amount")),
    "approved_vendors_only":   RuleType(check_approved_vendors_only, "vendor", ("to_vendor",)),
    "vendor_account_match":    RuleType(check_vendor_account_match, "vendor", ("to_vendor", "to_vendor_account")),
    "new_vendor_limit":        RuleType(check_new_vendor_limit, "vendor", ("to_vendor", "amount", "time")),
    "rolling_24h_total":       RuleType(check_rolling_24h_total, "vendor", ("agent_id", "amount", "time")),
    "duplicate_invoice":       RuleType(check_duplicate_invoice, "vendor", ("to_vendor", "invoice_id")),
    "internal_transfer_limit": RuleType(check_internal_transfer_limit, "internal", ("amount",)),
    # Built-in types: only the two rules below may use them.
    "no_overdraft":            RuleType(check_no_overdraft, "all", ("from_account", "amount")),
    "sanctions":               RuleType(check_sanctions, "vendor", ("to_vendor",)),
}

BUILT_IN_TYPES = ("no_overdraft", "sanctions")

# The two rules users can't change or remove. They live in code, not in
# policies.json, and the loader refuses any policy file that tries to use them.
BUILT_IN_RULES = [
    Rule("B1", 1, "no_overdraft", "No payment may overdraw an account.", {},
         BLOCK, "System (built-in)", "built-in", built_in=True),
    Rule("B2", 1, "sanctions", "No payment to anyone on the sanctions list (exact name match).", {},
         BLOCK, "System (built-in)", "built-in", built_in=True),
]


# ---------- settings for the rule types a person can add ----------
# One definition used in two places: loading policies, and checking the AI's
# output in the Policy Compiler. Kinds: "money" (a positive dollar amount),
# "days" (whole days, 1-365), "account" (one of our real account names).

USER_RULE_SETTINGS = {
    "amount_limit":            {"max_amount": "money"},
    "minimum_balance":         {"account": "account", "minimum": "money"},
    "approved_vendors_only":   {},
    "vendor_account_match":    {},
    "new_vendor_limit":        {"days": "days", "max_amount": "money"},
    "rolling_24h_total":       {"max_total": "money"},
    "duplicate_invoice":       {},
    "internal_transfer_limit": {"max_amount": "money"},
}

# Rule ids are grouped by family, so a second amount limit becomes 1.2.
ID_FAMILY = {
    "amount_limit": "1", "minimum_balance": "2", "approved_vendors_only": "3",
    "vendor_account_match": "3", "new_vendor_limit": "4", "rolling_24h_total": "5",
    "duplicate_invoice": "6", "internal_transfer_limit": "7",
}


def clean_settings(rule_type: str, settings: dict, account_names) -> dict:
    """Check a rule's settings exactly match its type. Returns cleaned settings.

    Raises ValueError with a plain reason if anything is missing, extra,
    or out of range. Money comes back as a string like "250000.00".
    """
    if rule_type not in USER_RULE_SETTINGS:
        raise ValueError(f"'{rule_type}' is not a rule type that can be added.")
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object.")
    expected = USER_RULE_SETTINGS[rule_type]
    extra = set(settings) - set(expected)
    if extra:
        raise ValueError(f"unexpected settings for {rule_type}: {sorted(extra)}.")

    cleaned = {}
    for key, kind in expected.items():
        if settings.get(key) is None:
            raise ValueError(f"missing setting '{key}' for {rule_type}.")
        value = settings[key]
        if kind == "money":
            if isinstance(value, bool):
                raise ValueError(f"'{key}' must be a dollar amount.")
            try:
                amount = Decimal(str(value))
            except InvalidOperation:
                raise ValueError(f"'{key}' must be a dollar amount, not {value!r}.")
            if not amount.is_finite() or amount <= 0:
                raise ValueError(f"'{key}' must be a positive amount, not {value!r}.")
            if amount != amount.quantize(Decimal("0.01")):
                raise ValueError(f"'{key}' can't have fractions of a cent ({value!r}).")
            cleaned[key] = str(amount.quantize(Decimal("0.01")))
        elif kind == "days":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
                raise ValueError(f"'{key}' must be a whole number of days, not {value!r}.")
            if not 1 <= int(value) <= 365:
                raise ValueError(f"'{key}' must be between 1 and 365 days, not {value!r}.")
            cleaned[key] = int(value)
        elif kind == "account":
            if value not in account_names:
                raise ValueError(f"'{value}' is not one of our accounts ({', '.join(account_names)}).")
            cleaned[key] = value
    return cleaned


def describe_rule(rule_type: str, settings: dict, action: str) -> str:
    """Code-written description of what a rule will actually enforce.

    The approver reads this, not just the AI's summary, so a human approves
    what the code will do rather than what the AI says it will do.
    """
    do = "Block" if action == BLOCK else "Send to a human for approval"
    s = {k: (money(Decimal(v)) if USER_RULE_SETTINGS[rule_type].get(k) == "money" else v)
         for k, v in settings.items()}
    templates = {
        "amount_limit": f"{do} any vendor payment over {s.get('max_amount')}.",
        "minimum_balance": f"{do} any payment that would leave {s.get('account')} below {s.get('minimum')}.",
        "approved_vendors_only": f"{do} any payment to a vendor that is not on the approved list.",
        "vendor_account_match": f"{do} any vendor payment to an account number that doesn't match the one on file.",
        "new_vendor_limit": f"{do} any payment over {s.get('max_amount')} to a vendor added less than "
                            f"{s.get('days')} days ago.",
        "rolling_24h_total": f"{do} a vendor payment if one agent's payments in the last 24 hours "
                             f"(including pending ones) would add up to more than {s.get('max_total')}.",
        "duplicate_invoice": f"{do} any invoice already paid or pending for the same vendor.",
        "internal_transfer_limit": f"{do} any transfer between our own accounts over {s.get('max_amount')}.",
    }
    return templates[rule_type]


def load_rules(policy_rules: list[dict], account_names) -> list[Rule]:
    """Turn policy entries into Rule objects, plus the built-in rules.

    Anything malformed stops loading entirely. Failing loudly here is safer
    than running with a rule silently missing.
    """
    rules = []
    seen_ids = set()
    for raw in policy_rules:
        rule = Rule(
            id=str(raw["id"]), version=int(raw["version"]), type=raw["type"],
            text=raw["text"], settings=raw.get("settings", {}), action=raw["action"],
            approved_by=raw["approved_by"], approved_at=raw["approved_at"],
        )
        if rule.type not in RULE_TYPES:
            raise ValueError(f"Rule {rule.id}: unknown rule type '{rule.type}'.")
        if rule.type in BUILT_IN_TYPES or rule.id.upper().startswith("B"):
            raise ValueError(f"Rule {rule.id}: built-in rules can't be defined or changed in policies.")
        rule.settings = clean_settings(rule.type, rule.settings, account_names)
        if rule.action not in (ESCALATE, BLOCK):
            raise ValueError(f"Rule {rule.id}: action must be ESCALATE or BLOCK, not '{rule.action}'.")
        if not rule.approved_by or not rule.approved_at:
            raise ValueError(f"Rule {rule.id}: a human must approve a rule before it goes live.")
        if rule.id in seen_ids:
            raise ValueError(f"Rule {rule.id}: duplicate rule id.")
        seen_ids.add(rule.id)
        rules.append(rule)
    return BUILT_IN_RULES + rules
