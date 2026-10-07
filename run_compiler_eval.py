"""Measure Policy Compiler translation accuracy with the real API.

Reads ONLY the locked cases in crash_lab/expected/compiler_cases.json, which
the owner reviews and moves there by hand. Grades each case against policy
version 1 (data/policies.json), runs it through the full pipeline (AI plus
plain-code checks), and prints the accuracy.

This calls the real API once per case, which costs a small amount of money.
The summary is saved to crash_lab/results/compiler_eval.json (the Home screen
reads it; copy it into crash_lab/history/ to keep it with the project).

Run:  python run_compiler_eval.py
"""

import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from guardrail import config
from guardrail.checker import load_data
from guardrail.compiler import compile_rule, make_client
from guardrail.rules import clean_settings

CASES_FILE = Path(__file__).resolve().parent / "crash_lab" / "expected" / "compiler_cases.json"
RESULTS_FILE = Path(__file__).resolve().parent / "crash_lab" / "results" / "compiler_eval.json"


def grade(case: dict, result, account_names) -> tuple[bool, str]:
    """Return (correct, what came back). Compiled cases must match type, settings, and action."""
    expected = case["expected"]
    if result.status != expected["result"]:
        got = result.reason or result.question or result.enforced or ""
        return False, f"got {result.status}: {got}"
    if expected["result"] != "compiled":
        return True, result.status
    want_settings = clean_settings(expected["rule_type"], expected.get("settings", {}), account_names)
    got = (result.rule_type, result.settings, result.action)
    want = (expected["rule_type"], want_settings, expected["action"])
    if got != want:
        return False, f"got {got}, expected {want}"
    return True, result.status


def is_unsafe(result, correct: bool) -> bool:
    """A miss is UNSAFE only if it produced a compiled rule that isn't the right one.

    That covers a wrong type, settings, or action, and compiling something that
    shouldn't have compiled. Asking for clarification or refusing is a SAFE miss:
    no wrong rule could reach a human for approval.
    """
    return not correct and result.status == "compiled"


def main():
    if not CASES_FILE.exists():
        print(f"No locked cases yet: {CASES_FILE}")
        print("Review crash_lab/drafts/compiler_cases.json, then move it to crash_lab/expected/ yourself.")
        sys.exit(1)

    with open(CASES_FILE, encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    data = load_data()                       # policy version 1
    account_names = list(data["accounts"])
    client = make_client()

    print(f"Policy Compiler eval: {len(cases)} cases, model {config.MODEL}, effort {config.EFFORT}")
    print("=" * 78)

    totals = defaultdict(lambda: [0, 0])     # group -> [correct, total]
    misses = []
    start = time.time()
    for case in cases:
        result = compile_rule(case["english"], data, client)
        correct, detail = grade(case, result, account_names)
        unsafe = is_unsafe(result, correct)
        mark = "PASS       " if correct else ("MISS UNSAFE" if unsafe else "MISS SAFE  ")
        print(f"{mark}  {case['id']:<5} {case['english'][:56]}")
        for group in ("all", f"expected {case['expected']['result']}",
                      f"style {case.get('style', '?')}", f"author {case.get('author', '?')}"):
            totals[group][0] += correct
            totals[group][1] += 1
        if not correct:
            misses.append((case, detail, unsafe))

    print("=" * 78)
    unsafe_count = sum(1 for _, _, unsafe in misses if unsafe)
    print(f"Unsafe translations: {unsafe_count} of {len(cases)}")
    for group, (right, total) in totals.items():
        print(f"{group:<35} {right:>3}/{total:<3}  {100 * right / total:5.1f}%")
    print(f"\nTook {time.time() - start:.0f}s.")
    save_summary(cases, totals, misses)

    if misses:
        print("\nMisses (report them; never change the expected answer to make a case pass):")
        for case, detail, unsafe in misses:
            label = "UNSAFE" if unsafe else "SAFE"
            print(f"- {case['id']} [{label}]: {case['english']}\n    expected {case['expected']}\n    {detail}")


def save_summary(cases: list[dict], totals: dict, misses: list) -> None:
    """Save the counts for the screens. Only the results folder is written, never expected/."""
    correct, total = totals["all"]
    summary = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "cases_file_hash": hashlib.sha256(CASES_FILE.read_bytes()).hexdigest()[:16],
        "model": config.MODEL, "effort": config.EFFORT,
        "cases": total, "correct": correct,
        "unsafe": sum(1 for _, _, unsafe in misses if unsafe),
        "groups": {group: {"correct": r, "total": t} for group, (r, t) in totals.items()},
        "misses": [{"id": case["id"], "english": case["english"], "detail": detail,
                    "unsafe": unsafe} for case, detail, unsafe in misses],
    }
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved: {RESULTS_FILE}")


if __name__ == "__main__":
    main()
