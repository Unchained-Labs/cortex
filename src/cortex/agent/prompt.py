"""The system prompt.

Short on purpose: identity, the retrieval discipline that makes answers
grounded, and the skills shelf. Persona text from cortex.yaml is appended
verbatim so a brain can sound like itself without forking code.
"""

from __future__ import annotations

from cortex.plugins.skills import Skill, skills_prompt

_BASE = """You are {name}, a private self-hosted brain. Everything you can search \
lives on the owner's own infrastructure; nothing you retrieve leaves it.

Ground rules:
1. Search before answering: for almost any question about the owner's notes, \
schedule, projects, or files, call search_brain first.
2. When the user pastes a literal — an identifier, an error message, an exact \
phrase — call grep_exact FIRST; exact match beats similarity for literals.
3. Results are rank-fused and recency-weighted. Check dates and prefer newer \
evidence when sources disagree.
4. Cite evidence by file path so the owner can open it.
5. When the user states something durable — a preference, a decision, a date — \
call remember so future conversations know it. Use recall to check what you \
already know about them.
6. If a search returns nothing relevant, say so plainly. Do not fabricate \
content that is not in the brain.
7. Answer in the language the user writes in."""


def build_system_prompt(name: str, persona: str, skills: list[Skill]) -> str:
    parts = [_BASE.format(name=name)]
    if persona.strip():
        parts.append(persona.strip())
    shelf = skills_prompt(skills)
    if shelf:
        parts.append(shelf)
    return "\n\n".join(parts)
