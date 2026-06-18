"""Run the wiki plan review web tool."""

from __future__ import annotations

import argparse
import webbrowser

import uvicorn

from plan_reviewer.server import app, configure_store, get_store, mount_static
from thread_tagger.port import free_port
from wiki_build.plan_paths import init_aggregated_edited, init_plan_edited


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review and edit the wiki page plan before generation"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--plan",
        type=str,
        default=None,
        metavar="PATH",
        help="read plan from this JSON file",
    )
    parser.add_argument(
        "--aggregated",
        type=str,
        default=None,
        metavar="PATH",
        help="read aggregated claims from this JSON file",
    )
    parser.add_argument(
        "--init-edited",
        action="store_true",
        help="create edited plan/aggregated copies from pipeline output, then exit",
    )
    parser.add_argument(
        "--no-kill-port",
        action="store_true",
        help="do not terminate an existing process on the target port",
    )
    args = parser.parse_args()

    if args.init_edited:
        created: list[str] = []
        plan = init_plan_edited()
        if plan:
            created.append(str(plan))
        agg = init_aggregated_edited()
        if agg:
            created.append(str(agg))
        if created:
            print("Created:\n  " + "\n  ".join(created))
        else:
            print("Edited files already exist; nothing to create.")
        return

    configure_store(plan_path=args.plan, aggregated_path=args.aggregated)
    store = get_store()
    info = store.meta()
    print(f"Loaded {info['page_count']} pages from {info['plan_path']}")
    print(f"Aggregated topics: {info['topic_count']} ({info['aggregated_path']})")

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
