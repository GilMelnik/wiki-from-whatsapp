# Wiki Plan Reviewer

Web tool for editing the wiki page plan before generation: merge pages, change titles and categories, adjust search queries, and move individual claims between aggregated topics.

## Prerequisites

- Python 3.11+ with project dependencies installed
- Node.js 18+ (for building the frontend once)
- Pipeline stages through **aggregate** and **plan** (`data/claims_aggregated.json`, `data/wiki_plan.json`)

```bash
pip install -r requirements.txt
```

## Build frontend

```bash
cd web/plan-reviewer
npm install
npm run build
```

This writes static assets to `plan_reviewer/static/`.

For development with hot reload:

```bash
# Terminal 1 — API
python -m plan_reviewer --no-browser

# Terminal 2 — Vite dev server (proxies /api to port 8767)
cd web/plan-reviewer && npm run dev
```

## Run

```bash
python -m plan_reviewer
```

Opens `http://127.0.0.1:8767` in your browser.

Options:

- `--port 8767` — listen port
- `--host 127.0.0.1` — bind address
- `--no-browser` — do not open a browser tab
- `--no-kill-port` — fail if the port is already in use
- `--init-edited` — create edited copies from pipeline output and exit
- `--plan PATH` / `--aggregated PATH` — load specific JSON files

## What it does

- Lists wiki pages grouped by navigation category
- Edit page **title**, **category**, **search_focus**, and **rationale**
- **Merge** one page into another (combines `source_tags`, removes the source page, fixes links)
- Browse merged community **claims** for each page
- **Move** a claim from one aggregated topic bucket to another
- Persists to `data/wiki_plan_edited.json` and `data/claims_aggregated_edited.json` (originals are never overwritten; first save creates a backup under `data/backups/`)

Downstream stages prefer edited files when present:

- `wiki_build.generate` reads edited plan + aggregated data
- `wiki_build.site` reads edited plan for navigation

Re-running **aggregate** or **plan** overwrites the original pipeline outputs but not your edited copies. Re-aggregate will not include manual claim moves unless you edit raw claims or re-apply moves.

## Pipeline placement

```
aggregate → claims_aggregated.json
plan      → wiki_plan.json
    ↓
plan_reviewer (manual) → wiki_plan_edited.json + claims_aggregated_edited.json
    ↓
generate → drafts/*.md → …
```
