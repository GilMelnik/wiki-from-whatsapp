"""Pin ``WIKI_DATA_DIR`` to a throw-away folder so tests can never touch real data.

This module is imported by pytest before any test module, hence before ``utils.config``
reads the variable. Fixtures that change ``WIKI_DATA_DIR`` and reload ``utils.config`` /
``utils.paths`` must restore this session value (``monkeypatch.undo()`` does), never unset it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

SESSION_DATA_DIR = Path(tempfile.mkdtemp(prefix="wiki-tests-data-")).resolve()
os.environ["WIKI_DATA_DIR"] = str(SESSION_DATA_DIR)


def assert_data_dir_pinned() -> None:
    """Fail loudly when the env var or the loaded config/paths left the session folder."""
    from utils import config, paths

    assert os.environ.get("WIKI_DATA_DIR") == str(SESSION_DATA_DIR), (
        f"WIKI_DATA_DIR is {os.environ.get('WIKI_DATA_DIR')!r}, expected {str(SESSION_DATA_DIR)!r}"
    )
    for name, value in (("config.DATA_DIR", config.DATA_DIR), ("paths.DATA_DIR", paths.DATA_DIR)):
        assert value == SESSION_DATA_DIR, (
            f"{name} is {value}, expected the session folder {SESSION_DATA_DIR}"
        )


@pytest.fixture(autouse=True)
def data_dir_stays_pinned() -> Iterator[None]:
    """Check before and after every test, so a leak is blamed on the test that caused it."""
    assert_data_dir_pinned()
    yield
    assert_data_dir_pinned()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    try:
        assert_data_dir_pinned()
    finally:
        shutil.rmtree(SESSION_DATA_DIR, ignore_errors=True)
