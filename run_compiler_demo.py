"""Command line Policy Compiler: type a rule, see the result, approve or reject it.

Rules are saved as new policy versions in policies.db (gitignored).
Commands: 'rules' lists live rules, 'history' lists versions, 'quit' exits.

Run:  python run_compiler_demo.py
"""

from guardrail.checker import load_data
from guardrail.compiler import compile_rule, make_client
from guardrail.policy_store import PolicyStore

STORE_FILE = "policies.db"


def show_rules(store):
    data = load_data(policy_store=store)
    print(f"\nPolicy version {data['policy_version']}:")
    for rule in data["rules"]:
        print(f"  {rule.id:<4} {rule.action:<9} {rule.text}  (approved by {rule.approved_by})")


def show_history(store):
    print()
    for v in store.history():
        print(f"  v{v['version']}  {v['changed_at']}  {v['changed_by']}: {v['change']}")


def handle(english, store, client):
    """Compile one rule and walk through approval or clarification."""
    while True:
        data = load_data(policy_store=store)
        print("  ...asking the AI")
        result = compile_rule(english, data, client)
        print(f"\nResult: {result.status.upper().replace('_', ' ')}")

        if result.status == "cannot_enforce":
            print(f"  Why: {result.reason}")
            if result.heads_up:
                print(f"  {result.heads_up}")
            return

        if result.status == "needs_clarification":
            print(f"  Question: {result.question}")
            for n, option in enumerate(result.options, start=1):
                print(f"    {n}. {option}")
            choice = input("  Pick a number (or press Enter to skip): ").strip()
            if not choice.isdigit() or not 1 <= int(choice) <= len(result.options):
                print("  Skipped. Nothing saved.")
                return
            # The chosen option goes through the whole pipeline again.
            english = result.options[int(choice) - 1]
            print(f"\nCompiling: {english}")
            continue

        # compiled
        print(f"  You typed:        {result.english}")
        print(f"  AI summary:       {result.ai_summary}")
        print(f"  Will be enforced: {result.enforced}")
        print(f"  Rule type:        {result.rule_type}  settings {result.settings}  action {result.action}")
        if result.heads_up:
            print(f"  {result.heads_up}")
        if input("\nApprove this rule? (y/n): ").strip().lower() != "y":
            print("  Rejected. Nothing saved.")
            return
        name = input("Your name: ").strip()
        try:
            rule = store.approve(result, name)
        except ValueError as error:
            print(f"  Not saved: {error}")
            return
        print(f"  Live as rule {rule['id']} in policy version {store.current_version()}.")
        return


def main():
    store = PolicyStore(STORE_FILE)
    client = make_client()
    print("Policy Compiler. Type a rule in plain English, or 'rules', 'history', 'quit'.")
    show_rules(store)
    while True:
        try:
            text = input("\nRule> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("quit", "exit", "q"):
            break
        if text.lower() == "rules":
            show_rules(store)
        elif text.lower() == "history":
            show_history(store)
        elif text:
            handle(text, store, client)
    store.close()


if __name__ == "__main__":
    main()
