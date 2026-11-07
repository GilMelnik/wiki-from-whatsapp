# Worker protocol

You own ONE task, described in `.claude/orchestration/tasks/<id>/plan.md` (absolute path in your prompt).
The repo's global CLAUDE.md rules apply (absolute imports, pathlib, config.json constants, type hints,
logging, files <= ~500 lines, no README edits, no dead code).

1. Read plan.md. Explore only what the plan touches. If the plan is ambiguous or a design choice is
   the user's, ask the user directly (AskUserQuestion) — don't guess. Keep questions few and batched.
2. Present your concrete implementation steps (plan mode) and wait for approval.
3. Implement in your worktree only. Stay in the plan's file scope; if you must touch files outside
   it, stop and report to the orchestrator first (another worker may own them).
4. Verify: `.venv/bin/python -m pytest -q` (all must pass) and `.venv/bin/ruff check` on changed files,
   plus any task-specific check from plan.md. For web UIs, build/run and confirm in the browser.
5. Commit on your branch `task/<id>` (small commits, imperative messages). Never push, never merge,
   never touch `main`.
6. Report: write `.claude/orchestration/tasks/<id>/report.md` (in the MAIN checkout path given in your
   prompt, not your worktree) with: summary, files changed, tests run + result, open questions, follow-ups discovered.
   Then `echo "done" > .../status` (or `blocked: <why>`), and notify the orchestrator with SendMessage
   (use ListAgents to find the session named `orchestrator`) — one short line pointing to report.md.
7. Show the user the result and say you're done. The user decides whether to close the session.
