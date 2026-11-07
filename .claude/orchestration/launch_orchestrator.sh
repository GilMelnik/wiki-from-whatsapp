#!/usr/bin/env bash
# Opens a terminal running Claude Squad (cs) whose instances run the orchestrator program.
# In the cs TUI press `n`, name the instance "orchestrator", and it starts in its own worktree.
set -euo pipefail
source "$(dirname "$0")/common.sh"
setsid gnome-terminal --title="Claude Squad" --working-directory="$MAIN_ROOT" -- \
  bash -lc "cs -p '$ORCH/orchestrator_start.sh'; exec bash" >/dev/null 2>&1 &
