"""Rule F1: the fuzzy "unusual payment" check.

After the hard rules say ALLOW, an AI looks at a vendor payment and decides
whether it looks unusual enough that a human should check it.

Why this can't weaken safety:
  - It only runs on payments the hard rules already allowed, and it can only
    return PASS or ESCALATE. No path here produces ALLOW or BLOCK.
  - Anything that goes wrong (API error, timeout, refusal, bad JSON, missing
    fields, unknown vendor) escalates. When in doubt, a human decides.
  - The AI sees only structured facts built by plain code: names from our own
    vendor records and sanctions list, numbers, and dates. It never sees the
    invoice text or any free text from the agent, so a fake invoice has
    nothing to talk to.
  - A near match to a sanctions name escalates in plain code, without asking
    the AI at all, so that case never depends on the model.
"""

import difflib
import json
from datetime import timedelta
from decimal import Decimal

from guardrail import config
from guardrail.rules import (
    ACTIVE, ESCALATE, Rule, fire, lookup_vendor, money, normalize_name, passed,
)

# F1 lives in code, but it is not one of the built-in rules (only B1 and B2 are),
# and it is not in data["rules"]: the checker runs it separately, and only after
# the hard rules said ALLOW. Its reasons say "Rule F1".
UNUSUAL_RULE = Rule("F1", 1, "unusual_payment",
                    "Payments that look unusual go to a human (AI judgment, can only escalate).",
                    {}, ESCALATE, "System (in code)", "in code")

# A sanctions name this similar (but not an exact match, which B2 blocks)
# always escalates. "Allegheny" vs "Alleghany" Steel Supply scores about 0.95.
NEAR_MATCH_THRESHOLD = 0.85

# How far back we count recent payments to the same vendor.
RECENT_DAYS = 30

# The AI's explanation goes into the audit log, so keep it short.
MAX_EXPLANATION = 300


# ---------- the facts the AI sees (plain code) ----------

def similarity(a: str, b: str) -> float:
    """How alike two names are, from 0 (nothing alike) to 1 (identical), ignoring case and spaces."""
    return difflib.SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def closest_sanctions_name(name: str, data: dict) -> tuple[str, float]:
    best = max(data["sanctions_names"], key=lambda s: similarity(name, s))
    return best, round(similarity(name, best), 2)


def gather_facts(req: dict, data: dict, history: list[dict]) -> dict:
    """Build the only information the AI gets.

    The vendor name comes from our records, not from the request, so text the
    agent typed never reaches the AI. Returns None if the vendor isn't on file.
    """
    vendor = lookup_vendor(req, data)
    if vendor is None:
        return None
    typical = Decimal(vendor["typical_invoice"])
    window_start = req["time"] - timedelta(days=RECENT_DAYS)
    recent = [
        e for e in history
        if e["kind"] == "vendor" and e["status"] in ACTIVE
        and e["to_vendor"] is not None and e["time"] is not None
        and normalize_name(e["to_vendor"]) == normalize_name(vendor["name"])
        and window_start < e["time"] <= req["time"]
    ]
    sanctions_name, score = closest_sanctions_name(vendor["name"], data)
    added = vendor["date_added"]
    return {
        "vendor_name": vendor["name"],
        "amount": str(req["amount"]),
        "typical_invoice": str(typical),
        "amount_vs_typical": f"{req['amount'] / typical:.1f}",   # e.g. "5.3" times usual
        "date_added": added.isoformat() if added else None,
        "days_since_added": (req["time"].date() - added).days if added else None,
        "usual_payment_frequency": vendor.get("payment_frequency"),
        f"payments_to_vendor_last_{RECENT_DAYS}_days": len(recent),
        "closest_sanctions_name": sanctions_name,
        "sanctions_similarity": score,
    }


# ---------- the AI call ----------

# Structured output: the API holds the reply to this shape. Code still checks it.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "unusual": {"type": "boolean"},
        "explanation": {"type": "string"},
    },
    "required": ["unusual", "explanation"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = f"""You review vendor payments for a company's treasury team. Each payment has
already passed the company's hard rules (approved vendor, account on file, amount limits, cash
minimums, duplicates, sanctions exact match). Your only job is to say whether it still looks
unusual enough that a person should check it before the money moves.

You get facts from the company's own records, not from the invoice:
- amount_vs_typical: this payment divided by the vendor's typical invoice
- days_since_added: how long the vendor has been in our records
- usual_payment_frequency and payments_to_vendor_last_{RECENT_DAYS}_days: how often we pay them,
  and how many payments to them are already in our log this month
- closest_sanctions_name and sanctions_similarity (0 to 1): the most similar name on the sanctions list

Flag a payment as unusual when something is clearly out of pattern, for example an amount well
above the vendor's typical invoice, a large payment to a recently added vendor, more payments
this month than the usual frequency explains, or a name close to a sanctions name.
Routine payments near the typical amount to established vendors are not unusual.

Answer with "unusual" (true or false) and "explanation": one short plain-English sentence a
treasury analyst can read, using the numbers given, e.g. "amount is 5.3 times this vendor's
usual invoice." Keep the explanation under 200 characters."""


def ask_ai(facts: dict, client) -> dict:
    """Ask Claude about one payment. Returns the checked answer. Raises on any problem."""
    response = client.with_options(
        timeout=config.UNUSUAL_TIMEOUT_SECONDS, max_retries=config.UNUSUAL_MAX_RETRIES,
    ).messages.create(
        model=config.UNUSUAL_MODEL,
        max_tokens=config.UNUSUAL_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(facts, indent=2)}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    if response.stop_reason != "end_turn":
        # e.g. "refusal" or "max_tokens": the answer may be missing or cut off.
        raise ValueError(f"the AI stopped early ({response.stop_reason})")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError("the AI returned no text")
    answer = json.loads(text)

    # Exactly the two fields, with the right types. Anything else is malformed.
    if not isinstance(answer, dict) or set(answer) != {"unusual", "explanation"}:
        raise ValueError("the AI's answer had the wrong fields")
    if not isinstance(answer["unusual"], bool):
        raise ValueError("the AI's 'unusual' was not true or false")
    explanation = answer["explanation"].strip() if isinstance(answer["explanation"], str) else ""
    if answer["unusual"] and not explanation:
        raise ValueError("the AI flagged the payment without an explanation")
    if len(explanation) > MAX_EXPLANATION:
        raise ValueError("the AI's explanation was too long")
    return {"unusual": answer["unusual"], "explanation": explanation}


# ---------- the check ----------

def check_unusual(req: dict, data: dict, history: list[dict], client):
    """Run rule F1 on a vendor payment the hard rules allowed. Returns PASS or ESCALATE only."""
    rule = UNUSUAL_RULE
    try:
        facts = gather_facts(req, data, history)
        if facts is None:
            return fire(rule, f"could not run the unusual-payment check: "
                              f"'{req['to_vendor']}' is not in our vendor records.")

        # Near match to a sanctions name: plain code escalates, no AI needed.
        if facts["sanctions_similarity"] >= NEAR_MATCH_THRESHOLD:
            return fire(rule, f"flagged as unusual: '{facts['vendor_name']}' is a "
                              f"{facts['sanctions_similarity']:.0%} match for "
                              f"'{facts['closest_sanctions_name']}' on the sanctions list. "
                              f"Confirm they are not the same party.")

        answer = ask_ai(facts, client)
    except Exception as error:
        # Any failure means a human decides. Only the error type is shown,
        # so nothing unexpected from the API ends up in the audit log.
        return fire(rule, f"could not run the unusual-payment check "
                          f"({type(error).__name__}), so a human should review this payment.")

    if answer["unusual"]:
        return fire(rule, f"flagged as unusual: {answer['explanation']} "
                          f"(This payment {money(req['amount'])}, typical invoice "
                          f"{money(Decimal(facts['typical_invoice']))}.)")
    return passed(rule)
