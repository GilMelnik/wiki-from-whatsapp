"""Step 8's batch route (task 74), fake models only: the same requests and the same pages as
the sync route, retries as a second batch round, failures per page, the route switches, a
re-run after an interrupted batch, the shared disk cache and the estimate. Every page here is
synthetic."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Sequence
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from review_tool.steps import SERVED_STEPS
from step_8_write_pages import run as pages_run
from step_8_write_pages.inputs import PAGES_CONFIG
from step_8_write_pages.run import UPDATE_CONFIG, WRITE_CONFIG
from tests.pages_fixtures import FakeWriter, PagesWorkspace
from update_run.estimate import Estimator
from utils.llm_client import BatchRequest, LLMClient, _flatten
from utils.paths import STEP_8
from utils.token_counters import CharTokenCounter

PAGES = ["agencies", "costs", "clinics", "hospital"]
WRITES = [f"write:{page_id}" for page_id in PAGES]
STAMP = "2026-01-01T00:00:00Z"
BAD_LINK = "[[no-such-page|דף שאינו בתוכנית]]\n"
ROUTE_FREE = ("sources", "batch")  # the metadata that differs between the two routes


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pages_run, "utc_now_z", lambda: STAMP)


def _respond(*, bad_once: Collection[str] = (), bad_always: Collection[str] = (),
             unusable_first: Collection[str] = (), unusable_retry: Collection[str] = (),
             no_change: Collection[str] = ()) -> Callable[[Any, Any, str], dict[str, Any]]:
    """The fake model's answers by page: ``bad_once`` links a page outside the plan in its
    first answer only, ``bad_always`` in both; ``unusable_*`` answer with no body;
    ``no_change`` answers an update with "no change needed"."""
    plain = FakeWriter()

    def respond(system: Any, user: Any, task: str) -> dict[str, Any]:
        page_id = task.split(":", 1)[1]
        retry = "<previous_answer>" in _flatten(user)
        if page_id in (unusable_retry if retry else unusable_first):
            return {"summary": "no body"}
        answer = plain.complete_json(system, user, task)
        if page_id in bad_always or (page_id in bad_once and not retry):
            answer["body"] += BAD_LINK
        if task.startswith("update:") and page_id in no_change:
            answer["no_change"] = True
        return answer

    return respond


def _models(batch: bool, **answers: Collection[str]) -> tuple[FakeWriter, FakeWriter]:
    return (FakeWriter(respond=_respond(**answers), batch=batch),
            FakeWriter("fake-update", respond=_respond(**answers), batch=batch))


def _requests(*models: FakeWriter) -> list[tuple[str, str, str, str]]:
    """Every request the fakes received, as sorted comparable tuples."""
    return sorted((call["task"], call["system"], _flatten(call["user"]),
                   json.dumps(call["schema"], sort_keys=True))
                  for model in models for call in model.calls)


def _comparable(ws: PagesWorkspace) -> dict[str, Any]:
    payload = ws.pages()
    metadata = {key: value for key, value in payload["metadata"].items() if key not in ROUTE_FREE}
    return {"pages": payload["pages"], "metadata": metadata}


def _scenario(ws: PagesWorkspace, *, batch: bool) -> list[tuple[str, str, str, str]]:
    """Write every page (a retry, a page still wrong after it), then update three pages (a
    retry, a "no change needed", a plain update); returns every request sent."""
    write, update = _models(batch, bad_once={"costs"}, bad_always={"clinics"})
    first = ws.run(write, update, use_batch=batch)
    assert first["batch"] is batch and first["failed"] == {}
    report = ws.pages()["metadata"]["pages"]["clinics"]["report"]
    assert report["calls"] == 2 and report["final_problems"]  # still wrong: repaired, recorded
    assert BAD_LINK.strip() not in ws.pages()["pages"]["clinics"]["body_md"]
    claims = ws.claims()
    for index in (0, 1, 2):  # agencies, costs, clinics
        claims[index]["supporter_count"] += 1
    ws.write_claims(claims)
    write_again, update_again = _models(batch, bad_once={"agencies"}, no_change={"costs"})
    second = ws.run(write_again, update_again, use_batch=batch)
    assert second["update"] == 2 and second["no_change"] == 1 and second["failed"] == {}
    if batch:
        assert write.batches == [WRITES, ["write:costs", "write:clinics"]]
        assert update_again.batches == [["update:agencies", "update:costs", "update:clinics"],
                                        ["update:agencies"]]
    return _requests(write, update, write_again, update_again)


def test_both_routes_send_the_same_requests_and_write_the_same_pages(tmp_path: Path) -> None:
    plain, batch = PagesWorkspace(tmp_path / "plain"), PagesWorkspace(tmp_path / "batch")
    assert _scenario(plain, batch=False) == _scenario(batch, batch=True)
    assert _comparable(plain) == _comparable(batch)
    assert (plain.pages()["metadata"]["batch"], batch.pages()["metadata"]["batch"]) == (False, True)
    reports = batch.pages()["metadata"]["pages"]
    assert reports["costs"]["report"]["calls"] == 1  # its update had no problem
    assert reports["agencies"]["report"]["calls"] == 2  # its update was retried


@pytest.mark.parametrize("batch", [False, True])
def test_a_failing_answer_gets_one_retry_and_an_unusable_one_fails_only_its_page(
        tmp_path: Path, batch: bool) -> None:
    ws = PagesWorkspace(tmp_path)
    write, update = _models(batch, bad_once={"costs", "clinics"}, unusable_first={"hospital"},
                            unusable_retry={"clinics"})
    result = ws.run(write, update, use_batch=batch)
    assert sorted(result["failed"]) == ["clinics", "hospital"]
    assert all("no body" in why for why in result["failed"].values())
    payload = ws.pages()
    assert list(payload["pages"]) == ["agencies", "costs"]
    report = payload["metadata"]["pages"]["costs"]["report"]
    assert report["calls"] == 2 and report["first_problems"] and report["final_problems"] == []
    assert sorted(write.tasks()) == sorted(WRITES + ["write:costs", "write:clinics"])
    if batch:  # the retries of one round go out together, as a second batch
        assert write.batches == [WRITES, ["write:costs", "write:clinics"]]
    again = FakeWriter(batch=batch)
    ws.run(again, FakeWriter("fake-update", batch=batch), use_batch=batch)
    assert sorted(again.tasks()) == ["write:clinics", "write:hospital"]  # asked again next run


@pytest.mark.parametrize("batch", [False, True])
def test_an_unusable_update_keeps_the_old_page_and_is_asked_again(tmp_path: Path,
                                                                  batch: bool) -> None:
    ws = PagesWorkspace(tmp_path)
    ws.run(FakeWriter(batch=batch), FakeWriter("fake-update", batch=batch), use_batch=batch)
    before = ws.pages()
    claims = ws.claims()
    claims[8]["supporter_count"] += 1  # agencies
    ws.write_claims(claims)
    write, update = _models(batch, unusable_first={"agencies"})
    result = ws.run(write, update, use_batch=batch)
    assert list(result["failed"]) == ["agencies"] and write.calls == []
    assert update.tasks() == ["update:agencies"]  # no retry, no full write in its place
    after = ws.pages()
    assert after["pages"]["agencies"] == before["pages"]["agencies"]
    assert after["metadata"]["pages"]["agencies"] == before["metadata"]["pages"]["agencies"]
    update = FakeWriter("fake-update", batch=batch)
    assert ws.run(FakeWriter(batch=batch), update, use_batch=batch)["update"] == 1
    assert update.tasks() == ["update:agencies"]


def test_the_sync_keyword_a_client_without_batch_and_the_config_take_the_plain_route(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, (write, update), kwargs in (
            ("sync", (FakeWriter(batch=True), FakeWriter(batch=True)), {"use_batch": False}),
            ("no batch API", (FakeWriter(batch=True), FakeWriter()), {}),
            ("default", (FakeWriter(batch=True), FakeWriter(batch=True)), {})):
        ws = PagesWorkspace(tmp_path / name.replace(" ", "-"))
        result = ws.run(write, update, **kwargs)
        assert result["batch"] is (name == "default"), name
        assert ws.pages()["metadata"]["batch"] is result["batch"]
        assert (write.batches == []) is (name != "default") and len(write.calls) == 4
    monkeypatch.setitem(PAGES_CONFIG, "batch", False)
    write = FakeWriter(batch=True)
    assert PagesWorkspace(tmp_path / "off").run(write, FakeWriter(batch=True))["batch"] is False
    assert write.batches == []


def test_the_command_line_sync_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(pages_run, "run", lambda **kwargs: seen.append(kwargs))
    pages_run.main(["--sync", "--only", "costs"])
    pages_run.main([])
    assert [(kw["use_batch"], kw["only"]) for kw in seen] == [(False, ["costs"]), (True, None)]


# ------------------------------------------------- the real client over a fake provider


class PagesProvider:
    """A fake Anthropic provider answering as ``FakeWriter(respond=respond)`` does;
    ``fail_polling`` makes a batch fail after it was submitted (the run dies or the connection
    drops while polling), ``fail_retries`` only a batch of retries."""

    supports_batch = True

    def __init__(self, respond: Callable[[Any, Any, str], dict[str, Any]] | None = None) -> None:
        self.max_tokens = 0
        self.usage: list[dict[str, Any]] = []
        self.fail_polling = False
        self.fail_retries = False
        self.batches: list[tuple[str | None, list[str]]] = []
        self.sync: list[str] = []
        self.model = FakeWriter(respond=respond)

    def _text(self, system: Any, user: Any, task: str) -> str:
        return json.dumps(self.model.complete_json(system, user, task), ensure_ascii=False)

    def generate(self, system: Any, user: Any, *, json_mode: bool = False,
                 response_schema: dict | None = None) -> tuple[str, bool]:
        task = ("update:" if "no_change" in json.dumps(response_schema) else "write:")
        self.sync.append(task)
        return self._text(system, user, task), False

    def generate_batch(self, requests: Sequence[BatchRequest], *, batch_id: str | None = None,
                       on_submit: Callable[[str], None] | None = None
                       ) -> dict[str, tuple[str, bool]]:
        self.batches.append((batch_id, [request.task for request in requests]))
        if batch_id is None and on_submit is not None:
            on_submit(f"batch_{len(self.batches)}")
        retries = any("<previous_answer>" in _flatten(r.user) for r in requests)
        if self.fail_polling or (self.fail_retries and retries):
            raise RuntimeError("the connection dropped while polling")
        return {r.request_id: (self._text(r.system, r.user, r.task), False) for r in requests}


def _clients(root: Path, provider: PagesProvider) -> tuple[LLMClient, LLMClient]:
    clients = []
    for effort, config in (("high", WRITE_CONFIG), ("medium", UPDATE_CONFIG)):
        client = LLMClient(provider="anthropic", model="claude-opus-5-5", cache_dir=root / "cache",
                           failure_log=root / "failures.jsonl", thinking_param=effort,
                           max_tokens=config["max_output_tokens"])
        client._provider = provider
        client.batch_state_dir = root / "batches"
        clients.append(client)
    return clients[0], clients[1]


def test_a_rerun_after_an_interrupted_batch_picks_it_up_and_pays_for_nothing_twice(
        tmp_path: Path) -> None:
    ws, provider = PagesWorkspace(tmp_path / "ws"), PagesProvider()
    provider.fail_polling = True
    with pytest.raises(RuntimeError, match="nothing was written. Run again"):
        ws.run(*_clients(tmp_path, provider))
    assert not ws.artifact.original.exists()
    assert len(list((tmp_path / "batches").glob("*.json"))) == 1  # the batch id is kept
    provider.fail_polling = False
    second = ws.run(*_clients(tmp_path, provider))
    assert second["failed"] == {} and list(ws.pages()["pages"]) == PAGES
    assert provider.batches == [(None, WRITES), ("batch_1", WRITES)]  # picked up, not sent
    assert not list((tmp_path / "batches").glob("*.json"))
    third = ws.run(*_clients(tmp_path, provider), rebuild=True)
    assert third["write"] == 4 and len(provider.batches) == 2  # every answer from the cache
    assert provider.sync == []


def test_an_interrupted_round_of_retries_is_picked_up_after_the_cached_first_round(
        tmp_path: Path) -> None:
    ws, provider = PagesWorkspace(tmp_path / "ws"), PagesProvider(_respond(bad_once={"costs"}))
    provider.fail_retries = True
    with pytest.raises(RuntimeError, match="round 2"):
        ws.run(*_clients(tmp_path, provider))
    provider.fail_retries = False
    result = ws.run(*_clients(tmp_path, provider))
    assert result["write"] == 4 and result["failed"] == {}
    assert provider.batches == [(None, WRITES), (None, ["write:costs"]),
                                ("batch_2", ["write:costs"])]
    assert ws.pages()["metadata"]["pages"]["costs"]["report"]["calls"] == 2


@pytest.mark.parametrize("first_batch", [True, False])
def test_either_route_reuses_the_answers_the_other_got(tmp_path: Path, first_batch: bool) -> None:
    ws, provider = PagesWorkspace(tmp_path / "ws"), PagesProvider()
    ws.run(*_clients(tmp_path, provider), use_batch=first_batch)
    sent = (len(provider.batches), len(provider.sync))
    assert sent == ((1, 0) if first_batch else (0, 4))
    again = ws.run(*_clients(tmp_path, provider), use_batch=not first_batch, rebuild=True)
    assert again["write"] == 4 and (len(provider.batches), len(provider.sync)) == sent


# ------------------------------------------------------------------ the estimate


def test_the_estimate_names_the_route_and_prices_the_batch_at_half(tmp_path: Path) -> None:
    ws = PagesWorkspace(tmp_path)
    write, update = FakeWriter(batch=True), FakeWriter("fake-update", batch=True)
    counter = CharTokenCounter()
    assert ws.run(write, update, estimate=True, count_tokens=counter)["batch"] is True
    assert ws.run(write, update, estimate=True, count_tokens=counter,
                  use_batch=False)["batch"] is False
    step = SERVED_STEPS.step(STEP_8)
    prices = {batch: Estimator(lambda _: partial(ws.run, write, update, use_batch=batch))(step)
              for batch in (False, True)}
    assert prices[False].usd > 0 and prices[True].usd == pytest.approx(prices[False].usd / 2)
    assert "batch price" in prices[True].counted and "batch" not in prices[False].counted
    assert write.calls == [] and update.calls == [] and not ws.artifact.original.exists()
