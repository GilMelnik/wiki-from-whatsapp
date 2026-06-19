# Step 6 — Plan reviewer

Web tool for editing the wiki page plan before generation.

## Build frontend

```bash
cd web/plan-reviewer
npm install
npm run build
```

Output: `step_6_plan/reviewer/static/`.

## Run (no CLI — use uvicorn)

Requires `data/claims_aggregated.json` and `data/wiki_plan.json` from steps 5–6.

```bash
uvicorn step_6_plan.reviewer.server:app --host 127.0.0.1 --port 8767
```

Create edited copies:

```python
from utils.paths import init_aggregated_edited, init_plan_edited
init_plan_edited()
init_aggregated_edited()
```

Steps 7–8 prefer edited plan/aggregated files when present.
