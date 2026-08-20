#!/usr/bin/env bash
# cortex bootstrap: install the package and run the setup wizard.
#   curl -fsSL https://raw.githubusercontent.com/Unchained-Labs/cortex/main/install.sh | bash
set -euo pipefail

need() { command -v "$1" >/dev/null 2>&1; }

PY=""
for candidate in python3.12 python3.11 python3; do
  if need "$candidate"; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      PY="$candidate"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "error: python 3.11+ is required" >&2
  exit 1
fi

if need pipx; then
  pipx install --force cortxai
  echo "installed with pipx"
elif need uv; then
  uv tool install --force cortxai
  echo "installed with uv tool"
else
  VENVDIR="${CORTEX_VENV:-$HOME/.local/share/cortex-venv}"
  "$PY" -m venv "$VENVDIR"
  "$VENVDIR/bin/pip" install --quiet --upgrade pip cortxai
  mkdir -p "$HOME/.local/bin"
  ln -sf "$VENVDIR/bin/cortex" "$HOME/.local/bin/cortex"
  echo "installed into $VENVDIR (symlinked to ~/.local/bin/cortex)"
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "note: add ~/.local/bin to your PATH" ;;
  esac
fi

echo
exec cortex setup
