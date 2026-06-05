from __future__ import annotations

from pathlib import Path

from threads_split.models import ThreadConfig
from threads_split.pipeline import run_pipeline


def run(
    input_path: Path | str,
    output_path: Path | str,
    config: ThreadConfig | None = None,
) -> dict:
    """Split a messages JSON file into conversation threads."""
    return run_pipeline(input_path, output_path, config=config)


if __name__ == "__main__":
    result = run(
        input_path=Path("data/messages_combined.json"),
        output_path=Path("data/threads.json"),
    )
    print(
        f"Wrote {result['metadata']['thread_count']} threads "
        f"from {result['metadata']['message_count']} messages"
    )
