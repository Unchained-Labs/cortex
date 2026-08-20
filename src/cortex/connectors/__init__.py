"""Ingestion connectors: pull a source, distill it to markdown, done.

The contract (borrowed from Cerebras' knowledge-base write-up): information
is generated wherever it is convenient,
so every source is extracted where it lives and normalized into one shared
interface — markdown files under ``sources/<connector>/``. The indexer treats
those files like any other note.

Two kinds of connectors run:

* built-ins, enabled by a block under ``connectors:`` in cortex.yaml
* drop-ins, ``connectors/*.py`` in the brain exposing
  ``sync(out_dir: Path, settings: dict) -> None``

Rules: distill, don't dump — a formatted title/date/participants/summary
retrieves far better than raw payloads; write stable filenames and delete
items that disappeared; a failing connector is reported and isolated, never
fatal. Files starting with ``_`` are shared libraries, not connectors.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cortex.config import BrainConfig

SyncFn = Callable[[Path, dict], None]


def builtin_connectors() -> dict[str, SyncFn]:
    from cortex.connectors.calendar_ics import sync as ics_sync

    return {"calendar_ics": ics_sync}


def run_connectors(config: BrainConfig) -> dict[str, str]:
    """Run every configured connector. Returns name -> "ok" | error text."""
    results: dict[str, str] = {}
    builtins = builtin_connectors()

    for name, settings in config.connectors.items():
        fn = builtins.get(name)
        if fn is None:
            results[name] = f"unknown built-in connector (known: {', '.join(sorted(builtins))})"
            continue
        results[name] = _run_one(fn, config.sources_dir / name, settings or {})

    directory = config.connectors_dir
    if directory.is_dir():
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            name = path.stem
            try:
                spec = importlib.util.spec_from_file_location(f"cortex_connector_{name}", path)
                if spec is None or spec.loader is None:
                    raise ImportError("could not build an import spec")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                fn = getattr(module, "sync", None)
                if not callable(fn):
                    raise AttributeError("connector defines no sync(out_dir, settings)")
            except Exception as exc:  # noqa: BLE001
                results[name] = str(exc)
                continue
            settings = dict(config.connectors.get(name) or {})
            results[name] = _run_one(fn, config.sources_dir / name, settings)

    return results


def _run_one(fn: SyncFn, out_dir: Path, settings: dict) -> str:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        fn(out_dir, settings)
    except Exception as exc:  # noqa: BLE001 - isolation is the contract
        return str(exc)
    return "ok"
