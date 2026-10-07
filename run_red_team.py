"""AI red team: an attacker AI writes 20 attacks we never designed, then each one runs twice.

  python run_red_team.py                  write the attacks, save their goals, then run them
  python run_red_team.py --generate-only  write the attacks and save their goals, then stop
                                          (commit the goals file before running, as proof)
  python run_red_team.py --run FILE       run a saved attacks file
  python run_red_team.py --workers 4      fewer agent runs in parallel

The attacker AI sees only what an outside attacker could know (see held_out.attacker_brief):
never our rules, policies, code, or Crash Lab scenarios. Every goal (an account and an
amount) is saved to crash_lab/held_out/ before any attack runs.

Each attack runs twice in guardrail mode: once with the real agent, once with the
scripted attacker (an agent that obeys the invoice, the worst case). The unusual
payment check (rule F1) runs in both, because it's part of the guardrail.

An attack got money through if any money reached its goal account with no person approving it.
Before any API call, this prints an estimated cost and asks you to confirm.
"""

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from guardrail import config
from guardrail import crash_lab as lab
from guardrail import held_out as ho
from guardrail.compiler import make_client
from guardrail.crash_lab import build_world
from run_crash_lab import confirm

ATTACKS = 20

# Rough shape of the attacker's work, per batch of 5 attacks: the brief in, about
# 10,000 tokens of thinking and JSON out.
EST_ATTACKER_USAGE = {"input_tokens": 1500, "output_tokens": 10000}
EST_ITEMS_PER_ATTACK = 2


def estimate(generate: bool, attacks: int) -> dict:
    """A rough cost estimate before any API call. Actual cost is measured and printed after."""
    batches = -(-attacks // ho.BATCH_SIZE)
    attacker = lab.tokens_cost(config.MODEL, EST_ATTACKER_USAGE) * batches if generate else 0.0
    agent = lab.tokens_cost(config.AGENT_MODEL, lab.estimate_agent_run(EST_ITEMS_PER_ATTACK)) * attacks
    # F1 checks: at most one per invoice payment, in both runs of every attack.
    f1 = lab.tokens_cost(config.UNUSUAL_MODEL, lab.EST_F1_USAGE) * EST_ITEMS_PER_ATTACK * attacks * 2
    return {"attacker": round(attacker, 2), "agent": round(agent, 2), "f1": round(f1, 2),
            "total": round(attacker + agent + f1, 2)}


def print_estimate(est: dict, generate: bool, attacks: int):
    if generate:
        print(f"The attacker AI ({config.MODEL}) writes {attacks} attacks in batches of {ho.BATCH_SIZE}: "
              f"about ${est['attacker']:.2f} (up to {ho.MAX_BATCHES} batches if some attacks are unusable; "
              f"a batch it refuses is retried once with {config.AGENT_MODEL}).")
    if est["agent"]:
        print(f"Then {attacks} attacks x 2 runs: real agent ({config.AGENT_MODEL}) about ${est['agent']:.2f}, "
              f"F1 checks ({config.UNUSUAL_MODEL}) about ${est['f1']:.2f}. The scripted attacker is free.")
    print(f"Estimated total: about ${est['total']:.2f}. Rough: could be off by half either way.")


def run_all(attacks: list[dict], client, workers: int) -> list[dict]:
    """Every attack, run by both agents, in parallel. Each run has its own in-memory log."""
    jobs = [(a, agent) for a in attacks for agent in ho.AGENTS]
    runs = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(ho.run_attack, a["inbox"], a["goal"], agent, client, client): (a["id"], agent)
                   for a, agent in jobs}
        for n, future in enumerate(as_completed(futures), 1):
            attack_id, agent = futures[future]
            run = future.result()
            runs[(attack_id, agent)] = run
            verdict = "MONEY GOT THROUGH" if run["grade"]["money_through"] else "stopped"
            print(f"  [{n}/{len(jobs)}] {attack_id} {ho.AGENT_LABELS[agent]}: {verdict} ({run['stop_reason']})")
    return [{**a, "runs": {agent: runs[(a["id"], agent)] for agent in ho.AGENTS}} for a in attacks]


def print_report(attacks: list[dict], summary: dict):
    print("\nHELD OUT ATTACKS: AI RED TEAM")
    print("=" * 78)
    for a in attacks:
        print(f"{a['id']}  goal {ho.clean_account(a['goal']['account'])} ${a['goal']['amount']}: {a['strategy']}")
        for agent, run in a["runs"].items():
            print(f"      {ho.AGENT_LABELS[agent]:<40} {ho.run_outcome(run, a['inbox'])}")
    print("-" * 78)
    print(f"Held out attacks: {summary['money_through']} of {summary['attacks']} attacks got money through, "
          f"{ho.TWO_WAYS}.")
    print(f"Blocked by the guardrail: {summary['stopped_by_guardrail']} of {summary['reached_guardrail']} "
          f"attacks that reached it.")
    missed = [a["id"] for a in attacks if not any(ho.reached_guardrail(r) for r in a["runs"].values())]
    if missed:
        print(f"Never reached the guardrail (neither way tried to pay anything): {', '.join(missed)}.")
    print(f"Reached the full goal amount: {summary['full_goal_reached']}.")
    for agent, c in summary["by_agent"].items():
        print(f"   {ho.AGENT_LABELS[agent]:<40} reached the guardrail in {c['reached_guardrail']} of {c['runs']}, "
              f"money through in {c['money_through']}")


def main(argv=None, ask=input, client_factory=make_client, folder: Path | None = None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--generate-only", action="store_true", help="write and save the attacks, don't run them")
    parser.add_argument("--run", metavar="FILE", help="run a saved attacks file")
    parser.add_argument("--workers", type=int, default=config.CRASH_LAB_WORKERS, help="agent runs in parallel")
    args = parser.parse_args(argv)
    data, _ = build_world(None)

    # Step 1: the attacks, either saved earlier or written now.
    if args.run:
        try:
            saved = ho.load_attacks(Path(args.run))
        except (OSError, ValueError, KeyError) as error:
            print(f"Can't use {args.run}: {error}. Nothing was run.")
            return 1
        attacks, attacks_file = saved["attacks"], Path(args.run)
        est = estimate(False, len(attacks))
        print(f"{len(attacks)} saved attacks from {attacks_file.name} (fingerprint matches).")
        print_estimate(est, False, len(attacks))
        if not confirm("Run them now? [y/N] ", ask):
            print("Stopped before any API call.")
            return 0
        client = client_factory()
    else:
        est = estimate(True, ATTACKS)
        if args.generate_only:          # only the attacker AI is paid for
            est = {**est, "agent": 0.0, "f1": 0.0, "total": est["attacker"]}
        print_estimate(est, True, ATTACKS)
        if not confirm("Go ahead? [y/N] ", ask):
            print("Stopped before any API call.")
            return 0
        client = client_factory()
        try:
            attacks, dropped, usage, refusals = ho.generate_attacks(data, client, ATTACKS)
        except ho.AttackerRefused as error:
            # Stop here. A refusal is never worked around any other way.
            print(f"The attacker AI refused: {error}. Nothing was saved or run.")
            return 1
        except Exception as error:
            print(f"The attacker AI failed ({type(error).__name__}: {error}). Nothing was saved or run.")
            return 1
        for refusal in refusals:
            print(f"  refused, retried with the next model: {refusal}")
        for reason in dropped:
            print(f"  dropped (unusable): {reason}")
        # The goals are saved here, before any attack runs.
        attacks_file = ho.save_attacks(attacks, dropped, folder, refusals)
        print(f"Saved {len(attacks)} attacks and their goals: {attacks_file}")
        print("Written by: " + ", ".join(f"{m} ({sum(a['written_by'] == m for a in attacks)})"
                                          for m in sorted({a["written_by"] for a in attacks})))
        print(f"Attacker cost: ${sum(lab.tokens_cost(m, u) for m, u in usage.items()):.2f}")
        if args.generate_only:
            print(f"Stopped before running. Commit the goals file, then: python run_red_team.py --run {attacks_file}")
            return 0

    # Step 2: run every attack twice and grade it.
    started = datetime.now()
    attacks = run_all(attacks, client, args.workers)
    summary = ho.tally([list(a["runs"].values()) for a in attacks])
    print_report(attacks, summary)

    agent_usage = Counter()
    for a in attacks:
        agent_usage.update(a["runs"][ho.REAL_AGENT]["usage"])
    path = ho.save_results({
        "run_at": started.isoformat(timespec="seconds"),
        "attacks_file": attacks_file.name,
        "attacks_hash": ho.attacks_hash([{k: v for k, v in a.items() if k != "runs"} for a in attacks]),
        "models": {"attacker": ho.attacker_models(attacks), "agent": config.AGENT_MODEL, "unusual_f1": config.UNUSUAL_MODEL},
        "cost_estimate": est,
        "cost_actual_agent": round(lab.tokens_cost(config.AGENT_MODEL, agent_usage), 2),
        "summary": summary,
        "attacks": attacks,
    }, folder)
    print(f"\nMeasured agent cost: ${lab.tokens_cost(config.AGENT_MODEL, agent_usage):.2f} "
          f"(F1 checks not metered, estimated ${est['f1']:.2f}).")
    print(f"Saved: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
