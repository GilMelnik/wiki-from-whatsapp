# Step 5 — Aggregate cluster reviewer

Web tool for reviewing DBSCAN merge groups before plan generation: browse cluster statistics, change the representative claim, move source claims between clusters, and split clusters.

## Build frontend

```bash
cd web/aggregate-reviewer
npm install
npm run build
```

Output: `step_5_aggregate/reviewer/static/`.

## Run

Step 5 first maps each claim's raw entity strings to canonical entities using
`data/entities_edited.json` (or `data/entities.json`) from step 4b, so review
entity merges with `python -m step_4b_entities.reviewer` before this step.

Requires `data/claims_aggregated.json` from step 5 aggregate.

```bash
python -m step_5_aggregate.reviewer
```

If port 8768 is already in use, the previous listener is stopped automatically. Pass `--no-kill-port` to disable that, or `--port` for a different port.

Create `data/claims_aggregated_edited.json` from pipeline output:

```python
from utils.paths import init_aggregated_edited
init_aggregated_edited()
```

Or: `python -m step_5_aggregate.reviewer --init-edited`

Step 6 plan prefers `claims_aggregated_edited.json` when it exists.

## Dev (hot reload)

Terminal 1:

```bash
python -m step_5_aggregate.reviewer --no-browser
```

Terminal 2:

```bash
cd web/aggregate-reviewer && npm run dev
```
