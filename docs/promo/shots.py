"""Drive the real dashboard and record the promo scenes.

Every scene is the shipped SPA against the running server — the streaming,
tool calls, WebSocket fan-out and vault writes on film are the product
working. Usage: shots.py <workdir> (expects the server on :8646).

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
CARDS = ["intro", "chat", "vault", "channels", "extend", "outro"]

GARDEN_Q = "what's on the garden list for this weekend?"
KYOTO_Q = "@cortex when is the Kyoto trip again — anything left to do?"

PLUGIN_CODE = """from cortex.plugins import ToolPlugin


def register(registry):
    def bin_night(week: str = "this week") -> str:
        return f"{week}: green bin Tuesday, recycling Friday"

    registry.register(
        ToolPlugin(
            name="bin_night",
            description="Which bin goes out, and when.",
            func=bin_night,
        )
    )
"""


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
                page.wait_for_timeout(600)

        # ---- scene 1: sign in, ask, watch the tool loop, click the cite --
        def scene_chat(ctx, page) -> None:
            page.goto(BASE)
            page.wait_for_selector("input")
            page.wait_for_timeout(900)
            page.locator("input").first.type("erwin", delay=70)
            page.locator("input[type=password]").type("demo-password", delay=45)
            page.wait_for_timeout(300)
            page.locator("button.primary").click()
            page.wait_for_selector(".chat .composer textarea")
            page.wait_for_timeout(1200)
            page.locator(".chat .composer textarea").type(GARDEN_Q, delay=34)
            page.wait_for_timeout(350)
            page.keyboard.press("Enter")
            page.wait_for_selector(".chat .vault-cite", timeout=45000)
            page.wait_for_timeout(2200)
            ctx.storage_state(path=str(state_path))
            page.locator(".chat .vault-cite").first.click()
            page.wait_for_timeout(2600)

        run_scene("chat", scene_chat)

        # ---- scene 2: the vault — preview, live task toggle, edit, save --
        def scene_vault(ctx, page) -> None:
            open_app(page, "Vault")
            page.wait_for_selector(".vault .tree-row.tree-file")
            page.wait_for_timeout(900)
            page.locator(".vault .tree-row.tree-file", has_text="garden.md").first.click()
            page.wait_for_timeout(1200)
            page.locator(".vault").get_by_role("button", name="Preview").click()
            page.wait_for_timeout(2200)
            boxes = page.locator(".vault .pane input[type=checkbox]:not(:checked)")
            if boxes.count():
                boxes.first.click()  # write-through task toggle
                page.wait_for_timeout(1700)
            page.locator(".vault").get_by_role("button", name="Edit").click()
            page.wait_for_selector(".vault .cm-content")
            page.locator(".vault .cm-content").click()
            page.keyboard.press("Control+End")
            page.keyboard.type("\n- [ ] Pick the last tomatoes for [[shakshuka]]", delay=32)
            page.wait_for_timeout(500)
            page.keyboard.press("Control+s")
            page.wait_for_timeout(900)
            page.locator(".vault").get_by_role("button", name="Preview").click()
            page.wait_for_timeout(2300)

        run_scene("vault", scene_vault, storage=str(state_path))

        # ---- scene 3: channels — peers, then @cortex answers live --------
        def scene_channels(ctx, page) -> None:
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

        run_scene("channels", scene_channels, storage=str(state_path))

        # ---- scene 4: the Extend panel — write a plugin, watch it land ---
        def scene_extend(ctx, page) -> None:
            open_app(page, "Extend")
            page.wait_for_selector(".ext-section")
            page.wait_for_timeout(2600)
            # write a new plugin in the browser
            page.locator("section", has_text="Plugins").locator(
                "button", has_text="+ New"
            ).first.click()
            page.wait_for_selector(".drawer .cm-content")
            page.wait_for_timeout(900)
            page.locator(".drawer input").first.type("bins", delay=60)
            page.locator(".drawer .cm-content").click()
            page.keyboard.press("Control+a")
            page.keyboard.insert_text(PLUGIN_CODE)
            page.wait_for_timeout(700)
            page.locator(".drawer").get_by_role("button", name="Save").click()
            # the row appears with the tool it registered — no restart
            page.wait_for_selector(".ext-row:has-text('bins')", timeout=20000)
            page.wait_for_timeout(2600)

        run_scene("extend", scene_extend, storage=str(state_path))

        browser.close()
    print(f"scenes in {scenes_dir}, cards in {cards_dir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
