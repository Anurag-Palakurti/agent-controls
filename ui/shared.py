"""Shared helpers for the Streamlit screens: file locations, saved results, and display pieces.

The screens only read and display. Every decision is still made by the
guardrail package: the checker, the compiler, the policy store, and the audit log.
Anything here that rewords a reason is for display only; the audit log keeps
the exact original wording, and the screens show it in an expander.
"""

import json
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import altair as alt
import streamlit as st

from guardrail import crash_lab as lab
from guardrail.audit_log import AuditLog
from guardrail.checker import load_data
from guardrail.policy_store import PolicyStore
from guardrail.rules import BUILT_IN_RULES, RULE_TYPES, lookup_vendor, money

ROOT = Path(__file__).resolve().parent.parent
APP_LOG_FILE = ROOT / "app_audit.db"       # the app's own audit log (gitignored, like every .db)
POLICY_FILE = ROOT / "policies.db"         # shared with run_compiler_demo.py (gitignored)
HISTORY_DIR = ROOT / "crash_lab" / "history"
RESULTS_DIR = ROOT / "crash_lab" / "results"

# The headline numbers always come from these saved files.
HEADLINE_FILE = HISTORY_DIR / "layer2_full_run1_regraded.json"
LAYER1_BEFORE_FILE = HISTORY_DIR / "layer1_before_fixes.json"
COMPILER_FIRST_RUN_FILE = HISTORY_DIR / "compiler_eval_run1.txt"   # terminal output of the first eval

# The product is shown as a feature of a bank's business platform. Both names are made up.
BANK_NAME = "Larkspur Commercial Bank (fictional)"
COMPANY_NAME = "Keystone Fabrication Co."

# Display only: the starting rules in data/policies.json are shown as approved by this
# made-up treasury manager, so the screens look like a real client's. The files and the
# policy store keep the real approver; nothing about the rules changes.
STARTING_APPROVER = "Dana Whitfield, Treasury Manager, Keystone Fabrication"
with open(ROOT / "data" / "policies.json", encoding="utf-8") as _f:
    _STARTING_RULES = {(r["id"], r["approved_at"]) for r in json.load(_f)["rules"]}

# The demo clock: the simulated company's day starts here (same as Crash Lab).
DEMO_START = datetime.fromisoformat("2026-10-04T09:00:00")

# The three Crash Lab modes, in plain words.
MODES = ("generic", "baseline", "guardrail")
MODE_LABELS = {"generic": "Off-the-shelf agent", "baseline": "Rules in the prompt only",
               "guardrail": "With the guardrail"}
MODE_BLURBS = {
    "generic": "An AI agent with no company rules and nothing checking its payments.",
    "baseline": "The same agent with every company rule written into its instructions. Nothing checks it.",
    "guardrail": "The same agent and instructions, with the guardrail checking every payment.",
}

# One color per outcome, everywhere: green allowed, amber needs a person, red blocked.
BADGES = {
    "ALLOW": ":green-badge[Allowed]", "ALLOWED": ":green-badge[Allowed]",
    "APPROVED": ":green-badge[Approved by a person]",
    "REQUIRE_APPROVAL": ":orange-badge[Needs approval]", "PENDING": ":orange-badge[Awaiting approval]",
    "BLOCK": ":red-badge[Blocked]", "BLOCKED": ":red-badge[Blocked]",
    "REJECTED": ":red-badge[Rejected by a person]",
    "SENT": ":gray-badge[Sent with no checks]",
}
WORDS = {"ALLOW": "Allowed", "ALLOWED": "Allowed", "APPROVED": "Approved by a person",
         "REQUIRE_APPROVAL": "Needs approval", "PENDING": "Awaiting approval",
         "BLOCK": "Blocked", "BLOCKED": "Blocked", "REJECTED": "Rejected by a person"}


def badge(word: str) -> str:
    return BADGES.get(word, f":gray-badge[{word}]")


def word(code: str) -> str:
    return WORDS.get(code, code)


# ---------- plain names for rules ----------

RULE_TYPE_NAMES = {
    "amount_limit": "Amount limit",
    "minimum_balance": "Minimum cash",
    "approved_vendors_only": "Approved vendors only",
    "vendor_account_match": "Bank account on file",
    "new_vendor_limit": "New vendor limit",
    "rolling_24h_total": "Daily total",
    "duplicate_invoice": "Duplicate invoice",
    "internal_transfer_limit": "Transfer limit",
    "no_overdraft": "No overdrafts",
    "sanctions": "Sanctions list",
    "unusual_payment": "Unusual payment check",
}
# Findings that aren't rules: problems with the request itself, or the checker failing safe.
OTHER_NAMES = {"data check": "Missing or unclear details", "checker error": "Couldn't be checked"}

# "Blocked (Policy 3.2): message", "Needs approval (Built-in rule B2): ...", "(Rule F1)", "(data check)".
REASON = re.compile(r"^(Blocked|Needs approval) \((?:(?:Built-in rule|Policy|Rule) )?([^)]+)\): (.*)$", re.S)


def approver(rule) -> str:
    """Who approved a rule, for display. Built-in rules are always on; starting rules show
    STARTING_APPROVER. Works on Rule objects and on raw rule dicts."""
    get = rule.get if isinstance(rule, dict) else lambda key: getattr(rule, key)
    if not isinstance(rule, dict) and rule.built_in:
        return "Always on: can't be changed"
    if (get("id"), get("approved_at")) in _STARTING_RULES:
        return STARTING_APPROVER
    return get("approved_by")


def version_change(entry: dict) -> tuple[str, str]:
    """(who approved, what changed) for a policy version, in plain words.

    Version 1 was loaded from data/policies.json, so it reads as the starting policy.
    "Added rule 1.2: ..." becomes "Added: ...".
    """
    if entry["version"] == 1 and entry["changed_by"].startswith("Initial policy"):
        return STARTING_APPROVER, "Starting policy."
    return entry["changed_by"], re.sub(r"^Added rule [\w.]+: ", "Added: ", entry["change"])


def rule_names(store: PolicyStore | None = None) -> dict:
    """Rule id -> plain name, for every rule in every policy version (old decisions name old rules)."""
    names = {r.id: RULE_TYPE_NAMES[r.type] for r in BUILT_IN_RULES}
    names["F1"] = RULE_TYPE_NAMES["unusual_payment"]
    with open(ROOT / "data" / "policies.json", encoding="utf-8") as f:
        versions = [json.load(f)["rules"]]
    if store is not None:
        versions += [store.get_rules(v["version"]) for v in store.history()]
    for rules in versions:
        for r in rules:
            names[r["id"]] = RULE_TYPE_NAMES.get(r["type"], "Company policy")
    return names


def plain_reason(reason: str, names: dict) -> str:
    """'Blocked (Built-in rule B2): pays a sanctioned name' -> '**Sanctions list**: pays a sanctioned name'.

    Display only. The exact wording stays in the audit log and is shown in an expander.
    """
    match = REASON.match(tidy(reason))
    if not match:
        return esc(reason)
    _, ref, message = match.groups()
    name = OTHER_NAMES.get(ref) or names.get(ref, "Company policy")
    return f"**{name}**: {esc(message[:1].upper() + message[1:])}"


# ---------- the app's log and policy store ----------
# Opened fresh on every screen draw and closed after, because Streamlit may draw
# each time on a different thread and a SQLite connection belongs to one thread.

def open_log() -> AuditLog:
    return AuditLog(str(APP_LOG_FILE))


def open_store() -> PolicyStore:
    return PolicyStore(str(POLICY_FILE))


# Resets read the module's current paths at call time (tests point them at temp files).
def reset_log():
    """Delete the app's audit log. The next open_log() starts a new, empty one."""
    APP_LOG_FILE.unlink(missing_ok=True)


def reset_policies():
    """Delete every policy version. The next open_store() starts again from data/policies.json."""
    POLICY_FILE.unlink(missing_ok=True)


def current_data(store: PolicyStore) -> dict:
    """Company data with the rules from the current approved policy version."""
    return load_data(policy_store=store)


def next_demo_start(log: AuditLog) -> str:
    """When the next agent run starts on the demo clock: 30 minutes after the last logged payment.

    Runs share one log and one simulated day, so the 24 hour total and the
    duplicate check see every earlier run, the way they would in real life.
    """
    times = [e["time"] for e in log.history() if e["time"] is not None]
    start = max(times) + timedelta(minutes=30) if times else DEMO_START
    return start.isoformat(timespec="seconds")


def reviewer_name() -> str:
    """The name typed in the sidebar, used for every human approval."""
    return (st.session_state.get("reviewer") or "").strip()


def api_key_found() -> bool:
    """Whether a key is set. Only yes or no is ever shown: never the key itself."""
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def make_ai_client():
    """The real API client, or None with a message on screen if it can't be made."""
    from guardrail.compiler import make_client
    try:
        return make_client()
    except Exception as error:
        st.error(f"Couldn't set up the AI client ({type(error).__name__}). Check that .env has the API key.")
        return None


# ---------- page furniture ----------

def product_banner():
    """The top of every Product page: whose product this is, and that it's simulated."""
    with st.container(border=True):
        st.markdown(f":material/account_balance: **Agent Controls**, offered by {BANK_NAME} "
                    f"for **{COMPANY_NAME}**")
        st.caption("Simulated: no real money, banks, or accounts.")


# ---------- saved results ----------

def load_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def compiler_first_run() -> dict | None:
    """The Policy Compiler's first eval run, counted from its saved terminal output.

    Returns None (and Story hides the section) if the file is missing, or if its
    counts don't add up, so a half-read file can never show a wrong number.
    A miss is a wrong rule only if it came back compiled ("got compiled: ..." or
    "got (type, settings, action), expected ..."); asking or refusing is a safe miss.
    """
    try:
        text = COMPILER_FIRST_RUN_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    total = re.search(r"^all\s+(\d+)/(\d+)", text, re.M)
    if total is None:
        return None
    correct, cases = int(total.group(1)), int(total.group(2))
    misses = re.findall(r"^\s+got (\w+|\()", text, re.M)
    if len(misses) != cases - correct:
        return None
    return {"correct": correct, "cases": cases, "misses": len(misses),
            "wrong_rule": sum(m in ("compiled", "(") for m in misses),
            "asked": sum(m == "needs_clarification" for m in misses)}


def saved_result_files() -> list[Path]:
    """Every saved Crash Lab results file: the kept history first, then the latest run if any."""
    files = sorted(p for p in HISTORY_DIR.glob("*.json") if p.name != "compiler_eval.json")
    latest = RESULTS_DIR / "latest.json"
    return files + ([latest] if latest.exists() else [])


def tidy(text: str) -> str:
    """A reason as it's shown on screen, without internal ids. Display only; the log is unchanged.

    - Older saved results called F1 a built-in rule. Only B1 and B2 are built in: "Rule F1".
    - "agent 'keystone-ap-agent'" becomes "the company's AI agent".
    - "(audit entry #12, status ALLOWED)" becomes "(already allowed)".
    - "1 day(s) ago" becomes "1 day ago", "3 day(s) ago" becomes "3 days ago" (older saved wording).
    """
    if not isinstance(text, str):
        return text
    text = text.replace("Built-in rule F1", "Rule F1")
    text = re.sub(r"agent '[^']*'", "the company's AI agent", text)
    text = re.sub(r"\b(\d+) day\(s\)", lambda m: f"{m.group(1)} day{'' if m.group(1) == '1' else 's'}", text)
    return re.sub(r"\(audit entry #\d+, status (\w+)\)",
                  lambda m: f"(already {word(m.group(1)).lower()})", text)


def esc(text) -> str:
    """Make text safe for Streamlit markdown: "$" would otherwise start a math formula."""
    return str(text).replace("$", "\\$")


def pct(value) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%".replace(".0%", "%")


def dollars(amount) -> str:
    try:
        return money(Decimal(str(amount)))
    except Exception:
        return str(amount)


def attack_label(summary: dict, repeats: int) -> str:
    """'22 attack invoices, run 3 times each', worked out from the saved counts so it stays true."""
    should_stop = summary["modes"]["guardrail"]["should_stop"]
    return f"{should_stop // repeats} attack invoices, run {repeats} times each"


def mode_cards(results: dict):
    """The three modes side by side: bad payments sent with no human, good payments handled right,
    and scenarios correct on every repeat."""
    summary, repeats = results["layer2"]["summary"], results["repeats"]
    st.caption(f"**{attack_label(summary, repeats)}.** All three ran the same "
               f"{summary['consistency']['guardrail']['scenarios']} test scenarios.")
    for column, mode in zip(st.columns(3), MODES):
        s, c = summary["modes"][mode], summary["consistency"][mode]
        good_right = round((s["valid_completion_rate"] or 0) * s["good"])
        with column.container(border=True):
            st.markdown(f"**{MODE_LABELS[mode]}**")
            st.caption(MODE_BLURBS[mode])
            st.metric("Bad payments sent with no human", f"{s['unsafe_paid']} of {s['should_stop']}")
            st.metric("Good payments handled right", pct(s["valid_completion_rate"]),
                      help=f"{good_right} of {s['good']}: paid once, or (for borderline invoices "
                           f"only) left for a person.")
            # Older results files were saved before this measure existed.
            correct = c.get("correct_every_repeat")
            st.metric("Scenarios correct every repeat",
                      f"{correct} of {c['scenarios']}" if correct is not None else "not measured")


# ---------- the decision colors, for charts and the live checkpoint ----------
# The same hex values as the theme in .streamlit/config.toml, so charts match the badges.
GREEN, AMBER, RED, GRAY = "#15803D", "#B45309", "#B91C1C", "#5B6B82"

# Big verdict shown after a payment is checked: (Streamlit color name, icon, word).
VERDICTS = {"ALLOW": ("green", "check_circle", "ALLOWED"),
            "REQUIRE_APPROVAL": ("orange", "pause_circle", "NEEDS APPROVAL"),
            "BLOCK": ("red", "cancel", "BLOCKED")}

# One rule's result in the checkpoint: (Streamlit color name, icon, word).
RULE_MARKS = {"PASS": ("green", "check_circle", "passed"),
              "ESCALATE": ("orange", "pause_circle", "needs approval"),
              "BLOCK": ("red", "cancel", "blocked")}


def verdict_heading(decision: str) -> str:
    color, icon, word = VERDICTS[decision]
    return f"### :{color}[:material/{icon}: {word}]"


def rule_line(outcome: str, name: str, detail: str = "") -> str:
    color, icon, word = RULE_MARKS[outcome]
    return f":{color}[:material/{icon}:] **{esc(name)}** · :{color}[{word}]" + (f" · {esc(detail)}" if detail else "")


# An audit entry's column for each field a rule type needs (the log calls "time" request_time).
ENTRY_FIELD = {"time": "request_time"}
ALLOWED_COUNT = re.compile(r"^Allowed: all (\d+) rules")


def steps_aside(rule, entry: dict, data: dict) -> bool:
    """Rules whose check returns no result for this payment, mirroring rules.py: a minimum
    for a different account, and two vendor rules that leave unknown or unapproved vendors to 3.1."""
    if rule.type == "minimum_balance":
        return entry["from_account"] != rule.settings["account"]
    if rule.type in ("vendor_account_match", "new_vendor_limit"):
        vendor = lookup_vendor({"to_vendor": entry["to_vendor"]}, data)
        return vendor is None or vendor["status"] != "approved"
    return False


def checkpoint_rows(entry: dict, data: dict, f1_on: bool) -> tuple[list[tuple], list[tuple]]:
    """Each rule's result for one payment, for the live checkpoint. Display only.

    The checker reports only the rules that fired, plus how many rules it ran. So
    this lists the rules in force that apply to this payment (the same applies_to
    and needs the checker uses, and steps_aside), and marks the ones that fired from
    the audit entry. Returns (passed, fired), each a list of (outcome, name, detail).
    If the list can't be matched to what the checker reported, the passed rules
    collapse into one line instead of guessing which ones ran.
    """
    applies = [r for r in data["rules"]
               if RULE_TYPES[r.type].applies_to in ("all", entry["kind"])
               and all(entry.get(ENTRY_FIELD.get(f, f)) is not None for f in RULE_TYPES[r.type].needs)
               and not steps_aside(r, entry, data)]

    fired, fired_refs = [], set()
    for reason in entry["reasons"]:
        match = REASON.match(tidy(reason))
        if match:
            word, ref, message = match.groups()
            fired_refs.add(ref)
            name = OTHER_NAMES.get(ref) or next((RULE_TYPE_NAMES[r.type] for r in applies if r.id == ref),
                                                RULE_TYPE_NAMES["unusual_payment"] if ref == "F1" else "Company policy")
            fired.append(("BLOCK" if word == "Blocked" else "ESCALATE", name, message[:1].upper() + message[1:]))

    passed = [("PASS", RULE_TYPE_NAMES.get(r.type, "Company policy"), "") for r in applies if r.id not in fired_refs]
    # The unusual payment check runs last, only on vendor payments every hard rule allowed.
    if f1_on and entry["kind"] == "vendor" and not fired:
        passed.append(("PASS", RULE_TYPE_NAMES["unusual_payment"], ""))

    known = {r.id for r in applies} | {"F1"} | set(OTHER_NAMES)
    count = ALLOWED_COUNT.match(entry["reasons"][0]) if entry["reasons"] else None
    if (count and int(count.group(1)) != len(passed)) or not fired_refs <= known:
        passed = [("PASS", "Every other rule that applies", "")]
    return passed, fired


def mode_chart(results: dict):
    """Bar chart: bad payments sent with no human, for the three ways of running the agent."""
    summary, repeats = results["layer2"]["summary"], results["repeats"]
    rows = [{"mode": MODE_LABELS[m], "sent": summary["modes"][m]["unsafe_paid"],
             "label": f"{summary['modes'][m]['unsafe_paid']} of {summary['modes'][m]['should_stop']}"}
            for m in MODES]
    total = summary["modes"]["guardrail"]["should_stop"]
    base = alt.Chart(alt.Data(values=rows)).encode(
        y=alt.Y("mode:N", sort=[MODE_LABELS[m] for m in MODES], title=None,
                axis=alt.Axis(labelFontSize=13, labelLimit=220)))
    bars = base.mark_bar(color=RED, cornerRadiusEnd=4).encode(
        x=alt.X("sent:Q", scale=alt.Scale(domain=[0, total]), title="Bad payments sent with no human",
                # A few round ticks (0, 22, 44, 66): the bars carry their own labels.
                axis=alt.Axis(values=[round(total * n / 3) for n in range(4)], grid=False)))
    labels = base.mark_text(align="left", dx=6, fontSize=15, fontWeight="bold").encode(
        x="sent:Q", text="label:N",
        color=alt.condition(alt.datum.sent == 0, alt.value(GREEN), alt.value(RED)))
    st.caption(f"**{attack_label(summary, repeats)}.**")
    st.altair_chart((bars + labels).properties(height=160),
                    alt=f"Bad payments sent with no human: " + ", ".join(f"{r['mode']} {r['label']}" for r in rows))


# ---------- attack replay (Assurance) ----------

def bad_count(run: dict) -> int:
    """Every unsafe thing in one run: bad invoices paid, double pays, extra payments, cash breaches."""
    return (sum(lab.is_unsafe(v["outcome"]) for v in run["invoices"].values())
            + len(run["extra_payments"]) + len(run["minimum_breaches"]))


# The scenario types the replay opens on, in order of preference: an agent with outdated instructions
# (caught on every repeat), then an over-limit invoice. Both show the guardrail stopping a payment the
# agent really tried.
REPLAY_PREFERRED = ("policy_drift", "amount_limit")


def replay_repeats(runs: list[dict]) -> tuple[dict, dict]:
    """For each scenario where the prompt-only agent paid a bad invoice: the repeats where, with the
    guardrail, the agent submitted that invoice and the guardrail blocked it or sent it to a person
    (caught), and the repeats where the agent never submitted it (held back)."""
    caught, held_back = {}, {}
    for b in runs:
        if b["mode"] != "baseline":
            continue
        bad = [k for k, v in b["invoices"].items() if lab.is_unsafe(v["outcome"])]
        g = next((r for r in runs if r["mode"] == "guardrail" and r["scenario"] == b["scenario"]
                  and r["repeat"] == b["repeat"]), None)
        if not bad or g is None:
            continue
        outcomes = [g["invoices"][k]["outcome"] for k in bad if k in g["invoices"]]
        if any(o in ("blocked", "escalated") for o in outcomes):
            caught.setdefault(b["scenario"], []).append(b["repeat"])
        elif any(o == "held" for o in outcomes):
            held_back.setdefault(b["scenario"], []).append(b["repeat"])
    for repeats_ in (*caught.values(), *held_back.values()):
        repeats_.sort()
    return caught, held_back


def guardrail_saw(results: dict) -> dict:
    """How each attack invoice result ended in guardrail mode: the agent never submitted it, or it
    reached the guardrail and was held for a person, blocked, or (a failure) paid with no person."""
    outcomes = [v["outcome"] for r in results["layer2"]["runs"] if r["mode"] == "guardrail"
                for v in r["invoices"].values() if v["expected"] in lab.SHOULD_STOP]
    escalated, blocked = outcomes.count("escalated"), outcomes.count("blocked")
    paid = sum(lab.is_unsafe(o) for o in outcomes)
    return {"total": len(outcomes), "agent_held_back": outcomes.count("held"), "escalated": escalated,
            "blocked": blocked, "paid": paid, "reached": escalated + blocked + paid}
