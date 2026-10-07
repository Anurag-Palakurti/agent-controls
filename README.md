# Agent Guardrail + Crash Lab

A safety layer between an AI treasury agent and a company's money, plus a crash test
that attacks it with 100+ scenarios.

All money, banks, and companies here are simulated.

## Run it on Windows

**1. Install Python 3.11.** Download it from [python.org](https://www.python.org/downloads/windows/)
and tick **"Add python.exe to PATH"** in the installer, or run `winget install Python.Python.3.11`.
Open a new PowerShell window and check it with `python --version`.

**2. Create and activate the virtual environment.** In PowerShell, from the project folder:

```powershell
python -m venv .venv            # one time
.\.venv\Scripts\Activate.ps1    # every new terminal; the prompt then starts with (.venv)
```

If activation fails with "running scripts is disabled on this system", run this once, then try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**3. Install the requirements.**

```powershell
pip install -r requirements.txt
```

**4. Add your Anthropic API key.** Copy the example file, then open `.env` and replace
`your-key-here` with your key (from [console.anthropic.com](https://console.anthropic.com/)).
`.env` is gitignored; never commit it.

```powershell
Copy-Item .env.example .env
notepad .env
```

**5. Start the app.** It opens in your browser at http://localhost:8501.

```powershell
streamlit run app.py
```

**No API key?** You can skip step 4. The **Story**, **Overview**, and **Assurance** pages read
saved results and work without a key, and so does **Load a sample morning** on the Simulator page,
which sends 12 payments straight through the checker with no AI calls. The sidebar shows
"AI connection: not set up". Anything that calls the AI (the Policies compiler and running the agent
in the Simulator) needs the key. The tests need no key either: `pytest`.

## Project status

Milestone 1 (Foundation, no AI) is done: fake company data in `data/`, the rule
types and checker in `guardrail/`, and the SQLite audit log.

```powershell
pytest                 # run all tests
python run_demo.py     # push the sample invoices through the checker
```

Milestone 2 (Policy Compiler) adds English rule -> rule type with the AI, plain-code
checks on every AI answer, human approval, and versioned policies (`policies.db`).

```powershell
python run_compiler_demo.py   # type a rule, see the result, approve or reject (uses the API)
python run_compiler_eval.py   # accuracy on crash_lab/expected/compiler_cases.json (uses the API)
```

Milestone 3 (sample treasury agent) adds an AI agent that reads the invoice inbox
(`data/agent_inbox.json`) and pays invoices using six tools. In guardrail mode every
payment goes through the checker; `--baseline` runs the same agent with no checker.

```powershell
python run_agent_demo.py              # guardrail mode (uses the API)
python run_agent_demo.py --baseline   # prompt-only baseline (uses the API)
```

Milestone 4 (unusual payment check, rule F1, `guardrail/unusual.py`) runs after the hard
rules say ALLOW. An AI (`UNUSUAL_MODEL` in `config.py`, Haiku 4.5) looks at plain-code facts
only, never invoice text, and can only turn ALLOW into REQUIRE_APPROVAL. Any AI failure
escalates. A near match to a sanctions name (similarity 0.85 or more) escalates in plain
code without asking the AI. `run_agent_demo.py` turns F1 on in guardrail mode.

```powershell
pytest tests/test_unusual.py   # F1 tests (no API calls)
```

Features are built one milestone at a time (see `CLAUDE.md`).

Milestone 5 (Crash Lab) attacks the guardrail with scenarios whose answers are locked in
`crash_lab/expected/` by hand. Layer 1 sends about 130 malicious requests straight to the
checker (free, no AI). Layer 2 runs the agent on 26 small inboxes in three modes: a generic
agent with no company rules, prompt-only (rules in the prompt, no checker), and guardrail
(checker plus F1). Drafts and a review table live in `crash_lab/drafts/`.

```powershell
python run_crash_lab.py --layer1-only   # free: direct attacks on the checker only
python run_crash_lab.py --quick         # Layer 2 once per mode (asks before spending money)
python run_crash_lab.py                 # Layer 2 three times per mode (asks before spending money)
```

Results are saved to `crash_lab/results/latest.json`. For every unsafe result, the AI suggests
one rule change; it is printed and saved, never applied.

Milestone 6 (the screens) is a Streamlit app in three sections, styled as a bank treasury console
(theme in `.streamlit/config.toml`):

- **Story**: the walkthrough to present from instead of slides (problem, why now, how it works,
  proof, business model, competition, user quotes, next steps), each with a button to its screen.
- **Product**: what a bank's business client sees, under a banner for a fictional bank:
  Overview, Policies, Approvals, Activity, and Assurance (the Crash Lab results as a report).
- **Simulator** (demo only): a stand-in for the company's own agent, plus the reset buttons and
  the Crash Lab quick rerun.

The app keeps its own audit log in `app_audit.db` and shares `policies.db` with
`run_compiler_demo.py` (both gitignored).

```powershell
streamlit run app.py
pytest tests/test_app.py   # every page loads, no API calls
```

Things to fill in (each stays hidden on screen until it has a value): the fraud number and user
quotes at the top of `ui/story.py`. Story's Policy Compiler result is read from the first eval run's
saved output, `crash_lab/history/compiler_eval_run1.txt`, and is hidden if that file is missing.
The bank's name is `BANK_NAME` in `ui/shared.py`.

Simulator demo tools include "Load a sample morning": it clears the demo log and sends 12 realistic
payments straight through the checker with no AI calls (`ui/sample_morning.py`), so the Overview
looks like a real company's day.

Simulator extras: a "simulate a compromised agent" switch (the real AI told to obey invoice text,
or a scripted attacker that is plain code, not an AI), an "outdated instructions" option that shows
the agent policy version 1 while the checker enforces the current version, and a split order paid
across two separate agent runs.

Milestone 7 (held out attacks) tests the guardrail with attacks we never designed, reported
separately on the Assurance page under "Held out attacks: attacks we never designed". Every attack
states its goal (the account it wants paid and how much) and is saved to `crash_lab/held_out/`
before it runs. An attack got money through if any money reached its goal account with no person
approving it; reaching the full amount is a secondary detail. Each attack runs alone against the
starting policy on an in-memory log (`guardrail/held_out.py`).

- **AI red team** (`run_red_team.py`): an attacker AI that knows only the company name, the
  vendor names, two normal invoices, and that payments are checked writes 20 attack inboxes. Each
  runs twice in guardrail mode: with the real agent, and with the scripted attacker (the worst case).
  The attacker's prompt explains the context honestly (authorized testing of our own prototype in a
  fictional sandbox). If the first model refuses a batch, that batch is retried once with the agent's
  model; each attack records which model wrote it. If both refuse, the script stops and saves nothing.
- **People**: the Simulator's "Try to break it" form. Scripted attacker by default, real agent optional.

```powershell
python run_red_team.py --generate-only     # write and save the 20 attacks and goals (asks first, ~$0.80)
git add crash_lab/held_out; git commit -m "Red team goals, before running"   # proof the goals came first
python run_red_team.py --run crash_lab/held_out/red_team_attacks_<stamp>.json   # asks first, ~$1.40
pytest tests/test_held_out.py              # no API calls
```
