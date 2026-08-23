"""Note templates: a shape to start from.

Capture is one keystroke because an unstructured thought should cost
nothing to record. Structured notes are the other half of the problem —
a meeting, a trip, a person all have a shape, and reproducing it from
memory every time is the friction that stops people writing them at all.

A template is a markdown file under ``templates/`` in the brain, with
optional frontmatter naming where notes made from it should land:

    ---
    name: Meeting
    target: meetings/{{date}}-{{slug}}.md
    ---
    # {{title}}

    Date: {{date}}

Placeholders are deliberately few — ``{{title}}``, ``{{slug}}``,
``{{date}}``, ``{{time}}``, ``{{datetime}}``, ``{{user}}`` — because a
template language is a programming language nobody asked for. Anything
more elaborate belongs in a skill, which the agent can actually reason
about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from cortex.config import BrainConfig
from cortex.vaults import VaultError, vault_path

TEMPLATES_DIR = "templates"
PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


class TemplateError(ValueError):
    pass


@dataclass
class Template:
    name: str
    title: str
    target: str
    body: str          # frontmatter stripped, for rendering
    raw: str = ""      # the file exactly as written, for editing

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "target": self.target,
            "body": self.body,
            # PUT writes the whole file, so an editor must round-trip this
            # one — saving `body` back would delete the frontmatter and with
            # it the target every note from this template lands at.
            "raw": self.raw or self.body,
        }


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:70] or "note"


def placeholders(title: str, when: datetime | None = None, user: str = "") -> dict[str, str]:
    stamp = when or datetime.now()
    return {
        "title": title,
        "slug": slugify(title),
        "date": stamp.date().isoformat(),
        "time": stamp.strftime("%H:%M"),
        "datetime": stamp.isoformat(timespec="minutes"),
        "user": user,
    }


def render(text: str, values: dict[str, str]) -> str:
    """Substitute known placeholders; leave unknown ones visible.

    An unrecognised ``{{whatever}}`` stays as written rather than becoming
    an empty string — a template with a typo should look wrong, not
    silently lose a line.
    """
    return PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def parse(name: str, text: str) -> Template:
    title = name.replace("-", " ").replace("_", " ").strip().capitalize()
    target = ""
    front = _FRONTMATTER_RE.match(text)
    body = text
    if front:
        for line in front.group(1).splitlines():
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip().strip("'\"")
            if key == "name" and value:
                title = value
            elif key == "target" and value:
                target = value
        body = text[front.end():]
    if not target:
        target = "{{slug}}.md"
    return Template(name=name, title=title, target=target, body=body, raw=text)


def list_templates(config: BrainConfig) -> list[Template]:
    directory = config.root / TEMPLATES_DIR
    if not directory.is_dir():
        return []
    out: list[Template] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("_"):
            continue
        out.append(parse(path.stem, path.read_text(encoding="utf-8", errors="replace")))
    return out


def get(config: BrainConfig, name: str) -> Template | None:
    return next((t for t in list_templates(config) if t.name == name), None)


def save(config: BrainConfig, name: str, text: str) -> Template:
    # slugify falls back to "note" so a titled note always gets a filename;
    # a template name has no such excuse, so check before the fallback runs
    # rather than silently saving someone's "!!!" as "note".
    if not re.sub(r"[^a-z0-9]+", "", name.lower()):
        raise TemplateError("a template name needs a letter or a digit in it")
    name = slugify(name)
    directory = config.root / TEMPLATES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(text, encoding="utf-8")
    return parse(name, text)


def delete(config: BrainConfig, name: str) -> bool:
    path = config.root / TEMPLATES_DIR / f"{slugify(name)}.md"
    if not path.is_file():
        return False
    path.unlink()
    return True


def create_note(
    config: BrainConfig,
    template: Template,
    vault: str,
    title: str,
    user: str = "",
    when: datetime | None = None,
) -> tuple[str, str]:
    """Make a note from a template. Returns (vault-relative path, body).

    Refuses to overwrite: a template is for starting something, and
    silently replacing an existing note would be the opposite.
    """
    title = " ".join(title.split())
    if not title:
        raise TemplateError("a new note needs a title")
    values = placeholders(title, when=when, user=user)
    rel = render(template.target, values)
    if not rel.endswith(".md"):
        rel += ".md"
    if ".." in rel:
        raise TemplateError("a template target cannot climb out of the vault")
    try:
        target = vault_path(config, vault, rel)
    except VaultError as exc:
        raise TemplateError(str(exc)) from exc
    if target.exists():
        raise TemplateError(f"{rel} already exists")
    body = render(template.body, values)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return rel, body


BUILTIN: dict[str, str] = {
    "meeting": """\
---
name: Meeting
target: meetings/{{date}}-{{slug}}.md
---
# {{title}}

- Date: {{date}} {{time}}
- Present:

## Decisions

## Actions

- [ ]

## Not decided

""",
    "person": """\
---
name: Person
target: people/{{slug}}.md
---
# {{title}}

- How we know them:
- Contact:

## Worth remembering

""",
    "trip": """\
---
name: Trip
target: trips/{{date}}-{{slug}}.md
---
# {{title}}

- Dates:
- Staying:

## Before we go

- [ ] Check passports and insurance

## Bookings

""",
    "recipe": """\
---
name: Recipe
target: recipes/{{slug}}.md
---
---
tags: [recipe]
---

# {{title}}

Serves:

## Ingredients

## Method

1.

""",
    "weekly-review": """\
---
name: Weekly review
target: reviews/{{date}}.md
---
# Week ending {{date}}

## What moved

## What slipped

## Decide this week

- [ ]

""",
}


def install_builtin(config: BrainConfig) -> list[str]:
    """Write the starter templates, never overwriting an edited one."""
    directory = config.root / TEMPLATES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for name, text in BUILTIN.items():
        path = directory / f"{name}.md"
        if path.exists():
            continue
        path.write_text(text, encoding="utf-8")
        written.append(name)
    return written
