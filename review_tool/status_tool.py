"""The pipeline status tool: the per-artifact details behind the dashboard.

The dashboard rows come from the common ``GET /api/artifacts``; this tool adds
``GET /api/status/artifacts/{key}``: the artifact's files (path, size, last change) and a
summary of the ``metadata`` block of the file the next step would read.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from review_tool.artifacts import ArtifactGraph, ArtifactNode
from review_tool.fs import stamp
from review_tool.registry import ToolSpec
from review_tool.stores import StoreHandle

MetadataSummary = dict[str, Any] | None


def _load_metadata_summary(node: ArtifactNode) -> MetadataSummary:
    """Summarise the top-level ``metadata`` of the node's effective file.

    Scalars are kept; lists and dicts are reduced to their size, so a large block (for
    example per-item fingerprints) never reaches the browser.
    """
    effective = node.effective
    if not effective.is_file():
        return None
    with effective.open(encoding="utf-8") as f:
        document = json.load(f)
    metadata = document.get("metadata") if isinstance(document, dict) else None
    if not isinstance(metadata, dict):
        return None
    return {
        name: {"count": len(value)} if isinstance(value, (dict, list)) else value
        for name, value in metadata.items()
    }


class StatusTool:
    NAME = "status"
    TITLE = "Status"

    def __init__(self, graph: ArtifactGraph, data_dir: Path) -> None:
        self._graph = graph
        self._data_dir = data_dir
        self._metadata_handles: dict[str, StoreHandle[MetadataSummary]] = {}
        router = APIRouter()
        router.add_api_route("/artifacts/{key}", self.artifact_detail, methods=["GET"])
        self.spec = ToolSpec(name=self.NAME, title=self.TITLE, router=router)

    def artifact_detail(self, key: str) -> dict[str, Any]:
        try:
            node = self._graph.node(key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=exc.args[0]) from None
        detail: dict[str, Any] = {
            "key": key,
            "files": [self._file_info(role, path) for role, path in node.files.items()],
            "metadata": None,
            "metadata_error": None,
        }
        if node.step is not None:  # the raw chat export has no metadata block to show
            try:
                detail["metadata"] = self._metadata_handle(node).get()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                detail["metadata_error"] = f"not valid JSON: {exc}"
        return detail

    def _metadata_handle(self, node: ArtifactNode) -> StoreHandle[MetadataSummary]:
        if node.key not in self._metadata_handles:
            self._metadata_handles[node.key] = StoreHandle(
                partial(_load_metadata_summary, node), node.files.values()
            )
        return self._metadata_handles[node.key]

    def _file_info(self, role: str, path: Path) -> dict[str, Any]:
        current = stamp(path)
        shown = path.relative_to(self._data_dir) if path.is_relative_to(self._data_dir) else path
        return {
            "role": role,
            "path": str(shown),
            "exists": current is not None,
            "mtime": current.iso if current else None,
            "size": current.size if current else None,
        }
