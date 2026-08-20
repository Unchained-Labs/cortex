"""Seed the demo brain the promo film runs against.

Real stack, real content: the only substituted part of the film is the model
(mock_model.py). Usage: seed.py <workdir> — creates <workdir>/brain.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cortex import auth  # noqa: E402
from cortex.brain import Brain  # noqa: E402

CONFIG = """\
name: home
persona: ""
providers:
  local:
    kind: openai
    base_url: "http://127.0.0.1:8199/v1"
    chat_model: qwen3
    embed_model: nomic-embed-text
roles:
  chat: local
  embed: local
"""

GARDEN = """\
---
area: garden
season: late summer
---

# Garden

> [!note] Watering
> The rosemary needs water on **Fridays** — it sulks if you skip a week.

## This weekend

- [ ] Repot the basil before it bolts
- [ ] Net the fig tree — the magpies found it
- [x] Order winter garlic (arrives Tuesday)

The last of the good tomatoes go into [[shakshuka]].
"""

SHAKSHUKA = """\
# Shakshuka

Weekend breakfast when the garden tomatoes are ripe.

1. Soften onion and red pepper, add garlic and cumin.
2. Crush in six very ripe tomatoes, simmer 15 min.
3. Crack in four eggs, lid on, 6 minutes.

> [!tip]
> A spoon of harissa in the base, not on top.
"""

KYOTO = """\
# Kyoto — October

Dates: **Oct 12 → 19**. Ryokan in Gion booked (confirmation in email).

- [ ] Activate the rail pass at Kansai airport
- [ ] Book Saihō-ji moss garden (needs advance postcard!)
- [x] Flights

> [!warning] Rail pass
> The pass must be activated within 90 days of purchase — that window
> closes Oct 30.
"""

HOMELAB = """\
# Homelab

The brain itself runs on the NUC next to the router.

```sh
cortex serve --brain ~/brain --host 0.0.0.0   # dashboard on :8642
```

Models come from the tower downstairs (Ollama on :11434). Nothing leaves
the LAN.
"""

JOURNAL = """\
# Journal

Private to erwin — sam's vault is his own.
"""

TIDES_PLUGIN = '''"""Tide times for the beach, so the agent can answer without a search."""

from cortex.plugins import ToolPlugin


def register(registry):
    def tide_times(day: str = "today") -> str:
        return f"{day}: low 06:40, high 12:55, low 19:10"

    registry.register(
        ToolPlugin(
            name="tide_times",
            description="Tide times for the local beach.",
            parameters={"day": {"type": "string", "description": "Which day."}},
            func=tide_times,
        )
    )
'''

REVIEW_SKILL = """\
---
name: weekly-review
description: How this household runs its Sunday review
---

1. Search for notes touched in the last seven days.
2. Carry unfinished tasks into next week.
3. End with the one thing that matters on Monday.
"""

CHANNEL_SEED = [
    ("sam", "the fig tree situation is getting out of hand, the magpies had a feast"),
    ("erwin", "netting it this weekend — it's on the garden list"),
    ("sam", "also we should lock the kyoto restaurants soon"),
]


def main(workdir: Path) -> None:
    root = workdir / "brain"
    for sub in ("vaults/shared/trips", "vaults/shared/recipes", "sources",
                "skills", "plugins", "connectors", ".cortex"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "cortex.yaml").write_text(CONFIG, encoding="utf-8")
    shared = root / "vaults" / "shared"
    (shared / "garden.md").write_text(GARDEN, encoding="utf-8")
    (shared / "recipes" / "shakshuka.md").write_text(SHAKSHUKA, encoding="utf-8")
    (shared / "trips" / "kyoto.md").write_text(KYOTO, encoding="utf-8")
    (shared / "homelab.md").write_text(HOMELAB, encoding="utf-8")

    brain = Brain(root)
    for username, role in (("erwin", "admin"), ("sam", "member")):
        pw_hash, salt = auth.hash_password("demo-password")
        brain.store.add_user(username, pw_hash, salt, role)
        (root / "vaults" / username).mkdir(exist_ok=True)
    (root / "vaults" / "erwin" / "journal.md").write_text(JOURNAL, encoding="utf-8")

    # extensions, so the Extend panel has something real on film
    (root / "plugins" / "tides.py").write_text(TIDES_PLUGIN, encoding="utf-8")
    (root / "skills" / "weekly-review").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "weekly-review" / "SKILL.md").write_text(
        REVIEW_SKILL, encoding="utf-8"
    )
    brain.store.upsert_mcp_server(
        "home-assistant",
        {
            "transport": "http",
            "url": "http://homeassistant.local:8123/mcp",
            "headers": {"Authorization": "Bearer redacted"},
            "args": [], "include": [], "exclude": [], "command": "",
        },
        False,  # disabled: the demo has no Home Assistant to reach
    )

    channel = brain.store.ensure_channel("general", "cortex")
    for author, body in CHANNEL_SEED:
        brain.store.add_channel_message(channel, author, body)
    brain.close()
    print(f"seeded {root}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
