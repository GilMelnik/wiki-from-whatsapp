"""On-disk cache for LLM and classifier responses.

A key is the sha256 of a canonical-JSON payload: the owner's fixed ``identity``
(provider, model and whatever else changes every answer, e.g. the reasoning
effort) merged with the fields of one request (prompts, JSON mode and schema for
chat calls; state and questions for classifier calls). Anything that can change
the answer must be in that payload, otherwise a config change silently returns a
stale entry.

An entry is ``{"response": ...}`` (a text completion, or a classifier's list of
answers) or a grounded result (``GroundedResult.to_dict()``, which sets a
``"grounded"`` marker plus citations/queries); reads tell the two apart from that
marker, so both share the same key space. Unparseable responses are copied
verbatim to a sibling ``*_bad`` dir (same key) for inspection.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from utils.json_io import write_json_file
from utils.llm_client.models import GroundedResult

CacheValue = str | list[dict[str, Any]] | GroundedResult


class LLMCache:
    def __init__(self, cache_dir: Path | str, identity: Mapping[str, Any]) -> None:
        self.cache_dir = Path(cache_dir)
        # Unparseable responses are copied here (same key) for later inspection.
        self.bad_cache_dir = self.cache_dir.parent / f"{self.cache_dir.name}_bad"
        self.identity = dict(identity)

    def _cache_key(self, **request: Any) -> str:
        """Hash ``identity`` merged with one request's fields (order-insensitive)."""

        payload = json.dumps(
            {**self.identity, **request}, ensure_ascii=False, sort_keys=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> CacheValue | None:
        """Return the cached value, rebuilding a ``GroundedResult`` when the file
        holds grounded data and the stored ``response`` otherwise."""

        path = self._cache_path(key)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if data.get("grounded") is True:
            return GroundedResult.from_dict(data)
        return data["response"]

    def _write_cache(self, key: str, response: CacheValue) -> None:
        """Persist a response or a grounded result, letting the value decide its
        own on-disk shape. The write is atomic, so concurrent writers of
        different keys (the classifier's thread pool) never leave a torn file."""

        payload = (
            response.to_dict()
            if isinstance(response, GroundedResult)
            else {"response": response}
        )
        write_json_file(payload, self._cache_path(key))

    def _delete_cache(self, key: str) -> None:
        self._cache_path(key).unlink(missing_ok=True)

    def _write_bad_cache(self, key: str, response: str) -> None:
        """Persist an unparseable response to the sibling ``*_bad`` cache dir."""

        write_json_file({"response": response}, self.bad_cache_dir / f"{key}.json")
