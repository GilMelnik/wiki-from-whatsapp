"""Provider-agnostic LLM client with on-disk caching.

The client exposes methods used across the wiki pipeline:

- ``complete_json(system, user, task=...)`` -> parsed ``dict``/``list``
- ``complete_text(system, user, task=...)`` -> ``str``
- ``complete_grounded(system, user)`` -> ``GroundedResult`` (Gemini + Google Search)
- ``complete_batch(requests)`` -> ``dict[request_id, (text, truncated)]`` (anthropic / gemini)
- ``complete_batch_json(requests)`` -> ``dict[request_id, parsed]`` (re-submits only
  items *truncated at max_tokens* as another batch with a larger window; other
  unparseable items are logged and dropped rather than retried)

Provider/model per stage and every other non-secret tunable live in
``config.json`` (see ``settings``); only API keys live in ``.env``. Build a
per-stage client with ``LLMClient.for_stage(stage)``; construct one directly with
an explicit ``provider`` for ad-hoc use. An unconfigured client raises.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Sequence

from utils.llm_client.cache import LLMCache
from utils.llm_client.models import BatchRequest, GroundedResult, PromptInput
from utils.llm_client.prompts import _flatten, extract_json
from utils.llm_client.providers import create_provider
from utils.llm_client.settings import CONFIG


class LLMClient:
    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        cache_dir: Path | str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_cache: bool = True,
        batch_poll_interval: float | None = None,
        failure_log: Path | str | None = None,
    ):
        if not provider:
            raise ValueError(
                "No LLM provider configured; pass provider=... or use "
                "LLMClient.for_stage(stage)."
            )
        if not model:
            raise ValueError(
                f"No model configured for provider {provider!r}; pass model=... or "
                "use LLMClient.for_stage(stage)."
            )
        self.provider = provider.lower()
        self.model = model
        self.temperature = (
            temperature if temperature is not None else CONFIG["default_temperature"]
        )
        self.max_tokens = (
            max_tokens if max_tokens is not None else CONFIG["default_max_tokens"]
        )
        self.use_cache = use_cache
        self.failure_log = Path(
            failure_log if failure_log is not None else CONFIG["failure_log"]
        )
        self.batch_poll_interval = (
            batch_poll_interval
            if batch_poll_interval is not None
            else CONFIG["batch_poll_interval"]
        )
        cache_dir = cache_dir if cache_dir is not None else CONFIG["cache_dir"]
        self.cache = LLMCache(cache_dir, self.provider, self.model, self.temperature)
        self._provider = create_provider(
            self.provider,
            self.model,
            self.temperature,
            self.max_tokens,
            self.batch_poll_interval,
        )

    def supports_batch(self) -> bool:
        return self._provider.supports_batch

    @classmethod
    def for_stage(cls, stage: str, **kwargs: Any) -> "LLMClient":
        """Build a client for one pipeline stage from ``config.json``."""

        stages = CONFIG["stages"]
        try:
            resolved = stages[stage]
        except KeyError:
            raise ValueError(
                f"Unknown stage {stage!r}; expected one of {sorted(stages)}"
            )
        return cls(provider=resolved["provider"], model=resolved["model"], **kwargs)

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
        examined and re-run differently later.
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

    # --------------------------------------------------------------- complete
    def complete_text(self, system: PromptInput, user: PromptInput, task: str = "") -> str:
        if self.use_cache:
            key = self.cache._cache_key(_flatten(system), _flatten(user))
            cached = self.cache._read_cache(key)
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
            self.cache._write_cache(key, response)
        return response

    def _raise_max_tokens(self) -> bool:
        """Double max_tokens toward the ceiling; return False if already there.

        ponytail: raising the ceiling is free (billing is on tokens actually
        generated), so the bump persists for the client's lifetime rather than
        being restored per-call — later batches likely need the extra room too.
        """

        ceiling = CONFIG["max_tokens_ceiling"]
        if self.max_tokens >= ceiling:
            return False
        self.max_tokens = min(self.max_tokens * 2, ceiling)
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
            self.cache._cache_key(_flatten(system), _flatten(user))
            if self.use_cache
            else None
        )
        if key is not None:
            cached = self.cache._read_cache(key)
            if cached is not None:
                try:
                    data = extract_json(cached)
                    self._log_call(True, task, "(cache)")
                    return data
                except ValueError as exc:
                    # Failed cache entry from a prior run: drop it and re-request;
                    # the fresh call's finish_reason decides any max_tokens bump.
                    self.cache._delete_cache(key)
                    self._log_call(
                        False, task, f"stale cache unparseable ({exc}); re-requesting"
                    )

        last_exc: ValueError | None = None
        response = ""
        for _ in range(CONFIG["json_parse_retries"] + 1):
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
                    self.cache._write_bad_cache(key, response)
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
                self.cache._write_cache(key, response)
            return data

        # Retries/ceiling exhausted: persist the last bad response for inspection.
        self._record_failure(
            task=task, kind="parse_error", error=str(last_exc),
            system=system, user=user, response=response,
        )
        self._log_call(False, task, "parse_error exhausted -> logged")
        raise last_exc  # type: ignore[misc]

    def complete_grounded(self, system: str, user: str) -> GroundedResult:
        """Run the provider with Google Search grounding; returns text + citations."""

        if self.use_cache:
            cached = self.cache._read_cache(self.cache._cache_key(system, user))
            if isinstance(cached, GroundedResult):
                return cached

        if not self._provider.supports_grounded:
            raise ValueError(
                f"Grounded search requires provider 'gemini'; got {self.provider!r}"
            )
        result = self._provider.generate_grounded(system, user)

        if self.use_cache:
            self.cache._write_cache(self.cache._cache_key(system, user), result)
        return result

    def complete_batch(
        self, requests: Sequence[BatchRequest]
    ) -> dict[str, tuple[str, bool]]:
        """Run many prompts via the provider batch API (50% cheaper, async).

        Returns ``{request_id: (text, truncated)}`` where ``truncated`` is True
        when the item hit max_output_tokens. Cached prompts are returned
        immediately (never truncated — only good responses are cached).
        """

        results: dict[str, tuple[str, bool]] = {}
        pending: list[BatchRequest] = []

        for req in requests:
            if self.use_cache:
                cached = self.cache._read_cache(self.cache._cache_key(req.system, req.user))
                if cached is not None:
                    results[req.request_id] = (cached, False)
                    self._log_call(True, req.task, f"(batch cache {req.request_id})")
                    continue
            pending.append(req)

        if not pending:
            return results

        if not self._provider.supports_batch:
            raise ValueError(f"Batch API not supported for provider {self.provider!r}")

        self._provider.max_tokens = self.max_tokens
        batch_results = self._provider.generate_batch(pending)

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
                    self.cache._write_cache(
                        self.cache._cache_key(req.system, req.user), response
                    )
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
                key = self.cache._cache_key(_flatten(req.system), _flatten(req.user))
                self.cache._write_bad_cache(key, text)
                # Never let an unparseable response linger in the good cache.
                self.cache._delete_cache(key)
                if truncated and self.max_tokens < CONFIG["max_tokens_ceiling"]:
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
        task: str = "",
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        """Return ``(text, truncated)``; ``truncated`` marks a max_tokens cutoff."""

        self._provider.max_tokens = self.max_tokens
        return self._provider.generate(
            system, user, json_mode=json_mode, response_schema=response_schema
        )
