"""Provider-agnostic LLM client with on-disk caching.

The client exposes methods used across the wiki pipeline:

- ``complete_json(system, user, task=...)`` -> parsed ``dict``/``list``
- ``complete_text(system, user, task=...)`` -> ``str``
- ``complete_grounded(system, user)`` -> ``GroundedResult`` (Gemini + Google Search)
- ``complete_batch(requests)`` -> ``dict[request_id, (text, truncated)]`` (anthropic / gemini)
- ``complete_batch_json(requests)`` -> ``dict[request_id, parsed]`` (re-submits only
  items *truncated at max_tokens* as another batch with a larger window; other
  unparseable items are logged and dropped rather than retried)

Unparseable responses are also copied verbatim to a sibling ``*_bad`` cache dir
for inspection, keyed the same way as the normal cache.

Configuration (most-specific wins):

1. Per-stage env vars (recommended for the hybrid setup)::

       WIKI_LLM_CLASSIFY_PROVIDER=gemini
       WIKI_LLM_CLASSIFY_MODEL=gemini-2.0-flash
       WIKI_LLM_EXTRACT_PROVIDER=anthropic
       WIKI_LLM_EXTRACT_MODEL=claude-sonnet-4-20250514
       WIKI_LLM_GENERATE_PROVIDER=anthropic
       WIKI_LLM_GENERATE_MODEL=claude-sonnet-4-20250514
       WIKI_LLM_PLAN_PROVIDER=gemini
       WIKI_LLM_PLAN_MODEL=gemini-3.1-flash-lite
       WIKI_LLM_RESEARCH_PROVIDER=gemini
       WIKI_LLM_RESEARCH_MODEL=gemini-2.5-flash

2. Global fallback (same model for every stage)::

       WIKI_LLM_PROVIDER=anthropic
       WIKI_LLM_MODEL=claude-3-5-sonnet-latest

3. Hybrid stage defaults via ``LLMClient.for_stage(stage, use_hybrid_defaults=True)``
   (used by ``pipeline``): cheap Flash for classify, Sonnet for
   extract/generate. Override any stage with the per-stage env vars above.

4. Offline mock when nothing is configured (``LLMClient()`` with no env vars).

Providers: ``anthropic``, ``openai``, ``gemini``, ``mock``. SDKs are imported
lazily so only the providers you use must be installed.

Every call is cached on disk keyed by a hash of (provider, model, system, user).
The cache lives in ``data/llm_cache/`` (gitignored).

Anthropic calls also use server-side prompt caching (``cache_control`` on the
system prefix and any ``CacheSegment(..., cache=True)`` in the user prompt). Set
``WIKI_LLM_LOG_CACHE=1`` to print per-call cache hit/write token counts.

Batch mode (``--batch`` on the pipeline or individual stages) submits uncached
prompts via the provider batch API at ~50% lower cost. Anthropic and Gemini are
supported; cached prompts are still served from disk without a batch job.

API keys and ``WIKI_LLM_*`` vars can live in a repo-root ``.env`` file; this module
loads it on import (``python-dotenv``). Variables already set in the shell win.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = Path("data/llm_cache")

load_dotenv(PROJECT_ROOT / ".env", override=False)


DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o",
    "gemini": "gemini-3.1-flash-lite",
    "mock": "mock",
}

# Hybrid defaults: cheap classify, strong extract + generate (override via env).
STAGE_DEFAULTS: dict[str, dict[str, str]] = {
    "classify": {"provider": "gemini", "model": "gemini-3.1-flash-lite"},
    "extract": {"provider": "anthropic", "model": "claude-sonnet-5"},
    "plan": {"provider": "gemini", "model": "gemini-3.5-flash"},
    "generate": {"provider": "anthropic", "model": "claude-sonnet-5"},
    "research": {"provider": "gemini", "model": "gemini-3.5-flash"},
}

VALID_STAGES = frozenset(STAGE_DEFAULTS)
BATCH_PROVIDERS = frozenset({"anthropic", "gemini"})
DEFAULT_BATCH_POLL_INTERVAL = 30.0

# When a JSON completion is truncated at max_tokens, complete_json doubles
# max_tokens and retries, up to this ceiling. A parse failure that is *not* a
# max_tokens truncation (e.g. the model returned prose) is not retried — a bigger
# window would not change the format — but is logged and saved for inspection.
MAX_TOKENS_CEILING = 16384
JSON_PARSE_RETRIES = 2

# Every unsuccessful call (API error, unparseable JSON after retries, empty batch
# response) is appended here as one JSON line for later inspection / manual retry.
DEFAULT_FAILURE_LOG = Path("data/llm_failures.jsonl")


@dataclass(frozen=True)
class GroundedCitation:
    title: str
    url: str


@dataclass(frozen=True)
class GroundedResult:
    text: str
    citations: tuple[GroundedCitation, ...] = ()
    search_queries: tuple[str, ...] = ()


@dataclass(frozen=True)
class CacheSegment:
    """One text segment of a prompt; ``cache`` places a cache_control breakpoint after it."""

    text: str
    cache: bool = False


PromptInput = str | Sequence["CacheSegment"]


@dataclass(frozen=True)
class BatchRequest:
    """One prompt submitted as part of a provider batch job.

    ``response_schema`` (a JSON Schema dict) turns on provider structured output
    so the response is guaranteed to be schema-valid JSON — used with models
    whose reasoning would otherwise leak into or truncate a free-form reply.
    """

    request_id: str
    system: str
    user: str
    task: str = ""
    response_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class StageLLMConfig:
    """Resolved provider/model for one pipeline stage."""

    stage: str
    provider: str
    model: str

    def label(self) -> str:
        return f"{self.stage}: {self.provider}/{self.model}"


def web_search_enabled(*, explicit: bool | None = None) -> bool:
    """Return whether Gemini Google Search grounding should run during generate."""

    if explicit is False:
        return False
    env = os.environ.get("WIKI_ENABLE_WEB_SEARCH", "").strip().lower()
    if env in {"0", "false", "no", "off"}:
        return False
    if env in {"1", "true", "yes", "on"}:
        return True
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def resolve_stage_config(
    stage: str,
    *,
    use_hybrid_defaults: bool = False,
) -> StageLLMConfig:
    """Resolve provider and model for a pipeline stage.

    Priority: per-stage env -> global env -> hybrid stage defaults -> mock.
    """

    if stage not in VALID_STAGES:
        raise ValueError(f"Unknown stage {stage!r}; expected one of {sorted(VALID_STAGES)}")

    stage_key = stage.upper()
    stage_provider = os.environ.get(f"WIKI_LLM_{stage_key}_PROVIDER")
    stage_model = os.environ.get(f"WIKI_LLM_{stage_key}_MODEL")
    global_provider = os.environ.get("WIKI_LLM_PROVIDER")
    global_model = os.environ.get("WIKI_LLM_MODEL")

    if stage_provider is not None:
        provider = stage_provider.lower()
        model = stage_model or DEFAULT_MODELS.get(provider, "mock")
    elif global_provider is not None:
        provider = global_provider.lower()
        model = global_model or DEFAULT_MODELS.get(provider, "mock")
    elif use_hybrid_defaults:
        defaults = STAGE_DEFAULTS[stage]
        provider = defaults["provider"]
        model = defaults["model"]
    else:
        provider = "mock"
        model = "mock"

    return StageLLMConfig(stage=stage, provider=provider, model=model)


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding ```json ... ``` fence if present."""

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def extract_json(text: str) -> Any:
    cleaned = _strip_code_fence(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the first balanced JSON object/array in the text.
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(1))


def _gemini_truncated(response: Any) -> bool:
    """True if a Gemini response stopped because it hit max_output_tokens."""

    for cand in getattr(response, "candidates", None) or []:
        reason = getattr(cand, "finish_reason", None)
        name = getattr(reason, "name", None) or (str(reason) if reason else "")
        if name.endswith("MAX_TOKENS"):
            return True
    return False


def _flatten(prompt: PromptInput) -> str:
    """Collapse a prompt into plain text for the disk cache key and non-anthropic providers."""

    if isinstance(prompt, str):
        return prompt
    return "".join(seg.text for seg in prompt)


def _to_blocks(prompt: PromptInput, *, cache_last: bool = False) -> list[dict[str, Any]]:
    """Build Anthropic text blocks, adding cache_control per-segment or on the final block."""

    if isinstance(prompt, str):
        segments = [CacheSegment(prompt)]
    else:
        segments = list(prompt)
    if not segments:
        segments = [CacheSegment("")]
    blocks: list[dict[str, Any]] = []
    for i, seg in enumerate(segments):
        block: dict[str, Any] = {"type": "text", "text": seg.text}
        if seg.cache or (cache_last and i == len(segments) - 1):
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
    return blocks


def _sanitize_batch_custom_id(request_id: str) -> str:
    """Anthropic batch custom_id: 1-64 chars, alphanumeric / hyphen / underscore."""

    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", request_id)
    return sanitized[:64] or "req"


class LLMClient:
    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        cache_dir: Path | str = DEFAULT_CACHE_DIR,
        temperature: float = 0.2,
        # Adaptive-thinking models (e.g. Claude Sonnet 5) count reasoning tokens
        # against max_tokens, so the answer needs headroom beyond its own size.
        max_tokens: int = 8192,
        use_cache: bool = True,
        batch_poll_interval: float | None = None,
        failure_log: Path | str | None = None,
    ):
        self.provider = (provider or os.environ.get("WIKI_LLM_PROVIDER") or "mock").lower()
        self.model = model or os.environ.get("WIKI_LLM_MODEL") or DEFAULT_MODELS.get(
            self.provider, "mock"
        )
        self.cache_dir = Path(cache_dir)
        # Unparseable responses are copied here (same key) for later inspection.
        self.bad_cache_dir = self.cache_dir.parent / f"{self.cache_dir.name}_bad"
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.use_cache = use_cache
        self.failure_log = Path(
            failure_log
            if failure_log is not None
            else os.environ.get("WIKI_LLM_FAILURE_LOG") or DEFAULT_FAILURE_LOG
        )
        env_poll = os.environ.get("WIKI_LLM_BATCH_POLL_INTERVAL")
        self.batch_poll_interval = (
            batch_poll_interval
            if batch_poll_interval is not None
            else float(env_poll) if env_poll else DEFAULT_BATCH_POLL_INTERVAL
        )
        self._client: Any = None

    def supports_batch(self) -> bool:
        return self.provider in BATCH_PROVIDERS

    @classmethod
    def for_stage(
        cls,
        stage: str,
        *,
        use_hybrid_defaults: bool = False,
        **kwargs: Any,
    ) -> LLMClient:
        """Build a client for one pipeline stage (classify / extract / generate)."""

        resolved = resolve_stage_config(stage, use_hybrid_defaults=use_hybrid_defaults)
        return cls(
            provider=resolved.provider,
            model=resolved.model,
            **kwargs,
        )

    # ------------------------------------------------------------------ cache
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

    def _read_cache(self, key: str) -> str | None:
        path = self._cache_path(key)
        if path.exists():
            with path.open(encoding="utf-8") as f:
                return json.load(f)["response"]
        return None

    def _write_cache(self, key: str, response: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self._cache_path(key).open("w", encoding="utf-8") as f:
            json.dump({"response": response}, f, ensure_ascii=False, indent=2)

    def _delete_cache(self, key: str) -> None:
        self._cache_path(key).unlink(missing_ok=True)

    def _write_bad_cache(self, key: str, response: str) -> None:
        """Persist an unparseable response to the sibling ``*_bad`` cache dir."""

        self.bad_cache_dir.mkdir(parents=True, exist_ok=True)
        with (self.bad_cache_dir / f"{key}.json").open("w", encoding="utf-8") as f:
            json.dump({"response": response}, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------- call logging
    def _log_call(self, ok: bool, task: str, detail: str) -> None:
        status = "ok  " if ok else "FAIL"
        print(f"  LLM {status} [{self.provider}/{self.model}] task={task or '-'} {detail}")

    def _record_failure(
        self,
        *,
        task: str,
        kind: str,
        error: str,
        system: PromptInput,
        user: PromptInput,
        response: str = "",
    ) -> None:
        """Append one unsuccessful call to the failure log (JSONL).

        The full prompts and any raw response are stored so a failed call can be
        examined and re-run differently later. Works for every provider because
        it's driven off the flattened prompt text, not provider objects.
        """

        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "provider": self.provider,
            "model": self.model,
            "task": task,
            "kind": kind,
            "error": error,
            "max_tokens": self.max_tokens,
            "system": _flatten(system),
            "user": _flatten(user),
            "response": response,
        }
        try:
            self.failure_log.parent.mkdir(parents=True, exist_ok=True)
            with self.failure_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"  LLM: could not write failure log {self.failure_log}: {exc}")

    def _grounded_cache_key(self, system: str, user: str) -> str:
        payload = json.dumps(
            {
                "provider": self.provider,
                "model": self.model,
                "system": system,
                "user": user,
                "temperature": self.temperature,
                "grounded": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _read_grounded_cache(self, key: str) -> GroundedResult | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if data.get("grounded") is not True:
            return None
        citations = tuple(
            GroundedCitation(title=c["title"], url=c["url"])
            for c in data.get("citations", [])
            if c.get("url")
        )
        return GroundedResult(
            text=data.get("response", ""),
            citations=citations,
            search_queries=tuple(data.get("search_queries", [])),
        )

    def _write_grounded_cache(self, key: str, result: GroundedResult) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with self._cache_path(key).open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "grounded": True,
                    "response": result.text,
                    "citations": [
                        {"title": c.title, "url": c.url} for c in result.citations
                    ],
                    "search_queries": list(result.search_queries),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    # --------------------------------------------------------------- complete
    def complete_text(self, system: PromptInput, user: PromptInput, task: str = "") -> str:
        if self.use_cache:
            key = self._cache_key(_flatten(system), _flatten(user))
            cached = self._read_cache(key)
            if cached is not None:
                self._log_call(True, task, "(cache)")
                return cached

        try:
            response, _ = self._dispatch(system, user, task)
        except Exception as exc:  # noqa: BLE001 - record any provider error, then re-raise
            self._record_failure(
                task=task, kind="api_error", error=repr(exc), system=system, user=user
            )
            self._log_call(False, task, f"api_error: {exc} -> logged")
            raise

        self._log_call(True, task, f"({len(response)} chars)")
        if self.use_cache:
            self._write_cache(key, response)
        return response

    def _raise_max_tokens(self) -> bool:
        """Double max_tokens toward the ceiling; return False if already there.

        ponytail: raising the ceiling is free (billing is on tokens actually
        generated), so the bump persists for the client's lifetime rather than
        being restored per-call — later batches likely need the extra room too.
        """

        if self.max_tokens >= MAX_TOKENS_CEILING:
            return False
        self.max_tokens = min(self.max_tokens * 2, MAX_TOKENS_CEILING)
        return True

    def complete_json(
        self,
        system: PromptInput,
        user: PromptInput,
        task: str = "",
        response_schema: dict[str, Any] | None = None,
    ) -> Any:
        """Parse the model's JSON output, growing the window only on truncation.

        A response truncated at max_tokens is invalid JSON; it is never cached and
        each *truncated* attempt doubles max_tokens before retrying. A parse
        failure that is not a truncation (e.g. the model returned prose) is not
        retried — a larger window would not fix the format. Every unparseable
        response is saved to the ``*_bad`` cache for inspection. ``response_schema``
        enables provider structured output for a hard JSON guarantee.
        """

        key = (
            self._cache_key(_flatten(system), _flatten(user))
            if self.use_cache
            else None
        )
        if key is not None:
            cached = self._read_cache(key)
            if cached is not None:
                try:
                    data = extract_json(cached)
                    self._log_call(True, task, "(cache)")
                    return data
                except ValueError as exc:
                    # Failed cache entry from a prior run: drop it and re-request;
                    # the fresh call's finish_reason decides any max_tokens bump.
                    self._delete_cache(key)
                    self._log_call(
                        False, task, f"stale cache unparseable ({exc}); re-requesting"
                    )

        last_exc: ValueError | None = None
        response = ""
        for _ in range(JSON_PARSE_RETRIES + 1):
            try:
                response, truncated = self._dispatch(
                    system, user, task, json_mode=True, response_schema=response_schema
                )
            except Exception as exc:  # noqa: BLE001 - record any provider error
                self._record_failure(
                    task=task, kind="api_error", error=repr(exc),
                    system=system, user=user,
                )
                self._log_call(False, task, f"api_error: {exc} -> logged")
                raise exc
            try:
                data = extract_json(response)
            except ValueError as exc:
                last_exc = exc
                if key is not None:
                    self._write_bad_cache(key, response)
                self._log_call(
                    False,
                    task,
                    f"parse_error (max_tokens={self.max_tokens}, "
                    f"truncated={truncated}): {exc}",
                )
                # Only a max_tokens truncation is worth retrying with more room.
                if truncated and self._raise_max_tokens():
                    continue
                break
            self._log_call(
                True, task, f"({len(response)} chars, max_tokens={self.max_tokens})"
            )
            if key is not None:
                self._write_cache(key, response)
            return data

        # Retries/ceiling exhausted: persist the last bad response for inspection.
        self._record_failure(
            task=task, kind="parse_error", error=str(last_exc),
            system=system, user=user, response=response,
        )
        self._log_call(False, task, "parse_error exhausted -> logged")
        raise last_exc  # type: ignore[misc]

    def complete_grounded(self, system: str, user: str) -> GroundedResult:
        """Run Gemini with Google Search grounding; returns text + citations."""

        if self.use_cache:
            key = self._grounded_cache_key(system, user)
            cached = self._read_grounded_cache(key)
            if cached is not None:
                return cached

        if self.provider == "mock":
            result = _mock_grounded(user)
        elif self.provider == "gemini":
            result = self._gemini_grounded(system, user)
        else:
            raise ValueError(
                f"Grounded search requires provider 'gemini' or 'mock'; got {self.provider!r}"
            )

        if self.use_cache:
            self._write_grounded_cache(self._grounded_cache_key(system, user), result)
        return result

    def complete_batch(
        self, requests: Sequence[BatchRequest]
    ) -> dict[str, tuple[str, bool]]:
        """Run many prompts via the provider batch API (50% cheaper, async).

        Returns ``{request_id: (text, truncated)}`` where ``truncated`` is True
        when the item hit max_output_tokens. Cached prompts are returned
        immediately (never truncated — only good responses are cached). Mock
        provider runs synchronously without an API call.
        """

        results: dict[str, tuple[str, bool]] = {}
        pending: list[BatchRequest] = []

        for req in requests:
            if self.use_cache:
                cached = self._read_cache(self._cache_key(req.system, req.user))
                if cached is not None:
                    results[req.request_id] = (cached, False)
                    self._log_call(True, req.task, f"(batch cache {req.request_id})")
                    continue
            pending.append(req)

        if not pending:
            return results

        if self.provider == "mock":
            for req in pending:
                response = _mock_response(req.system, req.user, req.task)
                results[req.request_id] = (response, False)
                self._log_call(True, req.task, f"(batch mock {req.request_id})")
                if self.use_cache:
                    self._write_cache(self._cache_key(req.system, req.user), response)
            return results

        if self.provider == "anthropic":
            batch_results = self._anthropic_batch(pending)
        elif self.provider == "gemini":
            batch_results = self._gemini_batch(pending)
        else:
            raise ValueError(
                f"Batch API not supported for provider {self.provider!r}; "
                f"supported: {sorted(BATCH_PROVIDERS)}"
            )

        for req in pending:
            response, truncated = batch_results.get(req.request_id, ("", False))
            results[req.request_id] = (response, truncated)
            if response:
                self._log_call(
                    True, req.task, f"(batch {req.request_id}, {len(response)} chars)"
                )
                # Only cache a parseable-looking response; the JSON layer decides
                # what to keep, but a truncated one must never persist as valid.
                if self.use_cache and not truncated:
                    self._write_cache(self._cache_key(req.system, req.user), response)
            else:
                self._log_call(False, req.task, f"batch_empty ({req.request_id})")
        return results

    def complete_batch_json(self, requests: Sequence[BatchRequest]) -> dict[str, Any]:
        """Batch call returning parsed JSON per request, self-healing bad ones.

        Runs the batch and parses each response. Items *truncated at max_tokens*
        are re-submitted as another batch with a doubled window, repeating until
        they parse or max_tokens hits the ceiling. Items that fail to parse for
        any other reason (e.g. the model returned prose) are logged and dropped —
        a bigger window would not change the format. Every unparseable response
        is copied to the ``*_bad`` cache. Returns ``{request_id: parsed}``;
        failed items are omitted from the result.
        """

        parsed: dict[str, Any] = {}
        pending: list[BatchRequest] = list(requests)
        while pending:
            raw = self.complete_batch(pending)
            retry: list[BatchRequest] = []  # truncated -> worth a larger window
            for req in pending:
                text, truncated = raw.get(req.request_id, ("", False))
                try:
                    if not text:
                        raise ValueError("empty batch response")
                    parsed[req.request_id] = extract_json(text)
                    continue
                except ValueError as exc:
                    error = str(exc)
                key = self._cache_key(_flatten(req.system), _flatten(req.user))
                self._write_bad_cache(key, text)
                # Never let an unparseable response linger in the good cache.
                self._delete_cache(key)
                if truncated and self.max_tokens < MAX_TOKENS_CEILING:
                    retry.append(req)
                    continue
                self._record_failure(
                    task=req.task, kind="parse_error",
                    error=(
                        f"unparseable batch response (truncated={truncated}): {error}"
                    ),
                    system=req.system, user=req.user, response=text,
                )
                self._log_call(
                    False, req.task, f"batch parse_error ({req.request_id}) -> logged"
                )
            if not retry:
                break

            self._raise_max_tokens()
            print(
                f"  LLM: {len(retry)}/{len(pending)} batch items truncated; "
                f"re-submitting those as a batch with max_tokens={self.max_tokens}"
            )
            pending = retry
        return parsed

    def _dispatch(
        self,
        system: PromptInput,
        user: PromptInput,
        task: str,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """Return ``(text, truncated)``; ``truncated`` marks a max_tokens cutoff."""

        if self.provider == "anthropic":
            return self._anthropic(system, user, response_schema=response_schema)
        # Other providers take plain text; cache segments only matter to anthropic.
        system_text, user_text = _flatten(system), _flatten(user)
        if self.provider == "mock":
            return _mock_response(system_text, user_text, task), False
        if self.provider == "openai":
            return self._openai(system_text, user_text)
        if self.provider == "gemini":
            return self._gemini(
                system_text, user_text,
                json_mode=json_mode, response_schema=response_schema,
            )
        raise ValueError(f"Unknown LLM provider: {self.provider}")

    @staticmethod
    def _anthropic_output_config(response_schema: dict[str, Any] | None) -> dict[str, Any]:
        """Extra ``messages.create`` kwargs for a JSON-schema structured output."""

        if response_schema is None:
            return {}
        return {
            "output_config": {
                "format": {"type": "json_schema", "schema": response_schema}
            }
        }

    # ----------------------------------------------------------- providers
    def _anthropic(
        self,
        system: PromptInput,
        user: PromptInput,
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        # No temperature: Sonnet 5+ rejects non-default sampling params (400).
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=_to_blocks(system, cache_last=True),
            messages=[{"role": "user", "content": _to_blocks(user)}],
            **self._anthropic_output_config(response_schema),
        )
        self._log_cache_usage(message.usage)
        text = "".join(block.text for block in message.content if block.type == "text")
        return text, message.stop_reason == "max_tokens"

    def _log_cache_usage(self, usage: Any) -> None:
        """Print prompt-cache stats when WIKI_LLM_LOG_CACHE is set (off by default).

        HIT means tokens were read from cache; WRITE means the prefix was (re)written;
        fresh is the uncached remainder. See the module docstring for the 1024-token
        minimum and 5-minute TTL caveats.
        """

        if not os.environ.get("WIKI_LLM_LOG_CACHE"):
            return
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        fresh = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        state = "HIT" if read else ("WRITE" if write else "MISS")
        print(
            f"  [cache {state}] read={read} write={write} fresh={fresh} "
            f"out={out} ({self.model})"
        )

    def _openai(self, system: str, user: str) -> tuple[str, bool]:
        if self._client is None:
            import openai

            self._client = openai.OpenAI()
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        choice = response.choices[0]
        return choice.message.content or "", choice.finish_reason == "length"

    def _gemini_config(
        self, json_mode: bool, response_schema: dict[str, Any] | None = None
    ) -> Any:
        """Config shared by sync/batch Gemini JSON calls.

        Gemini 3.x always reasons (thinking can't be disabled), so for JSON tasks
        we ask the API for JSON directly: the answer is then well-formed rather
        than fenced or mixed with the model's reasoning prose. When a schema is
        given we pass it via ``response_json_schema`` (raw JSON Schema, the
        recommended field) for a hard structural guarantee.
        """

        from google.genai import types as genai_types

        return genai_types.GenerateContentConfig(
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
            response_mime_type="application/json" if json_mode else None,
            response_json_schema=response_schema if json_mode else None,
        )

    def _gemini(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        if self._client is None:
            from google import genai

            self._client = genai.Client()
        response = self._client.models.generate_content(
            model=self.model,
            contents=f"{system}\n\n{user}",
            config=self._gemini_config(json_mode, response_schema),
        )
        return response.text or "", _gemini_truncated(response)

    def _gemini_grounded(self, system: str, user: str) -> GroundedResult:
        from google import genai
        from google.genai import types as genai_types

        if self._client is None:
            self._client = genai.Client()

        config = genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            temperature=self.temperature,
            max_output_tokens=self.max_tokens,
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=f"{system}\n\n{user}",
            config=config,
        )
        text = response.text or ""
        citations, search_queries = _parse_grounding_metadata(response)
        if not citations:
            citations = _citations_from_urls(text)
        return GroundedResult(
            text=text,
            citations=citations,
            search_queries=search_queries,
        )

    def _anthropic_batch(
        self, requests: Sequence[BatchRequest]
    ) -> dict[str, tuple[str, bool]]:
        import anthropic
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        if self._client is None:
            self._client = anthropic.Anthropic()

        id_map = {
            _sanitize_batch_custom_id(req.request_id): req.request_id for req in requests
        }
        api_requests = [
            Request(
                custom_id=_sanitize_batch_custom_id(req.request_id),
                params=MessageCreateParamsNonStreaming(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=req.system,
                    messages=[{"role": "user", "content": req.user}],
                    **self._anthropic_output_config(req.response_schema),
                ),
            )
            for req in requests
        ]

        batch = self._client.messages.batches.create(requests=api_requests)
        print(
            f"  Anthropic batch {batch.id} submitted "
            f"({len(requests)} requests, ~50% cheaper)..."
        )

        while True:
            batch = self._client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            counts = batch.request_counts
            print(
                f"  Anthropic batch {batch.id}: "
                f"processing={counts.processing}, succeeded={counts.succeeded}, "
                f"errored={counts.errored}"
            )
            time.sleep(self.batch_poll_interval)

        out: dict[str, tuple[str, bool]] = {}
        for result in self._client.messages.batches.results(batch.id):
            request_id = id_map.get(result.custom_id, result.custom_id)
            if result.result.type == "succeeded":
                message = result.result.message
                text = "".join(
                    block.text for block in message.content if block.type == "text"
                )
                out[request_id] = (text, message.stop_reason == "max_tokens")
            else:
                print(f"  Warning: Anthropic batch item {request_id} -> {result.result.type}")
                out[request_id] = ("", False)
        return out

    def _gemini_batch(
        self, requests: Sequence[BatchRequest]
    ) -> dict[str, tuple[str, bool]]:
        from google import genai
        from google.genai import types as genai_types

        if self._client is None:
            self._client = genai.Client()

        inline_requests = [
            {
                "contents": [
                    {
                        "parts": [{"text": f"{req.system}\n\n{req.user}"}],
                        "role": "user",
                    }
                ],
                "metadata": {"key": req.request_id},
                "config": {
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_tokens,
                    # Extract/classify/plan all want JSON; asking for it directly
                    # keeps the model's reasoning prose out of the response. A
                    # schema (when given) hard-constrains the structure too.
                    "response_mime_type": "application/json",
                    **(
                        {"response_json_schema": req.response_schema}
                        if req.response_schema is not None
                        else {}
                    ),
                },
            }
            for req in requests
        ]

        batch_job = self._client.batches.create(
            model=self.model,
            src=inline_requests,
            config={"display_name": "wiki-build-batch"},
        )
        print(
            f"  Gemini batch {batch_job.name} submitted "
            f"({len(requests)} requests, ~50% cheaper)..."
        )

        job_name = batch_job.name
        state_name = ""
        while True:
            batch_job = self._client.batches.get(name=job_name)
            state_name = batch_job.state.name if batch_job.state else ""
            if state_name in genai_types.JOB_STATES_ENDED:
                break
            print(f"  Gemini batch {job_name}: {state_name}")
            time.sleep(self.batch_poll_interval)

        if state_name not in genai_types.JOB_STATES_SUCCEEDED:
            raise RuntimeError(f"Gemini batch job ended with state {state_name!r}")

        out: dict[str, tuple[str, bool]] = {}
        dest = batch_job.dest
        inlined = dest.inlined_responses if dest else None
        if not inlined:
            return out

        for inline_resp in inlined:
            request_id = None
            if inline_resp.metadata:
                request_id = inline_resp.metadata.get("key")
            if inline_resp.response:
                out[request_id or ""] = (
                    inline_resp.response.text or "",
                    _gemini_truncated(inline_resp.response),
                )
            elif inline_resp.error:
                print(f"  Warning: Gemini batch item {request_id} error: {inline_resp.error}")
                if request_id:
                    out[request_id] = ("", False)
        return out


# --------------------------------------------------------------------- mock
def _mock_response(system: str, user: str, task: str) -> str:
    """Deterministic offline stub responses keyed by ``task``.

    These are intentionally simple but schema-valid so the pipeline can be
    exercised without an API key. Replace with a real provider for production.
    """

    if task == "classify":
        return _mock_classify(user)
    if task == "extract":
        return _mock_extract(user)
    if task == "generate":
        return _mock_generate(user)
    if task == "plan":
        return _mock_plan(user)
    if task == "community":
        return _mock_generate(user)
    if task == "community_agent":
        return _mock_community_agent(user)
    return ""


def _mock_thread_segment(user: str) -> str:
    """Isolate the rendered-thread part of a prompt for the heuristic mock."""

    marker = "השיחה:"
    if marker in user:
        return user.split(marker, 1)[1]
    return user


def _mock_classify(user: str) -> str:
    from utils.taxonomy import all_pages

    text = _mock_thread_segment(user).lower()
    tags: list[str] = []
    for page in all_pages():
        if any(kw.lower() in text for kw in page.keywords):
            tags.append(page.id)
    surrogacy_terms = ("פונדק", "תורמת", "ביצית", "עובר", "סוכנות", "פונדקאית")
    is_knowledge = bool(tags) or any(t in text for t in surrogacy_terms)
    payload = {
        "is_knowledge_bearing": is_knowledge,
        "topic_tags": tags[:5] or (["overview"] if is_knowledge else []),
        "entities": [],
        "reason": "mock heuristic keyword match",
    }
    return json.dumps(payload, ensure_ascii=False)


def _mock_extract(user: str) -> str:
    """Build one stub claim per distinct participant line in the rendered thread."""

    from utils.taxonomy import all_pages

    segment = _mock_thread_segment(user)
    lines = [ln for ln in segment.splitlines() if re.match(r"^\s*\[m\d+\]", ln)]
    text_lower = segment.lower()
    tags = [page.id for page in all_pages() if any(kw.lower() in text_lower for kw in page.keywords)]
    tags = tags[:3] or ["overview"]

    claims = []
    if lines:
        first = lines[0]
        idx_match = re.match(r"^\s*\[m(\d+)\]", first)
        msg_idx = int(idx_match.group(1)) if idx_match else 0
        snippet = re.sub(r"^\s*\[m\d+\]\s*\([^)]*\)\s*", "", first).strip()
        claims.append(
            {
                "claim_text": snippet[:200] or "תוכן הודעה",
                "topic_tags": tags,
                "entities": [],
                "stance": "factual",
                "supporting_message_ids": [msg_idx],
            }
        )
    return json.dumps({"claims": claims}, ensure_ascii=False)


def _mock_generate(user: str) -> str:
    """Return a schema-valid community JSON stub.

    If the prompt lists other wiki pages, link the first one inline so the
    link-validation path is exercised offline.
    """

    catalog_ids = re.findall(r"^- (\S+)\.md —", user, re.MULTILINE)
    related = catalog_ids[:2]
    if related:
        link = f"(ראו [{related[0]}]({related[0]}.md)) "
    else:
        link = ""
    body = (
        f"רוב המשתתפים בקבוצה (mock) ציינו מידע רלוונטי לנושא זה. {link}"
        "_עמוד זה נוצר במצב mock — הפעילו ספק LLM אמיתי לתוכן מלא._"
    )
    return json.dumps({"body": body, "related_pages": related}, ensure_ascii=False)


def _mock_community_agent(user: str) -> str:
    """Schema-valid agent stub: one statement per batch claim, citing its id.

    Parses the rendered batch prompt for the current page id and the claim ids so
    the offline pipeline and tests exercise the full store/render path.
    """

    page_match = re.search(r"עמוד נוכחי:\s*(\S+)", user)
    page_id = page_match.group(1) if page_match else "overview"
    claim_ids = re.findall(r"claim_id:\s*(\S+)", user)
    actions = [
        {
            "type": "upsert_statement",
            "page_id": page_id,
            "section": "",
            "statement_id": None,
            "text": "חברי הקבוצה שיתפו מידע רלוונטי לנושא זה.",
            "claim_ids": [cid],
        }
        for cid in claim_ids
    ]
    return json.dumps({"actions": actions}, ensure_ascii=False)


def _mock_plan(user: str) -> str:
    """Build a 1:1 identity plan from topic summaries embedded in the prompt."""

    pages: list[dict[str, Any]] = []
    for line in user.splitlines():
        if not line.startswith("- id:"):
            continue
        # Format: - id: tamuz | title: ... | claims: 5 | ...
        parts = line.split("|")
        page_id = parts[0].split(":", 1)[1].strip()
        title = page_id
        claim_count = 0
        for part in parts[1:]:
            key_val = part.strip().split(":", 1)
            if len(key_val) != 2:
                continue
            key, val = key_val[0].strip(), key_val[1].strip()
            if key == "title":
                title = val
            elif key == "claims":
                claim_count = int(val) if val.isdigit() else 0
        if claim_count > 0:
            from utils.taxonomy import resolve_search_focus

            pages.append(
                {
                    "id": page_id,
                    "title": title,
                    "category": "emergent",
                    "source_tags": [page_id],
                    "rationale": "mock identity mapping",
                    "search_focus": resolve_search_focus(page_id, [page_id]),
                }
            )
    return json.dumps({"pages": pages, "links": []}, ensure_ascii=False)


def _mock_grounded(user: str) -> GroundedResult:
    return GroundedResult(
        text="_מצב mock — חיפוש באינטרנט לא בוצע. הפעילו מפתח Gemini לרקע כללי._",
        citations=(),
        search_queries=(),
    )


def _parse_grounding_metadata(response: Any) -> tuple[tuple[GroundedCitation, ...], tuple[str, ...]]:
    citations: list[GroundedCitation] = []
    search_queries: list[str] = []
    seen_urls: set[str] = set()

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        metadata = getattr(candidate, "grounding_metadata", None)
        if metadata is None:
            continue

        for query in getattr(metadata, "web_search_queries", None) or []:
            if query and query not in search_queries:
                search_queries.append(str(query))

        chunks = getattr(metadata, "grounding_chunks", None) or []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if web is None:
                continue
            url = getattr(web, "uri", None) or getattr(web, "url", None) or ""
            title = getattr(web, "title", None) or url
            if url and url not in seen_urls:
                seen_urls.add(url)
                citations.append(GroundedCitation(title=str(title), url=str(url)))

        supports = getattr(metadata, "grounding_supports", None) or []
        for support in supports:
            for idx in getattr(support, "grounding_chunk_indices", None) or []:
                if 0 <= idx < len(chunks):
                    web = getattr(chunks[idx], "web", None)
                    if web is None:
                        continue
                    url = getattr(web, "uri", None) or getattr(web, "url", None) or ""
                    title = getattr(web, "title", None) or url
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        citations.append(GroundedCitation(title=str(title), url=str(url)))

    return tuple(citations), tuple(search_queries)


def _citations_from_urls(text: str) -> tuple[GroundedCitation, ...]:
    citations: list[GroundedCitation] = []
    seen: set[str] = set()
    for url in re.findall(r"https?://[^\s)\]>]+", text):
        url = url.rstrip(".,;")
        if url in seen:
            continue
        seen.add(url)
        citations.append(GroundedCitation(title=url, url=url))
    return tuple(citations)
