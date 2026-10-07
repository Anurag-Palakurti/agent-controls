"""Agent Guardrail + Crash Lab: the screens.

Run:  streamlit run app.py

Three sections in the sidebar:
  Story      the walkthrough to present from (opens first)
  Product    what a bank's business client sees: Overview, Policies, Approvals, Activity, Assurance
  Simulator  demo only: a stand-in for the company's own agent, plus the reset buttons
Everything is simulated: no real money and no real banks.
"""

import streamlit as st

from ui.shared import api_key_found

st.set_page_config(page_title="Agent Controls", page_icon=":material/shield:", layout="wide")

pages = st.navigation({
    "Story": [
        st.Page("ui/story.py", title="Story", icon=":material/auto_stories:", default=True),
    ],
    "Product": [
        st.Page("ui/overview.py", title="Overview", icon=":material/dashboard:"),
        st.Page("ui/policies.py", title="Policies", icon=":material/gavel:"),
        st.Page("ui/approvals.py", title="Approvals", icon=":material/pending_actions:"),
        st.Page("ui/activity.py", title="Activity", icon=":material/receipt_long:"),
        st.Page("ui/assurance.py", title="Assurance", icon=":material/verified_user:"),
    ],
    "Simulator": [
        st.Page("ui/simulator.py", title="Simulator", icon=":material/smart_toy:"),
    ],
})

with st.sidebar:
    # Every human approval (rules and payments) is recorded under this name.
    st.text_input("Your name (for approvals)", key="reviewer", placeholder="e.g. Jordan Lee, Treasury")
    st.caption("AI connection: " + ("ready" if api_key_found() else "**not set up** (add the key to .env)"))
    st.caption("Keystone Fabrication Co. and its bank are made up. No real money, banks, or accounts.")

pages.run()
