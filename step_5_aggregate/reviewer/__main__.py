"""Run the aggregate cluster review web tool."""

from __future__ import annotations

import argparse
import webbrowser

import uvicorn

from step_5_aggregate.reviewer.server import app, configure_store, get_store, mount_static
from utils.paths import init_aggregated_edited
from utils.port import free_port


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate cluster review web tool")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--aggregated",
        type=str,
        default=None,
        metavar="PATH",
        help="read aggregated JSON from this file",
    )
    parser.add_argument(
        "--claims",
        type=str,
        default=None,
        metavar="PATH",
        help="read source claims from this file",
    )
    parser.add_argument(
        "--init-edited",
        action="store_true",
        help="create data/claims_aggregated_edited.json from original, then exit",
    )
    parser.add_argument(
        "--no-kill-port",
        action="store_true",
        help="do not terminate an existing process on the target port",
    )
    args = parser.parse_args()

    if args.init_edited:
        path = init_aggregated_edited()
        if path:
            print(f"Created {path}")
        else:
            print("Edited aggregated file already exists; nothing to create.")
        return

    configure_store(
        aggregated_path=args.aggregated,
        claims_path=args.claims,
    )
    store = get_store()
    info = store.meta()
    print(f"Loaded {info['group_count']} groups from {info['aggregated_path']}")

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
