"""Policies: write a rule in English, review the translation, and approve or reject it.

The AI only proposes. Code checks its answer and writes its own description of
what will be enforced, and nothing goes live until a named person approves it.
(The reset button for rehearsals lives on the Simulator page.)
"""

import streamlit as st

from guardrail.compiler import compile_rule
from guardrail.rules import USER_RULE_SETTINGS, describe_rule, load_rules
from guardrail.unusual import UNUSUAL_RULE
from ui.shared import (
    RULE_TYPE_NAMES, approver, current_data, esc, make_ai_client, open_store, product_banner, reviewer_name,
    version_change,
)

product_banner()
st.title("Policies", anchor=False)
st.caption("Write payment rules in plain English. AI translates them, code checks the translation, "
           "and nothing goes live until a person approves it.")

store = open_store()
data = current_data(store)


def translate(english: str):
    """Compile a rule and keep the result on screen until it's approved, rejected, or replaced."""
    client = make_ai_client()
    if client is None:
        return
    with st.spinner("Translating your rule..."):
        st.session_state["compiled"] = compile_rule(english, data, client)


# ---------- write a rule ----------
with st.form("new_rule"):
    english = st.text_area("New rule", placeholder="e.g. Payments over $100,000 need approval.", height=80)
    if st.form_submit_button("Translate", type="primary", icon=":material/translate:") and english.strip():
        translate(english)

result = st.session_state.get("compiled")
if result is not None:
    with st.container(border=True):
        if result.status == "compiled":
            st.subheader("Ready for your approval", anchor=False)
            c1, c2, c3 = st.columns(3, border=True)
            c1.markdown("**You wrote**")
            c1.write(esc(result.english))
            c2.markdown("**How the AI read it**")
            c2.write(esc(result.ai_summary))
            c3.markdown("**What will be enforced**")
            c3.write(esc(result.enforced))
            c3.caption("Written by code from the exact rule, not by the AI.")
            if result.heads_up:
                st.warning(esc(result.heads_up))

            name = reviewer_name()
            with st.container(horizontal=True):
                if st.button("Approve", type="primary", disabled=not name, icon=":material/check:"):
                    try:
                        store.approve(result, name)
                        st.session_state["compiled"] = None
                        st.session_state["policy_message"] = (
                            f"Live now in policy version {store.current_version()}, approved by {name}.")
                        st.rerun()
                    except ValueError as error:
                        st.error(esc(f"Not saved: {error}"))
                if st.button("Reject", icon=":material/close:"):
                    st.session_state["compiled"] = None
                    st.session_state["policy_message"] = "Rejected. Nothing was saved."
                    st.rerun()
            if not name:
                st.caption("Type your name in the sidebar to approve. The person who approves a rule owns it.")

        elif result.status == "needs_clarification":
            st.subheader("One more detail needed", anchor=False)
            st.caption(esc(f"You wrote: {result.english}"))
            st.info(esc(result.question), icon=":material/help:")
            st.write("Pick one. It will be translated again from the start:")
            for n, option in enumerate(result.options):
                if st.button(esc(option), key=f"option_{n}"):
                    translate(option)
                    st.rerun()
            if result.heads_up:
                st.warning(esc(result.heads_up))

        else:  # cannot_enforce
            st.subheader("This rule can't be enforced", anchor=False)
            st.caption(esc(f"You wrote: {result.english}"))
            st.error(esc(result.reason))
            if result.heads_up:
                st.warning(esc(result.heads_up))
            st.caption("Nothing was saved. Rules can only add restrictions, and only of the kinds the "
                       "guardrail knows how to check.")

message = st.session_state.pop("policy_message", None)
if message:
    st.success(esc(message))

# ---------- the rules in force ----------
st.header(f"Rules in force (version {store.current_version()})", anchor=False)
rows = [{
    "Rule": RULE_TYPE_NAMES.get(rule.type, "Company policy"),
    "What it says": rule.text,
    "If broken": "Blocked" if rule.action == "BLOCK" else "Needs approval",
    "Approved by": approver(rule),
} for rule in data["rules"]]
rows.append({"Rule": RULE_TYPE_NAMES["unusual_payment"],
             "What it says": "Payments that look unusual go to a person. AI judgment: it can only ask for "
                             "a person, never approve.",
             "If broken": "Needs approval", "Approved by": "Part of the service"})
st.table(rows, hide_index=True)
st.caption("Every rule runs on every payment, and the strictest answer wins: any block means blocked, "
           "otherwise anything needing approval goes to a person.")

with st.expander("Policy numbers and exact settings"):
    st.caption("Reasons in Approvals and Activity name rules by these numbers.")
    st.table([{"Policy number": r.id, "Rule": RULE_TYPE_NAMES.get(r.type, "Company policy"),
               "Exactly what is enforced": describe_rule(r.type, r.settings, r.action)
               if r.type in USER_RULE_SETTINGS else r.text}
              for r in data["rules"] + [UNUSUAL_RULE]], hide_index=True)

# ---------- version history ----------
st.header("Version history", anchor=False)
history = store.history()
st.table([{"Version": v["version"], "Changed at": v["changed_at"].replace("T", " "),
           "Approved by": version_change(v)[0], "Change": version_change(v)[1]}
          for v in reversed(history)], hide_index=True)
with st.expander("See the rules in an earlier version"):
    version = st.selectbox("Version", [v["version"] for v in reversed(history)])
    st.table([{"Rule": RULE_TYPE_NAMES.get(r.type, "Company policy"), "What it says": r.text,
               "Approved by": approver(r)}
              for r in load_rules(store.get_rules(version), list(data["accounts"]))], hide_index=True)

store.close()
