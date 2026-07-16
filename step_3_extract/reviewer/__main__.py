"""Run the PII claims review web tool."""

from __future__ import annotations

import argparse
import webbrowser

import uvicorn

from step_3_extract.reviewer.server import app, configure_store, get_store, mount_static
from utils.logging_setup import setup_step_logging
from utils.paths import STEP_3, init_claims_edited
from utils.port import free_port


def main() -> None:
    logger = setup_step_logging(STEP_3)
    parser = argparse.ArgumentParser(description="PII claims review web tool")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--claims",
        type=str,
        default=None,
        metavar="PATH",
        help="read claims JSON from this file",
    )
    parser.add_argument(
        "--init-edited",
        action="store_true",
        help="create data/step_3_extract/claims_edited.json from original, then exit",
    )
    parser.add_argument(
        "--no-kill-port",
        action="store_true",
        help="do not terminate an existing process on the target port",
    )
    args = parser.parse_args()

    if args.init_edited:
        path = init_claims_edited()
        if path:
            logger.info(f"Created {path}")
        else:
            logger.info("Edited claims file already exists; nothing to create.")
        return

    configure_store(claims_path=args.claims)
    store = get_store()
    info = store.meta()
    logger.info(f"Loaded {info['claims_count']} claims from {info['claims_path']}")

    if not args.no_kill_port:
        killed = free_port(args.port, host=args.host)
        if killed:
            logger.info(f"Freed port {args.port} (stopped PIDs: {', '.join(map(str, killed))})")

    mount_static()
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
