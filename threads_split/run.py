from __future__ import annotations

import argparse
from pathlib import Path

from threads_split.models import ThreadConfig
from threads_split.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Split WhatsApp messages into conversation threads.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/messages.json"),
        help="Path to parsed messages JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/threads.json"),
        help="Path to write threads JSON",
    )
    parser.add_argument("--tau-minutes", type=float, help="Time decay constant in minutes")
    parser.add_argument("--attach-threshold", type=float, help="Minimum attach score to join a thread")
    parser.add_argument("--margin", type=float, help="Required margin between best and second-best thread")
    parser.add_argument("--embedding-model", type=str, help="Sentence-transformers model name")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = ThreadConfig()
    if args.tau_minutes is not None:
        config.tau_minutes = args.tau_minutes
    if args.attach_threshold is not None:
        config.attach_threshold = args.attach_threshold
    if args.margin is not None:
        config.margin = args.margin
    if args.embedding_model is not None:
        config.embedding_model = args.embedding_model

    result = run_pipeline(args.input, args.output, config=config)
    print(
        f"Wrote {result['metadata']['thread_count']} threads "
        f"from {result['metadata']['message_count']} messages to {args.output}"
    )


if __name__ == "__main__":
    main()
