"""Provider-agnostic LLM client with on-disk caching.

The client exposes methods used across the wiki pipeline:

- ``complete_json(system, user, task=...)`` -> parsed ``dict``/``list``
- ``complete_text(system, user, task=...)`` -> ``str``
- ``complete_batch(requests)`` -> ``dict[request_id, str]`` (anthropic / gemini)

Configuration (most-specific wins):

1. Per-stage env vars (recommended for the hybrid setup)::

       WIKI_LLM_CLASSIFY_PROVIDER=gemini
       WIKI_LLM_CLASSIFY_MODEL=gemini-2.0-flash
       WIKI_LLM_EXTRACT_PROVIDER=anthropic
       WIKI_LLM_EXTRACT_MODEL=claude-sonnet-4-20250514
       WIKI_LLM_GENERATE_PROVIDER=anthropic
       WIKI_LLM_GENERATE_MODEL=claude-sonnet-4-20250514

2. Global fallback (same model for every stage)::

       WIKI_LLM_PROVIDER=anthropic
       WIKI_LLM_MODEL=claude-3-5-sonnet-latest

3. Hybrid stage defaults via ``LLMClient.for_stage(stage, use_hybrid_defaults=True)``
   (used by ``wiki_build.pipeline``): cheap Flash for classify, Sonnet for
   extract/generate. Override any stage with the per-stage env vars above.

4. Offline mock when nothing is configured (``LLMClient()`` with no env vars).

Providers: ``anthropic``, ``openai``, ``gemini``, ``mock``. SDKs are imported
lazily so only the providers you use must be installed.

Every call is cached on disk keyed by a hash of (provider, model, system, user).
The cache lives in ``data/llm_cache/`` (gitignored).

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
    "anthropic": "claude-3-5-sonnet-latest",
    "openai": "gpt-4o",
    "gemini": "gemini-3.1-flash-lite",
    "mock": "mock",
}

# Hybrid defaults: cheap classify, strong extract + generate (override via env).
STAGE_DEFAULTS: dict[str, dict[str, str]] = {
    "classify": {"provider": "gemini", "model": "gemini-3.1-flash-lite"},
    "extract": {"provider": "gemini", "model": "gemini-3.5-flash"},
    "generate": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
}

VALID_STAGES = frozenset(STAGE_DEFAULTS)
BATCH_PROVIDERS = frozenset({"anthropic", "gemini"})
DEFAULT_BATCH_POLL_INTERVAL = 30.0


@dataclass(frozen=True)
class BatchRequest:
    """One prompt submitted as part of a provider batch job."""

    request_id: str
    system: str
    user: str
    task: str = ""


@dataclass(frozen=True)
class StageLLMConfig:
    """Resolved provider/model for one pipeline stage."""

    stage: str
    provider: str
    model: str

    def label(self) -> str:
        return f"{self.stage}: {self.provider}/{self.model}"


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
        max_tokens: int = 4096,
        use_cache: bool = True,
        batch_poll_interval: float | None = None,
    ):
        self.provider = (provider or os.environ.get("WIKI_LLM_PROVIDER") or "mock").lower()
        self.model = model or os.environ.get("WIKI_LLM_MODEL") or DEFAULT_MODELS.get(
            self.provider, "mock"
        )
        self.cache_dir = Path(cache_dir)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.use_cache = use_cache
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

    # --------------------------------------------------------------- complete
    def complete_text(self, system: str, user: str, task: str = "") -> str:
        if self.use_cache:
            key = self._cache_key(system, user)
            cached = self._read_cache(key)
            if cached is not None:
                return cached

        response = self._dispatch(system, user, task)

        if self.use_cache:
            self._write_cache(key, response)
        return response

    def complete_json(self, system: str, user: str, task: str = "") -> Any:
        raw = self.complete_text(system, user, task)
        return extract_json(raw)

    def complete_batch(self, requests: Sequence[BatchRequest]) -> dict[str, str]:
        """Run many prompts via the provider batch API (50% cheaper, async).

        Cached prompts are returned immediately; only uncached items are submitted.
        Mock provider runs synchronously without an API call.
        """

        results: dict[str, str] = {}
        pending: list[BatchRequest] = []

        for req in requests:
            if self.use_cache:
                cached = self._read_cache(self._cache_key(req.system, req.user))
                if cached is not None:
                    results[req.request_id] = cached
                    continue
            pending.append(req)

        if not pending:
            return results

        if self.provider == "mock":
            for req in pending:
                response = _mock_response(req.system, req.user, req.task)
                results[req.request_id] = response
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
            response = batch_results.get(req.request_id, "")
            results[req.request_id] = response
            if self.use_cache and response:
                self._write_cache(self._cache_key(req.system, req.user), response)
        return results

    def _dispatch(self, system: str, user: str, task: str) -> str:
        if self.provider == "mock":
            return _mock_response(system, user, task)
        if self.provider == "anthropic":
            return self._anthropic(system, user)
        if self.provider == "openai":
            return self._openai(system, user)
        if self.provider == "gemini":
            return self._gemini(system, user)
        raise ValueError(f"Unknown LLM provider: {self.provider}")

    # ----------------------------------------------------------- providers
    def _anthropic(self, system: str, user: str) -> str:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in message.content if block.type == "text")

    def _openai(self, system: str, user: str) -> str:
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
        return response.choices[0].message.content or ""

    def _gemini(self, system: str, user: str) -> str:
        if self._client is None:
            from google import genai

            self._client = genai.Client()
        response = self._client.models.generate_content(
            model=self.model,
            contents=f"{system}\n\n{user}",
        )
        return response.text or ""

    def _anthropic_batch(self, requests: Sequence[BatchRequest]) -> dict[str, str]:
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
                    temperature=self.temperature,
                    system=req.system,
                    messages=[{"role": "user", "content": req.user}],
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

        out: dict[str, str] = {}
        for result in self._client.messages.batches.results(batch.id):
            request_id = id_map.get(result.custom_id, result.custom_id)
            if result.result.type == "succeeded":
                message = result.result.message
                out[request_id] = "".join(
                    block.text for block in message.content if block.type == "text"
                )
            else:
                print(f"  Warning: Anthropic batch item {request_id} -> {result.result.type}")
                out[request_id] = ""
        return out

    def _gemini_batch(self, requests: Sequence[BatchRequest]) -> dict[str, str]:
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

        out: dict[str, str] = {}
        dest = batch_job.dest
        inlined = dest.inlined_responses if dest else None
        if not inlined:
            return out

        for inline_resp in inlined:
            request_id = None
            if inline_resp.metadata:
                request_id = inline_resp.metadata.get("key")
            if inline_resp.response:
                out[request_id or ""] = inline_resp.response.text or ""
            elif inline_resp.error:
                print(f"  Warning: Gemini batch item {request_id} error: {inline_resp.error}")
                if request_id:
                    out[request_id] = ""
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
    return ""


def _mock_thread_segment(user: str) -> str:
    """Isolate the rendered-thread part of a prompt for the heuristic mock."""

    marker = "השיחה:"
    if marker in user:
        return user.split(marker, 1)[1]
    return user


def _mock_classify(user: str) -> str:
    from wiki_build.taxonomy import all_pages

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

    from wiki_build.taxonomy import all_pages

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
    return (
        "## תקציר\n\n"
        "_עמוד זה נוצר במצב mock לבדיקת הצנרת בלבד. הפעילו ספק LLM אמיתי "
        "(`WIKI_LLM_PROVIDER`) כדי לייצר תוכן ערוך בעברית._\n\n"
        "להלן הטענות שחולצו עבור נושא זה:\n"
    )
