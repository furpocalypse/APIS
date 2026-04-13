#!/usr/bin/env bash
# Invoke a dev tool (ruff, ty, ...) through the right runner for the current OS.
#
# NixOS rejects generic dynamically-linked Linux binaries (which is what uv's
# .venv/bin/ruff and uv-tool-installed ty are) unless ``programs.nix-ld`` is
# enabled system-wide. Rather than require every NixOS contributor to flip
# that on, this wrapper detects NixOS via /etc/os-release and routes through
# ``nix-shell -p <tool>`` so the nixpkgs-built (correctly patched) copy runs
# instead. Cached after the first fetch.
#
# Everywhere else we use ``uv run`` so the project's pinned dev-deps win.
#
# Usage: run-dev-tool.sh <tool> [args...]
#   e.g. scripts/run-dev-tool.sh ruff check --fix path/to/file.py
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <tool> [args...]" >&2
    exit 2
fi

tool="$1"
shift

if grep -q '^ID=nixos' /etc/os-release 2>/dev/null; then
    # Build a properly-quoted command string for nix-shell --run.
    quoted=$(printf '%q ' "$tool" "$@")
    exec nix-shell -p "$tool" --run "$quoted"
else
    exec uv run "$tool" "$@"
fi
