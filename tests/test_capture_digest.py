from datetime import date, datetime

import pytest

from cortex import capture, digest, service
from cortex.brain import Brain
from cortex.vaults import VaultError


def test_capture_creates_and_appends(brain: Brain):
    rel, line, lineno = capture.append_note(
        brain.config, "shared", "the boiler service is due in March"
    )
    assert lineno == 3  # heading, blank, first captured line
    assert rel == f"journal/{date.today().isoformat()}.md"
    assert "boiler service" in line
    body = (brain.config.shared_vault / rel).read_text()
    assert body.startswith("# ")  # a human-readable day heading
    assert body.rstrip().endswith(line)

    _, second, second_no = capture.append_note(brain.config, "shared", "and the gutters")
    assert second_no == 4
    body = (brain.config.shared_vault / rel).read_text()
    assert line in body and second in body  # appends, never overwrites


def test_capture_normalises_and_refuses_empty(brain: Brain):
    _, line, _ = capture.append_note(brain.config, "shared", "  spread   over\n lines  ")
    assert "spread over lines" in line
    for bad in ("", "   ", "\n"):
        with pytest.raises(VaultError, match="nothing to capture"):
            capture.append_note(brain.config, "shared", bad)


def test_capture_stamps_the_time_it_was_given(brain: Brain):
    _, line, _ = capture.append_note(
        brain.config, "shared", "late thought", when=datetime(2026, 8, 21, 23, 5)
    )
    assert line.startswith("- **23:05**")


def test_digest_is_empty_on_an_empty_brain(brain: Brain):
    built = digest.build_digest(brain.config, brain.store)
    assert built.is_empty()
    assert "nothing on" in digest.format_digest(built)


def test_digest_finds_open_tasks_only(brain: Brain):
    (brain.config.shared_vault / "list.md").write_text(
        "# Shopping\n\n- [ ] milk\n- [x] eggs\n* [ ] bread\n", encoding="utf-8"
    )
    tasks = digest.open_tasks(brain.config)
    assert {t.text for t in tasks} == {"milk", "bread"}
    assert all(t.path == "vaults/shared/list.md" for t in tasks)
    # line numbers must be exact: complete_task addresses tasks by them
    milk = next(t for t in tasks if t.text == "milk")
    lines = (brain.config.shared_vault / "list.md").read_text().splitlines()
    assert lines[milk.line - 1] == "- [ ] milk"


def test_digest_scope_hides_other_peoples_tasks(brain: Brain):
    (brain.config.vaults_dir / "sam").mkdir(parents=True, exist_ok=True)
    (brain.config.shared_vault / "s.md").write_text("- [ ] shared thing\n", encoding="utf-8")
    (brain.config.vaults_dir / "sam" / "p.md").write_text("- [ ] sam thing\n", encoding="utf-8")
    tasks = digest.open_tasks(brain.config, prefixes=("vaults/shared/",))
    assert [t.text for t in tasks] == ["shared thing"]
    assert digest.open_tasks(brain.config, prefixes=()) == []


def test_digest_counts_captures(brain: Brain):
    capture.append_note(brain.config, "shared", "one")
    capture.append_note(brain.config, "shared", "two")
    built = digest.build_digest(brain.config, brain.store, vault="shared")
    assert built.captured_today == 2
    assert "Captured today: 2" in digest.format_digest(built)


def test_digest_lists_todays_events(brain: Brain):
    day = date.today().isoformat()
    out = brain.config.sources_dir / "calendar_ics"
    out.mkdir(parents=True, exist_ok=True)
    (out / "e.md").write_text(
        f"# Standup\n\n- Calendar: home\n- Start: {day}T09:30:00+00:00\n", encoding="utf-8"
    )
    events = digest.upcoming_events(brain.config)
    assert len(events) == 1 and events[0].today and events[0].title == "Standup"
    assert "09:30 Standup" in digest.format_digest(
        digest.build_digest(brain.config, brain.store)
    )


def test_service_unit_is_well_formed(brain: Brain):
    text = service.unit_text(brain.config.root, "0.0.0.0", 8642, "home")
    assert "[Unit]" in text and "[Service]" in text and "[Install]" in text
    assert "serve --brain" in text and "--port 8642" in text
    assert "Restart=on-failure" in text
    assert "WantedBy=default.target" in text


def test_on_this_day_finds_earlier_years_only(brain: Brain):
    from datetime import date as _date

    today = _date(2026, 8, 21)
    journal = brain.config.shared_vault / "journal"
    journal.mkdir(parents=True, exist_ok=True)
    for name in ("2025-08-21.md", "2024-08-21.md", "2026-08-21.md", "2025-08-20.md"):
        (journal / name).write_text("an entry\n", encoding="utf-8")
    got = digest.on_this_day(brain.config, today=today)
    assert [r.path for r in got] == [
        "vaults/shared/journal/2025-08-21.md",
        "vaults/shared/journal/2024-08-21.md",
    ]
    assert [r.years for r in got] == [1, 2]


def test_on_this_day_is_silent_and_unsentimental(brain: Brain):
    from datetime import date as _date

    assert digest.on_this_day(brain.config, today=_date(2026, 8, 21)) == []
    journal = brain.config.shared_vault / "journal"
    journal.mkdir(parents=True, exist_ok=True)
    (journal / "2025-08-21.md").write_text("x\n", encoding="utf-8")
    built = digest.build_digest(brain.config, brain.store)
    built.day = "2026-08-21"
    built.on_this_day = digest.on_this_day(brain.config, today=_date(2026, 8, 21))
    text = digest.format_digest(built)
    assert "a year ago" in text
    for gush in ("!", "memories", "Look back", "🎉"):
        assert gush not in text


def test_demo_content_install_and_remove(brain: Brain):
    from cortex import demo

    assert not demo.installed(brain.config)
    written = demo.install(brain.config)
    assert len(written) == 5
    assert demo.installed(brain.config)
    # every sample says it is a sample, so nobody mistakes it for their own
    for rel in written:
        body = (brain.config.shared_vault / rel).read_text()
        assert "Example note" in body and "example" in body.lower()
    assert demo.install(brain.config) == []  # never overwrites
    assert demo.remove(brain.config) == 5
    assert not demo.installed(brain.config)


def test_digest_is_bounded_and_never_shows_a_debt_counter(brain: Brain):
    """A daily surface must be finishable. An unbounded 'you have N pending'
    counter is the documented way to make people stop opening it."""
    lines = "\n".join(f"- [ ] task number {i}" for i in range(50))
    (brain.config.shared_vault / "many.md").write_text(f"# Many\n\n{lines}\n", encoding="utf-8")
    built = digest.build_digest(brain.config, brain.store)
    assert len(built.tasks) == digest.MAX_TASKS <= 5
    text = digest.format_digest(built)
    assert "more" not in text  # no "… 37 more"
    assert "(50)" not in text and "(45)" not in text
    assert text.rstrip().endswith("That is everything for today.")
    assert "total_open_tasks" not in built.as_dict()


def test_concurrent_captures_never_lose_a_line(brain: Brain):
    """Four surfaces can append at once; none of them may overwrite another."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda i: capture.append_note(brain.config, "shared", f"thought {i}"),
            range(40),
        ))
    body = capture.read_daily_note(brain.config, "shared")
    for i in range(40):
        assert f"thought {i}" in body, f"lost thought {i}"
