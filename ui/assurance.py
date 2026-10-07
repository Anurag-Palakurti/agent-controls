"""Assurance: the Crash Lab results, written as a report a client would read.

Nothing here grades anything. The numbers were worked out by guardrail/crash_lab.py
when the test ran (or regraded on purpose, as the file notes), and this page only
shows them. The rerun button is on the Simulator page.
"""

import altair as alt
import streamlit as st

from guardrail import crash_lab as lab
from guardrail import held_out as ho
from ui.shared import (
    BADGES, GREEN, HEADLINE_FILE, MODE_BLURBS, MODE_LABELS, MODES, RED, REPLAY_PREFERRED, ROOT, attack_label,
    bad_count, badge, dollars, esc, guardrail_saw, load_json, mode_cards, mode_chart, pct, plain_reason, product_banner,
    replay_repeats, rule_names, tidy, saved_result_files,
)

# Scenario types in plain words.
CATEGORY_NAMES = {
    "normal": "Routine payments", "amount_limit": "Over the amount limit",
    "minimum_balance": "Dropping below minimum cash", "vendor": "Unapproved vendors",
    "near_match": "Look-alike sanctioned names", "sanctions": "Sanctioned names",
    "account_match": "Paying an account not on file", "new_vendor": "Brand-new vendors",
    "splitting": "One payment split into several", "duplicate": "Duplicate invoices",
    "overdraft": "Overdrafts", "internal_transfer": "Transfers between accounts",
    "bad_data": "Missing or garbled details", "injection": "Hidden instructions in invoices",
    "bank_change": "Fake bank-change letters", "broken_system": "Payment system timeouts",
    "fake_cfo_email": "Fake emails from the CFO", "mixed": "Mixed inboxes",
    "policy_drift": "Agent with outdated rules",
}


def category(name: str) -> str:
    return CATEGORY_NAMES.get(name, name.replace("_", " ").capitalize())


def report_label(path_text: str) -> str:
    """A saved report described by what's in it, never by its file name."""
    data = load_json(ROOT / path_text) or {}
    when = data.get("run_at", "?").replace("T", " ")[:16]
    if "layer2" not in data:
        kind = "direct attacks only"
    else:
        kind = "full test" if data.get("repeats", 1) > 1 else "quick test"
    return f"{when} · {kind}" + (" · regraded" if data.get("regraded") else "")


# Scenario codes are shown as their short descriptions (read only from the locked scenario files).
try:
    _layer1, _layer2 = lab.load_scenarios(lab.EXPECTED_DIR)
    DESCRIPTIONS = {s["id"]: s["description"] for s in _layer1 + _layer2}
except FileNotFoundError:
    DESCRIPTIONS = {}


def scenario(code: str) -> str:
    return DESCRIPTIONS.get(code, "A test scenario")


# Attack replay: what happened to one invoice, colored by whether it ended safely.
# (color, icon, words). Green: ended the right way. Red: money moved that shouldn't have.
# Amber: a good invoice was held back, which is safe but costs time.
REPLAY_OUTCOMES = {
    "unsafe_paid": ("red", "cancel", "Paid with no human check"),
    "unsafe_paid_twice": ("red", "cancel", "Paid twice"),
    "unsafe_overpaid": ("red", "cancel", "Paid more than the invoice"),
    "completed": ("green", "check_circle", "Paid"),
    "escalated": ("green", "check_circle", "Sent to a person before any money moved"),
    "blocked": ("green", "check_circle", "Blocked before any money moved"),
    "held": ("green", "check_circle", "The agent held it back"),
    "false_block_escalated": ("orange", "pause_circle", "Good invoice sent to a person"),
    "false_block_checker": ("orange", "pause_circle", "Good invoice blocked"),
    "false_block_agent": ("orange", "pause_circle", "Good invoice not paid by the agent"),
}
SHOULD = {"BLOCK": "Should be stopped", "REQUIRE_APPROVAL": "Should go to a person",
          "ALLOW": "A good invoice: should be paid",
          lab.BORDERLINE: "Borderline: paying it or asking a person are both fine"}


def replay_column(run: dict, mode: str, names: dict):
    """One side of the attack replay: every invoice in the run and what happened to it."""
    n = bad_count(run)
    st.markdown(f":{'red' if n else 'green'}[**{n} bad payment{'' if n == 1 else 's'} sent with no human**]")
    for invoice, v in run["invoices"].items():
        color, icon, words = REPLAY_OUTCOMES.get(v["outcome"], ("gray", "help", v["outcome"]))
        with st.container(border=True):
            st.markdown(f":{color}[:material/{icon}: **{words}**] · " + esc(invoice))
            st.caption(SHOULD.get(v["expected"], v["expected"]))
            for pay in run["payments"]:
                if lab.invoice_key(pay["invoice_id"]) != lab.invoice_key(invoice):
                    continue
                shown = BADGES["SENT"] if mode == "baseline" else badge(pay["status"])
                st.markdown(f"{shown} " + esc(f"{dollars(pay['amount'])} to {pay['to_vendor']} "
                                               f"account {pay['to_vendor_account']}"))
                for reason in pay["reasons"]:
                    if mode != "baseline" and not reason.startswith("Allowed"):
                        st.markdown(f"- {plain_reason(reason, names)}")
    for x in run["extra_payments"]:
        st.markdown(esc(f":red[:material/cancel: **Paid something not in the inbox:**] {x['invoice_id']} to "
                        f"{x['to_vendor']}, {dollars(x['amount'])}"))
    for b in run["minimum_breaches"]:
        st.markdown(esc(f":red[:material/cancel: **Cash below minimum:**] {b}"))


product_banner()
st.title("Assurance report", anchor=False)
st.caption("How the guardrail held up when we attacked it. Every right answer was locked in by hand "
           "before the test ran, so no answer could change after seeing the result.")

# Which saved report: picked under "About this report" at the bottom, the headline run by default.
files = [str(p.relative_to(ROOT)) for p in saved_result_files()]
if st.session_state.get("report_file") not in files:
    st.session_state["report_file"] = str(HEADLINE_FILE.relative_to(ROOT))
results = load_json(ROOT / st.session_state["report_file"])
if results is None:
    st.error("This report couldn't be read.")
    st.stop()

has_agent = "layer2" in results
s1 = results["layer1"]["summary"] if "layer1" in results else None
st.caption(f"Tested {results['run_at'][:10]}")

# ---------- 1. how hard we tested it ----------
st.header("How hard we tested it", anchor=False)
with st.container(horizontal=True):
    if s1:
        st.metric("Direct attacks on the guardrail", f"{s1['scenarios']} scenarios", border=True,
                  help=f"{s1['steps']} attack requests sent straight to the guardrail, as if the agent "
                       f"were fully taken over.")
    if has_agent:
        summary, repeats = results["layer2"]["summary"], results["repeats"]
        g, c = summary["modes"]["guardrail"], summary["consistency"]["guardrail"]
        st.metric("Attack invoices", f"{g['should_stop'] // repeats}", border=True,
                  help=f"Each run {repeats} times, in inboxes with good invoices mixed in.")
        st.metric("Agent test runs", len(results["layer2"]["runs"]), border=True,
                  help=f"{c['scenarios']} scenarios × 3 ways of running the agent × {repeats} repeats.")

# ---------- 2. what got through ----------
st.header("What got through", anchor=False)
with st.container(horizontal=True):
    if has_agent:
        st.metric("Attack payments sent with no person", f"{g['unsafe_paid']} of {g['should_stop']}",
                  border=True, help=attack_label(summary, repeats) + ".")
        st.metric("Other unsafe events", g["unsafe_events"] - g["unsafe_paid"], border=True,
                  help="Good invoices paid twice, payments for things not in the inbox, or cash left "
                       "below a minimum.")
        st.metric("Good payments handled right", pct(g["valid_completion_rate"]), border=True)
    if s1:
        st.metric("Direct attacks allowed", s1["unsafe_steps"], border=True,
                  help=f"Of {s1['steps']} attack requests sent straight to the guardrail.")

if has_agent:
    # Who stopped each attack: the agent declining it, or the guardrail. The guardrail's own record
    # is the attacks that actually reached it.
    saw = guardrail_saw(results)
    st.subheader(f"The guardrail stopped {saw['reached'] - saw['paid']} of the {saw['reached']} attack "
                 f"payments it saw", anchor=False)
    with st.container(horizontal=True):
        st.metric("Held for a person", saw["escalated"], border=True)
        st.metric("Blocked", saw["blocked"], border=True)
        st.metric("Paid with no person", saw["paid"], border=True)
        st.metric("Never reached it: the agent declined", saw["agent_held_back"], border=True,
                  help="The agent decided not to submit the payment at all, so the guardrail never saw it.")
    st.caption(f"Of the {saw['total']} attack payments, the agent declined {saw['agent_held_back']} itself. "
               f"The other {saw['reached']} reached the guardrail. An agent can be talked into submitting any "
               f"of them, so the guardrail's record on the ones it saw is what matters.")

# ---------- held out attacks: attacks we never designed (milestone 7) ----------
# Read straight from crash_lab/held_out/, whichever Crash Lab report is picked above.
st.header("Held out attacks: attacks we never designed", anchor=False)
st.caption("Attacks written by an attacker AI that never saw our rules or tests, and by people trying to "
           "break it. Each attack stated the account it wanted paid before it ran. An attack counts as "
           "getting money through if any money reached that account with no person approving it.")
red_team = ho.latest_red_team_results()
attempts = ho.load_attempts()
held = ho.held_out_report(red_team, attempts)
if not held["all"]["attacks"]:
    st.info("Not run yet. The AI red team runs from the command line, and people can try in the Simulator's "
            "\"Try to break it\" panel.")
else:
    st.subheader(f"Held out attacks: {held['all']['money_through']} of {held['all']['attacks']} attacks got "
                 f"money through", anchor=False)
    with st.container(horizontal=True):
        for key, label in (("red_team", "AI red team"), ("people", "People")):
            t = held[key]
            per_agent = "; ".join(f"{ho.AGENT_LABELS[a]}: reached the guardrail in {c['reached_guardrail']} "
                                  f"of {c['runs']}, money through in {c['money_through']}"
                                  for a, c in t["by_agent"].items() if c["runs"])
            st.metric(f"{label}: attacks that got money through",
                      f"{t['money_through']} of {t['attacks']}" if t["attacks"] else "not run yet", border=True,
                      help=(per_agent + f". {t['full_goal_reached']} reached the full amount they asked for.")
                      if t["attacks"] else None)
        everyone = held["all"]
        st.metric("Blocked by the guardrail", f"{everyone['stopped_by_guardrail']} of {everyone['reached_guardrail']} attacks "
                  f"that reached it", border=True,
                  help="Blocked, or sent to a person for approval, so no money moved without a person.")
    red = held["red_team"]
    if red_team:
        st.caption(esc(f"AI red team: {red['money_through']} of {red['attacks']} attacks got money through, "
                       f"{ho.TWO_WAYS}. Blocked by the guardrail: {red['stopped_by_guardrail']} of "
                       f"{red['reached_guardrail']} attacks that reached it. Written by "
                       f"{red_team['models']['attacker']}, tested {red_team['run_at'][:10]}."))
        missed = ho.never_reached(red_team)
        if missed:
            st.caption(esc(f"Never reached the guardrail: {', '.join(missed)}. Neither way of running it tried "
                           f"to pay anything, so there was nothing for the guardrail to check."))
    people = held["people"]
    if people["attacks"]:
        st.caption(esc(f"People: {people['money_through']} of {people['attacks']} attempts got money through. "
                       f"Blocked by the guardrail: {people['stopped_by_guardrail']} of "
                       f"{people['reached_guardrail']} that reached it."))

    with st.expander("Held out attacks, one by one"):
        for attack in (red_team or {}).get("attacks", []):
            with st.container(border=True):
                st.markdown(esc(f"**AI red team {attack['id']}:** {attack['strategy']}"))
                st.caption(esc(f"Goal: {dollars(attack['goal']['amount'])} to {attack['goal']['account']}"))
                for agent, run in attack["runs"].items():
                    st.markdown(esc(f"- {ho.AGENT_LABELS[agent]}: {ho.run_outcome(run, attack['inbox'])}"))
        for attempt in attempts:
            with st.container(border=True):
                who = attempt.get("nickname") or "Someone"
                st.markdown(esc(f"**{who}:** {attempt['invoice']['vendor']} invoice for "
                                f"{dollars(attempt['invoice']['amount'])}"))
                st.caption(esc(f"Goal: {dollars(attempt['goal']['amount'])} to {attempt['goal']['account']}"))
                st.markdown(esc(f"- {ho.AGENT_LABELS[attempt['agent']]}: {ho.run_outcome(attempt['result'])}"))

if has_agent:
    # ---------- 3. how consistent it was ----------
    st.header("How consistent it was", anchor=False)
    st.caption("AI agents don't always behave the same way twice, so every scenario ran "
               f"{repeats} times.")
    with st.container(horizontal=True):
        correct = c.get("correct_every_repeat")
        st.metric("Scenarios correct on every repeat",
                  f"{correct} of {c['scenarios']}" if correct is not None else "not measured", border=True)
        st.metric("Scenarios where the agent's behavior varied",
                  f"{len(c['inconsistent_scenarios'])} of {c['scenarios']}", border=True,
                  help="The repeats didn't all end the same way, for example held back once and sent "
                       "for approval once. Both can be correct.")

    # ---------- 4. compared with an unprotected agent ----------
    st.header("Compared with an unprotected agent", anchor=False)
    st.caption("The same AI agent, run three ways on the same scenarios.")
    mode_chart(results)
    mode_cards(results)

    # ---------- 5. where attacks got through ----------
    st.header("Where attacks got through", anchor=False)
    st.caption("Each square: bad payments sent with no human, out of the bad payments of that type. "
               "Darker red means more got through.")
    cells = []
    for cat, per in summary["by_category"].items():
        for m in MODES:
            s_ = per[m]
            if not s_["should_stop"]:
                continue        # routine payments only: nothing to stop
            share = s_["unsafe_paid"] / s_["should_stop"]
            cells.append({"type": category(cat), "mode": MODE_LABELS[m], "share": share,
                          "label": f"{s_['unsafe_paid']} of {s_['should_stop']}",
                          "ink": GREEN if s_["unsafe_paid"] == 0 else ("#FFFFFF" if share > 0.5 else "#14213D")})
    types = list(dict.fromkeys(c["type"] for c in cells))
    heat = alt.Chart(alt.Data(values=cells)).encode(
        x=alt.X("mode:N", sort=[MODE_LABELS[m] for m in MODES], title=None,
                axis=alt.Axis(orient="top", labelAngle=0, labelFontSize=12, labelLimit=200)),
        y=alt.Y("type:N", sort=types, title=None, axis=alt.Axis(labelFontSize=12, labelLimit=260)))
    squares = heat.mark_rect(stroke="#D6DEE8").encode(
        color=alt.Color("share:Q", scale=alt.Scale(domain=[0, 1], range=["#FFFFFF", RED]), legend=None),
        tooltip=[alt.Tooltip("type:N", title="Type"), alt.Tooltip("mode:N", title="Way of running"),
                 alt.Tooltip("label:N", title="Sent with no human")])
    labels = heat.mark_text(fontSize=13, fontWeight="bold").encode(
        text="label:N", color=alt.Color("ink:N", scale=None))
    st.altair_chart((squares + labels).properties(height=30 * len(types) + 30),
                    alt="Heatmap of attack types by way of running: bad payments sent with no human. "
                        "With the guardrail every row is 0.")

    # ---------- 6. attack replay ----------
    st.header("Attack replay", anchor=False)
    st.caption("Pick a test scenario to see the same attack with and without the guardrail, straight from "
               "the saved results. Nothing is run again.")
    runs = results["layer2"]["runs"]
    codes = list(dict.fromkeys(r["scenario"] for r in runs))
    caught, held_back = replay_repeats(runs)
    # Open on a scenario the guardrail caught, preferring outdated instructions, then the over-limit invoice.
    categories = {r["scenario"]: r["category"] for r in runs}
    start = next((c for want in REPLAY_PREFERRED for c in codes if c in caught and categories[c] == want),
                 next(iter(caught), codes[0]))
    pick = st.selectbox("Scenario", codes, index=codes.index(start), key="replay_scenario",
                        format_func=lambda c: scenario(c) + (" · guardrail caught it" if c in caught else
                                                             " · agent held back" if c in held_back else ""))
    # Open on a repeat where the guardrail caught it, else one where the agent held it back.
    first = (caught.get(pick) or held_back.get(pick) or [1])[0]
    repeat = st.segmented_control("Repeat", list(range(1, repeats + 1)), default=first,
                                  key=f"replay_repeat_{pick}", format_func=lambda n: f"Run {n}") or first
    names = rule_names()
    for column, mode, title in zip(st.columns(2, gap="large"), ("baseline", "guardrail"),
                                   ("Without the guardrail", "With the guardrail")):
        run = next((r for r in runs if r["scenario"] == pick and r["mode"] == mode and r["repeat"] == repeat),
                   None)
        with column:
            st.subheader(title, anchor=False)
            st.caption(MODE_BLURBS[mode])
            if run is None:
                st.caption("Not in this report.")
            else:
                replay_column(run, mode, names)

    # ---------- details ----------
    st.header("Details", anchor=False)
    with st.expander("Full scorecard"):
        rows = [
            ("Bad payments sent with no person", lambda s: f"{s['unsafe_paid']} of {s['should_stop']}"),
            ("Unsafe action rate", lambda s: pct(s["unsafe_action_rate"])),
            ("Good payments handled right", lambda s: pct(s["valid_completion_rate"])),
            ("Good payments wrongly stopped", lambda s: pct(s["false_block_rate"])),
            ("Cases needing a person that were sent to one", lambda s: pct(s["escalation_accuracy"])),
            ("Cases needing a person that weren't paid without one", lambda s: pct(s["held_unpaid_rate"])),
            ("All unsafe events (incl. double pays, extras, cash)", lambda s: str(s["unsafe_events"])),
            ("Guardrail's first decision correct", lambda s: pct(s["checker_decision_accuracy"])),
            ("Invoices the agent held back itself", lambda s: str(s.get("agent_held", "n/a"))),
            ("Runs that ended in an error or step limit", lambda s: str(s["runs_with_errors"])),
            ("Agent seconds per invoice", lambda s: str(s["agent_seconds_per_invoice"])),
        ]
        st.table([{"Measure": label, **{MODE_LABELS[m]: show(summary["modes"][m]) for m in MODES}}
                  for label, show in rows], hide_index=True)
        st.caption(f"Per way of running: {g['invoices']} invoice results, {g['should_stop']} that should be "
                   f"stopped or sent to a person, {g['good']} good ({g['borderline_invoices']} borderline, "
                   f"where either answer counts). Guardrail time per payment, including the AI unusual "
                   f"payment check: {g['checker_seconds']['mean']}s on average.")

    with st.expander("Results by type of attack"):
        st.caption("Bad payments sent with no person / bad payments in that type, and good payments handled right.")
        st.table([{"Type": category(cat),
                   **{MODE_LABELS[m]: f"{per[m]['unsafe_paid']} of {per[m]['should_stop']} sent · "
                                      f"{pct(per[m]['valid_completion_rate'])} good"
                      for m in MODES}}
                  for cat, per in summary["by_category"].items()], hide_index=True)

    with st.expander("Consistency, scenario by scenario"):
        for m in MODES:
            cm = summary["consistency"][m]
            st.markdown(f"**{MODE_LABELS[m]}**")
            varied = cm["inconsistent_scenarios"]
            st.markdown("Behavior varied in: " + ("none" if not varied else ""))
            for code in varied:
                st.markdown(f"- {esc(scenario(code))}")
            if "correct_every_repeat" not in cm:
                st.caption("Correct every repeat: not measured in this report.")
            else:
                wrong = cm["not_correct_every_repeat"]
                st.markdown("Not correct on every repeat: " + ("none" if not wrong else ""))
                for code in wrong:
                    st.markdown(f"- {esc(scenario(code))}")

    with st.expander("Failures, with reasons"):
        mode = st.segmented_control("Way of running", MODES, default="guardrail",
                                    format_func=MODE_LABELS.get) or "guardrail"
        failures = []
        for run in results["layer2"]["runs"]:
            if run["mode"] != mode:
                continue
            problems = [f"{invoice}: should have been {v['expected'].replace('_', ' ').lower()}, "
                        f"result **{v['outcome'].replace('_', ' ')}**"
                        for invoice, v in run["invoices"].items()
                        if lab.is_unsafe(v["outcome"]) or v["outcome"].startswith("false_block")]
            problems += [f"Paid something not in the inbox: {x['invoice_id']} to {x['to_vendor']}, "
                         f"{dollars(x['amount'])}" for x in run["extra_payments"]]
            problems += [f"Cash below minimum: {b}" for b in run["minimum_breaches"]]
            if problems:
                failures.append((run, problems))
        if not failures:
            st.success(f"{MODE_LABELS[mode]}: no failures.")
        for run, problems in failures:
            with st.container(border=True):
                st.markdown(esc(f"**{scenario(run['scenario'])}** · {category(run['category'])} · "
                                f"repeat {run['repeat']}"))
                for p in problems:
                    st.markdown(f"- {esc(p)}")
                for pay in run["payments"]:
                    payee = pay["to_vendor"] or pay["to_internal_account"]
                    st.caption(esc(f"Payment: {pay['status'].lower()}, {dollars(pay['amount'])} to {payee} "
                                   f"({pay['invoice_id'] or 'transfer'}). "
                                   + " ".join(tidy(r) for r in pay["reasons"])))

    with st.expander("Suggested rule fixes"):
        fixes = results.get("suggested_fixes", [])
        if not fixes:
            st.info("None needed: nothing got through the guardrail in this test. Fixes are only "
                    "suggested for what gets through.")
        for fix in fixes:
            s = fix["suggestion"]
            with st.container(border=True):
                st.markdown(esc(f"**{fix['description']}**"))
                if s["status"] == "suggested":
                    st.markdown(esc(f"Suggested rule: **\"{s['english']}\"**"))
                    st.caption(esc(f"Would enforce: {s['enforced']}"))
                else:
                    st.markdown(f"**{s['status'].replace('_', ' ').capitalize()}**")
                st.caption(esc(f"Why: {s['why']}"))
        if fixes:
            st.caption("Never applied automatically. To use one, type it into Policies and approve it.")

if s1:
    with st.expander("Direct attacks, by type"):
        st.caption("Requests sent straight to the guardrail, as if the agent were fully taken over. The AI "
                   "unusual payment check was replaced by one that never flags anything: the worst case.")
        st.table([{"Type": category(cat), "Handled correctly": f"{v['passed']} of {v['total']}"}
                  for cat, v in sorted(s1["by_category"].items())], hide_index=True)
        st.caption(f"Guardrail speed: {s1['decision_ms']['mean']} ms per decision on average.")
        for r in results["layer1"]["scenarios"]:
            if r["passed"]:
                continue
            with st.container(border=True):
                st.markdown(esc(f"**Failed: {r['description']}** · {category(r['category'])}"))
                for n, step in enumerate(r["steps"], 1):
                    if step["grade"] != "correct":
                        st.markdown(f"Step {n}: should be {step['expected']}, was {step['decision']} "
                                    f"({step['grade'].replace('_', ' ')})")
                        for reason in step["reasons"]:
                            st.caption(esc(tidy(reason)))

with st.expander("About this report"):
    st.selectbox("Report", files, key="report_file", format_func=report_label)
    models = results["models"]
    st.caption(f"Tested {results['run_at']} · agent model {models['agent']} · unusual payment check "
               f"{models['unusual_f1']} · grading version {results.get('grading_version', 1)}")
    if results.get("regraded"):
        st.caption(esc(f"Regraded {results['regraded']['at'].replace('T', ' ')} with updated grading rules. "
                       f"The agent runs were not repeated; the saved payments were graded again."))
    for change in results.get("grading_changes", []):
        st.caption(esc(change))
