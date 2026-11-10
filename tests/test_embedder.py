"""Offline tests for ``utils.embedder``: the API call is always replaced by a fake."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from google.genai import errors as genai_errors

from utils import embedder as embedder_module
from utils.embedder import Embedder, Kind, cosine_matrix, text_key

DIM = 16


def _fake_vector(text: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha1(text.encode("utf-8")).digest()[:4], "little")
    return np.random.default_rng(seed).normal(size=DIM).astype(np.float32) * 3.0


class FakeApi:
    """Stands in for ``Embedder._request`` and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, texts: Sequence[str], kind: Kind) -> list[list[float]]:
        self.calls.append((list(texts), kind))
        return [_fake_vector(t).tolist() for t in texts]

    @property
    def sent(self) -> list[str]:
        return [t for texts, _ in self.calls for t in texts]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(Embedder, "_request", lambda self, texts, kind: fake(texts, kind))
    monkeypatch.setattr(embedder_module.time, "sleep", lambda seconds: None)
    return fake


def make_embedder(tmp_path: Path, model: str = "gemini-embedding-001") -> Embedder:
    emb = Embedder(model=model, output_dimensionality=DIM, cache_dir=tmp_path / "cache")
    emb.batch_size = 3
    return emb


def test_vectors_are_unit_length_float32(api: FakeApi, tmp_path: Path) -> None:
    out = make_embedder(tmp_path).embed_texts(["שלום", "hello", "x"], "similarity")
    assert out.shape == (3, DIM)
    assert out.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)


def test_second_call_hits_cache_with_no_api_calls(api: FakeApi, tmp_path: Path) -> None:
    emb = make_embedder(tmp_path)
    first = emb.embed_texts(["a", "b", "c", "d"], "document")
    calls_after_first = len(api.calls)
    assert calls_after_first == 2  # batch_size 3 -> batches of 3 and 1
    second = emb.embed_texts(["a", "b", "c", "d"], "document")
    assert len(api.calls) == calls_after_first
    np.testing.assert_array_equal(first, second)


def test_cache_survives_a_new_embedder_instance(api: FakeApi, tmp_path: Path) -> None:
    first = make_embedder(tmp_path).embed_texts(["a", "b"], "query")
    n_calls = len(api.calls)
    again = make_embedder(tmp_path).embed_texts(["b", "a"], "query")
    assert len(api.calls) == n_calls
    np.testing.assert_array_equal(again, first[::-1])


def test_partial_hits_only_send_the_misses(api: FakeApi, tmp_path: Path) -> None:
    emb = make_embedder(tmp_path)
    emb.embed_texts(["a", "b"], "similarity")
    api.calls.clear()
    out = emb.embed_texts(["a", "new", "b", "new", "other"], "similarity")
    assert api.sent == ["new", "other"]  # duplicates sent once, hits not sent
    assert out.shape == (5, DIM)
    np.testing.assert_array_equal(out[1], out[3])


def test_kinds_have_separate_stores_and_task_types(api: FakeApi, tmp_path: Path) -> None:
    emb = make_embedder(tmp_path)
    emb.embed_texts(["a"], "query")
    emb.embed_texts(["a"], "document")
    assert [kind for _, kind in api.calls] == ["query", "document"]
    cache = tmp_path / "cache"
    for kind in ("query", "document"):
        assert (cache / f"gemini-embedding-001-{DIM}-{kind}.npy").exists()
        assert (cache / f"gemini-embedding-001-{DIM}-{kind}.npy".replace(".npy", ".keys.json")).exists()


def test_blank_texts_get_zero_vectors_without_api_call(
    api: FakeApi, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        out = make_embedder(tmp_path).embed_texts(["", "   ", "\n"], "similarity")
    assert api.calls == []
    assert not out.any()
    assert "zero vectors" in caplog.text


def test_keys_file_holds_sha1_of_exact_text(api: FakeApi, tmp_path: Path) -> None:
    emb = make_embedder(tmp_path)
    emb.embed_texts(["abc", "abc "], "similarity")
    store = emb._store("similarity")
    assert store.keys == [text_key("abc"), text_key("abc ")]
    assert text_key("abc") == hashlib.sha1(b"abc").hexdigest()


def test_unknown_kind_raises(api: FakeApi, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown embedding kind"):
        make_embedder(tmp_path).embed_texts(["a"], "nope")  # type: ignore[arg-type]


def test_inconsistent_cache_is_ignored_and_rebuilt(api: FakeApi, tmp_path: Path) -> None:
    emb = make_embedder(tmp_path)
    emb.embed_texts(["a"], "similarity")
    (tmp_path / "cache" / f"gemini-embedding-001-{DIM}-similarity.keys.json").write_text(
        '["x", "y", "z"]', encoding="utf-8"
    )
    api.calls.clear()
    make_embedder(tmp_path).embed_texts(["a"], "similarity")
    assert api.sent == ["a"]


def test_failed_batch_keeps_earlier_batches_in_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeApi()

    def flaky(self: Embedder, texts: Sequence[str], kind: Kind) -> list[list[float]]:
        if "boom" in texts:
            raise ValueError("bad request")
        return fake(texts, kind)

    monkeypatch.setattr(Embedder, "_request", flaky)
    emb = make_embedder(tmp_path)
    with pytest.raises(RuntimeError, match=r"model=gemini-embedding-001 kind=similarity batch=1"):
        emb.embed_texts(["a", "b", "c", "boom"], "similarity")
    fake.calls.clear()
    make_embedder(tmp_path).embed_texts(["a", "b", "c"], "similarity")
    assert fake.calls == []


@pytest.mark.parametrize(
    "error",
    [
        genai_errors.ClientError(429, {"error": {"message": "slow down"}}),
        genai_errors.ServerError(503, {"error": {"message": "unavailable"}}),
        httpx.ReadTimeout("timeout"),
    ],
)
def test_retryable_errors_are_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    attempts: list[int] = []

    def flaky(self: Embedder, texts: Sequence[str], kind: Kind) -> list[list[float]]:
        attempts.append(1)
        if len(attempts) < 3:
            raise error
        return [_fake_vector(t).tolist() for t in texts]

    monkeypatch.setattr(Embedder, "_request", flaky)
    monkeypatch.setattr(embedder_module.time, "sleep", lambda seconds: None)
    out = make_embedder(tmp_path).embed_texts(["a"], "similarity")
    assert len(attempts) == 3
    assert out.shape == (1, DIM)


def test_retries_give_up_after_max_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts: list[int] = []

    def always_429(self: Embedder, texts: Sequence[str], kind: Kind) -> list[list[float]]:
        attempts.append(1)
        raise genai_errors.ClientError(429, {"error": {"message": "slow down"}})

    monkeypatch.setattr(Embedder, "_request", always_429)
    monkeypatch.setattr(embedder_module.time, "sleep", lambda seconds: None)
    emb = make_embedder(tmp_path)
    with pytest.raises(RuntimeError, match="embedding request failed"):
        emb.embed_texts(["a"], "similarity")
    assert len(attempts) == emb.max_retries + 1


def test_non_retryable_client_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts: list[int] = []

    def bad_request(self: Embedder, texts: Sequence[str], kind: Kind) -> list[list[float]]:
        attempts.append(1)
        raise genai_errors.ClientError(400, {"error": {"message": "invalid"}})

    monkeypatch.setattr(Embedder, "_request", bad_request)
    with pytest.raises(RuntimeError, match="kind=similarity batch=0"):
        make_embedder(tmp_path).embed_texts(["a"], "similarity")
    assert len(attempts) == 1


class FakeModels:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def embed_content(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        n = len(kwargs["contents"])
        values = [type("E", (), {"values": [1.0] * DIM})() for _ in range(n)]
        return type("R", (), {"embeddings": values})()


@pytest.mark.parametrize(
    ("kind", "task_type"),
    [("similarity", "SEMANTIC_SIMILARITY"), ("query", "RETRIEVAL_QUERY"), ("document", "RETRIEVAL_DOCUMENT")],
)
def test_001_request_uses_task_type_and_plain_strings(tmp_path: Path, kind: Kind, task_type: str) -> None:
    emb = make_embedder(tmp_path)
    emb._client = type("C", (), {"models": FakeModels()})()
    emb._request(["a", "b"], kind)
    sent = emb._client.models.kwargs
    assert sent["model"] == "gemini-embedding-001"
    assert sent["contents"] == ["a", "b"]
    assert sent["config"].task_type == task_type
    assert sent["config"].output_dimensionality == DIM


def test_v2_request_wraps_each_text_in_its_own_content_with_prompt(tmp_path: Path) -> None:
    emb = make_embedder(tmp_path, model="gemini-embedding-2")
    emb._client = type("C", (), {"models": FakeModels()})()
    emb._request(["a", "b"], "query")
    sent = emb._client.models.kwargs
    assert [c.parts[0].text for c in sent["contents"]] == [
        "task: search result | query: a",
        "task: search result | query: b",
    ]
    assert sent["config"].task_type is None


def test_cosine_matrix_shape_and_values() -> None:
    a = np.array([[1.0, 0.0], [0.0, 2.0]], dtype=np.float32)
    b = np.array([[1.0, 0.0], [1.0, 1.0], [0.0, 0.0]], dtype=np.float32)
    sims = cosine_matrix(a, b)
    assert sims.shape == (2, 3)
    np.testing.assert_allclose(sims, [[1.0, 2**-0.5, 0.0], [0.0, 2**-0.5, 0.0]], atol=1e-6)
