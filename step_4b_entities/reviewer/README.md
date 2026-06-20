# Step 4b — Entity resolution reviewer

Web tool for reviewing suggested entity merges before aggregation: browse
canonical-entity suggestions, see sample claims per member (color-coded with the
entity name highlighted), accept/reject suggestions, pick the canonical name,
move a single name or specific claims to another/new entity, and merge a whole
suggestion into another. Contact details (email/phone/website) are preserved per
entity.

## Build frontend

```bash
cd web/entity-reviewer
npm install
npm run build
```

Output: `step_4b_entities/reviewer/static/`.

## Run

Requires `data/entities.json` from step 4b (`python -m step_4b_entities.run`).

```bash
python -m step_4b_entities.reviewer
```

If port 8770 is already in use, the previous listener is stopped automatically.
Pass `--no-kill-port` to disable that, or `--port` for a different port.

Create `data/entities_edited.json` from pipeline output:

```bash
python -m step_4b_entities.reviewer --init-edited
```

Step 5 aggregate prefers `entities_edited.json` when it exists and maps each
claim's raw entities to the canonical entity.

## Dev (hot reload)

Terminal 1:

```bash
python -m step_4b_entities.reviewer --no-browser
```

Terminal 2:

```bash
cd web/entity-reviewer && npm run dev
```
