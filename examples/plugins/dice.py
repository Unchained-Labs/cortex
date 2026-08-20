"""Example drop-in tool plugin.

Copy into a brain's ``plugins/`` directory; the agent picks it up on the next
run. The whole contract is a module-level ``register(registry)``.
"""

import random

from cortex.plugins import ToolPlugin


def register(registry):
    def roll(sides: int = 6, count: int = 1) -> str:
        sides = max(2, min(int(sides), 1000))
        count = max(1, min(int(count), 20))
        rolls = [random.randint(1, sides) for _ in range(count)]
        return f"{count}d{sides}: {rolls} (total {sum(rolls)})"

    registry.register(
        ToolPlugin(
            name="roll_dice",
            description="Roll dice, e.g. for deciding who does the dishes.",
            parameters={
                "sides": {"type": "integer", "description": "Sides per die (default 6)."},
                "count": {"type": "integer", "description": "Number of dice (default 1)."},
            },
            func=roll,
        )
    )
