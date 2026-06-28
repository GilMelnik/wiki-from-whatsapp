"""Run the thread tagging web tool."""

from __future__ import annotations

import argparse
import webbrowser

import uvicorn

from utils.port import free_port
from utils.paths import has_classification_data, init_edited_files
from step_1_threads_split.review.server import app, configure_store, get_store, mount_static


def main() -> None:
    parser = argparse.ArgumentParser(description="Thread tagging web tool")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="browse threads.json only (no classification / tagging)",
    )
    parser.add_argument(
        "--threads",
        type=str,
        default=None,
        metavar="PATH",
        help="read threads from this JSON file (inspect mode; no tagging)",
    )
    parser.add_argument(
        "--init-edited",
        action="store_true",
        help="create data/threads_edited.json (and classified if available), then exit",
    )
    parser.add_argument(
        "--no-kill-port",
        action="store_true",
        help="do not terminate an existing process on the target port",
    )
    args = parser.parse_args()

    if args.init_edited:
        created = init_edited_files(require_classified=False)
        if created:
            for name, path in created.items():
                print(f"Created {path} ({name})")
        else:
            print("Edited files already exist; nothing to create.")
        if not has_classification_data():
            print("Note: no threads_classified.json — tagging will be unavailable.")
        return

    inspect_only = args.inspect or args.threads is not None
    if not inspect_only and not has_classification_data():
        print("No classification file found — starting in inspect mode.")
        inspect_only = True

    configure_store(inspect_only=inspect_only, threads_path=args.threads)
    if inspect_only:
        if args.threads:
            print(f"Inspect mode: {args.threads}")
        else:
            print("Inspect mode: threads only (no tagging)")

    store = get_store()
    info = store.meta()
    print(f"Loaded {info['thread_count']} threads from {info['threads_path']}")

    if not args.no_kill_port:
        killed = free_port(args.port, host=args.host)
        if killed:
            print(f"Freed port {args.port} (stopped PIDs: {', '.join(map(str, killed))})")

    mount_static()
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
