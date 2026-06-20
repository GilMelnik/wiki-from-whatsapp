"""Run the entity resolution review web tool."""

from __future__ import annotations

import argparse
import webbrowser

import uvicorn

from step_4b_entities.reviewer.server import (
    app,
    configure_store,
    get_store,
    mount_static,
)
from utils.paths import init_entities_edited
from utils.port import free_port


def main() -> None:
    parser = argparse.ArgumentParser(description="Entity resolution review web tool")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--entities",
        type=str,
        default=None,
        metavar="PATH",
        help="read entities JSON from this file",
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
        help="create data/entities_edited.json from original, then exit",
    )
    parser.add_argument(
        "--no-kill-port",
        action="store_true",
        help="do not terminate an existing process on the target port",
    )
    args = parser.parse_args()

    if args.init_edited:
        path = init_entities_edited()
        if path:
            print(f"Created {path}")
        else:
            print("Edited entities file already exists; nothing to create.")
        return

    configure_store(entities_path=args.entities, claims_path=args.claims)
    store = get_store()
    info = store.meta()
    print(f"Loaded {info['entity_count']} entities from {info['entities_path']}")

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
