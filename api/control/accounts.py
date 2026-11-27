"""Sign-in, first-run setup and account management, with an audit record for each."""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from . import auth
from .access import Admin, SessionToken, User, catalog, client, remember, set_session_cookie, store
from .logs import audit
from .models import ControlResult, error

router = APIRouter()


class SessionUser(BaseModel):
    username: str
    admin: bool


class SessionState(BaseModel):
    setupRequired: bool
    user: SessionUser | None = None


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current: str
    password: str


class Permissions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modules: list[str] = []
    services: list[str] = []
    actions: list[str] = []


class NewAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str
    admin: bool = False
    permissions: Permissions = Permissions()


class AccountChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str | None = None
    admin: bool | None = None
    permissions: Permissions | None = None


def auth_failure(exc: auth.AuthError):
    return error(exc.status, str(exc))


def claimed(username) -> str:
    """An attempted username for the audit log (whatever the client sent, normalized when possible)."""
    try:
        return auth.normalize_username(username)
    except auth.AuthError:
        return str(username)[:32]


def outcome(exc: auth.AuthError) -> str:
    return {401: "failed", 403: "failed", 429: "locked"}.get(exc.status, "refused")


@router.get("/api/control/auth/session", response_model=SessionState)
def read_session(request: Request, response: Response, token: SessionToken = None):
    if store().setup_required():
        return SessionState(setupRequired=True)
    user, renewed = store().session(token)
    if renewed:
        set_session_cookie(request, response, token)
    remember(request, user and user["username"])
    return SessionState(setupRequired=False, user=user and SessionUser(username=user["username"], admin=user["admin"]))


@router.post("/api/control/auth/setup", response_model=SessionState)
def first_run_setup(credentials: Credentials, request: Request, response: Response):
    username = claimed(credentials.username)
    try:
        token = store().setup(credentials.username, credentials.password)
    except auth.AuthError as exc:
        audit("setup", user=username, result=outcome(exc), status=exc.status, client=client(request))
        return auth_failure(exc)
    audit("setup", user=username, result="ok", client=client(request))
    remember(request, username)
    set_session_cookie(request, response, token)
    return SessionState(setupRequired=False, user=SessionUser(username=username, admin=True))


@router.post("/api/control/auth/login", response_model=SessionState)
def sign_in(credentials: Credentials, request: Request, response: Response):
    username = claimed(credentials.username)
    try:
        token = store().login(credentials.username, credentials.password)
    except auth.AuthError as exc:
        audit("login", user=username, result=outcome(exc), client=client(request),
              retry_after=store().throttle.retry_after(username) or None)
        return auth_failure(exc)
    user, _ = store().session(token)
    audit("login", user=user["username"], result="ok", client=client(request))
    remember(request, user["username"])
    set_session_cookie(request, response, token)
    return SessionState(setupRequired=False, user=SessionUser(username=user["username"], admin=user["admin"]))


@router.post("/api/control/auth/logout", response_model=ControlResult)
def sign_out(request: Request, response: Response, token: SessionToken = None):
    username = store().logout(token)
    if username:
        audit("logout", user=username, client=client(request))
        remember(request, username)
    response.delete_cookie(auth.SESSION_COOKIE, path="/api/control")
    return ControlResult(message="Signed out")


@router.post("/api/control/auth/password", response_model=ControlResult)
def change_own_password(change: PasswordChange, user: User, request: Request):
    try:
        store().change_password(user["username"], change.current, change.password, request.state.token)
    except auth.AuthError as exc:
        audit("password-change", user=user["username"], result=outcome(exc), client=client(request))
        return auth_failure(exc)
    audit("password-change", user=user["username"], result="ok", client=client(request))
    return ControlResult(message="Password changed")


@router.get("/api/control/users")
def list_users(admin: Admin):
    return {"catalog": catalog().describe(), "users": store().users()}


@router.post("/api/control/users", response_model=ControlResult)
def create_user(account: NewAccount, admin: Admin):
    target = claimed(account.username)
    try:
        permissions = catalog().clean(account.permissions.model_dump())
        store().create(account.username, account.password, account.admin, permissions)
    except auth.AuthError as exc:
        audit("user-create", user=admin["username"], target=target, result="refused", status=exc.status)
        return auth_failure(exc)
    audit("user-create", user=admin["username"], target=target, result="ok", admin=account.admin,
          modules=",".join(permissions["modules"]), services=len(permissions["services"]),
          actions=",".join(permissions["actions"]))
    return ControlResult(message="User created")


@router.put("/api/control/users/{username}", response_model=ControlResult)
def update_user(username: str, change: AccountChange, admin: Admin, request: Request):
    changed = {"password": change.password is not None, "admin": change.admin,
               "permissions": change.permissions is not None}
    try:
        store().update(admin["username"], username, password=change.password, admin=change.admin,
                       permissions=None if change.permissions is None else catalog().clean(change.permissions.model_dump()),
                       keep_token=request.state.token if username == admin["username"] else None)
    except auth.AuthError as exc:
        audit("user-update", user=admin["username"], target=claimed(username), result="refused", status=exc.status)
        return auth_failure(exc)
    audit("user-update", user=admin["username"], target=username, result="ok", **changed)
    return ControlResult(message="User updated")


@router.delete("/api/control/users/{username}", response_model=ControlResult)
def delete_user(username: str, admin: Admin):
    try:
        store().delete(admin["username"], username)
    except auth.AuthError as exc:
        audit("user-delete", user=admin["username"], target=claimed(username), result="refused", status=exc.status)
        return auth_failure(exc)
    audit("user-delete", user=admin["username"], target=username, result="ok")
    return ControlResult(message="User deleted")
