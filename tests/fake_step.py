"""A stand-in pipeline step for the job runner tests: ``python -m tests.fake_step [--rebuild]``.

Driven by environment variables (the runner passes its environment on):

- ``FAKE_STEP_LINES``  number of log lines to write (default 3), ``FAKE_STEP_SLEEP`` seconds
  between them (default 0)
- ``FAKE_STEP_HIGHLIGHT``  one extra line to log (e.g. a reconcile's superseded-edits line)
- ``FAKE_STEP_HOLD``  seconds to wait before finishing (a long run to cancel)
- ``FAKE_STEP_IGNORE_SIGINT``  "1": ignore SIGINT (a step that does not stop when asked)
- ``FAKE_STEP_TARGET``  file to rewrite at the end, ``FAKE_STEP_EXIT`` exit code (default 0)
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from utils.json_io import write_json_file
from utils.logging_setup import setup_step_logging


def main() -> int:
    if os.environ.get("FAKE_STEP_IGNORE_SIGINT") == "1":
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    logger = setup_step_logging("fake_step")
    logger.info(f"fake step started (rebuild={'--rebuild' in sys.argv[1:]})")
    for number in range(int(os.environ.get("FAKE_STEP_LINES", "3"))):
        logger.info(f"line {number}")
        time.sleep(float(os.environ.get("FAKE_STEP_SLEEP", "0")))
    if highlight := os.environ.get("FAKE_STEP_HIGHLIGHT"):
        logger.warning(highlight)
    time.sleep(float(os.environ.get("FAKE_STEP_HOLD", "0")))
    if target := os.environ.get("FAKE_STEP_TARGET"):
        write_json_file({"written_by": "fake_step"}, Path(target))
    exit_code = int(os.environ.get("FAKE_STEP_EXIT", "0"))
    logger.info(f"fake step done (exit code {exit_code})")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
