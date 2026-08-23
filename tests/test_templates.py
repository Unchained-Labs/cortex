from datetime import datetime

import pytest

from cortex import templates
from cortex.brain import Brain


def install(brain: Brain) -> None:
    templates.install_builtin(brain.config)


# -- parsing and rendering -------------------------------------------------


def test_parse_reads_frontmatter_and_strips_it():
    parsed = templates.parse(
        "meeting", "---\nname: Meeting\ntarget: m/{{slug}}.md\n---\n# {{title}}\n"
    )
    assert parsed.title == "Meeting"
    assert parsed.target == "m/{{slug}}.md"
    assert parsed.body == "# {{title}}\n"


def test_parse_without_frontmatter_has_sensible_defaults():
    parsed = templates.parse("shopping-list", "# {{title}}\n")
    assert parsed.title == "Shopping list"
    assert parsed.target == "{{slug}}.md"


def test_render_substitutes_known_placeholders():
    values = templates.placeholders("Roast chicken", when=datetime(2026, 8, 23, 9, 5), user="erwin")
    out = templates.render("{{title}} {{slug}} {{date}} {{time}} {{user}}", values)
    assert out == "Roast chicken roast-chicken 2026-08-23 09:05 erwin"


def test_render_leaves_an_unknown_placeholder_visible():
    """A template with a typo should look wrong, not silently lose a line."""
    out = templates.render("{{title}} {{nonsense}}", templates.placeholders("X"))
    assert out == "X {{nonsense}}"


# -- the library -----------------------------------------------------------


def test_builtin_templates_install_and_parse(brain: Brain):
    written = templates.install_builtin(brain.config)
    assert {"meeting", "person", "trip", "recipe"} <= set(written)
    found = {t.name: t for t in templates.list_templates(brain.config)}
    assert found["meeting"].target.startswith("meetings/")
    for template in found.values():
        assert template.title and template.body.strip()
    # never clobbers an edited template
    (brain.config.templates_dir / "meeting.md").write_text("mine\n", encoding="utf-8")
    assert "meeting" not in templates.install_builtin(brain.config)
    assert (brain.config.templates_dir / "meeting.md").read_text() == "mine\n"


def test_save_and_delete(brain: Brain):
    saved = templates.save(brain.config, "Book Notes", "# {{title}}\n")
    assert saved.name == "book-notes"
    assert templates.get(brain.config, "book-notes") is not None
    assert templates.delete(brain.config, "book-notes")
    assert not templates.delete(brain.config, "book-notes")
    with pytest.raises(templates.TemplateError, match="needs a letter or a digit"):
        templates.save(brain.config, "!!!", "x")


# -- making notes ----------------------------------------------------------


def test_create_note_lands_where_the_target_says(brain: Brain):
    install(brain)
    template = templates.get(brain.config, "meeting")
    rel, body = templates.create_note(
        brain.config, template, "shared", "Kitchen rota",
        when=datetime(2026, 8, 23, 9, 5),
    )
    assert rel == "meetings/2026-08-23-kitchen-rota.md"
    assert (brain.config.shared_vault / rel).is_file()
    assert "# Kitchen rota" in body
    assert "{{" not in body  # every placeholder resolved


def test_create_note_refuses_to_overwrite(brain: Brain):
    install(brain)
    template = templates.get(brain.config, "recipe")
    templates.create_note(brain.config, template, "shared", "Roast chicken")
    with pytest.raises(templates.TemplateError, match="already exists"):
        templates.create_note(brain.config, template, "shared", "Roast chicken")


def test_create_note_needs_a_title_and_cannot_escape(brain: Brain):
    install(brain)
    template = templates.get(brain.config, "recipe")
    with pytest.raises(templates.TemplateError, match="needs a title"):
        templates.create_note(brain.config, template, "shared", "   ")
    escaping = templates.Template("x", "X", "../../{{slug}}.md", "hi")
    with pytest.raises(templates.TemplateError, match="climb out"):
        templates.create_note(brain.config, escaping, "shared", "Escape")


def test_raw_round_trips_where_body_would_not(brain: Brain):
    """`body` is frontmatter-stripped for rendering; saving it back would
    delete the target and silently relocate every future note. `raw` is
    the file, and it round-trips byte for byte."""
    templates.install_builtin(brain.config)
    for original in templates.list_templates(brain.config):
        assert "target:" in original.raw
        assert "target:" not in original.body  # stripped, by design
        templates.save(brain.config, original.name, original.raw)
        again = templates.get(brain.config, original.name)
        assert again.raw == original.raw
        assert again.target == original.target
