"""Console + per-step rotating-file logging (per ``.cursor/rules/python-rules.mdc``).

``setup_step_logging(step_folder)`` attaches a single console handler once and
points a shared rotating file handler at ``<data_dir>/<step_folder>/<step_folder>.log``.
Because the handlers live on the root logger, any logger (including the shared
``utils.llm_client`` logger, when its client is given the returned step logger)
lands in the active step's file. Sequential pipeline steps each get their own
log file: calling this again for a new step swaps the file handler.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from utils.config import DATA_DIR, LOGGING_CONFIG

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_FORMATTER = logging.Formatter(LOG_FORMAT)
_LEVEL = getattr(logging, str(LOGGING_CONFIG.get("level", "INFO")).upper(), logging.INFO)

# ponytail: module-level handler state keeps configuration idempotent across the
# many step entrypoints that call setup in one process (e.g. the full pipeline).
_console_handler: logging.StreamHandler | None = None
_file_handler: RotatingFileHandler | None = None
_current_step: str | None = None


def _ensure_console() -> None:
    global _console_handler
    root = logging.getLogger()
    root.setLevel(_LEVEL)
    if _console_handler is None:
        _console_handler = logging.StreamHandler()
        _console_handler.setFormatter(_FORMATTER)
        root.addHandler(_console_handler)


def setup_step_logging(step_folder: str) -> logging.Logger:
    """Route logging to ``<data_dir>/<step_folder>/<step_folder>.log`` plus console.

    Returns a logger named after the step; use it directly and pass it to
    ``LLMClient`` so provider/call logs land in the same step file.
    """

    global _file_handler, _current_step
    _ensure_console()
    root = logging.getLogger()
    if _current_step != step_folder:
        if _file_handler is not None:
            root.removeHandler(_file_handler)
            _file_handler.close()
        log_dir = DATA_DIR / step_folder
        log_dir.mkdir(parents=True, exist_ok=True)
        _file_handler = RotatingFileHandler(
            log_dir / f"{step_folder}.log",
            maxBytes=int(LOGGING_CONFIG["max_bytes"]),
            backupCount=int(LOGGING_CONFIG["backup_count"]),
            encoding="utf-8",
        )
        _file_handler.setFormatter(_FORMATTER)
        root.addHandler(_file_handler)
        _current_step = step_folder
    return logging.getLogger(step_folder)
