"""Usage and tool telemetry, appended as JSONL.

Two rules, both inherited from tools that got burned without them:

* Telemetry must never make an agent or tool call fail — every write is
  wrapped, and a full disk costs you metrics, not answers.
* Absent numbers stay absent. A model that reports no token counts produces
  a row without them; ``preflight calibrate`` consumes these rows and rejects
  records that fake counts, so the field names ``prompt_tokens`` and
  ``completion_tokens`` are load-bearing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class Obs:
    def __init__(self, path: Path):
        self.path = path

    def _write(self, row: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001, S110 - telemetry never breaks the call
            pass

    def usage(self, model: str, usage: dict[str, int], latency_ms: int, thread: str) -> None:
        row: dict = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "kind": "usage",
            "model": model,
            "latency_ms": latency_ms,
            "thread": thread,
        }
        row.update(usage)  # prompt_tokens / completion_tokens when reported
        self._write(row)

    def tool_event(self, tool: str, ok: bool, latency_ms: int, source: str = "chat") -> None:
        self._write(
            {
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "kind": "tool",
                "tool": tool,
                "status": "ok" if ok else "error",
                "latency_ms": latency_ms,
                "source": source,
            }
        )
