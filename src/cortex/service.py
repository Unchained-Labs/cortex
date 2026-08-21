"""Run the brain as a background service.

A household brain that only exists while a terminal is open is a brain
nobody comes to rely on. This generates a systemd **user** unit — user, not
system, so installing it needs no root and it runs as the person who owns
the notes.

Generation and installation are separate on purpose: ``--print`` shows you
the unit, installing writes it and tells you the two commands to run. We do
not silently enable services on someone's machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_NAME = "cortex.service"

UNIT_TEMPLATE = """\
[Unit]
Description=cortex — a self-hosted brain ({name})
Documentation=https://unchained-labs.github.io/cortex/
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
WorkingDirectory={workdir}
Restart=on-failure
RestartSec=5
# The brain is the only thing this service should be able to write.
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=full
{environment}
[Install]
WantedBy=default.target
"""


def unit_text(brain: Path, host: str, port: int, name: str, env: dict | None = None) -> str:
    cortex_bin = shutil.which("cortex") or f"{sys.executable} -m cortex.cli"
    exec_start = (
        f"{cortex_bin} serve --brain {brain} --host {host} --port {port}"
    )
    lines = "".join(f'Environment="{k}={v}"\n' for k, v in sorted((env or {}).items()))
    return UNIT_TEMPLATE.format(
        name=name,
        exec_start=exec_start,
        workdir=brain,
        environment=lines,
    )


def unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def install(text: str) -> Path:
    target = unit_dir()
    target.mkdir(parents=True, exist_ok=True)
    path = target / UNIT_NAME
    path.write_text(text, encoding="utf-8")
    return path


def systemd_available() -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "--version"], capture_output=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0
