"""The account store and the FastAPI dependencies that sign requests in."""

import threading
from typing import Annotated

from fastapi import Cookie, Depends, Request, Response
from starlette.exceptions import HTTPException

from . import auth, settings

_STORE = None
_STORE_LOCK = threading.Lock()


def store() -> auth.Store:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = auth.Store(settings.state_path())
        return _STORE


def catalog() -> auth.Catalog:
    return auth.Catalog(settings.SETTINGS, settings.ENABLED_ACTIONS)


def client(request: Request) -> str:
    """The browser's address as the local reverse proxy reports it (the API only listens on loopback)."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded[:64] or (request.client.host if request.client else "-")


def remember(request: Request, username: str | None):
    """Name the account in the request log line."""
    if username:
        request.state.username = username


def set_session_cookie(request: Request, response: Response, token: str):
    # Caddy replaces X-Forwarded-Proto; the API itself only listens on loopback.
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_SECONDS, path="/api/control",
                        httponly=True, samesite="strict",
                        secure=request.headers.get("x-forwarded-proto") == "https")


SessionToken = Annotated[str | None, Cookie(alias=auth.SESSION_COOKIE)]


def signed_in(request: Request, response: Response, token: SessionToken = None):
    user, renewed = store().session(token)
    if not user:
        raise HTTPException(401, "Sign in required")
    if renewed:
        set_session_cookie(request, response, token)
    request.state.token = token
    remember(request, user["username"])
    return user


def administrator(user: Annotated[dict, Depends(signed_in)]):
    if not user["admin"]:
        raise HTTPException(403, "Administrator access required")
    return user


User = Annotated[dict, Depends(signed_in)]
Admin = Annotated[dict, Depends(administrator)]

__all__ = ["Admin", "SessionToken", "User", "catalog", "client", "remember", "set_session_cookie", "signed_in",
           "store"]
