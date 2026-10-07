"""Activity: every decision the guardrail made, searchable, with the full record behind each one."""

import re

import streamlit as st

from ui.shared import (
    badge, dollars, esc, open_log, open_store, plain_reason, product_banner, tidy, rule_names, word,
)

product_banner()
st.title("Activity", anchor=False)
st.caption("Every payment the agent tried: what it asked for, when, the policy in force, the decision "
           "and the exact reason, and who approved it if a person did.")

store = open_store()
names = rule_names(store)
store.close()
log = open_log()
entries = log.all_entries()
log.close()

if not entries:
    st.info("No activity yet.", icon=":material/receipt_long:")
    st.stop()


def why(entry: dict) -> str:
    """The first reason, in plain words without markdown, for the table."""
    reasons = [r for r in entry["reasons"] if not r.startswith("Allowed")]
    if not reasons:
        return "Passed every check"
    return re.sub(r"\*\*|\\", "", plain_reason(reasons[0], names)) + (
        f" (+{len(reasons) - 1} more)" if len(reasons) > 1 else "")


rows = [{
    "When (simulated)": (e["request_time"] or "").replace("T", " "),
    "Payee": e["to_vendor"] or f"Our {e['to_internal_account']} account",
    "Invoice": e["invoice_id"] or "Transfer",
    "Amount": dollars(e["amount"]) if e["amount"] else "",
    "Decision": word(e["decision"]),
    "Status now": word(e["status"]),
    "Why": why(e),
    "Approved or rejected by": e["reviewer"] or "",
    "Policy version": e["policy_version"],
    # Kept for search and for picking a record, but not shown in the table.
    "_id": e["id"],
    "_exact": " ".join(tidy(r) for r in e["reasons"]),
} for e in reversed(entries)]

search, status_col = st.columns([3, 2])
text = search.text_input("Search", placeholder="Vendor, invoice, amount, reason, person...",
                         icon=":material/search:")
statuses = status_col.pills("Status now", sorted({r["Status now"] for r in rows}), selection_mode="multi")

shown = [r for r in rows
         if (not text or text.casefold() in " ".join(str(v) for v in r.values()).casefold())
         and (not statuses or r["Status now"] in statuses)]
st.caption(f"{len(shown)} of {len(rows)} payments")
st.table([{k: v for k, v in r.items() if not k.startswith("_")} for r in shown], hide_index=True)

# ---------- one full record ----------
if shown:
    with st.expander("Full record for one payment"):
        labels = {r["_id"]: f"{r['When (simulated)']} · {r['Amount']} to {r['Payee']} ({r['Invoice']})"
                  for r in shown}
        entry_id = st.selectbox("Payment", list(labels), format_func=labels.get)
        e = next(x for x in entries if x["id"] == entry_id)
        st.markdown(f"{badge(e['decision'])} → {badge(e['status'])} " +
                    esc(f"· policy version {e['policy_version']}"))
        st.markdown("**Reasons**")
        for reason in e["reasons"]:
            st.markdown(f"- {plain_reason(reason, names)}")
        st.markdown("**Rule references**")
        for reason in e["reasons"]:
            st.markdown(f"- {esc(tidy(reason))}")
        st.caption(f"Received {e['request_time'].replace('T', ' ')} from the company's AI agent · decided "
                   f"{e['decided_at'].replace('T', ' ')}"
                   + (f" · {e['reviewer']} decided at {e['reviewed_at'].replace('T', ' ')}" if e["reviewer"] else ""))
        invoice_text = e["request"].get("invoice_text") if isinstance(e["request"], dict) else None
        if invoice_text:
            st.markdown("**The invoice text the agent read.** It's kept for the record; the guardrail "
                        "never reads it.")
            st.code(invoice_text, language=None, wrap_lines=True)
