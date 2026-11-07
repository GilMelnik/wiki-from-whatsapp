# Sourced by the launchers. MAIN_ROOT is the primary checkout even when run from a
# Claude Squad / task worktree, so task state always lives in one place.
MAIN_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
ORCH="$MAIN_ROOT/.claude/orchestration"
