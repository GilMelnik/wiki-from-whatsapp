"""Self-check: a step's log line lands in that step's data folder."""

from __future__ import annotations

import logging
from pathlib import Path

from utils import logging_setup


def test_setup_step_logging_writes_to_step_folder(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(logging_setup, "DATA_DIR", tmp_path)
    monkeypatch.setattr(logging_setup, "_current_step", None)
    monkeypatch.setattr(logging_setup, "_file_handler", None)

    logger = logging_setup.setup_step_logging("step_x")
    handler = logging_setup._file_handler
    try:
        logger.info("hello-check")
        handler.flush()
        log_file = tmp_path / "step_x" / "step_x.log"
        assert log_file.is_file()
        assert "hello-check" in log_file.read_text(encoding="utf-8")
    finally:
        # Detach the tmp handler so it doesn't outlive tmp_path.
        logging.getLogger().removeHandler(handler)
        handler.close()
