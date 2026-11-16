"""Provider-agnostic LLM client with on-disk caching.

The client exposes methods used across the wiki pipeline:

- ``complete_json(system, user, task=...)`` -> parsed ``dict``/``list``
- ``complete_text(system, user, task=...)`` -> ``str``
- ``complete_batch(requests)`` -> ``dict[request_id, (text, truncated)]`` (anthropic)
- ``complete_batch_json(requests)`` -> ``dict[request_id, parsed]`` (re-submits only
  items *truncated at max_tokens* as another batch with a larger window; other
  unparseable items are logged and dropped rather than retried)

A batch call with ``resumable=True`` saves the id of the batch it submits under
``<data dir>/<batch_state_dir>/<key>.json`` (the key covers the client's identity, its
``max_tokens`` and every request's cache key) before polling it. When that run dies or is
interrupted, the next call with the same requests picks the batch up (poll, fetch, cache)
instead of paying for it again; the file is removed once the answers are cached. A saved
batch that can no longer be retrieved drops its file and raises ``BatchGone``; a batch that
ended with items that never ran (expired, canceled) caches the answered ones, drops its
file and raises ``BatchUnfinished``, so the next call submits only those. Without
``resumable`` nothing changes.

A response cut off at ``max_tokens`` is retried only with a window at least
``min_retry_growth`` times larger (twice the window, at most ``max_tokens_ceiling``); a
client already at or near the ceiling gives up at once instead of paying for a retry that
cannot help.

Provider/model per stage and every other non-secret tunable live in
``config.json`` (see ``settings``); only API keys live in ``.env``. Build a
per-stage client with ``LLMClient.for_stage(stage)``; construct one directly with
an explicit ``provider`` for ad-hoc use. An unconfigured client raises.

The response cache and the failure log default to ``cache_dir`` / ``failure_log``
from ``config.json``, which are relative to the data dir (``utils.config.DATA_DIR``)
so every worktree / ``WIKI_DATA_DIR`` gets its own. The cache key covers
everything that changes an answer: provider, model, ``thinking_param``, the
flattened prompts, JSON mode and the response schema.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Sequence

from utils.json_io import read_json_if_present, write_json_file
from utils.llm_client.cache import LLMCache
from utils.llm_client.failure_log import append_failure
from utils.llm_client.models import BatchGone, BatchRequest, BatchUnfinished, PromptInput
from utils.llm_client.prompts import _flatten, extract_json
from utils.llm_client.providers import create_provider
from utils.llm_client.settings import CONFIG, DATA_DIR
from utils.timestamps import utc_now


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
        logger: logging.Logger | None = None,
        thinking_param: str | None = None,
    ) -> None:
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
        self.logger = logger or logging.getLogger("utils.llm_client")
        self.temperature = (
            temperature if temperature is not None else CONFIG["default_temperature"]
        )
        self.max_tokens = (
            max_tokens if max_tokens is not None else CONFIG["default_max_tokens"]
        )
        self.use_cache = use_cache
        self.failure_log = Path(
            failure_log
            if failure_log is not None
            else DATA_DIR / CONFIG["failure_log"]
        )
        self.batch_poll_interval = (
            batch_poll_interval
            if batch_poll_interval is not None
            else CONFIG["batch_poll_interval"]
        )
        self._provider = create_provider(
            self.provider,
            self.model,
            self.temperature,
            self.max_tokens,
            self.batch_poll_interval,
            logger=self.logger,
            thinking_param=thinking_param,
        )
        # No temperature: the provider never sends it (Anthropic 5.x rejects
        # non-default sampling), so it never changes an answer.
        identity: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "thinking_param": thinking_param,
        }
        self.cache = LLMCache(
            cache_dir if cache_dir is not None else DATA_DIR / CONFIG["cache_dir"],
            identity,
        )
        self.batch_state_dir = DATA_DIR / CONFIG["batch_state_dir"]

    def supports_batch(self) -> bool:
        return self._provider.supports_batch

    @property
    def usage(self) -> list[dict[str, Any]]:
        """Token usage as the provider's API reported it, one entry per response, in order
        (``request_id`` is the batch item's id, ``None`` for a sync call). Answers served
        from the disk cache add nothing."""

        return self._provider.usage

    def _cache_key(
        self,
        system: PromptInput,
        user: PromptInput,
        *,
        json_mode: bool = False,
        response_schema: dict[str, Any] | None = None,
    ) -> str:
        """Cache key of one request: the prompts plus the per-call settings that
        change the answer (the client-wide ones are the cache's identity)."""

        return self.cache._cache_key(
            system=_flatten(system),
            user=_flatten(user),
            json_mode=json_mode,
            response_schema=response_schema,
        )

    def _batch_key(self, req: BatchRequest) -> str:
        """Cache key of a batch item. Batch items are JSON requests (Anthropic
        ignores the flag), so they share
        keys with ``complete_json`` and either path can reuse the other's answer."""

        return self._cache_key(
            req.system, req.user, json_mode=True, response_schema=req.response_schema
        )

    def cached_json(self, req: BatchRequest) -> Any | None:
        """The parsed answer the disk cache holds for this batch item, or ``None`` when
        there is none or it does not parse (never asks the provider): for estimates."""

        entry = self.cache._read_cache(self._batch_key(req))
        if not isinstance(entry, str):
            return None
        try:
            return extract_json(entry)
        except ValueError:
            return None

    def set_logger(self, logger: logging.Logger) -> None:
        """Retarget this client's (and its provider's) logging, e.g. per step."""
        self.logger = logger
        self._provider.logger = logger

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
        return cls(
            provider=resolved["provider"],
            model=resolved["model"],
            thinking_param=resolved.get("thinking_param"),
            **kwargs,
        )

    # ------------------------------------------------------------- call logging
    def _log_call(self, ok: bool, task: str, detail: str) -> None:
        status = "ok  " if ok else "FAIL"
        self.logger.info(
            f"LLM {status} [{self.provider}/{self.model}] task={task or '-'} {detail}"
        )

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

        append_failure(
            self.failure_log,
            {
                "provider": self.provider,
                "model": self.model,
                "task": task,
                "kind": kind,
                "error": error,
                "max_tokens": self.max_tokens,
                "system": _flatten(system),
                "user": _flatten(user),
                "response": response,
            },
            self.logger,
        )

    # --------------------------------------------------------------- complete
    def complete_text(self, system: PromptInput, user: PromptInput, task: str = "") -> str:
        if self.use_cache:
            key = self._cache_key(system, user)
            cached = self.cache._read_cache(key)
            # An entry can hold a classifier's answer list, which is not plain text.
            if isinstance(cached, str):
                self._log_call(True, task, "(cache)")
                return cached

        try:
            response, _ = self._dispatch(system, user)
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

    def _grown_max_tokens(self) -> int | None:
        """The window a truncated response is retried with (double, at most the ceiling), or
        ``None`` when it would not be at least ``min_retry_growth`` times the current one."""

        grown = min(self.max_tokens * 2, CONFIG["max_tokens_ceiling"])
        return grown if grown >= self.max_tokens * CONFIG["min_retry_growth"] else None

    def _raise_max_tokens(self) -> bool:
        """Grow max_tokens for a retry (``_grown_max_tokens``); False when that cannot help.

        ponytail: raising the ceiling is free (billing is on tokens actually
        generated), so the bump persists for the client's lifetime rather than
        being restored per-call — later batches likely need the extra room too.
        """

        grown = self._grown_max_tokens()
        if grown is None:
            return False
        self.max_tokens = grown
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
            self._cache_key(
                system, user, json_mode=True, response_schema=response_schema
            )
            if self.use_cache
            else None
        )
        if key is not None:
            cached = self.cache._read_cache(key)
            # Only a str cache entry is a candidate for extract_json.
            if isinstance(cached, str):
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
                response, truncated = self._dispatch(system, user, json_mode=True, response_schema=response_schema)
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

    def _batch_state_path(self, pending: Sequence[BatchRequest]) -> Path:
        """Where a resumable batch of exactly these requests keeps its batch id."""

        keys = sorted(self._batch_key(req) for req in pending)
        seed = json.dumps([self.cache.identity, self.max_tokens, keys], sort_keys=True)
        return self.batch_state_dir / f"{hashlib.sha256(seed.encode('utf-8')).hexdigest()}.json"

    def complete_batch(
        self, requests: Sequence[BatchRequest], *, resumable: bool = False
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
                cached = self.cache._read_cache(self._batch_key(req))
                # An entry can hold a classifier's answer list, which is not batch text.
                if isinstance(cached, str):
                    results[req.request_id] = (cached, False)
                    self._log_call(True, req.task, f"(batch cache {req.request_id})")
                    continue
            pending.append(req)

        if not pending:
            return results

        if not self._provider.supports_batch:
            raise ValueError(f"Batch API not supported for provider {self.provider!r}")

        self._provider.max_tokens = self.max_tokens
        state = self._batch_state_path(pending) if resumable else None
        saved = (read_json_if_present(state) or {}).get("batch_id") if state else None
        if saved:
            self.logger.info(f"LLM: picking up batch {saved} of an earlier run ({state.name})")

        def remember(batch_id: str) -> None:
            if state is not None:
                write_json_file({"batch_id": batch_id, "requests": len(pending),
                                 "model": self.model, "submitted_at": utc_now()}, state)

        try:
            batch_results = self._provider.generate_batch(
                pending, batch_id=saved, on_submit=remember
            )
        except BatchGone:
            if state is not None:
                state.unlink(missing_ok=True)
                self.logger.error(f"LLM: {saved} can no longer be retrieved; dropped {state}")
            raise
        except Exception:  # noqa: BLE001 - say where the batch id is kept, then re-raise
            if state is not None and state.exists():
                self.logger.error(
                    f"LLM: the batch did not finish here; its id is kept in {state}, so the "
                    "next run with the same requests picks it up (delete the file to "
                    "submit a new batch)"
                )
            raise

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
                    self.cache._write_cache(self._batch_key(req), response)
            else:
                self._log_call(False, req.task, f"batch_empty ({req.request_id})")
        if state is not None:
            state.unlink(missing_ok=True)
            unfinished = list(getattr(self._provider, "unfinished", []) or [])
            if unfinished:
                raise BatchUnfinished(unfinished)
        return results

    def complete_batch_json(
        self, requests: Sequence[BatchRequest], *, resumable: bool = False
    ) -> dict[str, Any]:
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
            raw = (self.complete_batch(pending, resumable=True) if resumable
                   else self.complete_batch(pending))
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
                key = self._batch_key(req)
                self.cache._write_bad_cache(key, text)
                # Never let an unparseable response linger in the good cache.
                self.cache._delete_cache(key)
                if truncated and self._grown_max_tokens() is not None:
                    retry.append(req)
                    continue
                self._record_failure(
                    task=req.task, kind="parse_error",
                    error=f"unparseable batch response (truncated={truncated}): {error}",
                    system=req.system, user=req.user, response=text,
                )
                self._log_call(
                    False, req.task, f"batch parse_error ({req.request_id}) -> logged"
                )
            if not retry:
                break

            self._raise_max_tokens()
            self.logger.info(
                f"LLM: {len(retry)}/{len(pending)} batch items truncated; "
                f"re-submitting those as a batch with max_tokens={self.max_tokens}"
            )
            pending = retry
        return parsed

    def _dispatch(self, system: PromptInput, user: PromptInput, json_mode: bool = False,
                  response_schema: dict[str, Any] | None = None) -> tuple[str, bool]:
        """Return ``(text, truncated)``; ``truncated`` marks a max_tokens cutoff."""

        self._provider.max_tokens = self.max_tokens
        return self._provider.generate(
            system, user, json_mode=json_mode, response_schema=response_schema
        )
