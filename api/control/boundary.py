"""The browser boundary (same origin, tiny bodies) and the request log, as ASGI middleware."""

import ipaddress
import logging
import time
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import settings
from .logs import access_log, event

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# Request body limits: actions are tiny, account bodies carry permission lists.
BODY_LIMITS = {"/api/control": 256, "/api/control/client-log": 2048}
DEFAULT_LIMIT = 4096
TAILNET = ipaddress.ip_network("100.64.0.0/10")


def same_origin(headers: Headers) -> bool:
    try:
        origin = urlsplit(headers.get("origin", ""))
        hostname = origin.hostname or ""
        try:
            address = ipaddress.ip_address(hostname)
            trusted = address.is_private and not address.is_unspecified and not address.is_multicast
            if address.version == 4:
                trusted |= address in TAILNET
        except ValueError:
            trusted = hostname in settings.HOSTS
        return bool(trusted and origin.scheme in {"http", "https"}
                    and origin.netloc == headers.get("host", "")
                    and not (origin.path or origin.query or origin.fragment)
                    and headers.get("x-erebor-control") == "1"
                    and headers.get("content-type") == "application/json"
                    and all(len(headers.getlist(name)) == 1 for name in
                            ["host", "origin", "x-erebor-control", "content-type"]))
    except ValueError:
        return False


class DashboardBoundary:
    """Apply the browser boundary before FastAPI parses any body; nothing is cached."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def no_store(message: Message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)

        if scope["method"] in MUTATING:
            headers = Headers(scope=scope)
            if not same_origin(headers):
                await JSONResponse({"error": "Same-origin dashboard requests only"}, 403)(scope, receive, no_store)
                return
            if not scope["path"].startswith("/api/control") or scope.get("query_string"):
                await JSONResponse({"error": "Unknown endpoint"}, 404)(scope, receive, no_store)
                return
            limit = BODY_LIMITS.get(scope["path"], DEFAULT_LIMIT)
            try:
                length = int(headers.get("content-length", "0"))
                lengths = len(headers.getlist("content-length"))
                if scope["method"] == "DELETE" and length == 0 and lengths <= 1:
                    pass  # Deletions are addressed by path and carry no body.
                elif not 0 < length <= limit or lengths != 1:
                    raise ValueError("Invalid request size")
            except ValueError:
                await JSONResponse({"error": "Invalid action"}, 400)(scope, receive, no_store)
                return
            received = 0
            original_receive = receive

            async def bounded_receive():
                nonlocal received
                message = await original_receive()
                if message["type"] == "http.request":
                    received += len(message.get("body", b""))
                    if received > limit or (not message.get("more_body", False) and received != length):
                        raise HTTPException(400, "Invalid action")
                return message

            receive = bounded_receive
        await self.app(scope, receive, no_store)


class AccessLog:
    """One line per request worth keeping: every mutating request and every non-2xx.

    Successful GETs (the dashboard polls several endpoints every few seconds) are
    logged only at DEBUG. The signed-in account is in scope["state"]["username"].
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.monotonic()
        state = scope.setdefault("state", {})
        status = 500

        async def record(message: Message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, record)
        finally:
            quiet = scope["method"] not in MUTATING and 200 <= status < 300
            level = logging.DEBUG if quiet else logging.WARNING if status >= 500 else logging.INFO
            if access_log.isEnabledFor(level):
                event(access_log, level, "request", method=scope["method"], path=scope["path"], status=status,
                      ms=round((time.monotonic() - start) * 1000), user=state.get("username"))
