"""Approvals: payments the guardrail sent to a person, with the reasons.

Approve and Reject use the audit log's own approve() and reject(). Approving
re-checks the hard rules first, so a person can approve past an escalation but
never past a block (for example, if the balance has dropped since).
"""

import streamlit as st

from guardrail.audit_log import ApprovalBlocked
from ui.shared import (
    badge, current_data, dollars, esc, open_log, open_store, plain_reason, product_banner, tidy,
    reviewer_name, rule_names,
)

product_banner()
st.title("Approvals", anchor=False)
st.caption("Payments the guardrail held for a person. Nothing moves until someone approves it.")

store = open_store()
log = open_log()
data = current_data(store)
names = rule_names(store)
name = reviewer_name()

message = st.session_state.pop("queue_message", None)
if message:
    kind, text = message
    (st.success if kind == "ok" else st.error)(esc(text))
    if kind == "ok":
        # A quick pop-up too, so the decision registers even when the message is off screen.
        st.toast(esc(text), icon=":material/check_circle:" if text.startswith("Approved") else ":material/cancel:")

entries = log.all_entries()
pending = [e for e in entries if e["status"] == "PENDING"]
if pending and not name:
    st.warning("Type your name in the sidebar to approve or reject. Every decision is recorded under it.")
if not pending:
    st.info("Nothing is waiting for approval.", icon=":material/task_alt:")

for e in pending:
    payee = e["to_vendor"] or f"our {e['to_internal_account']} account"
    with st.container(border=True):
        st.markdown(f"{badge('PENDING')} " + esc(
            f"**{dollars(e['amount'])} to {payee}** · "
            f"{'invoice ' + e['invoice_id'] if e['invoice_id'] else 'transfer between our accounts'}"))
        st.caption(f"From the {e['from_account']} account · to account {e['to_vendor_account'] or '-'} · "
                   f"requested {e['request_time'].replace('T', ' ')} by the company's AI agent")
        st.markdown("**Why it's waiting**")
        for reason in e["reasons"]:
            st.markdown(f"- {plain_reason(reason, names)}")
        with st.expander("Rule references"):
            for reason in e["reasons"]:
                st.markdown(f"- {esc(tidy(reason))}")
            st.caption(f"Checked under policy version {e['policy_version']}.")
        with st.container(horizontal=True):
            if st.button("Approve", key=f"approve_{e['id']}", type="primary", disabled=not name,
                         icon=":material/check:"):
                try:
                    log.approve(e["id"], name, data)
                    st.session_state["queue_message"] = (
                        "ok", f"Approved by {name}: {dollars(e['amount'])} to {payee} was sent.")
                except ApprovalBlocked as error:
                    st.session_state["queue_message"] = (
                        "error", f"Can't be approved: a hard rule now blocks this payment. {error}")
                except ValueError as error:
                    st.session_state["queue_message"] = ("error", str(error))
                st.rerun()
            if st.button("Reject", key=f"reject_{e['id']}", disabled=not name, icon=":material/close:"):
                try:
                    log.reject(e["id"], name)
                    st.session_state["queue_message"] = (
                        "ok", f"Rejected by {name}: {dollars(e['amount'])} to {payee}. No money moved.")
                except ValueError as error:
                    st.session_state["queue_message"] = ("error", str(error))
                st.rerun()

reviewed = [e for e in entries if e["reviewer"]]
if reviewed:
    st.header("Decided by a person", anchor=False)
    st.table([{"Decision": "Approved" if e["status"] == "APPROVED" else "Rejected",
                   "Amount": dollars(e["amount"]),
                   "Payee": e["to_vendor"] or e["to_internal_account"], "Invoice": e["invoice_id"],
                   "Decided by": e["reviewer"], "When": (e["reviewed_at"] or "").replace("T", " ")}
                  for e in reversed(reviewed)], hide_index=True)

log.close()
store.close()
