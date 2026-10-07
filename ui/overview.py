"""Overview: the client's dashboard. Today's payments, what's waiting, the policy in force, recent
activity, and the latest assurance result. Read only."""

from datetime import datetime
from decimal import Decimal

import streamlit as st

import altair as alt

from ui.shared import (
    AMBER, GREEN, HEADLINE_FILE, RED, badge, dollars, esc, load_json, open_log, open_store, plain_reason,
    product_banner, rule_names, version_change,
)

product_banner()
st.title("Overview", anchor=False)

store = open_store()
log = open_log()
entries = log.all_entries()
names = rule_names(store)
policy = store.history()[-1]
version = store.current_version()
log.close()
store.close()

# "Today" is the simulated day of the most recent payment (the demo runs on a simulated clock).
times = [datetime.fromisoformat(e["request_time"]) for e in entries if e["request_time"]]
today = max(times).date() if times else None
todays = [e for e in entries if e["request_time"] and datetime.fromisoformat(e["request_time"]).date() == today]
pending = [e for e in entries if e["status"] == "PENDING"]
held = sum((Decimal(e["amount"]) for e in todays if e["status"] in ("PENDING", "BLOCKED") and e["amount"]),
           Decimal("0"))

st.caption(f"Today (simulated): {today:%B %d, %Y}" if today else
           "No payments yet. Use the Simulator to send the agent an inbox.")

with st.container(horizontal=True):
    st.metric("Payments checked", len(todays), border=True)
    st.metric("Allowed", sum(e["decision"] == "ALLOW" for e in todays), border=True)
    st.metric("Sent for approval", sum(e["decision"] == "REQUIRE_APPROVAL" for e in todays), border=True)
    st.metric("Blocked", sum(e["decision"] == "BLOCK" for e in todays), border=True)
    st.metric("Held for review or blocked", dollars(held), border=True,
              help="Blocked payments plus payments waiting for a person. A person can still approve "
                   "the waiting ones.")

# ---------- where today's money went ----------
# Grouped by where the money ended up: sent (by the guardrail or a person), waiting, or stopped.
WHERE = [("Allowed", ("ALLOWED", "APPROVED"), GREEN), ("Held for approval", ("PENDING",), AMBER),
         ("Blocked or rejected", ("BLOCKED", "REJECTED"), RED)]
if todays:
    rows = [{"where": label, "dollars": float(sum((Decimal(e["amount"]) for e in todays
                                                   if e["status"] in statuses and e["amount"]), Decimal("0"))),
             "count": sum(e["status"] in statuses for e in todays)}
            for label, statuses, _ in WHERE]
    for r in rows:
        r["label"] = f"{dollars(r['dollars'])} ({r['count']})"
    st.subheader("Where today's money went", anchor=False)
    base = alt.Chart(alt.Data(values=rows)).encode(
        y=alt.Y("where:N", sort=[w[0] for w in WHERE], title=None, axis=alt.Axis(labelFontSize=13)))
    bars = base.mark_bar(cornerRadiusEnd=4).encode(
        # At most 5 ticks with short labels: $0, $200K, $400K, $1.2M.
        x=alt.X("dollars:Q", title="Dollars", axis=alt.Axis(tickCount=5, labelExpr=(
            "datum.value >= 1e6 ? '$' + format(datum.value / 1e6, '~r') + 'M' : "
            "datum.value >= 1e3 ? '$' + format(datum.value / 1e3, '~r') + 'K' : '$' + datum.value"))),
        color=alt.Color("where:N", scale=alt.Scale(domain=[w[0] for w in WHERE], range=[w[2] for w in WHERE]),
                        legend=None),
        tooltip=[alt.Tooltip("where:N", title="Where"), alt.Tooltip("dollars:Q", title="Dollars", format="$,.2f"),
                 alt.Tooltip("count:Q", title="Payments")])
    text = base.mark_text(align="left", dx=6, fontSize=13).encode(x="dollars:Q", text="label:N")
    st.altair_chart((bars + text).properties(height=140),
                    alt="Today's money: " + ", ".join(f"{r['where']} {r['label']}" for r in rows))

left, right = st.columns([3, 2], gap="large")

# ---------- recent activity ----------
with left:
    st.subheader("Recent activity", anchor=False)
    if not entries:
        st.caption("Nothing yet.")
    for e in list(reversed(entries))[:8]:
        payee = e["to_vendor"] or f"our {e['to_internal_account']} account"
        with st.container(border=True):
            st.markdown(f"{badge(e['status'])} " + esc(f"**{dollars(e['amount'])}** to {payee}"))
            reasons = [r for r in e["reasons"] if not r.startswith("Allowed")]
            detail = plain_reason(reasons[0], names) if reasons else "Passed every check."
            st.caption(f"{e['invoice_id'] or 'Transfer'} · {e['request_time'][11:16]} · {detail}")
    if entries:
        if st.button("See all activity", icon=":material/arrow_forward:"):
            st.switch_page("ui/activity.py")

# ---------- waiting, policy, assurance ----------
with right:
    with st.container(border=True):
        st.subheader("Waiting for you", anchor=False)
        st.metric("Payments awaiting approval", len(pending))
        if pending:
            st.caption(esc(f"{dollars(sum(Decimal(e['amount']) for e in pending if e['amount']))} in total"))
        if st.button("Review approvals", type="primary" if pending else "secondary",
                     icon=":material/pending_actions:"):
            st.switch_page("ui/approvals.py")

    with st.container(border=True):
        st.subheader("Policy in force", anchor=False)
        st.metric("Active version", f"Version {version}")
        who, change = version_change(policy)
        st.caption(esc(f"Last change {policy['changed_at'].replace('T', ' ')} by {who}: {change}"))
        if st.button("Manage policies", icon=":material/gavel:"):
            st.switch_page("ui/policies.py")

    results = load_json(HEADLINE_FILE)
    if results and "layer2" in results:
        g = results["layer2"]["summary"]["modes"]["guardrail"]
        c = results["layer2"]["summary"]["consistency"]["guardrail"]
        with st.container(border=True):
            st.subheader(":material/verified_user: Assurance", anchor=False)
            st.markdown(f":green-badge[Tested {results['run_at'][:10]}] "
                        f"**{g['unsafe_paid']} of {g['should_stop']}** attack payments got through.")
            st.caption(f"Correct on every repeat in {c['correct_every_repeat']} of {c['scenarios']} "
                       f"test scenarios.")
            if st.button("Read the assurance report", icon=":material/arrow_forward:"):
                st.switch_page("ui/assurance.py")
