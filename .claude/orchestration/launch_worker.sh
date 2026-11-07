#!/usr/bin/env bash
# Usage: launch_worker.sh <task-id>
# Needs tasks/<task-id>/plan.md (written by the orchestrator). Creates a git worktree on branch
# task/<task-id>, links gitignored runtime state, starts Claude (plan mode) in a detached tmux
# session "worker-<id>", and opens a terminal attached to it.
set -euo pipefail
source "$(dirname "$0")/common.sh"
ID="${1:?task id}"
TASK="$ORCH/tasks/$ID"
WT="$MAIN_ROOT/../wiki-from-whatsapp-worktrees/$ID"
SESSION="worker-$ID"
[ -f "$TASK/plan.md" ] || { echo "missing $TASK/plan.md" >&2; exit 1; }
tmux has-session -t "$SESSION" 2>/dev/null && { echo "$SESSION already running" >&2; exit 1; }

if [ ! -d "$WT" ]; then
  git -C "$MAIN_ROOT" worktree add -b "task/$ID" "$WT" HEAD
  for p in data .env .venv; do   # gitignored, shared with the main checkout
    [ -e "$MAIN_ROOT/$p" ] && ln -s "$MAIN_ROOT/$p" "$WT/$p"
  done
fi
echo "launched $(date -Is)" > "$TASK/status"

cat > "$TASK/prompt.txt" <<PROMPT
You are a WORKER session for task "$ID". Read $ORCH/WORKER.md and then $TASK/plan.md in full.
Follow them exactly. Your worktree is $WT (branch task/$ID); work only there.
Main checkout (for report.md/status): $MAIN_ROOT
PROMPT

tmux new-session -d -s "$SESSION" -c "$WT" \
  "claude --permission-mode plan -n 'worker:$ID' \"\$(cat '$TASK/prompt.txt')\"; exec bash"
setsid gnome-terminal --title="$SESSION" -- tmux attach -t "$SESSION" >/dev/null 2>&1 &
