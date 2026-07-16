# Step 6 — Plan reviewer

Web tool for editing the wiki page plan before generation.

## Build frontend

```bash
cd web/plan-reviewer
npm install
npm run build
```

Output: `step_6_plan/reviewer/static/`.

## Run

Requires `data/claims_aggregated.json` and `data/wiki_plan.json` from steps 5–6.

```bash
python -m step_6_plan.reviewer
```

If port 8767 is already in use, the previous listener is stopped automatically.
Pass `--no-kill-port` to disable that, or `--port` for a different port.

Create edited copies from pipeline output:

```bash
python -m step_6_plan.reviewer --init-edited
```

Steps 7–8 prefer edited plan/aggregated files when present.
