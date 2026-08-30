"""Unit tests for FlaskProxyMiddleware streaming behavior (HLD U17.21).

The proxy must relay responses incrementally so Server-Sent Events
(`/events/stream` for mail, `/app/chat/api/stream` for chat) reach the
browser without a page refresh. These tests drive the ASGI middleware
directly with a mock httpx backend — no Flask app or network involved.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.mcp import FlaskProxyMiddleware

BACKEND = "http://flask-backend:5001"


def _scope(path: str = "/app/chat/api/stream", method: str = "GET") -> dict[str, object]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": [
            (b"host", b"localhost:8001"),
            (b"accept", b"text/event-stream"),
            (b"cookie", b"session=abc"),
        ],
        "client": ("127.0.0.1", 54321),
        "server": ("localhost", 8001),
    }


class _Harness:
    """Records ASGI send messages; feeds receive from a queue."""

    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.queue: asyncio.Queue[Message] = asyncio.Queue()

    async def receive(self) -> Message:
        return await self.queue.get()

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    def body_bytes(self) -> bytes:
        return b"".join(m.get("body", b"") for m in self.sent if m["type"] == "http.response.body")

    def start(self) -> Message:
        starts = [m for m in self.sent if m["type"] == "http.response.start"]
        assert len(starts) == 1, f"expected exactly one response.start, got {starts}"
        return starts[0]


async def _noop_app(scope: Scope, receive: Receive, send: Send) -> None:  # pragma: no cover
    raise AssertionError("downstream app must not be called for proxied paths")


def _middleware(
    handler: Callable[[httpx.Request], Any], downstream: ASGIApp | None = None
) -> FlaskProxyMiddleware:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FlaskProxyMiddleware(downstream or _noop_app, BACKEND, client=client)


def _run(mw: FlaskProxyMiddleware, harness: _Harness, scope: dict[str, object]) -> None:
    async def scenario() -> None:
        client = mw._client
        assert client is not None
        async with client:
            await mw(scope, harness.receive, harness.send)

    asyncio.run(scenario())


class TestRouting:
    @pytest.mark.parametrize("prefix", ["/mcp", "/.well-known/oauth-protected-resource"])
    def test_handled_prefixes_bypass_proxy(self, prefix: str):
        seen: list[object] = []

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            seen.append(scope.get("path", ""))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def fail_handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError(f"backend must not be called for {prefix}")

        mw = _middleware(fail_handler, downstream=downstream)
        harness = _Harness()
        _run(mw, harness, _scope(path=prefix))
        assert seen == [prefix]
        assert harness.start()["status"] == 200


class TestStreaming:
    def test_sse_response_streams_incrementally(self):
        first_chunk_sent = asyncio.Event()
        release = asyncio.Event()

        async def upstream() -> AsyncIterator[bytes]:
            yield b"event: chat_ready\ndata: {}\n\n"
            first_chunk_sent.set()
            await release.wait()
            yield b"event: chat_sync\ndata: {}\n\n"

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/app/chat/api/stream"
            assert request.headers["cookie"] == "session=abc"
            return httpx.Response(
                200,
                headers={
                    "content-type": "text/event-stream",
                    "cache-control": "no-cache",
                    "content-length": "999",
                },
                content=upstream(),
            )

        mw = _middleware(handler)
        harness = _Harness()
        snapshot: list[Message] = []

        def scenario() -> None:
            async def run() -> None:
                client = mw._client
                assert client is not None
                async with client:
                    task = asyncio.create_task(mw(_scope(), harness.receive, harness.send))
                    await first_chunk_sent.wait()
                    # While the upstream generator is still open (waiting on
                    # `release`), the browser must already have headers + chunk 1.
                    snapshot.extend(harness.sent)
                    release.set()
                    await task

            asyncio.run(run())

        scenario()

        # Snapshot taken mid-stream: start + first event already delivered,
        # stream not yet complete (no final empty chunk).
        start = [m for m in snapshot if m["type"] == "http.response.start"]
        assert len(start) == 1
        body_chunks = [m for m in snapshot if m["type"] == "http.response.body"]
        assert any(b"chat_ready" in m.get("body", b"") for m in body_chunks)
        assert all(m.get("more_body") for m in body_chunks)
        assert not any(
            m["type"] == "http.response.body" and not m.get("more_body") for m in snapshot
        )

        # Final state: full payload relayed, terminated by an empty final chunk.
        assert b"chat_ready" in harness.body_bytes()
        assert b"chat_sync" in harness.body_bytes()
        assert harness.sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}

        # Hop-by-hop / length headers stripped, SSE headers preserved.
        headers = {k.decode().lower(): v.decode() for k, v in start[0]["headers"]}
        assert "content-length" not in headers
        assert headers["content-type"] == "text/event-stream"
        assert headers["cache-control"] == "no-cache"

    def test_client_disconnect_aborts_upstream(self):
        first_chunk_sent = asyncio.Event()
        upstream_closed = asyncio.Event()

        async def upstream() -> AsyncIterator[bytes]:
            try:
                yield b"event: chat_ready\ndata: {}\n\n"
                first_chunk_sent.set()
                await asyncio.Event().wait()  # hang: more events would follow
                yield b"event: chat_sync\ndata: {}\n\n"  # pragma: no cover
            finally:
                upstream_closed.set()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=upstream()
            )

        mw = _middleware(handler)
        harness = _Harness()

        async def scenario() -> None:
            client = mw._client
            assert client is not None
            async with client:
                task = asyncio.create_task(mw(_scope(), harness.receive, harness.send))
                await first_chunk_sent.wait()
                harness.queue.put_nowait({"type": "http.disconnect"})
                await asyncio.wait_for(task, timeout=5)
                assert upstream_closed.is_set(), "upstream generator must be closed on disconnect"

        asyncio.run(scenario())

        # No completion frame after the disconnect — the browser went away and
        # the (still-open) stream must not claim completion.
        assert not any(
            m["type"] == "http.response.body" and not m.get("more_body") for m in harness.sent
        )
        assert b"chat_ready" in harness.body_bytes()


class TestBufferedResponses:
    def test_json_response_relayed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, headers={"x-backend": "gunicorn"}, json={"rooms": [], "identity": None}
            )

        mw = _middleware(handler)
        harness = _Harness()
        _run(mw, harness, _scope(path="/app/chat/api/sync"))
        assert harness.start()["status"] == 200
        headers = {k.decode().lower(): v.decode() for k, v in harness.start()["headers"]}
        assert headers["content-type"].startswith("application/json")
        assert headers["x-backend"] == "gunicorn"
        assert "content-length" not in headers
        assert json.loads(harness.body_bytes()) == {"rooms": [], "identity": None}
        assert harness.sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}

    def test_post_body_and_headers_forwarded(self):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["body"] = request.content
            captured["cookie"] = request.headers.get("cookie")
            return httpx.Response(204)

        mw = _middleware(handler)
        harness = _Harness()
        harness.queue.put_nowait(
            {"type": "http.request", "body": b'{"text":"hello"}', "more_body": False}
        )
        _run(mw, harness, _scope(path="/app/chat/api/send", method="POST"))
        assert captured["method"] == "POST"
        assert captured["path"] == "/app/chat/api/send"
        assert captured["body"] == b'{"text":"hello"}'
        assert captured["cookie"] == "session=abc"
        assert harness.start()["status"] == 204

    def test_backend_unreachable_returns_structured_502(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        mw = _middleware(handler)
        harness = _Harness()
        _run(mw, harness, _scope(path="/app/chat/"))
        assert harness.start()["status"] == 502
        payload = json.loads(harness.body_bytes())
        assert payload["error"]["code"] == "BAD_GATEWAY"
        assert payload["error"]["message"]


class TestTimeouts:
    def test_proxy_read_timeout_disabled(self):
        # SSE streams idle between events; a read timeout would kill them.
        from app.mcp import _PROXY_TIMEOUT

        assert _PROXY_TIMEOUT.read is None
        assert _PROXY_TIMEOUT.connect is not None
