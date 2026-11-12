"""The HTTP application: common endpoints, tool routers, guard, static files, status tool."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from review_tool.locks import ArtifactLockTimeout
from review_tool.registry import ToolRegistry, ToolSpec
from review_tool.settings import SETTINGS
from review_tool.status_tool import StatusTool
from review_tool.update_status import UpdateStatus
from tests.review_tool_support import BASE_URL, build_app, build_graph, write_file
from utils.json_io import write_json_file


def demo_spec() -> ToolSpec:
    """A tool that reads threads, writes claims and can fail in the two mapped ways."""
    router = APIRouter()

    @router.get("/ping")
    def ping() -> dict[str, str]:
        return {"pong": "demo"}

    @router.get("/needs-file")
    def needs_file() -> None:
        raise FileNotFoundError("claims.json")

    @router.post("/busy")
    def busy() -> None:
        raise ArtifactLockTimeout("claims.json is locked by another process")

    return ToolSpec("demo", "Demo", router, reads=("threads", "claims"), writes=("claims",))


def client_for(root: Path, **app_options: object) -> TestClient:
    return TestClient(build_app(root, **app_options), base_url=BASE_URL)


def build_dist(root: Path) -> None:
    (root / "dist" / "assets").mkdir(parents=True)
    (root / "dist" / "index.html").write_text("<html>review shell</html>", encoding="utf-8")
    (root / "dist" / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (root / "secret.txt").write_text("outside the build folder", encoding="utf-8")


# --------------------------------------------------------- common endpoints


def test_health_identifies_the_app_and_its_data_dir(tmp_path: Path) -> None:
    health = client_for(tmp_path).get("/api/health").json()
    assert health["app"] == "review_tool"
    assert health["data_dir"] == str(tmp_path)
    assert isinstance(health["pid"], int) and health["started_at"].endswith("+00:00")


def test_tools_lists_specs_and_missing_inputs(tmp_path: Path) -> None:
    client = client_for(tmp_path, specs=(demo_spec(),))
    assert client.get("/api/tools").json() == [
        {
            "name": "demo",
            "title": "Demo",
            "reads": ["threads", "claims"],
            "writes": ["claims"],
            "missing": ["threads", "claims"],
        }
    ]
    write_file(tmp_path / "threads.json", {})
    assert client.get("/api/tools").json()[0]["missing"] == ["claims"]


def test_tool_router_is_mounted_under_its_name(tmp_path: Path) -> None:
    client = client_for(tmp_path, specs=(demo_spec(),))
    assert client.get("/api/demo/ping").json() == {"pong": "demo"}


def test_artifacts_reports_exists_edited_and_stale_flags(tmp_path: Path) -> None:
    write_file(tmp_path / "export.json", {}, mtime=1000)
    write_file(tmp_path / "threads.json", {}, mtime=2000)
    write_file(tmp_path / "claims.json", {}, mtime=3000)
    write_file(tmp_path / "threads_edited.json", {}, mtime=4000)
    export, threads, claims = client_for(tmp_path).get("/api/artifacts").json()
    assert export == {
        "key": "export",
        "title": "Export",
        "step": None,
        "upstream": [],
        "has_edited_slot": False,
        "exists": True,
        "mtime": "1970-01-01T00:16:40.000+00:00",
        "edited_exists": False,
        "edited_mtime": None,
        "stale_because": [],
    }
    assert (threads["exists"], threads["edited_exists"], threads["stale_because"]) == (
        True,
        True,
        [],
    )
    assert (claims["exists"], claims["edited_exists"], claims["stale_because"]) == (
        True,
        False,
        ["threads"],
    )


def test_tool_with_unknown_artifact_key_is_rejected_at_startup(tmp_path: Path) -> None:
    spec = ToolSpec("demo", "Demo", APIRouter(), reads=("thraeds",))
    with pytest.raises(ValueError, match="tool 'demo'.*unknown artifact key 'thraeds'"):
        build_app(tmp_path, specs=(spec,))


@pytest.mark.parametrize("name", ["Demo", "1tool", "has space", "health", "events", "jobs"])
def test_registry_rejects_invalid_and_reserved_names(name: str) -> None:
    with pytest.raises(ValueError):
        ToolRegistry([ToolSpec(name, "Title", APIRouter())])


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry([demo_spec()])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(demo_spec())
    assert registry.names == ("demo",)


def test_tool_errors_have_one_shape(tmp_path: Path) -> None:
    client = client_for(tmp_path, specs=(demo_spec(),))
    missing = client.get("/api/demo/needs-file")
    assert missing.status_code == 409
    assert missing.json() == {
        "detail": "A file this needs is missing (claims.json). Run the step that makes it first."
    }
    locked = client.post("/api/demo/busy")
    assert locked.status_code == 423
    assert locked.json() == {"detail": "claims.json is locked by another process"}
    unknown = client.get("/api/demo/nope")
    assert (unknown.status_code, set(unknown.json())) == (404, {"detail"})


def test_lifespan_starts_and_stops_the_watcher(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.get("/api/health").status_code == 200


# ------------------------------------------------------------------- guard


def test_requests_from_other_origins_are_refused(tmp_path: Path) -> None:
    client = client_for(tmp_path, specs=(demo_spec(),))
    evil = {"origin": "http://evil.example"}
    assert client.get("/api/artifacts", headers=evil).status_code == 403
    assert client.post("/api/demo/busy", headers=evil).status_code == 403
    assert client.get("/api/events", headers={"origin": "null"}).status_code == 403


def test_same_origin_and_dev_origin_are_accepted(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert client.get("/api/health", headers={"origin": BASE_URL}).status_code == 200
    dev_origin = SETTINGS.dev_origins[0]
    response = client.get("/api/health", headers={"origin": dev_origin})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers  # never any CORS header


def test_unknown_host_header_is_refused(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    assert client.get("/api/health", headers={"host": "evil.example:8780"}).status_code == 400
    assert client.get("/api/health", headers={"host": "localhost:8780"}).status_code == 200


def test_host_check_is_off_when_listening_on_every_interface(tmp_path: Path) -> None:
    client = client_for(tmp_path, bind_host="0.0.0.0")
    assert client.get("/api/health", headers={"host": "my-laptop.lan:8780"}).status_code == 200
    evil = {"host": "my-laptop.lan:8780", "origin": "http://evil.example"}
    assert client.get("/api/health", headers=evil).status_code == 403


# ------------------------------------------------------------ static files


def test_built_app_is_served_at_root_and_for_tool_urls(tmp_path: Path) -> None:
    build_dist(tmp_path)
    client = client_for(tmp_path)
    for url in ("/", "/?tool=status", "/some/app/route"):
        response = client.get(url)
        assert (response.status_code, response.text) == (200, "<html>review shell</html>")
        assert response.headers["cache-control"] == "no-cache"
    asset = client.get("/assets/app.js")
    assert (asset.status_code, asset.text) == (200, "console.log(1)")


def test_missing_asset_and_unknown_api_path_are_404_json(tmp_path: Path) -> None:
    build_dist(tmp_path)
    client = client_for(tmp_path)
    assert client.get("/assets/gone.js").status_code == 404
    unknown = client.get("/api/nope")
    assert (unknown.status_code, unknown.json()) == (
        404,
        {"detail": "no such API endpoint: /api/nope"},
    )


def test_files_outside_the_build_folder_are_never_served(tmp_path: Path) -> None:
    build_dist(tmp_path)
    response = client_for(tmp_path).get("/assets/%2e%2e/%2e%2e/secret.txt")
    assert response.status_code == 404
    assert "outside the build folder" not in response.text


def test_without_a_build_the_page_explains_how_to_build(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    response = client.get("/?tool=status")
    assert response.status_code == 503 and "npm run build" in response.text
    assert client.get("/api/health").status_code == 200


# -------------------------------------------------------------- status tool


def status_client(root: Path) -> TestClient:
    graph = build_graph(root)
    update = UpdateStatus(root / "state.json", root / "running.json", (), graph)
    return client_for(root, graph=graph, specs=(StatusTool(graph, root, update).spec,))


def test_status_tool_reports_files_and_metadata_summary(tmp_path: Path) -> None:
    write_file(
        tmp_path / "claims.json",
        {"claims": [], "metadata": {"model": "m1", "count": 3, "inputs": {"t1": "x", "t2": "y"}}},
        mtime=3000,
    )
    detail = status_client(tmp_path).get("/api/status/artifacts/claims").json()
    assert detail == {
        "key": "claims",
        "files": [
            {
                "role": "original",
                "path": "claims.json",
                "exists": True,
                "mtime": "1970-01-01T00:50:00.000+00:00",
                "size": (tmp_path / "claims.json").stat().st_size,
            },
            {"role": "edited", "path": "claims_edited.json", "exists": False, "mtime": None,
             "size": None},
        ],
        "metadata": {"model": "m1", "count": 3, "inputs": {"count": 2}},
        "metadata_error": None,
    }  # fmt: skip


def test_status_tool_follows_the_file_on_disk(tmp_path: Path) -> None:
    client = status_client(tmp_path)
    assert client.get("/api/status/artifacts/claims").json()["metadata"] is None

    write_json_file({"metadata": {"count": 1}}, tmp_path / "claims.json")
    assert client.get("/api/status/artifacts/claims").json()["metadata"] == {"count": 1}

    write_json_file({"metadata": {"count": 2}}, tmp_path / "claims_edited.json")  # now effective
    assert client.get("/api/status/artifacts/claims").json()["metadata"] == {"count": 2}


def test_status_tool_reports_invalid_json_without_failing(tmp_path: Path) -> None:
    (tmp_path / "claims.json").write_text("{ not json", encoding="utf-8")
    detail = status_client(tmp_path).get("/api/status/artifacts/claims").json()
    assert detail["metadata"] is None and detail["metadata_error"].startswith("not valid JSON")


def test_status_tool_skips_the_raw_export_and_unknown_keys(tmp_path: Path) -> None:
    write_file(tmp_path / "export.json", {"metadata": {"private": True}})
    client = status_client(tmp_path)
    assert client.get("/api/status/artifacts/export").json()["metadata"] is None
    assert client.get("/api/status/artifacts/nope").status_code == 404
