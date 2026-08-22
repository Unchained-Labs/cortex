"""Tidying rules: put things where they belong, on a schedule.

Capture is deliberately unfiled — a thought goes into today's note and
search finds it later. That works until the vault is a year old and every
recipe, receipt and trip note lives in a journal entry. Rules are the
answer: say once where a kind of note belongs, and the brain files it
while you are not looking.

Three deliberate constraints, because this moves someone's writing:

* **Nothing is destructive.** A rule can move, tag or archive. There is no
  delete action and there will not be one.
* **Preview is free and always available.** Every run can be a dry run, and
  the panel shows exactly what would happen before anything does.
* **Every action is logged** to the rule_runs table with the before and
  after path, so "where did my note go" always has an answer.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from cortex.config import BrainConfig
from cortex.vaults import VaultError, vault_path

MATCH_KINDS = ("path", "tag", "frontmatter", "content", "older_than_days")
ACTION_KINDS = ("move", "tag", "archive")
MAX_ACTIONS_PER_RUN = 200

_TAG_RE = re.compile(r"(?:^|\s)#([a-z0-9][\w/-]*)", re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


class RuleError(ValueError):
    pass


@dataclass
class Match:
    kind: str
    value: str = ""

    def describe(self) -> str:
        if self.kind == "older_than_days":
            return f"older than {self.value} days"
        return f"{self.kind} matches {self.value!r}"


@dataclass
class Action:
    kind: str
    value: str = ""

    def describe(self) -> str:
        if self.kind == "move":
            return f"move into {self.value}/"
        if self.kind == "archive":
            return f"archive into {self.value or 'archive'}/"
        return f"add #{self.value}"


@dataclass
class Rule:
    name: str
    vault: str = "shared"
    matches: list[Match] = field(default_factory=list)
    action: Action = field(default_factory=lambda: Action("tag", "sorted"))
    enabled: bool = True

    def describe(self) -> str:
        conditions = " and ".join(m.describe() for m in self.matches) or "everything"
        return f"{conditions} → {self.action.describe()}"


@dataclass
class Planned:
    path: str
    rule: str
    action: str
    target: str = ""

    def as_dict(self) -> dict:
        return {"path": self.path, "rule": self.rule, "action": self.action,
                "target": self.target}


def parse_rule(raw: dict) -> Rule:
    name = str(raw.get("name") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9 _-]{0,47}", name, re.IGNORECASE):
        raise RuleError("a rule needs a short name (letters, digits, spaces, - or _)")
    matches = [
        Match(kind=str(m.get("kind")), value=str(m.get("value", "")))
        for m in (raw.get("matches") or [])
    ]
    if not matches:
        raise RuleError("a rule with no conditions would match every note")
    for m in matches:
        if m.kind not in MATCH_KINDS:
            raise RuleError(f"unknown condition {m.kind!r} (expected {', '.join(MATCH_KINDS)})")
        if m.kind == "older_than_days":
            try:
                if int(m.value) <= 0:
                    raise ValueError
            except ValueError as exc:
                raise RuleError("older_than_days needs a positive number of days") from exc
        elif not m.value.strip():
            raise RuleError(f"the {m.kind} condition needs a value")
    action_raw = raw.get("action") or {}
    action = Action(kind=str(action_raw.get("kind", "")), value=str(action_raw.get("value", "")))
    if action.kind not in ACTION_KINDS:
        raise RuleError(f"unknown action {action.kind!r} (expected {', '.join(ACTION_KINDS)})")
    if action.kind == "move" and not action.value.strip():
        raise RuleError("a move needs a destination folder")
    if action.kind == "tag" and not re.fullmatch(r"[a-z0-9][\w/-]*", action.value, re.IGNORECASE):
        raise RuleError("a tag is letters, digits, - _ or /")
    if action.kind in ("move", "archive") and ".." in action.value:
        raise RuleError("a destination cannot climb out of the vault")
    return Rule(
        name=name,
        vault=str(raw.get("vault") or "shared"),
        matches=matches,
        action=action,
        enabled=bool(raw.get("enabled", True)),
    )


def rule_to_dict(rule: Rule) -> dict:
    return {
        "name": rule.name,
        "vault": rule.vault,
        "matches": [{"kind": m.kind, "value": m.value} for m in rule.matches],
        "action": {"kind": rule.action.kind, "value": rule.action.value},
        "enabled": rule.enabled,
        "describes": rule.describe(),
    }


def tags_of(text: str) -> set[str]:
    """Inline #tags plus a frontmatter tags: [a, b] list."""
    found = {t.lower() for t in _TAG_RE.findall(text)}
    front = _FRONTMATTER_RE.match(text)
    if front:
        for line in front.group(1).splitlines():
            if line.strip().lower().startswith("tags:"):
                raw = line.split(":", 1)[1].strip().strip("[]")
                found |= {t.strip().strip("'\"").lower() for t in raw.split(",") if t.strip()}
    return found


def _frontmatter_has(text: str, expression: str) -> bool:
    """`key: value`, or just `key` to test presence."""
    front = _FRONTMATTER_RE.match(text)
    if not front:
        return False
    key, _, wanted = expression.partition(":")
    key = key.strip().lower()
    wanted = wanted.strip().lower()
    for line in front.group(1).splitlines():
        name, _, value = line.partition(":")
        if name.strip().lower() != key:
            continue
        return not wanted or wanted in value.strip().lower()
    return False


def matches(rule: Rule, rel: str, text: str, mtime: float, now: float) -> bool:
    for condition in rule.matches:
        if condition.kind == "path":
            if not Path(rel).match(condition.value):
                return False
        elif condition.kind == "tag":
            if condition.value.lstrip("#").lower() not in tags_of(text):
                return False
        elif condition.kind == "frontmatter":
            if not _frontmatter_has(text, condition.value):
                return False
        elif condition.kind == "content":
            if condition.value.lower() not in text.lower():
                return False
        elif condition.kind == "older_than_days":
            if now - mtime < int(condition.value) * 86400:
                return False
    return True


def _unique(target: Path) -> Path:
    """Never overwrite an existing note when filing one."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for n in range(2, 100):
        candidate = target.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuleError(f"too many notes named {target.name}")


def plan(config: BrainConfig, rules: list[Rule], now: float | None = None) -> list[Planned]:
    """What would happen. Runs no side effects."""
    now = now or datetime.now().timestamp()
    planned: list[Planned] = []
    for rule in rules:
        if not rule.enabled:
            continue
        try:
            root = vault_path(config, rule.vault)
        except VaultError:
            continue
        for path in sorted(root.rglob("*.md")):
            rel = str(path.relative_to(root))
            parts = Path(rel).parts
            if any(p.startswith(".") for p in parts):
                continue
            # never re-file something already sitting where the rule wants it
            if rule.action.kind in ("move", "archive"):
                destination = rule.action.value or "archive"
                if rel.startswith(f"{destination}/"):
                    continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if not matches(rule, rel, text, mtime, now):
                continue
            if rule.action.kind == "tag":
                if rule.action.value.lower() in tags_of(text):
                    continue
                planned.append(Planned(rel, rule.name, "tag", rule.action.value))
            else:
                destination = rule.action.value or "archive"
                planned.append(
                    Planned(rel, rule.name, rule.action.kind, f"{destination}/{Path(rel).name}")
                )
            if len(planned) >= MAX_ACTIONS_PER_RUN:
                return planned
    return planned


def apply(config: BrainConfig, rules: list[Rule], now: float | None = None) -> list[dict]:
    """Do it. Returns one record per action, for the audit log."""
    done: list[dict] = []
    for item in plan(config, rules, now):
        rule = next((r for r in rules if r.name == item.rule), None)
        if rule is None:
            continue
        try:
            source = vault_path(config, rule.vault, item.path)
            if not source.is_file():
                continue
            if item.action == "tag":
                text = source.read_text(encoding="utf-8", errors="replace")
                if not text.endswith("\n"):
                    text += "\n"
                source.write_text(f"{text}\n#{item.target}\n", encoding="utf-8")
                done.append({"rule": rule.name, "action": "tag", "path": item.path,
                             "target": item.target})
            else:
                target = _unique(vault_path(config, rule.vault, item.target))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                done.append({
                    "rule": rule.name,
                    "action": item.action,
                    "path": item.path,
                    "target": str(target.relative_to(vault_path(config, rule.vault))),
                })
        except (OSError, VaultError, RuleError) as exc:
            done.append({"rule": rule.name, "action": "error", "path": item.path,
                         "target": str(exc)})
    return done


def suggested_rules() -> list[dict]:
    """Starting points, so the panel is not an empty form.

    Each is switched off until someone reads it and turns it on, and each
    carries the same ``describes`` sentence a saved rule does so no client
    has to reimplement describe().
    """
    return [
        {**raw, "describes": parse_rule(raw).describe()} for raw in _SUGGESTED_RULES
    ]


_SUGGESTED_RULES: list[dict] = [
        {
            "name": "file recipes",
            "vault": "shared",
            "matches": [{"kind": "tag", "value": "recipe"}],
            "action": {"kind": "move", "value": "recipes"},
            "enabled": False,
        },
        {
            "name": "archive old clips",
            "vault": "shared",
            "matches": [
                {"kind": "path", "value": "clips/*"},
                {"kind": "older_than_days", "value": "90"},
            ],
            "action": {"kind": "archive", "value": "archive/clips"},
            "enabled": False,
        },
        {
            "name": "file trip notes",
            "vault": "shared",
            "matches": [{"kind": "tag", "value": "trip"}],
            "action": {"kind": "move", "value": "trips"},
            "enabled": False,
        },
        {
            "name": "tag anything mentioning the boiler",
            "vault": "shared",
            "matches": [{"kind": "content", "value": "boiler"}],
            "action": {"kind": "tag", "value": "house"},
            "enabled": False,
        },
    ]


def next_due(interval_hours: float, last_run: datetime | None) -> bool:
    if last_run is None:
        return True
    return datetime.now() - last_run >= timedelta(hours=interval_hours)
