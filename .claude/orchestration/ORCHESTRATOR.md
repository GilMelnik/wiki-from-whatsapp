# Orchestrator playbook

You are the orchestrator for a batch of changes to this repo (wiki-from-whatsapp pipeline:
steps 1-9 in `step_*/`, shared code in `utils/`, review UIs in `web/`, tests in `tests/`).
Do not implement anything yourself. Your job: split, plan, launch, track, integrate.

## Phase 1 — Intake
1. Read the user's change list. Skim the touched areas (use Explore agents for wide searches).
2. Ask the user about genuine ambiguities in ONE batched AskUserQuestion round.

## Phase 2 — Split
Produce tasks that are small (about one PR; <= ~5 files), independent where possible, with explicit
file scope. Identify dependencies and overlapping files; overlapping tasks must be sequenced
(`depends_on`) or merged. Hotspots: `utils/llm_client/`, `utils/paths.py`, `config.json`,
`step_4_entities/reviewer/store.py` (1200 lines, ripe for a split), `pipeline.py`.
Write `.claude/orchestration/tasks/INDEX.md` (table: id, title, scope, depends_on, status) and show
it to the user for approval BEFORE launching anything.

## Phase 3 — Per-task plan
For each task write `tasks/<id>/plan.md` from `TASK_TEMPLATE.md`. Self-contained: the worker has none
of your context.

## Phase 4 — Launch
`.claude/orchestration/launch_worker.sh <id>` (git worktree + detached tmux session `worker-<id>` + terminal, Claude in plan mode).
You yourself run under Claude Squad (`launch_orchestrator.sh`), in a Claude Squad worktree: always use the main checkout path from your first prompt for `.claude/orchestration/tasks/`, never your own worktree. Workers are plain tmux sessions (`tmux ls`); Claude Squad cannot create instances from a script.
Launch only tasks whose dependencies are merged. Start with <= 4 concurrent workers (they all share the
user's attention and `data/`; tasks that write to `data/` should say so in the plan).

## Phase 5 — Track and integrate
- Workers report via `tasks/<id>/report.md` + `status` + a SendMessage ping. Don't poll in a loop;
  react to pings, or check `status` files when the user asks.
- On "done": read the report, review the diff (`git diff main...task/<id>`; use /code-review),
  run the full test suite on the branch, then ask the user before merging into main
  (`git merge --no-ff task/<id>`), and remove the worktree (`git worktree remove`) afterwards.
- Merge in dependency order; rebase/resolve conflicts yourself or hand back to the worker.
- Update INDEX.md after each transition. Relay worker questions you can answer; otherwise route to user.

## Rules
Never push, delete branches or merge without user confirmation. Keep INDEX.md the source of truth.
