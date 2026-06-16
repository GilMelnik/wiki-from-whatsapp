# Thread Tagger

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

This writes static assets to `thread_tagger/static/`.

For development with hot reload:

```bash
# Terminal 1 — API
python -m thread_tagger --no-browser

# Terminal 2 — Vite dev server (proxies /api to port 8765)
cd web/thread-tagger && npm run dev
```

## Run

Requires `data/threads.json` (from the threading pipeline). Classification is optional.

```bash
python -m thread_tagger
```

Opens `http://127.0.0.1:8765` in your browser.

**Inspect threads only** (no `threads_classified.json` needed):

```bash
python -m thread_tagger --inspect
```

**Read a specific threads file:**

```bash
python -m thread_tagger --threads data/threads.json --inspect
```

If no classification file exists, the tool automatically starts in inspect mode.

Options:

- `--port 8765` — listen port
- `--host 127.0.0.1` — bind address
- `--no-browser` — do not open a browser tab
- `--no-kill-port` — fail if the port is already in use (default: stop the existing listener first)
- `--init-edited` — create `*_edited.json` from originals and exit (no server)
- `--inspect` — browse threads without classification / tagging
- `--threads PATH` — load a specific `threads.json` (implies inspect mode)

## What it does

- Review threads marked as not knowledge-bearing (`is_knowledge_bearing: false`)
- Change classification (knowledge-bearing flag, topic tags, entities, reason)
- Browse threads sorted by message count, participants, start time, or duration
- View distribution histograms (click a bar to filter)
- Navigate chronological neighbors (before/after regardless of tagging)
- Merge threads, split by message selection, move messages to another thread
- **Recent changes** panel (session) for quick return to edited/split/merged threads
- **Split result banner** with one-click navigation between split parts
- **Quote display** on messages that reply to earlier messages

## Persistence

Review copies live at:

- `data/threads_edited.json`
- `data/threads_classified_edited.json`

They are **created automatically** when missing:

- **Tagging tool** — on startup (`python -m thread_tagger`), copies from the pipeline originals
- **Classify stage** — at start creates missing edited files; after classify syncs `threads_classified_edited.json` from the new output
- **Manual** — `python -m thread_tagger --init-edited`

Create them manually any time from existing pipeline output:

```bash
python -m thread_tagger --init-edited
```

On the first **edit** in a session, copies of the current source files are also stored under `data/backups/`.

## Continue the wiki pipeline

After review, run the pipeline as usual. Stages automatically prefer edited files when they exist:

```bash
python -m wiki_build.pipeline
# or individual stages:
python -m wiki_build.extract
```

If edited files are absent, the pipeline uses the original `data/threads.json` and `data/threads_classified.json`.

After structural changes (merge/split/move), re-run classification on affected threads:

```bash
python -m wiki_build.classify
```
