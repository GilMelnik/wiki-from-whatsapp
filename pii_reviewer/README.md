# PII Claims Reviewer

Web tool for reviewing auto-scrubbed phone numbers and email addresses in extracted claims. Approve redactions or restore the original text before aggregation and wiki generation.

## Prerequisites

- Python 3.11+ with project dependencies installed
- Node.js 18+ (for building the frontend once)

```bash
pip install -r requirements.txt
```

## Build frontend

```bash
cd web/pii-reviewer
npm install
npm run build
```

This writes static assets to `pii_reviewer/static/`.

For development with hot reload:

```bash
# Terminal 1 — API
python -m pii_reviewer --no-browser

# Terminal 2 — Vite dev server (proxies /api to port 8766)
cd web/pii-reviewer && npm run dev
```

## Run

Requires `data/claims.json` (from the extract stage). Claims with auto-redacted phone/email appear in the review queue.

```bash
python -m pii_reviewer
```

Opens `http://127.0.0.1:8766` in your browser.

**Read a specific claims file:**

```bash
python -m pii_reviewer --claims data/claims.json
```

Options:

- `--port 8766` — listen port
- `--host 127.0.0.1` — bind address
- `--no-browser` — do not open a browser tab
- `--no-kill-port` — fail if the port is already in use (default: stop the existing listener first)
- `--init-edited` — create `data/claims_edited.json` from `claims.json` and exit (no server)
- `--claims PATH` — load a specific claims JSON file

## What it does

- Lists claims flagged by the scrub step (`_redactions` metadata)
- Shows scrubbed text alongside a reconstructed original with highlighted redacted values
- **Accept redaction** — keep `[הוסר]` in the claim for downstream stages
- **Restore data** — put the original phone/email back into `claim_text` so it continues through the pipeline
- Persists decisions to `data/claims_edited.json` (original `claims.json` is never overwritten)
- Aggregation (`wiki_build.aggregate`) prefers `claims_edited.json` when it exists

## Pipeline placement

```
extract → claims.json
    ↓
pii_reviewer (manual) → claims_edited.json
    ↓
aggregate → claims_aggregated.json → generate → …
```
