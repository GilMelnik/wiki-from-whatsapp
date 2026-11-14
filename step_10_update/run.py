"""Step 10: absorb a newer chat export as an update of the wiki (contract C13).

    python -m step_10_update.run [EXPORT] [--plan] [--pay STEP ...] [--through STEP]
                                 [--no-stops] [--drop-missing]

``EXPORT`` is a new FULL export of the chat (default: the newest ``*_chat_android.json`` in
``data/updates/``). The command finds what is new, records it, and runs the pipeline's steps
in order through their own ``run()`` until a person must review something or a step would
spend money; it then prints one line saying what to do. Running the same command again goes
on from there (``step_10_update.driver`` explains the whole flow).

- ``--plan``: say what the update would do and estimate the next step; change nothing.
- ``--pay STEP ...``: allow these paid steps (``step_3_extract``, ``step_6_assign``,
  ``step_7_write_pages``, ``step_8_review``, ``step_8_proofread``) to make their model calls.
  Without it a paid step stops the update with its estimate (unless it would make no paid
  call). Proofreading comes after the review's stop: it reads the text once the owner's
  decisions on the suggestions are applied.
- ``--through STEP``: run up to STEP without stopping for reviews, then stop.
- ``--no-stops``: never stop for a review (paid steps still need ``--pay``).
- ``--drop-missing``: messages of the current input that the export lacks are removed
  (default: kept, since an export can be partial).

Exit code 0 when the update completed, stopped or had nothing to do; 1 when a step failed
(the next run retries it); 2 when the update was refused (nothing changed).
"""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from review_tool.artifacts import GRAPH
from review_tool.steps import SERVED_STEPS
from step_10_update.driver import FAILED, Options, Outcome, StepFailed, UpdateDriver
from step_10_update.estimate import Estimator
from step_10_update.server import ReviewServerCheck
from utils.logging_setup import setup_step_logging
from utils.paths import STEP_10


def main(argv: Sequence[str] | None = None) -> int:
    keys = [step.key for step in SERVED_STEPS.steps]
    parser = argparse.ArgumentParser(
        prog="python -m step_10_update.run",
        description="Take a newer chat export through the pipeline as an update.",
    )
    parser.add_argument("export", nargs="?", type=Path, help="the new full export")
    parser.add_argument("--plan", action="store_true", help="say what would happen; change nothing")
    parser.add_argument("--pay", nargs="+", default=[], choices=keys, metavar="STEP",
                        help="paid steps allowed to make model calls")
    parser.add_argument("--through", choices=keys, metavar="STEP",
                        help="run up to this step without review stops, then stop")
    parser.add_argument("--no-stops", action="store_true", help="never stop for a review")
    parser.add_argument("--drop-missing", action="store_true",
                        help="remove input messages the export lacks (default: keep them)")
    args = parser.parse_args(argv)

    logger = setup_step_logging(STEP_10)
    random.seed(0)
    np.random.seed(0)
    driver = UpdateDriver(SERVED_STEPS.steps, GRAPH, estimator=Estimator(),
                          server_check=ReviewServerCheck(), logger=logger)
    options = Options(pay=frozenset(args.pay), through=args.through, no_stops=args.no_stops,
                      drop_missing=args.drop_missing)
    if args.plan:
        return driver.plan(args.export, options).exit_code
    try:
        return driver.run(args.export, options).exit_code
    except StepFailed as exc:
        logger.exception(str(exc))
        return Outcome(FAILED, str(exc)).exit_code


if __name__ == "__main__":
    sys.exit(main())
