"""Same-origin guard: the server edits private data and has no login.

Two checks on every HTTP request, instead of the old ``Access-Control-Allow-Origin: *``:

- ``Host`` must be a name this server is known by. A web page that points its own domain at
  127.0.0.1 (DNS rebinding) arrives with that domain in ``Host`` and is refused.
- When the browser sends ``Origin`` (every cross-site ``fetch``/``EventSource``, every POST),
  it must be this server itself or a configured dev origin (the Vite dev server).

No CORS headers are ever sent, so another site can neither read a response nor write.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::"})


def allowed_hosts_for(bind_host: str) -> frozenset[str] | None:
    """Host names to accept for a server bound to ``bind_host``.

    ``None`` (no check) when it listens on every interface: the machine can then be reached
    under names this process cannot know.
    """
    if bind_host in WILDCARD_HOSTS:
        return None
    return LOOPBACK_HOSTS | {bind_host, f"[{bind_host}]"}


def _hostname(host_header: str) -> str:
    """Strip the port from a ``Host`` header value (``[::1]:8780`` -> ``[::1]``)."""
    if host_header.endswith("]") or ":" not in host_header:
        return host_header
    return host_header.rsplit(":", 1)[0]


class OriginGuard:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: frozenset[str] | None,
        allowed_origins: frozenset[str],
    ) -> None:
        self._app = app
        self._allowed_hosts = allowed_hosts
        self._allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host_header = headers.get("host", "")
        origin = headers.get("origin")
        if self._allowed_hosts is not None and _hostname(host_header) not in self._allowed_hosts:
            refusal = PlainTextResponse(f"Host {host_header!r} is not allowed", status_code=400)
        elif origin is not None and not self._origin_allowed(origin, scope["scheme"], host_header):
            refusal = PlainTextResponse(f"Origin {origin!r} is not allowed", status_code=403)
        else:
            await self._app(scope, receive, send)
            return
        await refusal(scope, receive, send)

    def _origin_allowed(self, origin: str, scheme: str, host_header: str) -> bool:
        return origin == f"{scheme}://{host_header}" or origin in self._allowed_origins
