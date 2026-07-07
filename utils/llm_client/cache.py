"""On-disk cache for LLM responses.

Every call is keyed by a hash of (provider, model, system, user, temperature).
A cache entry is either a plain text completion (``{"response": ...}``) or a
grounded result (``GroundedResult.to_dict()``, which sets a ``"grounded"``
marker plus citations/queries); reads tell the two apart from that marker in the
file, so both share the same key space. Unparseable responses are copied
verbatim to a sibling ``*_bad`` dir (same key) for inspection.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from utils.llm_client.models import GroundedResult


class LLMCache:
    def __init__(
        self, cache_dir: Path | str, provider: str, model: str, temperature: float
    ) -> None:
        self.cache_dir = Path(cache_dir)
        # Unparseable responses are copied here (same key) for later inspection.
        self.bad_cache_dir = self.cache_dir.parent / f"{self.cache_dir.name}_bad"
        self.provider = provider
        self.model = model
        self.temperature = temperature

    def _cache_key(self, system: str, user: str) -> str:
        payload = json.dumps(
            {
                "provider": self.provider,
                "model": self.model,
                "system": system,
                "user": user,
                "temperature": self.temperature,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> str | GroundedResult | None:
        """Return the cached value, rebuilding a ``GroundedResult`` when the file
        holds grounded data and the plain text otherwise."""

        path = self._cache_path(key)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if data.get("grounded") is True:
            return GroundedResult.from_dict(data)
        return data["response"]

    def _write_cache(self, key: str, response: str | GroundedResult) -> None:
        """Persist a plain completion or a grounded result, letting the value
        decide its own on-disk shape."""

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = (
            response.to_dict()
            if isinstance(response, GroundedResult)
            else {"response": response}
        )
        with self._cache_path(key).open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _delete_cache(self, key: str) -> None:
        self._cache_path(key).unlink(missing_ok=True)

    def _write_bad_cache(self, key: str, response: str) -> None:
        """Persist an unparseable response to the sibling ``*_bad`` cache dir."""

        self.bad_cache_dir.mkdir(parents=True, exist_ok=True)
        with (self.bad_cache_dir / f"{key}.json").open("w", encoding="utf-8") as f:
            json.dump({"response": response}, f, ensure_ascii=False, indent=2)
