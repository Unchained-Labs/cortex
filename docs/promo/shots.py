"""Drive the real dashboard and record the promo scenes.

Every scene is the shipped SPA against the running server — the digest,
capture, streaming, tool calls, WebSocket fan-out, rule preview and vault
writes on film are the product working. Only the model is scripted.
Usage: shots.py <workdir> (expects the server on :8646).

Scenes land as <workdir>/scenes/<name>.webm; title cards as
<workdir>/cards/<name>.png. A failing scene leaves fail-<name>.png and its
console log in the workdir.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8646"
SIZE = {"width": 1280, "height": 800}
CARDS = ["intro", "today", "capture", "ask", "together", "tidy", "expand", "outro"]

CAPTURE_TEXT = "the boiler service is due in March"
GARDEN_Q = "what's on the garden list for this weekend?"
KYOTO_Q = "@cortex when is the Kyoto trip again — anything left to do?"


def main(workdir: Path) -> None:  # noqa: C901
    scenes_dir = workdir / "scenes"
    cards_dir = workdir / "cards"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    cards_dir.mkdir(parents=True, exist_ok=True)
    state_path = workdir / "state.json"
    cards_page = Path(__file__).resolve().parent / "cards.html"
    console_log: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)

        # ---- title cards (stills) ---------------------------------------
        page = browser.new_page(viewport=SIZE)
        for name in CARDS:
            page.goto(f"file://{cards_page}?card={name}")
            page.wait_for_timeout(400)
            page.screenshot(path=cards_dir / f"{name}.png")
        page.close()

        def run_scene(name: str, fn, storage: str | None = None) -> None:
            ctx = browser.new_context(
                viewport=SIZE,
                record_video_dir=str(scenes_dir),
                record_video_size=SIZE,
                storage_state=storage,
            )
            page = ctx.new_page()
            page.set_default_timeout(45000)
            page.on("console", lambda m: console_log.append(f"[{name}] {m.type}: {m.text}"))
            page.on("dialog", lambda d: d.accept())
            try:
                fn(ctx, page)
            except Exception:
                page.screenshot(path=workdir / f"fail-{name}.png")
                (workdir / f"fail-{name}.console.txt").write_text(
                    "\n".join(console_log[-40:]), encoding="utf-8"
                )
                raise
            video = page.video
            page.close()
            ctx.close()
            Path(video.path()).rename(scenes_dir / f"{name}.webm")

        def open_app(page, tab: str | None = None) -> None:
            page.goto(BASE)
            page.wait_for_selector(".app-tabs")
            page.wait_for_timeout(800)
            if tab:
                page.locator(".app-tabs .tab", has_text=tab).click()
                page.wait_for_timeout(700)

        # ---- 1: sign in, land on Today, tick something off ---------------
        def scene_today(ctx, page) -> None:
            page.goto(BASE)
            page.wait_for_selector("input")
            page.wait_for_timeout(900)
            page.locator("input").first.type("erwin", delay=70)
            page.locator("input[type=password]").type("demo-password", delay=45)
            page.wait_for_timeout(300)
            page.locator("button.primary").click()
            # Today is the default tab: no navigation, it is just there
            page.wait_for_selector(".today-view")
            page.wait_for_timeout(3000)
            ctx.storage_state(path=str(state_path))
            boxes = page.locator(".today-view input[type=checkbox]:not(:checked)")
            if boxes.count():
                boxes.first.click()  # writes through to the real file
                page.wait_for_timeout(2400)

        run_scene("today", scene_today)

        # ---- 2: capture — one key, one line ------------------------------
        def scene_capture(ctx, page) -> None:
            open_app(page)
            page.wait_for_timeout(700)
            page.keyboard.press("c")
            page.wait_for_selector(".capture-box .capture-input")
            page.wait_for_timeout(800)
            page.keyboard.type(CAPTURE_TEXT, delay=42)
            page.wait_for_timeout(500)
            page.keyboard.press("Enter")
            page.wait_for_timeout(2600)

        run_scene("capture", scene_capture, storage=str(state_path))

        # ---- 3: ask, watch the tool call, follow the citation ------------
        def scene_ask(ctx, page) -> None:
            open_app(page, "Chat")
            page.wait_for_selector(".chat .composer textarea")
            page.wait_for_timeout(900)
            page.locator(".chat .composer textarea").type(GARDEN_Q, delay=34)
            page.wait_for_timeout(350)
            page.keyboard.press("Enter")
            page.wait_for_selector(".chat .vault-cite", timeout=45000)
            page.wait_for_timeout(2400)
            page.locator(".chat .vault-cite").first.click()
            page.wait_for_timeout(2600)

        run_scene("ask", scene_ask, storage=str(state_path))

        # ---- 4: channels — peers, then @cortex answers live --------------
        def scene_together(ctx, page) -> None:
            open_app(page, "Channels")
            page.locator(".channels .side-item", has_text="general").first.click()
            page.wait_for_selector(".channels .composer textarea:not([disabled])")
            page.wait_for_timeout(1300)
            page.locator(".channels .composer textarea").type(KYOTO_Q, delay=34)
            page.wait_for_timeout(350)
            page.keyboard.press("Enter")
            page.wait_for_selector(
                ".channels .msg-assistant:not(.msg-partial)", timeout=45000
            )
            page.wait_for_timeout(2600)

        run_scene("together", scene_together, storage=str(state_path))

        # ---- 5: automation — add a rule, preview it, apply it ------------
        def scene_tidy(ctx, page) -> None:
            open_app(page, "Automation")
            page.wait_for_timeout(1800)
            # take the ready-made "file recipes" rule
            page.locator("button", has_text="tag matches 'recipe'").first.click()
            page.wait_for_timeout(1600)
            # it arrives switched off — turn it on, then look before leaping
            toggle = page.locator(".auto-row .toggle").first
            if toggle.count():
                toggle.click()
                page.wait_for_timeout(1600)
            page.locator("button", has_text="Preview changes").first.click()
            page.wait_for_timeout(2800)
            page.locator("button", has_text="Apply").first.click()
            page.wait_for_timeout(3000)

        run_scene("tidy", scene_tidy, storage=str(state_path))

        # ---- 6: extend — take a ready-made skill -------------------------
        def scene_expand(ctx, page) -> None:
            open_app(page, "Extend")
            page.wait_for_selector(".lib-card")
            page.wait_for_timeout(2000)
            card = page.locator(".lib-card", has_text="meeting-notes").first
            card.scroll_into_view_if_needed()
            page.wait_for_timeout(900)
            card.locator("button", has_text="Add").click()
            page.wait_for_timeout(2600)

        run_scene("expand", scene_expand, storage=str(state_path))

        browser.close()
    print(f"scenes in {scenes_dir}, cards in {cards_dir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
