"""Optional example notes, for a brain that is still empty.

An empty brain cannot answer anything, so a new user's first question
returns nothing and the product looks broken when it is merely unfed. The
fix every mature tool in this space converged on is: never show someone an
empty page. Notion previews templates with demo data; Loggly's empty state
offers "add real data" *or* "explore with sample data".

These notes are household-shaped, obviously fake, and every one of them
says so in its own frontmatter and body. They live under ``examples/`` so
deleting them is one directory removal, and ``cortex demo --remove`` does
it for you. They are never installed without being asked for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from cortex.config import BrainConfig
from cortex.vaults import vault_path

EXAMPLES_DIR = "examples"

_BANNER = (
    "> [!note] Example note\n"
    "> Part of the cortex sample set. Delete it any time with "
    "`cortex demo --remove`.\n"
)


@dataclass
class Sample:
    path: str
    body: str


def _samples() -> list[Sample]:
    soon = date.today() + timedelta(days=9)
    return [
        Sample(
            "shakshuka.md",
            f"""---
tags: [recipe, example]
---

# Shakshuka

{_BANNER}
Weekend breakfast when the tomatoes are good.

1. Soften onion and red pepper, then garlic and cumin.
2. Crush in six ripe tomatoes; simmer 15 minutes.
3. Crack in four eggs, lid on, six minutes.

> [!tip]
> A spoon of harissa in the base, not on top.

Serves four. Doubles badly — the eggs steam instead of setting.
""",
        ),
        Sample(
            "house/boiler.md",
            f"""---
tags: [house, maintenance, example]
---

# Boiler

{_BANNER}
Vaillant ecoTEC, installed 2021, under the stairs.

- Service is annual, due each March
- Pressure should sit between 1.0 and 1.5 bar when cold
- Repressurising: the filling loop is the grey braided hose

- [ ] Book the March service
- [ ] Photograph the pressure gauge when it is behaving

The installer is Meridian Heating; their number is on the sticker inside
the casing.
""",
        ),
        Sample(
            "house/wifi.md",
            f"""---
tags: [house, example]
---

# Wifi and the network

{_BANNER}
The router is in the hall cupboard. The brain itself runs on the NUC next
to it.

Guest network is a separate SSID; the password is on the whiteboard in the
kitchen rather than in here, because notes get shared and whiteboards do
not.

- [ ] Move the NUC off the shelf where it overheats
""",
        ),
        Sample(
            f"trips/{soon.isoformat()}-lisbon.md",
            f"""---
tags: [trip, example]
---

# Lisbon — {soon.strftime('%B %Y')}

{_BANNER}
Four nights, flying out on the {soon.day}th.

- [ ] Renew the travel insurance
- [ ] Book the Sintra train tickets
- [x] Flights

> [!warning] Passports
> Both expire next year — check the six-month rule before booking anything
> else.

Staying in Alfama. The tram is charming and useless for luggage.
""",
        ),
        Sample(
            "garden.md",
            f"""---
tags: [garden, example]
---

# Garden

{_BANNER}
> [!note] Watering
> The rosemary wants water on Fridays and sulks if it is skipped.

## This month

- [ ] Repot the basil before it bolts
- [ ] Net the fig tree — the magpies found it

The tomatoes go into [[shakshuka]] when they are past looking pretty.
""",
        ),
    ]


def install(config: BrainConfig, vault: str = "shared") -> list[str]:
    """Write the sample notes. Existing files are never overwritten."""
    written: list[str] = []
    for sample in _samples():
        rel = f"{EXAMPLES_DIR}/{sample.path}"
        target = vault_path(config, vault, rel)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(sample.body, encoding="utf-8")
        written.append(rel)
    return written


def remove(config: BrainConfig, vault: str = "shared") -> int:
    """Delete the sample set. Only ever touches examples/."""
    import shutil

    root = vault_path(config, vault, EXAMPLES_DIR)
    if not root.is_dir():
        return 0
    count = sum(1 for p in root.rglob("*") if p.is_file())
    shutil.rmtree(root)
    return count


def installed(config: BrainConfig, vault: str = "shared") -> bool:
    try:
        return vault_path(config, vault, EXAMPLES_DIR).is_dir()
    except Exception:  # noqa: BLE001 - a missing vault is simply "not installed"
        return False
