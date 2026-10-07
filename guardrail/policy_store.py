"""Versioned policy rules in SQLite.

Every approved change saves a full snapshot of all rules as a new version.
Old versions are never edited, so for any decision in the audit log
(which records its policy_version) we can show exactly which rules were active.

Version 1 is copied from data/policies.json the first time the store is opened.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from guardrail.rules import ID_FAMILY, load_rules

SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "policies.json"
COMPANY_FILE = Path(__file__).resolve().parent.parent / "data" / "company.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_versions (
    version     INTEGER PRIMARY KEY,
    rules_json  TEXT NOT NULL,     -- every user rule active in this version
    changed_by  TEXT NOT NULL,     -- the human who approved the change
    changed_at  TEXT NOT NULL,
    change      TEXT NOT NULL      -- one line: what changed
)
"""


class PolicyStore:
    def __init__(self, path: str, seed_file: Path = SEED_FILE):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(SCHEMA)
        if self.current_version() == 0:
            with open(seed_file, encoding="utf-8") as f:
                seed = json.load(f)
            self._save(int(seed["policy_version"]), seed["rules"],
                       "Initial policy (data/policies.json)", "Starting rules.")
        with open(COMPANY_FILE, encoding="utf-8") as f:
            self.account_names = list(json.load(f)["accounts"])

    def close(self):
        self.conn.close()

    def _save(self, version: int, rules: list[dict], changed_by: str, change: str):
        self.conn.execute(
            "INSERT INTO policy_versions (version, rules_json, changed_by, changed_at, change) "
            "VALUES (?, ?, ?, ?, ?)",
            (version, json.dumps(rules), changed_by,
             datetime.now().isoformat(timespec="seconds"), change),
        )
        self.conn.commit()

    def current_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) AS v FROM policy_versions").fetchone()
        return row["v"] or 0

    def get_rules(self, version: int | None = None) -> list[dict]:
        """The user rules in a version (the current one by default)."""
        version = version or self.current_version()
        row = self.conn.execute("SELECT rules_json FROM policy_versions WHERE version = ?",
                                (version,)).fetchone()
        if row is None:
            raise KeyError(f"No policy version {version}")
        return json.loads(row["rules_json"])

    def history(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT version, changed_by, changed_at, change FROM policy_versions ORDER BY version")
        return [dict(r) for r in rows]

    def approve(self, result, approver: str) -> dict:
        """A human approves a compiled rule. It goes live as a new policy version.

        `result` is a CompileResult from the compiler. Only "compiled" results
        can be approved. The new rule list is fully re-checked before saving,
        so nothing malformed can go live even if it got this far.
        """
        if not approver or not approver.strip():
            raise ValueError("An approver name is required.")
        if result.status != "compiled":
            raise ValueError(f"Only compiled rules can be approved, not '{result.status}'.")

        current = self.get_rules()
        for rule in load_rules(current, self.account_names):
            if (rule.type, rule.settings, rule.action) == (result.rule_type, result.settings, result.action):
                raise ValueError(f"This rule is already active as rule {rule.id}.")

        # Next id in the type's family, e.g. a second amount limit becomes 1.2.
        family = ID_FAMILY[result.rule_type]
        used = [int(r["id"].split(".")[1]) for r in current if r["id"].split(".")[0] == family]
        new_rule = {
            "id": f"{family}.{max(used, default=0) + 1}",
            "version": 1,
            "type": result.rule_type,
            "text": result.english,
            "settings": result.settings,
            "action": result.action,
            "approved_by": approver.strip(),
            "approved_at": datetime.now().isoformat(timespec="seconds"),
        }
        new_rules = current + [new_rule]
        load_rules(new_rules, self.account_names)   # raises if anything is wrong

        self._save(self.current_version() + 1, new_rules, approver.strip(),
                   f"Added rule {new_rule['id']}: {result.english}")
        return new_rule
