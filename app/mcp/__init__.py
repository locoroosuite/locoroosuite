from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os

import httpx
from flask import Flask
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app import create_app as _create_flask_app

_FLASK_BACKEND_URL = os.environ.get("FLASK_BACKEND_URL", "http://localhost:5001")

logger = logging.getLogger(__name__)

# Connect/write/pool timeouts apply, but reads never time out: proxied SSE
# streams (mail /events/stream, chat /app/chat/api/stream) stay idle between
# events, and the chat stream long-polls Matrix for up to 25s per cycle.
_PROXY_TIMEOUT = httpx.Timeout(10.0, read=None)

# Hop-by-hop or length/framing headers that must not be forwarded: the body is
# relayed decoded and re-chunked, so upstream lengths and encodings no longer
# describe what we send.
_STRIPPED_RESPONSE_HEADERS = ("transfer-encoding", "content-encoding", "content-length")


class ContentTypeNormalizeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method") == "POST":
            headers = list(scope.get("headers", []))
            new_headers: list[tuple[bytes, bytes]] = []
            for name, value in headers:
                if name == b"content-type" and value == b"application/octet-stream":
                    new_headers.append((name, b"application/json"))
                elif name == b"accept" and value in (b"*/*", b"application/octet-stream"):
                    new_headers.append((name, b"application/json, text/event-stream"))
                else:
                    new_headers.append((name, value))
            scope["headers"] = new_headers
        await self.app(scope, receive, send)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        from app.mcp.auth import set_current_request_host, set_current_token

        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            set_current_token(auth_header[7:])
        else:
            set_current_token("")

        set_current_request_host(request.headers.get("host", ""))

        response = await call_next(request)
        return response


def create_mcp_app() -> tuple[FastMCP, Flask]:
    flask_app = _create_flask_app()

    mcp = FastMCP(
        name="locoroomail",
        instructions=(
            "LocoRooSuite MCP server. Provides tools for email (IMAP), "
            "contacts (CardDAV), calendar (CalDAV), and documents. "
            "All tools require authentication via Bearer token (API key or OAuth JWT)."
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    from app.mcp.tools.calendar import register as register_calendar
    from app.mcp.tools.chat import register as register_chat
    from app.mcp.tools.contacts import register as register_contacts
    from app.mcp.tools.docs import register as register_docs
    from app.mcp.tools.mail import register as register_mail

    register_mail(mcp, flask_app)
    register_contacts(mcp, flask_app)
    register_calendar(mcp, flask_app)
    register_chat(mcp, flask_app)
    register_docs(mcp, flask_app)

    return mcp, flask_app


def _issuer_from_request(request: Request) -> str:
    host = request.headers.get("host", "")
    if not host:
        return "https://localhost"
    scheme = "https" if request.url.scheme == "https" or ":" not in host else "http"
    return f"{scheme}://{host}"


def _make_well_known_handler(scopes: list[str]):
    async def _protected_resource_metadata(request: Request) -> JSONResponse:
        issuer = _issuer_from_request(request)
        return JSONResponse(
            {
                "resource": issuer,
                "authorization_servers": [issuer],
                "bearer_methods_supported": ["header"],
                "scopes_supported": scopes,
            }
        )

    return _protected_resource_metadata


def _build_transport_security() -> TransportSecuritySettings:
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


async def _wait_for_disconnect(receive: Receive) -> None:
    """Block until the downstream client disconnects."""
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return


class FlaskProxyMiddleware:
    """Reverse-proxies non-MCP requests to the Flask backend.

    Responses are relayed incrementally (headers first, then body chunks as
    they arrive) so Server-Sent Events reach the browser in real time. An
    optional ``client`` (e.g. ``httpx.MockTransport`` in tests) can be
    injected; injected clients are owned by the caller and never closed here.
    """

    def __init__(
        self, app: ASGIApp, backend_url: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self.app = app
        self._backend_url = backend_url.rstrip("/")
        self._handled_prefixes = ("/mcp", "/.well-known/oauth-protected-resource")
        self._client = client

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self._handled_prefixes):
            await self.app(scope, receive, send)
            return

        await self._proxy_to_flask(scope, receive, send)

    async def _proxy_to_flask(self, scope: Scope, receive: Receive, send: Send) -> None:
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        query = scope.get("query_string", b"").decode()
        url = f"{self._backend_url}{path}"
        if query:
            url += f"?{query}"

        headers: dict[str, str] = {}
        body = b""
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            body_parts: list[bytes] = []
            while True:
                message = await receive()
                if message["type"] == "http.request":
                    body_parts.append(message.get("body", b""))
                    if not message.get("more_body", False):
                        break
                elif message["type"] == "http.disconnect":
                    return
            body = b"".join(body_parts)

        for name, value in scope.get("headers", []):
            key = name.decode()
            if key.lower() in ("transfer-encoding",):
                continue
            headers[key] = value.decode()

        client = self._client or httpx.AsyncClient(timeout=_PROXY_TIMEOUT)
        response_started = False
        try:
            async with client.stream(
                method,
                url,
                headers=headers,
                content=body,
                follow_redirects=False,
            ) as resp:
                response_headers: list[tuple[bytes, bytes]] = []
                for key, value in resp.headers.multi_items():
                    if key.lower() in _STRIPPED_RESPONSE_HEADERS:
                        continue
                    response_headers.append((key.encode(), value.encode()))
                await send(
                    {
                        "type": "http.response.start",
                        "status": resp.status_code,
                        "headers": response_headers,
                    }
                )
                response_started = True
                await self._relay_body(resp, receive, send, path)
        except httpx.RequestError:
            logger.warning(
                "proxy backend request failed path=%s method=%s url=%s",
                path,
                method,
                url,
                exc_info=True,
            )
            if not response_started:
                payload = json.dumps(
                    {
                        "error": {
                            "code": "BAD_GATEWAY",
                            "message": "App server unreachable; retry or check that the web app is running.",
                        }
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 502,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": payload})
        except Exception:
            # Most often the browser went away mid-stream (send on a closed
            # connection); nothing further can be delivered either way.
            logger.warning(
                "proxy relay aborted path=%s method=%s response_started=%s",
                path,
                method,
                response_started,
                exc_info=True,
            )
        finally:
            if client is not self._client:
                await client.aclose()

    async def _relay_body(
        self,
        resp: httpx.Response,
        receive: Receive,
        send: Send,
        path: str,
    ) -> None:
        """Stream the upstream body to the client; abort on disconnect.

        The relay task and a disconnect watcher race; whichever finishes first
        cancels the other. Cancelling the relay closes the upstream response
        (via the caller's stream context), releasing backend resources held by
        long-lived SSE generators.
        """

        async def pump() -> None:
            async for chunk in resp.aiter_bytes():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        relay = asyncio.create_task(pump())
        watcher = asyncio.create_task(_wait_for_disconnect(receive))
        try:
            done, _pending = await asyncio.wait(
                {relay, watcher}, return_when=asyncio.FIRST_COMPLETED
            )
            if watcher in done and relay not in done:
                logger.info("proxy client disconnected mid-stream path=%s", path)
        finally:
            for task in (relay, watcher):
                task.cancel()
        for task in (relay, watcher):
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_asgi_app():
    mcp, flask_app = create_mcp_app()

    with flask_app.app_context():
        from app.shared.oauth import _SCOPE_DESCRIPTIONS

        scopes = list(_SCOPE_DESCRIPTIONS.keys())

    mcp.settings.transport_security = _build_transport_security()

    mcp_starlette = mcp.streamable_http_app()
    mcp_starlette.add_middleware(ContentTypeNormalizeMiddleware)
    mcp_starlette.add_middleware(BearerTokenMiddleware)

    protected_resource = _make_well_known_handler(scopes)

    async def _mcp_status(request: Request) -> JSONResponse:
        from app.mcp.errors import health_check

        return JSONResponse(health_check(mcp))

    mcp_starlette.router.routes.insert(0, Route("/mcp/status", _mcp_status))
    mcp_starlette.router.routes.insert(
        0,
        Route(
            "/mcp/.well-known/oauth-protected-resource",
            protected_resource,
        ),
    )
    mcp_starlette.router.routes.insert(
        0,
        Route(
            "/.well-known/oauth-protected-resource",
            protected_resource,
        ),
    )

    wrapped = FlaskProxyMiddleware(mcp_starlette, _FLASK_BACKEND_URL)
    return wrapped
