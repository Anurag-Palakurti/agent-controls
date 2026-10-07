# CLAUDE.md

Rules for working on this project.

## What this is

Agent Guardrail + Crash Lab: a safety layer between an AI treasury agent and a
company's money. Every payment the agent tries is checked and gets ALLOW, BLOCK,
or REQUIRE HUMAN APPROVAL, with the exact reason. Crash Lab attacks it with
100+ scenarios to prove it works. This is an interview prototype: it has to work,
and the owner has to be able to explain every line.

## Stack

- Python 3.11, virtual environment in `.venv`
- Streamlit for the screens
- Claude via the Anthropic API (`anthropic` package) for all AI parts
- SQLite (Python's built-in `sqlite3`) for the audit log
- `pytest` for tests
- Don't add new libraries without asking first.

## Secrets

- The API key is `ANTHROPIC_API_KEY`. It lives only in `.env`, loaded with `python-dotenv`.
- `.env` is in `.gitignore` and must never be committed, printed, or logged.
- `.env.example` (committed) shows the key name with a placeholder value.

## Safety rules for the code (the core design)

1. **Hard rules are plain code, never AI.** Amount limits, minimum cash, approved
   vendors, daily totals, duplicates, overdrafts, and the sanctions list are checked
   with ordinary Python. These checks never call the AI and never read invoice text.
2. **AI judgment can only escalate, never approve money.** The AI may turn ALLOW
   into REQUIRE APPROVAL. It can never produce ALLOW by itself or overrule a BLOCK.
   If an AI call fails, times out, or returns something unexpected, the payment goes to a human.
3. **Strictest answer wins.** Every rule runs on every payment. Any BLOCK means BLOCK,
   otherwise any ESCALATE means a human decides, otherwise ALLOW.
4. **Built-in rules can't be changed by users:** no overdrafts, and no payments to
   anyone on the sanctions list.
5. **The AI never activates a policy rule.** A human approves every rule before it goes live.

## Crash Lab expected answers are locked

- Claude may write draft scenarios **only** in `crash_lab/drafts/`.
- The owner reviews each draft by hand and moves it into `crash_lab/expected/` personally.
- Claude **never** creates, edits, moves, or deletes anything in `crash_lab/expected/`.
  This is also enforced by a deny rule in `.claude/settings.json`.
- If a scenario fails, report it and fix the code. Never change the expected answer.

## How we work

- **Build one milestone at a time.** Finish it, show how to run and test it, then stop
  and wait for the owner's OK before starting the next one.
- **Keep the code simple and well commented.** Plain, readable Python. Short comments
  explain *why* each piece exists. No clever tricks. Prefer a few clear files over many small ones.
- No real money, no real bank connections, and no real bank or customer information.

## Milestones (in order)

1. **Foundation (no AI):** fake company data, rule types, the checker (strictest wins,
   built-in overdraft and sanctions rules), and the audit log.
2. **Policy Compiler:** English rule to rule type, with human approval, clarifying
   questions for vague rules, and "I can't enforce this" for unsupported rules.
3. **Sample treasury agent** that sends every payment through the checker.
4. **Fuzzy "unusual payment" check** that can only escalate.
5. **Crash Lab:** scenarios, runner, scores, prompt-only baseline, suggested rule fixes.
6. **Streamlit screens:** Policy Center, Agent Activity, Approval Queue, Crash Lab, Audit Trail.
7. **Outside attacks and final numbers.**
