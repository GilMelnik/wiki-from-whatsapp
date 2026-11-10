"""Self-check: config sections, the ``section()`` accessor and ``WIKI_DATA_DIR``."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from utils import config, paths


@pytest.fixture(autouse=True)
def restore_modules(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("WIKI_DATA_DIR", raising=False)
    yield
    monkeypatch.undo()  # restores the session value pinned by conftest.py
    importlib.reload(config)
    importlib.reload(paths)


def test_section_returns_known_section() -> None:
    assert config.section("input")["cutoff_date"] == "2026-09-01"


def test_unknown_section_raises_keyerror_naming_known_sections() -> None:
    with pytest.raises(KeyError) as exc_info:
        config.section("nope")
    message = str(exc_info.value)
    assert "'nope'" in message
    assert "input" in message and "logging" in message


def test_wiki_data_dir_env_overrides_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WIKI_DATA_DIR", str(tmp_path))
    importlib.reload(config)
    importlib.reload(paths)
    assert config.DATA_DIR == tmp_path
    assert paths.CHAT_ANDROID_PATH == tmp_path / "chat_android.json"
    assert paths.UPDATES_DIR == tmp_path / "updates"


def test_relative_data_dir_resolves_under_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    importlib.reload(config)
    assert config.DATA_DIR == config.PROJECT_ROOT / "data"

    monkeypatch.setenv("WIKI_DATA_DIR", "data/scratch/my-task")
    importlib.reload(config)
    assert config.DATA_DIR == config.PROJECT_ROOT / "data/scratch/my-task"
