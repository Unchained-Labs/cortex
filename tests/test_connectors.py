from cortex.config import load_config
from cortex.connectors import run_connectors
from cortex.connectors.calendar_ics import parse_events, slugify

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:1@example
SUMMARY:Garden planning — spring
DTSTART:20260901T100000Z
DTEND:20260901T110000Z
ATTENDEE:mailto:erwin@example.com
ATTENDEE;CN=Sam:mailto:sam@example.com
DESCRIPTION:A very long line that ICS folds onto the next physical line beca
 use the spec says lines are 75 octets
END:VEVENT
BEGIN:VEVENT
SUMMARY:No start so this one is dropped
END:VEVENT
END:VCALENDAR
"""


def test_parse_events_unfolds_and_collects():
    (event,) = parse_events(ICS)
    assert event["summary"] == "Garden planning — spring"
    assert event["start"].year == 2026
    assert event["attendees"] == ["erwin@example.com", "sam@example.com"]


def test_slugify():
    assert slugify("Garden planning — spring!") == "garden-planning-spring"
    assert slugify("???") == "untitled"


def test_dropin_connector_runs_and_broken_one_is_isolated(brain_dir):
    (brain_dir / "connectors" / "good.py").write_text(
        "def sync(out_dir, settings):\n"
        "    (out_dir / 'note.md').write_text('# distilled\\n')\n",
        encoding="utf-8",
    )
    (brain_dir / "connectors" / "bad.py").write_text(
        "def sync(out_dir, settings):\n    raise RuntimeError('feed offline')\n",
        encoding="utf-8",
    )
    (brain_dir / "connectors" / "_shared.py").write_text("raise SystemExit\n", encoding="utf-8")

    config = load_config(brain_dir)
    results = run_connectors(config)
    assert results["good"] == "ok"
    assert "feed offline" in results["bad"]
    assert "_shared" not in results
    assert (config.sources_dir / "good" / "note.md").read_text().startswith("# distilled")


def test_unknown_builtin_reports_known_names(brain_dir):
    (brain_dir / "cortex.yaml").write_text(
        "connectors:\n  no_such_thing: {}\n", encoding="utf-8"
    )
    config = load_config(brain_dir)
    results = run_connectors(config)
    assert "calendar_ics" in results["no_such_thing"]
