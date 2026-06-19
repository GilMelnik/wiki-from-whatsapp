"""Shared FastAPI reviewer server helpers."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Generic, TypeVar

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

T = TypeVar("T")


def make_reviewer_app(title: str) -> FastAPI:
    app = FastAPI(title=title, version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


class StoreRegistry(Generic[T]):
    """Thread-safe lazy store loader for reviewer UIs."""

    def __init__(self, store_cls: type[T], default_config: dict[str, Any]):
        self._store_cls = store_cls
        self._store: T | None = None
        self._config = dict(default_config)
        self._lock = threading.Lock()

    def configure(self, **kwargs: Any) -> None:
        with self._lock:
            self._store = None
            self._config.update(kwargs)

    def get(self) -> T:
        if self._store is not None and self._store.loaded:
            return self._store
        with self._lock:
            if self._store is None or not self._store.loaded:
                self._store = self._store_cls(**self._config)
                self._store.load()
            return self._store


def mount_static(app: FastAPI, static_dir: Path) -> None:
    if static_dir.is_dir() and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
