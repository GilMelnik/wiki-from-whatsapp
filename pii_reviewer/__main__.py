"""Run the PII claims review web tool."""

from __future__ import annotations

import argparse
import webbrowser

import uvicorn

from pii_reviewer.server import app, configure_store, get_store, mount_static
from thread_tagger.port import free_port
from wiki_build.claims_paths import init_claims_edited


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review scrubbed claims and accept or restore PII redactions"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--claims",
        type=str,
        default=None,
        metavar="PATH",
        help="read claims from this JSON file",
    )
    parser.add_argument(
        "--init-edited",
        action="store_true",
        help="create data/claims_edited.json from claims.json, then exit",
    )
    parser.add_argument(
        "--no-kill-port",
        action="store_true",
        help="do not terminate an existing process on the target port",
    )
    args = parser.parse_args()

    if args.init_edited:
        created = init_claims_edited()
        if created:
            print(f"Created {created}")
        else:
            print("Edited claims file already exists; nothing to create.")
        return

    configure_store(claims_path=args.claims)
    store = get_store()
    info = store.meta()
    review = info["review"]
    print(f"Loaded {info['claims_count']} claims from {info['claims_path']}")
    print(
        "PII review queue: "
        f"{review['pending']} pending, "
        f"{review['accepted']} accepted, "
        f"{review['restored']} restored"
    )

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
