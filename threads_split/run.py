from __future__ import annotations

from pathlib import Path
from typing import Sequence

from threads_split.models import ThreadConfig
from threads_split.pipeline import run_pipeline


def run(
    input_paths: Path | str | Sequence[Path | str],
    output_path: Path | str,
    config: ThreadConfig | None = None,
) -> dict:
    """Split one or more messages JSON files into conversation threads.

    Multiple input files are processed in order as one continuous message stream.
    Open threads and assigner state carry over between files.
    """
    return run_pipeline(input_paths, output_path, config=config)


if __name__ == "__main__":
    result = run(
        input_paths=[
            Path("data/messages_old.json"),
            Path("data/messages.json"),
        ],
        output_path=Path("data/threads.json"),
    )
    print(
        f"Wrote {result['metadata']['thread_count']} threads "
        f"from {result['metadata']['message_count']} messages"
    )
