"""Crash Lab: run every locked scenario and report what got through.

  python run_crash_lab.py                Layer 1, then Layer 2 (3 runs per scenario per mode)
  python run_crash_lab.py --quick        Layer 2 runs once per mode
  python run_crash_lab.py --layer1-only  only the free checker attacks, no API calls
  python run_crash_lab.py --workers 4    fewer agent runs in parallel
  python run_crash_lab.py --regrade FILE grade a saved run again with today's grading rules (no API calls)

Reads ONLY crash_lab/expected/, which the owner reviews and fills by hand.
Layer 1 is free. Before Layer 2 makes any API call, it prints an estimated cost
and asks you to confirm. Suggested rule fixes are printed and saved, never applied.
Results go to crash_lab/results/ (latest.json is what the screens read).
"""

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from guardrail import config
from guardrail import crash_lab as lab
from guardrail.agent import GUARDRAIL
from guardrail.checker import load_data
from guardrail.compiler import make_client
from guardrail.fix_suggester import suggest_fix


def pct(value) -> str:
    return "  n/a" if value is None else f"{100 * value:5.1f}%"


# ---------- printing ----------

def print_layer1(results: list[dict], summary: dict):
    print(f"\nLAYER 1: direct attacks on the checker (no AI, F1's AI never flags)")
    print("=" * 78)
    for r in results:
        if not r["passed"]:
            print(f"FAIL  {r['id']:<8} [{r['category']}] {r['description']}")
            for n, s in enumerate(r["steps"], 1):
                if s["grade"] != "correct":
                    print(f"        step {n}: expected {s['expected']}, got {s['decision']} -> {s['grade'].upper()}")
                    for reason in s["reasons"][:3]:
                        print(f"          - {reason}")
    print("-" * 78)
    print(f"Scenarios passed: {summary['scenarios_passed']}/{summary['scenarios']}   "
          f"Steps correct: {summary['steps_correct']}/{summary['steps']}   "
          f"UNSAFE steps (allowed when they shouldn't be): {summary['unsafe_steps']}")
    print(f"Grades: {summary['grades']}   Borderline steps: {summary['borderline_steps']}")
    print(f"Checker speed: {summary['decision_ms']['mean']} ms average, {summary['decision_ms']['max']} ms slowest")
    print("By scenario type:")
    for category, c in sorted(summary["by_category"].items()):
        print(f"   {category:<24} {c['passed']:>3}/{c['total']:<3}")


def print_layer2(summary: dict, repeats: int):
    modes = [m for m in lab.MODES if m in summary["modes"]]
    print(f"\nLAYER 2: the agent on small inboxes ({repeats} run(s) per scenario per mode)")
    print("=" * 78)
    rows = [
        ("Unsafe action rate (bad invoices paid, no human)", "unsafe_action_rate", pct),
        ("Valid completion rate (good invoices handled right)", "valid_completion_rate", pct),
        ("False block rate (good invoices stopped)", "false_block_rate", pct),
        ("Escalation accuracy (needs-a-human cases sent to one)", "escalation_accuracy", pct),
        ("Needs-a-human cases not paid without one", "held_unpaid_rate", pct),
        ("All unsafe events (incl. double pays, extras, cash)", "unsafe_events", str),
        ("Checker decision accuracy (first decision per invoice)", "checker_decision_accuracy", pct),
        ("Invoices the agent held back (never submitted)", "agent_held", str),
        ("Runs that ended in an error or step limit", "runs_with_errors", str),
    ]
    print(f"{'':<56}" + "".join(f"{lab.MODE_LABELS[m]:>14}" for m in modes))
    for label, key, fmt in rows:
        print(f"{label:<56}" + "".join(f"{fmt(summary['modes'][m][key]):>14}" for m in modes))
    print(f"{'Agent seconds per invoice':<56}" +
          "".join(f"{summary['modes'][m]['agent_seconds_per_invoice']:>14}" for m in modes))
    g = summary["modes"].get(GUARDRAIL)
    if g and g["checker_seconds"]["mean"] is not None:
        print(f"Guardrail checker time per payment (hard rules + F1 AI): {g['checker_seconds']['mean']:.3f}s average, "
              f"{g['checker_seconds']['max']:.3f}s slowest")
    first = summary["modes"][modes[0]]
    print(f"Per mode: {first['invoices']} invoice results, {first['should_stop']} should be stopped or escalated, "
          f"{first['good']} good, of which {first['borderline_invoices']} borderline (either answer counts).")

    print("\nHEADLINE")
    for m in modes:
        s = summary["modes"][m]
        print(f"   {lab.MODE_LABELS[m]:<14} let {s['unsafe_paid']} of {s['should_stop']} bad invoices through "
              f"with no human; {s['unsafe_events']} unsafe events in all; "
              f"valid completion {pct(s['valid_completion_rate']).strip()}")

    print("\nBy scenario type: unsafe paid / should stop   (valid completion)")
    print(f"{'':<24}" + "".join(f"{lab.MODE_LABELS[m]:>22}" for m in modes))
    for category, per_mode in summary["by_category"].items():
        cells = []
        for m in modes:
            s = per_mode[m]
            cells.append(f"{s['unsafe_paid']}/{s['should_stop']} ({pct(s['valid_completion_rate']).strip()})")
        print(f"{category:<24}" + "".join(f"{c:>22}" for c in cells))

    print("\nAgent behavior varied across repeats")
    for m, c in summary["consistency"].items():
        print(f"   {lab.MODE_LABELS[m]:<14} {c['consistent']}/{c['scenarios']} scenarios ended the same way every time"
              + (f"; differed: {', '.join(c['inconsistent_scenarios'])}" if c["inconsistent_scenarios"] else ""))
    print("Correct every repeat (no unsafe result and no false block in any repeat)")
    for m, c in summary["consistency"].items():
        print(f"   {lab.MODE_LABELS[m]:<14} {c['correct_every_repeat']}/{c['scenarios']} scenarios"
              + (f"; not: {', '.join(c['not_correct_every_repeat'])}" if c["not_correct_every_repeat"] else ""))


def print_fixes(cases: list[dict], fixes: list[dict]):
    print("\nSUGGESTED RULE FIXES (not applied; a person decides)")
    print("=" * 78)
    if not cases:
        print("No unsafe results in guardrail mode or Layer 1, so nothing to suggest.")
    for case, fix in zip(cases, fixes):
        print(f"\n{case['scenario']} ({case['source']}): {case['description']}")
        for line in case["what_happened"]:
            print(f"   what happened: {line}")
        if fix["status"] == "suggested":
            print(f"   SUGGESTED: \"{fix['english']}\"")
            print(f"   would enforce: {fix['enforced']}")
        else:
            print(f"   {fix['status'].upper().replace('_', ' ')}")
        print(f"   why: {fix['why']}")


# ---------- running ----------

def confirm(question: str, ask=input) -> bool:
    try:
        return ask(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run_layer2(layer2: list[dict], repeats: int, workers: int, client) -> list[dict]:
    """Every scenario x mode x repeat, in parallel. Each job has its own in-memory log."""
    jobs = [(s, m, r) for s in layer2 for m in lab.MODES for r in range(1, repeats + 1)]
    runs = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(lab.run_layer2_once, s, m, client, client, r): (s["id"], m, r) for s, m, r in jobs}
        for n, future in enumerate(as_completed(futures), 1):
            sid, mode, repeat = futures[future]
            run = future.result()
            runs.append(run)
            unsafe = sum(lab.is_unsafe(v["outcome"]) for v in run["invoices"].values())
            print(f"  [{n}/{len(jobs)}] {sid} {lab.MODE_LABELS[mode]} #{repeat}: "
                  f"{unsafe} unsafe, {run['seconds']:.0f}s, {', '.join(set(run['stop_reasons']))}")
    order = {s["id"]: i for i, s in enumerate(layer2)}
    runs.sort(key=lambda r: (order[r["scenario"]], lab.MODES.index(r["mode"]), r["repeat"]))
    return runs


def regrade(path_text: str) -> int:
    """Grade a saved full run again with today's grading rules, print the report, save a copy.

    Uses the locked scenarios, and refuses if they changed since the run, because then
    the saved runs would be graded against answers they were never run against.
    The original file is left as it was; the regraded copy notes every grading change.
    """
    source = Path(path_text)
    saved = json.loads(source.read_text(encoding="utf-8"))
    if "layer2" not in saved:
        print(f"{source} has no Layer 2 results to regrade.")
        return 1
    _, layer2 = lab.load_scenarios(lab.EXPECTED_DIR)
    now_hash = file_hash(lab.EXPECTED_DIR / lab.LAYER2_FILE)
    if saved["scenario_files"].get(lab.LAYER2_FILE) != now_hash:
        print(f"The locked {lab.LAYER2_FILE} changed since this run, so it can't be regraded honestly.")
        return 1

    old = saved["layer2"]["summary"]["modes"]
    runs = lab.regrade_runs(saved["layer2"]["runs"], {s["id"]: s for s in layer2})
    summary = lab.summarize_layer2(runs)
    saved["layer2"].update(runs=runs, summary=summary)
    saved["grading_version"] = lab.GRADING_VERSION
    saved["grading_changes"] = lab.GRADING_CHANGES
    saved["regraded"] = {"at": datetime.now().isoformat(timespec="seconds"), "from": source.name,
                         "previous_summary": old,
                         "note": "Agent runs were not repeated; the saved payments were graded again."}

    print(f"Regrading {source.name} (run {saved['run_at']}) with grading v{lab.GRADING_VERSION}. No API calls.")
    print(f"Change: {lab.GRADING_CHANGES[-1]}")
    print_layer2(summary, saved["repeats"])
    print("\nBefore -> after this regrade")
    for key, label in (("valid_completion_rate", "Valid completion"), ("false_block_rate", "False block"),
                       ("unsafe_action_rate", "Unsafe action")):
        print(f"   {label:<18}" + "".join(
            f"{lab.MODE_LABELS[m]}: {pct(old[m][key]).strip()} -> {pct(summary['modes'][m][key]).strip()}   "
            for m in lab.MODES if m in old))

    out = source.with_name(f"{source.stem}_regraded.json")
    out.write_text(json.dumps(saved, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {out} (the original {source.name} is unchanged)")
    return 0


def file_hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def save(results: dict) -> str:
    lab.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    text = json.dumps(results, indent=2, default=str)
    (lab.RESULTS_DIR / f"crash_lab_{stamp}.json").write_text(text, encoding="utf-8")
    (lab.RESULTS_DIR / "latest.json").write_text(text, encoding="utf-8")
    return f"{lab.RESULTS_DIR / f'crash_lab_{stamp}.json'} (and latest.json)"


def main(argv=None, ask=input, client_factory=make_client):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="Layer 2 runs once per mode instead of 3 times")
    parser.add_argument("--layer1-only", action="store_true", help="only Layer 1 (free, no API calls)")
    parser.add_argument("--workers", type=int, default=config.CRASH_LAB_WORKERS, help="agent runs in parallel")
    parser.add_argument("--regrade", metavar="FILE", help="grade a saved run again (no API calls)")
    args = parser.parse_args(argv)
    if args.regrade:
        return regrade(args.regrade)
    repeats = 1 if args.quick else config.CRASH_LAB_REPEATS

    try:
        layer1, layer2 = lab.load_scenarios(lab.EXPECTED_DIR)
    except FileNotFoundError as error:
        print(f"No locked scenarios yet ({error.filename}).")
        print(f"Review crash_lab/drafts/REVIEW.md, then move {lab.LAYER1_FILE} and {lab.LAYER2_FILE} "
              f"into crash_lab/expected/ yourself.")
        return 1
    problems = lab.validate_scenarios(layer1, layer2)
    if problems:
        print("The scenario files have problems, so nothing was run:")
        for p in problems:
            print(f"  - {p}")
        return 1

    results = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "scenario_files": {name: file_hash(lab.EXPECTED_DIR / name) for name in (lab.LAYER1_FILE, lab.LAYER2_FILE)},
        "models": {"agent": config.AGENT_MODEL, "agent_effort": config.AGENT_EFFORT,
                   "unusual_f1": config.UNUSUAL_MODEL, "fixes": config.FIX_MODEL},
        "repeats": repeats,
        "grading_version": lab.GRADING_VERSION,
        "grading_changes": lab.GRADING_CHANGES,
    }

    # Layer 1: free, so it always runs.
    layer1_results = [lab.run_layer1_scenario(s) for s in layer1]
    results["layer1"] = {"summary": lab.summarize_layer1(layer1_results), "scenarios": layer1_results}
    print_layer1(layer1_results, results["layer1"]["summary"])

    scenarios = {s["id"]: s for s in layer1 + layer2}
    layer2_runs = []
    if not args.layer1_only:
        layer1_unsafe = len(lab.unsafe_cases(layer1_results, [], scenarios))
        estimate = lab.estimate_cost(layer2, repeats, max_fixes=layer1_unsafe + len(layer2))
        print(f"\nLayer 2 will make {estimate['agent_runs']} agent runs ({len(layer2)} scenarios x "
              f"{len(lab.MODES)} modes x {repeats}), model {config.AGENT_MODEL}.")
        print(f"Estimated cost: about ${estimate['total']:.2f} (agent ${estimate['agent']:.2f}, "
              f"F1 checks ${estimate['f1']:.2f}, up to {estimate['max_fixes']} suggested fixes ${estimate['fixes']:.2f}). "
              f"Rough: could be off by half either way.")
        results["cost_estimate"] = estimate
        if not confirm("Run Layer 2 now? [y/N] ", ask):
            print("Stopped before any API call. Layer 1 results saved.")
            print(f"Saved: {save(results)}")
            return 0

        client = client_factory()
        started = time.time()
        layer2_runs = run_layer2(layer2, repeats, args.workers, client)
        summary = lab.summarize_layer2(layer2_runs)
        results["layer2"] = {"summary": summary, "runs": layer2_runs,
                             "seconds": round(time.time() - started)}
        print_layer2(summary, repeats)

        # Suggested fixes for every unsafe result in guardrail mode (and Layer 1). Shown, never applied.
        cases = lab.unsafe_cases(layer1_results, layer2_runs, scenarios)
        data = load_data()
        fixes = [suggest_fix(case, data, client) for case in cases]
        results["suggested_fixes"] = [{**case, "suggestion": fix} for case, fix in zip(cases, fixes)]
        print_fixes(cases, fixes)

        agent_usage = Counter()
        for run in layer2_runs:
            agent_usage.update(run["usage"])
        fix_usage = Counter()
        for fix in fixes:
            fix_usage.update(fix.get("usage", {}))
        actual = lab.tokens_cost(config.AGENT_MODEL, agent_usage) + lab.tokens_cost(config.FIX_MODEL, fix_usage)
        results["cost_actual"] = {"agent_and_fixes": round(actual, 2),
                                  "note": "F1 checks (Haiku) are not metered; see the estimate."}
        print(f"\nMeasured cost: ${actual:.2f} for the agent and suggested fixes "
              f"(F1 checks not metered, estimated ${estimate['f1']:.2f}).")

    results["borderline"] = {
        "layer1_steps": results["layer1"]["summary"]["borderline_steps"],
        "layer2_invoices": sum(v == lab.BORDERLINE for s in layer2 for v in s["expected"].values()),
    }
    print(f"\nBorderline cases (either ALLOW or REQUIRE_APPROVAL counts as correct): "
          f"{results['borderline']['layer1_steps']} Layer 1 steps, "
          f"{results['borderline']['layer2_invoices']} Layer 2 invoices.")
    print(f"Saved: {save(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
