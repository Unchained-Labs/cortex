# The promo film

Builds `docs/assets/cortex-promo.mp4` (the site hero), `cortex-demo.gif`
(the README loop) and `cortex-poster.png`, entirely from the real product:

```sh
bash docs/promo/build.sh
```

What runs: `seed.py` creates a demo brain (two users, a shared vault with
Obsidian-style notes, calendar events, an unfiled recipe for the rules scene,
a seeded #general channel); `mock_model.py` is an OpenAI-compatible stand-in
that streams scripted answers at a human pace and always routes through a
real `search_brain` tool call; `shots.py` drives the shipped dashboard in
headless Chrome and records six scenes — Today, capture, ask, channels,
rules, extend — and `build.sh` assembles them with brand title cards
(`cards.html`) via ffmpeg.

The rules scene is the one worth knowing about: it takes a ready-made rule,
switches it on, previews it, applies it, and the note it moves
(`roast-chicken.md` → `recipes/`) is really moved on disk, with the history
line underneath showing where it went.

The dashboard, digest, capture, agent loop, retrieval, SSE streaming,
WebSocket fan-out, rule engine and vault write-through on film are the
shipped code — **only the model is scripted**. That boundary is the point: the film shows the product working,
not a mock of the product.

Needs: the repo venv with `playwright` installed (`playwright install
chromium`), Google Chrome, ffmpeg, and free ports 8199/8646. A failing scene
leaves `fail-<scene>.png` plus its console log in the workdir it prints.
