#!/usr/bin/env bash
# Thin compatibility wrapper; implementation lives in scripts/update.py.
# Python loads the updater fully before any `git reset` moves the worktree.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 scripts/update.py "$@"
