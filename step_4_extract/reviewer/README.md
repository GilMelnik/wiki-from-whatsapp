# Step 4 — PII claims reviewer

Web tool for reviewing auto-scrubbed phone numbers and email addresses in extracted claims.

## Build frontend

```bash
cd web/pii-reviewer
npm install
npm run build
```

Output: `step_4_extract/reviewer/static/`.

## Run (no CLI — use uvicorn)

Requires `data/claims.json` from step 4 extract.

```bash
uvicorn step_4_extract.reviewer.server:app --host 127.0.0.1 --port 8766
```

Create `data/claims_edited.json` from pipeline output:

```python
from utils.paths import init_claims_edited
init_claims_edited()
```

Step 5 aggregate prefers `claims_edited.json` when it exists.
