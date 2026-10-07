"""Run the sample treasury agent on the demo inbox and print every step.

  python run_agent_demo.py              guardrail mode: every payment goes through the checker
  python run_agent_demo.py --baseline   same agent, same prompt, no checker

Uses the real API (the agent model in guardrail/config.py) and policy version 1.
Each run starts with a fresh log: agent_demo.db or agent_baseline.db.
"""

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from guardrail import config
from guardrail.agent import BASELINE, GUARDRAIL, run_agent
from guardrail.audit_log import AuditLog
from guardrail.checker import DATA_DIR, load_data
from guardrail.compiler import make_client
from guardrail.rules import current_balance, money

ROOT = Path(__file__).resolve().parent


def show_event(event):
    """Print one step in plain words."""
    if event["kind"] == "says":
        print(f"  AGENT: {event['text']}")
        return
    if event["kind"] == "bad call":
        print(f"  BAD TOOL CALL {event['tool']}: {event['error']}")
        return

    tool, given, out = event["tool"], event["input"], event["output"]
    if event["is_error"]:
        print(f"  {tool}({', '.join(given.values())}) -> {out['error']}")
    elif tool == "list_invoices":
        print(f"  LIST inbox: {len(out['invoices'])} invoices")
    elif tool == "read_invoice":
        print(f"  READ {out['invoice_id']}: {out['vendor']}, {money(Decimal(out['amount']))}, "
              f"asks to be paid to {out['account_number']}")
    elif tool == "lookup_vendor":
        print(f"  LOOKUP {out['name']}: {out['status']}, account on file {out['account_number_on_file']}, "
              f"typical invoice {money(Decimal(out['typical_invoice']))}, added {out['date_added']}")
    elif tool == "get_balances":
        print("  BALANCES " + ", ".join(f"{a} {money(Decimal(b))}" for a, b in out["balances"].items()))
    else:
        if tool == "submit_vendor_payment":
            what = (f"PAY {given['amount']} to {given['vendor']} account {given['account_number']} "
                    f"(invoice {given['invoice_id']}, from {given['from_account']})")
        else:
            what = f"TRANSFER {given['amount']} from {given['from_account']} to {given['to_account']}"
        print(f"  {what}")
        if out["decision"] == "SENT":
            print("    -> SENT (no guardrail)")
        else:
            print(f"    -> CHECKER: {out['decision']}")
            for reason in out["reasons"]:
                print(f"       - {reason}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", action="store_true", help="run with no checker (prompt-only baseline)")
    args = parser.parse_args()
    mode = BASELINE if args.baseline else GUARDRAIL

    db_path = ROOT / ("agent_baseline.db" if args.baseline else "agent_demo.db")
    if db_path.exists():
        db_path.unlink()   # fresh log every run
    log = AuditLog(str(db_path))
    data = load_data()
    with open(DATA_DIR / "agent_inbox.json", encoding="utf-8") as f:
        inbox = json.load(f)["invoices"]

    print(f"Treasury agent, {mode.upper()} mode, model {config.AGENT_MODEL} (effort {config.AGENT_EFFORT}), "
          f"{len(inbox)} invoices, policy version {data['policy_version']}")
    if mode == GUARDRAIL:
        print(f"Unusual payment check (F1): model {config.UNUSUAL_MODEL}")
    print("=" * 78)

    # One real client for the agent and for the unusual payment check (F1).
    # F1 only runs in guardrail mode; the baseline has no checker at all.
    client = make_client()
    result = run_agent(data, log, inbox, mode, client,
                       unusual_client=client if mode == GUARDRAIL else None)
    step = None
    for event in result.events:
        if event["step"] != step:
            step = event["step"]
            print(f"\nStep {step}")
        show_event(event)

    print("\n" + "=" * 78)
    print(f"Run ended: {result.stop_reason} after {result.steps} model calls.")
    entries = log.all_entries()
    print("Payments logged: " + (", ".join(f"{k} {v}" for k, v in sorted(Counter(
        e["status"] for e in entries).items())) or "none"))
    for e in entries:
        target = e["to_vendor"] or e["to_internal_account"]
        print(f"   #{e['id']} {e['status']:<8} {money(Decimal(e['amount'])) if e['amount'] else '?':>14} "
              f"to {target} ({e['invoice_id'] or 'transfer'})")
    print("Ending balances:")
    history = log.history()
    for account in data["accounts"]:
        print(f"   {account:<10} {money(data['accounts'][account]):>16} -> "
              f"{money(current_balance(account, data, history)):>16}")
    u = result.usage
    print(f"Tokens: {u['input_tokens']:,} input, {u['cache_read_input_tokens']:,} cache read, "
          f"{u['cache_creation_input_tokens']:,} cache write, {u['output_tokens']:,} output")
    print(f"Audit log saved to {db_path.name}")
    log.close()


if __name__ == "__main__":
    main()
