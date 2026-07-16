"""Run the wiki plan review web tool."""

from __future__ import annotations

import argparse
import webbrowser

import uvicorn

from step_6_plan.reviewer.server import app, configure_store, get_store, mount_static
from utils.logging_setup import setup_step_logging
from utils.paths import STEP_6, init_aggregated_edited, init_plan_edited
from utils.port import free_port


def main() -> None:
    logger = setup_step_logging(STEP_6)
    parser = argparse.ArgumentParser(description="Wiki plan review web tool")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--plan",
        type=str,
        default=None,
        metavar="PATH",
        help="read wiki plan JSON from this file",
    )
    parser.add_argument(
        "--aggregated",
        type=str,
        default=None,
        metavar="PATH",
        help="read aggregated claims from this file",
    )
    parser.add_argument(
        "--init-edited",
        action="store_true",
        help="create data/step_6_plan/wiki_plan_edited.json (and aggregated) from original, then exit",
    )
    parser.add_argument(
        "--no-kill-port",
        action="store_true",
        help="do not terminate an existing process on the target port",
    )
    args = parser.parse_args()

    if args.init_edited:
        created = {"plan": init_plan_edited(), "aggregated": init_aggregated_edited()}
        made = {name: path for name, path in created.items() if path}
        if made:
            for name, path in made.items():
                logger.info(f"Created {path} ({name})")
        else:
            logger.info("Edited files already exist; nothing to create.")
        return

    configure_store(plan_path=args.plan, aggregated_path=args.aggregated)
    store = get_store()
    info = store.meta()
    logger.info(f"Loaded {info['page_count']} pages from {info['plan_path']}")

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
