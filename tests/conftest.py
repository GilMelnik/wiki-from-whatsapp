"""Pin ``WIKI_DATA_DIR`` to a throw-away folder so tests can never touch real data, and make
every real classifier, embedder or LLM client fail, so no test can spend money.

This module is imported by pytest before any test module, hence before ``utils.config``
reads the variable. Fixtures that change ``WIKI_DATA_DIR`` and reload ``utils.config`` /
``utils.paths`` must restore this session value (``monkeypatch.undo()`` does), never unset it.

The network guard replaces the SDK clients the code builds (``anthropic.Anthropic`` for the
chat models, the token counter and the Claude classifier; ``google.genai.Client`` for the
embeddings; ``TypeSafeClient`` for Jev) with a stand-in that raises ``RealNetworkCall``. Tests
use fakes (``_client`` set by hand, fake classifiers, embed functions), which never build one.
A building attempt also fails the test at teardown, even when the code under test caught the
error.
"""

from __future__ import annotations

import importlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

NETWORK_CLIENTS = (
    ("anthropic", "Anthropic"),
    ("anthropic", "AsyncAnthropic"),
    ("google.genai", "Client"),
    ("utils.llm_client.providers.typesafe", "TypeSafeClient"),
)
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


class RealNetworkCall(RuntimeError):
    """A test tried to build a real classifier, embedder or LLM client."""


_network_attempts: list[str] = []


def _blocked(name: str) -> type:
    class Blocked:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _network_attempts.append(name)
            raise RealNetworkCall(
                f"a test tried to build a real {name} (a paid network call); use a fake"
            )

    return Blocked


@pytest.fixture(autouse=True, scope="session")
def no_real_network_clients() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        for module, attribute in NETWORK_CLIENTS:
            patch.setattr(importlib.import_module(module), attribute,
                          _blocked(f"{module}.{attribute}"))
        yield


@pytest.fixture
def real_network_attempts() -> list[str]:
    """The blocked attempts of the running test (the guard's own test empties it)."""
    return _network_attempts


@pytest.fixture(autouse=True)
def no_network_attempt() -> Iterator[None]:
    """Fail the test that tried, even if the code under test swallowed the error."""
    _network_attempts.clear()
    yield
    attempts, _network_attempts[:] = list(_network_attempts), []
    assert not attempts, f"real network clients were built in this test: {attempts}"


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
