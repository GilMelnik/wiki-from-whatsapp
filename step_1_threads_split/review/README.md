# Step 2 — Thread review

Web-based tool for reviewing WhatsApp thread classification, editing tags, and restructuring threads (merge / split / move messages).

## Prerequisites

- Python 3.11+ with project dependencies installed
- Node.js 18+ (for building the frontend once)

```bash
pip install -r requirements.txt
```

## Build frontend

```bash
cd web/thread-tagger
npm install
npm run build
```

This writes static assets to `step_2_thread_review/static/`.

For development with hot reload:

```bash
# Terminal 1 — API
python -m review --no-browser

# Terminal 2 — Vite dev server (proxies /api to port 8765)
cd web/thread-tagger && npm run dev
```

## Run

Requires `data/threads.json` (from step 1). Classification is optional.

```bash
python -m review
```

Opens `http://127.0.0.1:8765` in your browser.

**Inspect threads only** (no `threads_classified.json` needed):

```bash
python -m review --inspect
```

Options: `--port`, `--host`, `--no-browser`, `--no-kill-port`, `--init-edited`, `--inspect`, `--threads PATH`.

## Persistence

Review copies live at `data/threads_edited.json` and `data/threads_classified_edited.json`. The pipeline (import `pipeline.run`) prefers edited files when they exist.

After structural changes (merge/split/move), re-run step 3 classify on affected threads via `step_3_classify.reclassify.run`.
