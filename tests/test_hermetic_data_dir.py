"""The suite must run against a throw-away data folder, never the real one."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from utils import config, paths


def test_data_dir_is_the_session_temp_folder() -> None:
    session_dir = Path(os.environ["WIKI_DATA_DIR"])
    assert session_dir.parent == Path(tempfile.gettempdir()).resolve()
    assert session_dir.name.startswith("wiki-tests-data-")
    assert config.DATA_DIR == session_dir
    assert paths.DATA_DIR == session_dir
    assert paths.CHAT_ANDROID_PATH.is_relative_to(session_dir)
