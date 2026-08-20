# The promo film

Builds `docs/assets/cortex-promo.mp4` (the site hero), `cortex-demo.gif`
(the README loop) and `cortex-poster.png`, entirely from the real product:

```sh
bash docs/promo/build.sh
```

What runs: `seed.py` creates a demo brain (two users, a shared vault with
Obsidian-style notes, a seeded #general channel); `mock_model.py` is an
OpenAI-compatible stand-in that streams scripted answers at a human pace and
always routes through a real `search_brain` tool call; `shots.py` drives the
shipped dashboard in headless Chrome and records each scene; `build.sh`
assembles scenes with brand title cards (`cards.html`) via ffmpeg.

The dashboard, agent loop, retrieval, SSE streaming, WebSocket fan-out, and
vault write-through on film are the shipped code — **only the model is
scripted**. That boundary is the point: the film shows the product working,
not a mock of the product.

Needs: the repo venv with `playwright` installed (`playwright install
chromium`), Google Chrome, ffmpeg, and free ports 8199/8646. A failing scene
leaves `fail-<scene>.png` plus its console log in the workdir it prints.
