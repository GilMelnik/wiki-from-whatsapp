# Step 3 — PII claims reviewer

Web tool for reviewing auto-scrubbed phone numbers and email addresses in extracted claims.

## Build frontend

```bash
cd web/pii-reviewer
npm install
npm run build
```

Output: `step_3_extract/reviewer/static/`.

## Run

Requires `data/claims.json` from step 3 extract.

```bash
python -m step_3_extract.reviewer
```

If port 8766 is already in use, the previous listener is stopped automatically.
Pass `--no-kill-port` to disable that, or `--port` for a different port.

Create `data/claims_edited.json` from pipeline output:

```bash
python -m step_3_extract.reviewer --init-edited
```

Downstream steps prefer `claims_edited.json` when it exists.

## Dev (hot reload)

Terminal 1:

```bash
python -m step_3_extract.reviewer --no-browser
```

Terminal 2:

```bash
cd web/pii-reviewer && npm run dev
```
