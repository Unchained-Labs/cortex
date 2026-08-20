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


def run_connectors(
    config: BrainConfig, settings_by_name: dict[str, dict] | None = None, only: str = ""
) -> dict[str, str]:
    """Run every configured connector. Returns name -> "ok" | error text.

    ``settings_by_name`` overrides cortex.yaml (the dashboard passes the
    merged, enabled-only set); ``only`` runs a single connector."""
    results: dict[str, str] = {}
    builtins = builtin_connectors()
    configured = config.connectors if settings_by_name is None else settings_by_name
    directory = config.connectors_dir
    dropins = (
        {p.stem for p in directory.glob("*.py") if not p.name.startswith("_")}
        if directory.is_dir()
        else set()
    )

    for name, settings in configured.items():
        if only and name != only:
            continue
        if name in dropins:
            continue  # handled below, with its module loaded
        if name not in builtins:
            # a name in cortex.yaml matching nothing is a typo, not a no-op
            results[name] = (
                f"unknown connector (built-ins: {', '.join(sorted(builtins))}; "
                "drop-ins live in connectors/*.py)"
            )
            continue
        results[name] = _run_one(builtins[name], config.sources_dir / name, settings or {})

    if directory.is_dir():
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            name = path.stem
            if only and name != only:
                continue
            if settings_by_name is not None and name not in settings_by_name:
                continue  # disabled in the dashboard
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
            settings = dict(configured.get(name) or {})
            results[name] = _run_one(fn, config.sources_dir / name, settings)

    return results


def _run_one(fn: SyncFn, out_dir: Path, settings: dict) -> str:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        fn(out_dir, settings)
    except Exception as exc:  # noqa: BLE001 - isolation is the contract
        return str(exc)
    return "ok"
