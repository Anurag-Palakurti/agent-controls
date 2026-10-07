"""Story: the walkthrough to present from, instead of slides.

Short sections in order, each with a button to the matching screen. Numbers come
from saved results. Anything the owner hasn't filled in yet (the fraud number,
user quotes) or that has no saved file (the compiler result) is simply left out,
never shown as a placeholder.
"""

import streamlit as st

from guardrail import held_out as ho
from ui.shared import (
    HEADLINE_FILE, LAYER1_BEFORE_FILE, compiler_first_run, esc, load_json, mode_cards, mode_chart,
)

# OWNER: fill these in. Each one stays hidden until it has text.
# One real fraud number, checked at its source (e.g. the latest FBI IC3 business email compromise figure).
FRAUD_NUMBER = ""          # e.g. "$X.X billion"
FRAUD_CONTEXT = ""         # e.g. "lost to business email compromise in 20XX"
FRAUD_SOURCE = ""          # e.g. "FBI Internet Crime Report 20XX"
# Three to five short quotes from user conversations: (quote, who, e.g. "Treasury analyst").
USER_QUOTES: list[tuple[str, str]] = []


def jump(label: str, page: str, key: str):
    """A button that opens the matching screen."""
    if st.button(label, key=key, icon=":material/arrow_forward:"):
        st.switch_page(page)


st.title("Agent Controls", anchor=False)
st.subheader("A safety layer between a company's AI agent and its money, and the proof that it holds.",
             anchor=False)

# ---------- the three hero numbers, all worked out from saved results ----------
results = load_json(HEADLINE_FILE)
held = ho.held_out_report(ho.latest_red_team_results(), ho.load_attempts())["all"]
with st.container(horizontal=True):
    if results and "layer2" in results:
        modes = results["layer2"]["summary"]["modes"]
        g, b = modes["guardrail"], modes["baseline"]
        st.metric("Attack payments that got through with the guardrail", f"{g['unsafe_paid']} of {g['should_stop']}",
                  border=True, help="Bad payments sent with no human, in the Crash Lab test.")
        st.metric("Got through with rules in the prompt only", f"{b['unsafe_paid']} of {b['should_stop']}",
                  border=True, help="The same agent with every rule written into its instructions, and no guardrail.")
    if held["reached_guardrail"]:
        st.metric("New attacks blocked (attacks we never designed)", f"{held['stopped_by_guardrail']} of {held['reached_guardrail']}",
                  border=True, help="Attacks we never designed, written by an attacker AI and by people: "
                                    "how many the guardrail stopped, of those that reached it.")

# ---------- 1. the problem ----------
st.header("The problem", anchor=False)
st.markdown(
    "AI agents can now read an invoice and send the payment themselves. One fake invoice with hidden "
    "instructions can talk an agent into wiring money to a scammer, and nobody can prove what the "
    "agent is and isn't allowed to do.")
if FRAUD_NUMBER:
    st.metric(FRAUD_CONTEXT or "Fraud losses", FRAUD_NUMBER, border=True)
    if FRAUD_SOURCE:
        st.caption(f"Source: {FRAUD_SOURCE}")
jump("See a trick invoice", "ui/simulator.py", "jump_problem")

# ---------- 2. why now ----------
st.header("Why now", anchor=False)
st.markdown(
    "AI agents are starting to take actions, not just answer questions, and moving money is next. "
    "Today most treasury AI stops at **recommending** payments: a person still presses send, "
    "because no one can show the agent will stay inside the company's rules.")
jump("See what a treasury team would see", "ui/overview.py", "jump_why")

# ---------- 3. how it works ----------
st.header("How it works", anchor=False)
st.mermaid_chart(
    """
flowchart LR
    agent["Company's AI agent<br/>wants to pay an invoice"] --> guard
    subgraph guard["Guardrail: checks every payment"]
        direction TB
        hard["Hard limits<br/>plain code"]
        fuzzy["Unusual payment check<br/>AI, can only ask for a person"]
    end
    policies["`Company policies: written in English, translated by AI, approved by a person.`"] -.-> guard
    guard --> allowed["Allowed"]
    guard --> person["Sent to a person"]
    guard --> blocked["Blocked"]
    allowed --> log[("Every decision logged<br/>with the exact reason")]
    person --> log
    blocked --> log
    classDef ok fill:#DCFCE7,stroke:#15803D,color:#14532D
    classDef wait fill:#FEF3C7,stroke:#B45309,color:#78350F
    classDef stop fill:#FEE2E2,stroke:#B91C1C,color:#7F1D1D
    classDef navy fill:#0B2A5B,stroke:#0B2A5B,color:#FFFFFF
    class allowed ok
    class person wait
    class blocked stop
    class agent navy
""",
    alt="Flow: the company's AI agent sends a payment to the guardrail, which checks it against company "
        "policies with plain-code hard limits and an AI unusual-payment check that can only ask for a "
        "person. The payment is allowed, sent to a person, or blocked, and every decision is logged.",
)
plain, ai, people = st.columns(3)
with plain.container(border=True):
    st.markdown(":material/lock: **Plain code** where certainty is possible")
    st.caption("Amount limits, minimum cash, approved vendors, bank account on file, daily totals, "
               "duplicates, no overdrafts, the sanctions list. Code can't be talked out of anything, "
               "and it never reads invoice text.")
with ai.container(border=True):
    st.markdown(":material/auto_awesome: **AI** where interpretation is needed")
    st.caption("Translating English policy into exact rules, and judging fuzzy cases like an unusual "
               "amount. AI can send a payment to a person, but it can never approve money.")
with people.container(border=True):
    st.markdown(":material/person: **People** where accountability matters")
    st.caption("A person approves every rule before it goes live, and decides every payment the "
               "guardrail holds. The log records who and when.")
jump("See policies in plain English", "ui/policies.py", "jump_how")

# ---------- 4. proof ----------
st.header("Proof", anchor=False)
st.markdown("We attacked the same AI agent three ways, with every right answer locked in before the test ran.")
if results and "layer2" in results:
    mode_chart(results)
    mode_cards(results)
else:
    st.error("The saved test results couldn't be read.")

before = load_json(LAYER1_BEFORE_FILE)
compiler = compiler_first_run()
extra = st.columns(2 if compiler else 1)
if before and results and "layer1" in results:
    b, a = before["layer1"]["summary"], results["layer1"]["summary"]
    with extra[0].container(border=True):
        st.markdown("**Direct attacks on the guardrail**, as if the agent were fully taken over")
        st.metric("Attack scenarios handled correctly", f"{a['scenarios_passed']} of {a['scenarios']}",
                  delta=f"from {b['scenarios_passed']} before fixes", delta_color="off")
        st.caption("Failures were fixed in code. The locked answers were never changed.")
if compiler:
    with extra[-1].container(border=True):
        st.markdown("**Policy translation**: English rules turned into exact rules")
        st.metric("Rules translated correctly", f"{compiler['correct']} of {compiler['cases']}")
        sentence = (f"{compiler['correct']} of {compiler['cases']} rules translated correctly on the first "
                    f"run, {compiler['wrong_rule']} translated into a wrong rule.")
        if compiler["misses"] and compiler["asked"] == compiler["misses"]:
            sentence += f" The {compiler['misses']} misses asked a clarifying question instead of guessing."
        st.markdown(esc(sentence))
        st.caption("First run, before any tuning.")
jump("Open the assurance report", "ui/assurance.py", "jump_proof")

# ---------- 5. business model ----------
st.header("Business model", anchor=False)
st.markdown(
    "**Built for businesses, sold by banks.** The user is a company's treasury team that wants an AI "
    "agent to pay bills and move cash safely. The bank offers it as a feature of the business platform "
    "its clients already use, the same way it already gives their employees payment limits and two "
    "person approval.")
for col, (title, text) in zip(st.columns(2), [
        ("The business", "Saves time without risking its money."),
        ("The bank", "Gets a new product and less fraud on its systems.")]):
    with col.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(text)
for col, (title, text) in zip(st.columns(2), [
        ("How it makes money", "The bank charges the business a monthly fee per AI agent connected, as "
                               "part of its treasury services package."),
        ("Deloitte's role", "Help banks design, build, and launch it, and run the assurance testing.")]):
    with col.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(text)
jump("See the client's console", "ui/overview.py", "jump_business")

# ---------- 6. competition ----------
st.header("Competition", anchor=False)
st.markdown(
    "Spend controls for AI agents already exist, mostly for agents buying things with cards or paying "
    "for software. Ours is different in three ways:")
for col, (title, text) in zip(st.columns(3), [
        ("Treasury money movement", "Wires, vendor payments, and cash minimums, not card purchases."),
        ("Works with any agent", "Offered by the bank, in front of whatever agent the company uses."),
        ("Comes with proof", "Every claim is backed by a test with answers locked in advance.")]):
    with col.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(text)
jump("See the proof", "ui/assurance.py", "jump_competition")

# ---------- 7. what users told us (only once there are quotes) ----------
if USER_QUOTES:
    st.header("What users told us", anchor=False)
    for quote, who in USER_QUOTES:
        with st.container(border=True):
            st.markdown(f"*“{esc(quote)}”*")
            st.caption(who)

# ---------- 8. next steps ----------
st.header("Next steps", anchor=False)
st.markdown("A bank would roll it out in three stages, so trust is earned before control is handed over.")
for col, (step, title, text) in zip(st.columns(3), [
        ("1", "Watch only", "Review the agent's past payments and report what would have been stopped."),
        ("2", "Block clear violations", "Stop only payments that break a hard rule. Everything else flows."),
        ("3", "Full control", "Every payment checked, with people deciding everything held for review.")]):
    with col.container(border=True):
        st.markdown(f"**Stage {step}: {title}**")
        st.caption(text)
st.markdown("Later, the same layer can sit in front of other agents that take real actions: refunds, "
            "lending, and fraud operations.")
jump("See the activity log", "ui/activity.py", "jump_next")
