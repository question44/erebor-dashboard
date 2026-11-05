"""Loopback-only dashboard actions; Caddy supplies the private-network boundary."""

import ipaddress
import json
import os
import platform
import re
import socket
import subprocess
import threading
from pathlib import Path
from typing import Annotated, Literal, get_args
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError
from urllib.request import Request as URLRequest, urlopen

from fastapi import Cookie, Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
import uvicorn

import auth


HOSTS = set(os.environ.get("DASHBOARD_HOSTS", "localhost,127.0.0.1").split(","))
ENABLED_ACTIONS = set(filter(None, os.environ.get("DASHBOARD_ACTIONS", "").split(",")))
ActionName = Literal["display-on", "display-off", "screen-dp", "screen-hdmi", "ambilight-on", "ambilight-off",
                     "sleep", "power-off", "reboot-windows"]
ACTIONS = set(get_args(ActionName))
# Needs a PIN, so it has its own endpoint instead of POST /api/control.
MOONSHINE_PAIR = "moonshine-pair"
MOONSHINE_URL = os.environ.get("DASHBOARD_MOONSHINE_URL", "http://127.0.0.1:47989").rstrip("/")
# Moonlight clients send this fixed ID; Moonshine logs any other ID it waits for.
MOONLIGHT_DEFAULT_ID = "0123456789ABCDEF"
PIN_WAIT = re.compile(r"Waiting for pin to be sent at \S*[?&]uniqueid=([0-9A-Fa-f]{1,16})\b")
AMBILIGHT_LOCK = threading.Lock()


def load_settings():
    path = os.environ.get("DASHBOARD_SETTINGS")
    if not path:
        return {}
    with open(path) as stream:
        return json.load(stream)


def state_path():
    base = (os.environ.get("DASHBOARD_STATE_DIR") or os.environ.get("STATE_DIRECTORY")
            or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "erebor-dashboard")
    return Path(base) / "users.sqlite3"


SETTINGS = load_settings()
_STORE = None
_STORE_LOCK = threading.Lock()


def store():
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = auth.Store(state_path())
        return _STORE


def catalog():
    return auth.Catalog(SETTINGS, ENABLED_ACTIONS)


def wled_state(state):
    url = os.environ["DASHBOARD_WLED_URL"].rstrip("/") + "/json/state"
    request = URLRequest(url, data=json.dumps(state).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        result = json.load(response)
    if not result.get("success"):
        raise RuntimeError("WLED rejected the power command")


def hyperhdr_leds(enabled):
    request = {"command": "componentstate", "componentstate": {
        "component": "LEDDEVICE", "state": enabled}, "tan": 1}
    with socket.create_connection(("127.0.0.1", 19444), timeout=5) as sock:
        sock.sendall((json.dumps(request) + "\n").encode())
        with sock.makefile("rb") as stream:
            response = json.loads(stream.readline(65536))
    if not response.get("success"):
        raise RuntimeError(response.get("error", "HyperHDR rejected the request"))


def run(*args, env=None, timeout=12):
    return subprocess.run(args, env=env, check=True, capture_output=True, text=True, timeout=timeout).stdout


def session_environment(timeout=12):
    env = os.environ.copy()
    # Refresh for each request: the service can outlive compositor logins.
    for line in run("systemctl", "--user", "show-environment", timeout=timeout).splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "NIRI_SOCKET",
                           "UMBRIEL_SOCKET", "MANGO_INSTANCE_SIGNATURE"}:
            env[key] = value
    return env


def action(name):
    if name.startswith("display-"):
        state = name.removeprefix("display-")
        env = session_environment()
        desktop = env.get("XDG_CURRENT_DESKTOP", "").lower()
        if desktop == "mango":
            monitors = json.loads(run("mmsg", "get", "all-monitors", env=env))["monitors"]
            outputs = [m["name"] for m in monitors if m["name"] not in {"ambilight", "sunshine"}]
            if not outputs:
                raise RuntimeError("No physical display found")
            for output in outputs:
                run("wlopm", "--" + state, output, env=env)
        elif desktop == "umbriel":
            outputs = json.loads(run("umbriel", "outputs", "--json", env=env))
            for output in outputs:
                # A screen switched away by umbriel-display-mode stays disabled.
                if output["name"] not in {"ambilight", "sunshine"} and output.get("enabled") is not False:
                    run("umbriel", "msg", "dpms-" + state, output["name"], env=env)
        elif desktop == "niri":
            run("niri", "msg", "action", "power-" + state + "-monitors", env=env)
        else:
            raise RuntimeError("No supported desktop session is running")
    elif name.startswith("screen-"):
        env = session_environment()
        if env.get("XDG_CURRENT_DESKTOP", "").lower() != "umbriel":
            raise RuntimeError("Screen switching needs an Umbriel session")
        run("umbriel-display-mode", name.removeprefix("screen-"), env=env)
    elif name.startswith("ambilight-"):
        # WLED can retain realtime output despite acknowledging power off.
        # Override it until explicitly enabled, preserving PipeWire capture.
        with AMBILIGHT_LOCK:
            if name == "ambilight-on":
                wled_state({"lor": 0, "on": True})
                hyperhdr_leds(True)
            else:
                hyperhdr_leds(False)
                wled_state({"on": False, "live": False, "lor": 2})
    elif name == "reboot-windows":
        run("systemctl", "--system", "--no-ask-password", "start",
            "dashboard-reboot-windows.service", timeout=65)
    elif name in {"sleep", "power-off"}:
        # login1 applies the existing wheel policy and respects inhibitors.
        method = "Suspend" if name == "sleep" else "PowerOff"
        run("busctl", "--system", "--allow-interactive-authorization=no", "call",
            "org.freedesktop.login1", "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager", method, "b", "false")
    else:
        raise ValueError("Unknown action")


def moonshine_pending():
    """Client IDs Moonshine is waiting on, from its recent log (newest last)."""
    output = run("journalctl", "--unit", "moonshine.service", "--since", "-3min",
                 "--output", "cat", "--no-pager", "--quiet", timeout=5)
    pending = []
    for line in output.splitlines():
        if match := PIN_WAIT.search(line):
            unique_id = match.group(1).upper()
            pending = [i for i in pending if i != unique_id] + [unique_id]
        elif "PIN registered successfully" in line:
            pending = []
    return pending


def moonshine_submit(unique_id, pin):
    request = URLRequest(MOONSHINE_URL + "/submit-pin", data=urlencode({"uniqueid": unique_id, "pin": pin}).encode(),
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status == 200
    except HTTPError as error:
        if error.code == 400:
            return False
        raise


def moonshine_pair(pin):
    """Give the PIN to the newest waiting client only, so other requests keep their state."""
    try:
        candidates = moonshine_pending()
    except (OSError, subprocess.SubprocessError):
        candidates = []
    for unique_id in dict.fromkeys([*reversed(candidates), MOONLIGHT_DEFAULT_ID]):
        # Moonshine returns 400 for IDs that are not waiting, and 200 for the first that is.
        if moonshine_submit(unique_id, pin):
            return True
    return False


class DeviceStatus(BaseModel):
    state: Literal["on", "off", "mixed", "unknown"]
    detail: str


class ScreenStatus(BaseModel):
    state: Literal["dp", "hdmi", "both", "none", "unknown"]
    detail: str


class ControlStatus(BaseModel):
    display: DeviceStatus
    screen: ScreenStatus
    ambilight: DeviceStatus


class HostOS(BaseModel):
    state: Literal["nixos", "unknown"]


def display_status():
    try:
        env = session_environment(timeout=2)
        desktop = env.get("XDG_CURRENT_DESKTOP", "").lower()
        if desktop == "umbriel":
            outputs = json.loads(run("umbriel", "outputs", "--json", env=env, timeout=2))
            physical = [o for o in outputs if o["name"] not in {"ambilight", "sunshine"}]
            # Only one screen is enabled at a time; the switched-away one is not "off".
            enabled = [o for o in physical if o.get("enabled") is not False]
            powers = [o.get("powered") for o in enabled] if enabled else [False for o in physical]
        elif desktop in {"mango", "niri"}:
            outputs = json.loads(run("wlopm", "--json", env=env, timeout=2))
            powers = [o["power-mode"] == "on" if o.get("power-mode") in {"on", "off"} else None
                      for o in outputs if o["output"] not in {"ambilight", "sunshine"}]
        else:
            return DeviceStatus(state="unknown", detail="No supported desktop session is running.")
        if not powers:
            return DeviceStatus(state="unknown", detail="No physical display found.")
        if any(type(power) is not bool for power in powers):
            return DeviceStatus(state="unknown", detail="Desktop did not report display power.")
        count, total = sum(powers), len(powers)
        if count == total:
            return DeviceStatus(state="on", detail=f"{total} display{'s' if total != 1 else ''} powered on by the desktop.")
        if count == 0:
            return DeviceStatus(state="off", detail=f"{total} display{'s' if total != 1 else ''} powered off by the desktop.")
        return DeviceStatus(state="mixed", detail=f"Desktop reports {count} of {total} displays powered on.")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return DeviceStatus(state="unknown", detail="Display power status is unavailable.")


SCREEN_DETAILS = {"dp": "Showing on DP-1; HDMI is disabled.", "hdmi": "Showing on HDMI; DP-1 is disabled.",
                  "both": "DP-1 and HDMI are both enabled.", "none": "No physical screen is enabled."}


def screen_status():
    try:
        env = session_environment(timeout=2)
        if env.get("XDG_CURRENT_DESKTOP", "").lower() != "umbriel":
            return ScreenStatus(state="unknown", detail="Screen switching needs an Umbriel session.")
        state = run("umbriel-display-mode", "status", env=env, timeout=2).strip()
        return ScreenStatus(state=state, detail=SCREEN_DETAILS[state])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return ScreenStatus(state="unknown", detail="Screen status is unavailable.")


def ambilight_status():
    try:
        url = os.environ["DASHBOARD_WLED_URL"].rstrip("/") + "/json/si"
        with urlopen(url, timeout=2) as response:
            wled = json.load(response)
        state, info = wled["state"], wled["info"]
        on, brightness, live, override = state["on"], state["bri"], info["live"], state["lor"]
        if (type(on) is not bool or type(live) is not bool
                or type(brightness) is not int or not 0 <= brightness <= 255
                or type(override) is not int or override not in {0, 1, 2}):
            raise ValueError("Missing WLED status")
        # Realtime data can light LEDs even when the normal WLED power flag is off.
        lights_on = (live and override == 0) or (on and brightness > 0)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return DeviceStatus(state="unknown", detail="LED controller status is unavailable.")
    try:
        with socket.create_connection(("127.0.0.1", 19444), timeout=2) as sock:
            sock.sendall(b'{"command":"serverinfo","tan":1}\n')
            with sock.makefile("rb") as stream:
                response = json.loads(stream.readline(1048576))
        if not response.get("success"):
            raise ValueError("HyperHDR status failed")
        components = {c["name"]: c["enabled"] for c in response["info"]["components"]}
        led_output = components["LEDDEVICE"]
        if type(led_output) is not bool or type(components.get("ALL", True)) is not bool:
            raise ValueError("Missing LED output status")
        led_output = led_output and components.get("ALL", True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration):
        return DeviceStatus(state="unknown", detail="Ambilight output status is unavailable.")
    if lights_on and led_output:
        detail = "Live LED stream active." if live and override == 0 else "Enabled; waiting for live LED data."
        return DeviceStatus(state="on", detail=detail)
    if not lights_on and not led_output:
        return DeviceStatus(state="off", detail="LEDs and Ambilight output are off.")
    detail = "Lights are on; Ambilight output is off." if lights_on else "Ambilight output is enabled; LEDs report off."
    return DeviceStatus(state="mixed", detail=detail)


class ControlCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ActionName


class ControlResult(BaseModel):
    message: str


class ControlError(BaseModel):
    error: str


def same_origin(headers: Headers) -> bool:
    try:
        origin = urlsplit(headers.get("origin", ""))
        hostname = origin.hostname or ""
        try:
            address = ipaddress.ip_address(hostname)
            trusted = address.is_private and not address.is_unspecified and not address.is_multicast
            if address.version == 4:
                trusted |= address in ipaddress.ip_network("100.64.0.0/10")
        except ValueError:
            trusted = hostname in HOSTS
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
    """Apply the existing browser boundary before FastAPI parses any body."""

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

        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            headers = Headers(scope=scope)
            if not same_origin(headers):
                await JSONResponse({"error": "Same-origin dashboard requests only"}, 403)(scope, receive, no_store)
                return
            if not scope["path"].startswith("/api/control") or scope.get("query_string"):
                await JSONResponse({"error": "Unknown endpoint"}, 404)(scope, receive, no_store)
                return
            # Actions are tiny; account bodies carry permission lists.
            limit = 256 if scope["path"] == "/api/control" else 4096
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


app = FastAPI(title="erebor control API", version="1.0.0", redirect_slashes=False,
              docs_url="/api/control/docs", redoc_url=None,
              openapi_url="/api/control/openapi.json")
app.add_middleware(DashboardBoundary)


@app.exception_handler(RequestValidationError)
async def invalid_command(request: Request, exc: RequestValidationError):
    return JSONResponse({"error": "Invalid action" if request.url.path == "/api/control" else "Invalid request"}, 400)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    invalid = "Invalid action" if request.url.path == "/api/control" else "Invalid request"
    return JSONResponse({"error": invalid if exc.status_code == 400 else str(exc.detail)},
                        exc.status_code, headers=exc.headers)


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
    return JSONResponse({"error": str(exc)}, exc.status)


def set_session_cookie(request: Request, response: Response, token: str):
    # Caddy replaces X-Forwarded-Proto; the API itself only listens on loopback.
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_SECONDS, path="/api/control",
                        httponly=True, samesite="strict",
                        secure=request.headers.get("x-forwarded-proto") == "https")


def signed_in(request: Request, response: Response,
              token: Annotated[str | None, Cookie(alias=auth.SESSION_COOKIE)] = None):
    user, renewed = store().session(token)
    if not user:
        raise HTTPException(401, "Sign in required")
    if renewed:
        set_session_cookie(request, response, token)
    request.state.token = token
    return user


def administrator(user: Annotated[dict, Depends(signed_in)]):
    if not user["admin"]:
        raise HTTPException(403, "Administrator access required")
    return user


User = Annotated[dict, Depends(signed_in)]
Admin = Annotated[dict, Depends(administrator)]


@app.get("/api/control/auth/session", response_model=SessionState)
def read_session(request: Request, response: Response,
                 token: Annotated[str | None, Cookie(alias=auth.SESSION_COOKIE)] = None):
    if store().setup_required():
        return SessionState(setupRequired=True)
    user, renewed = store().session(token)
    if renewed:
        set_session_cookie(request, response, token)
    return SessionState(setupRequired=False, user=user and SessionUser(username=user["username"], admin=user["admin"]))


@app.post("/api/control/auth/setup", response_model=SessionState)
def first_run_setup(credentials: Credentials, request: Request, response: Response):
    try:
        token = store().setup(credentials.username, credentials.password)
    except auth.AuthError as exc:
        return auth_failure(exc)
    set_session_cookie(request, response, token)
    return SessionState(setupRequired=False, user=SessionUser(username=auth.normalize_username(credentials.username), admin=True))


@app.post("/api/control/auth/login", response_model=SessionState)
def sign_in(credentials: Credentials, request: Request, response: Response):
    try:
        token = store().login(credentials.username, credentials.password)
    except auth.AuthError as exc:
        return auth_failure(exc)
    user, _ = store().session(token)
    set_session_cookie(request, response, token)
    return SessionState(setupRequired=False, user=SessionUser(username=user["username"], admin=user["admin"]))


@app.post("/api/control/auth/logout", response_model=ControlResult)
def sign_out(response: Response, token: Annotated[str | None, Cookie(alias=auth.SESSION_COOKIE)] = None):
    store().logout(token)
    response.delete_cookie(auth.SESSION_COOKIE, path="/api/control")
    return ControlResult(message="Signed out")


@app.post("/api/control/auth/password", response_model=ControlResult)
def change_own_password(change: PasswordChange, user: User, request: Request):
    try:
        store().change_password(user["username"], change.current, change.password, request.state.token)
    except auth.AuthError as exc:
        return auth_failure(exc)
    return ControlResult(message="Password changed")


@app.get("/api/control/config")
def read_config(user: User):
    """Dashboard settings reduced to what this account may see."""
    return {**catalog().view(user), "user": {"username": user["username"], "admin": user["admin"]}}


@app.get("/api/control/users")
def list_users(admin: Admin):
    return {"catalog": catalog().describe(), "users": store().users()}


@app.post("/api/control/users", response_model=ControlResult)
def create_user(account: NewAccount, admin: Admin):
    try:
        store().create(account.username, account.password, account.admin,
                       catalog().clean(account.permissions.model_dump()))
    except auth.AuthError as exc:
        return auth_failure(exc)
    return ControlResult(message="User created")


@app.put("/api/control/users/{username}", response_model=ControlResult)
def update_user(username: str, change: AccountChange, admin: Admin, request: Request):
    try:
        store().update(admin["username"], username, password=change.password, admin=change.admin,
                       permissions=None if change.permissions is None else catalog().clean(change.permissions.model_dump()),
                       keep_token=request.state.token if username == admin["username"] else None)
    except auth.AuthError as exc:
        return auth_failure(exc)
    return ControlResult(message="User updated")


@app.delete("/api/control/users/{username}", response_model=ControlResult)
def delete_user(username: str, admin: Admin):
    try:
        store().delete(admin["username"], username)
    except auth.AuthError as exc:
        return auth_failure(exc)
    return ControlResult(message="User deleted")


class PairingPin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pin: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}$")]


class PairingStatus(BaseModel):
    waiting: bool | None


def pairing_allowed(user):
    if MOONSHINE_PAIR not in ENABLED_ACTIONS or MOONSHINE_PAIR not in catalog().view(user)["actions"]:
        raise HTTPException(403, "Your account cannot pair Moonlight clients")


@app.get("/api/control/moonshine/pairing", response_model=PairingStatus)
def read_pairing(user: User):
    pairing_allowed(user)
    try:
        return PairingStatus(waiting=bool(moonshine_pending()))
    except (OSError, subprocess.SubprocessError):
        return PairingStatus(waiting=None)


@app.post("/api/control/moonshine/pair", response_model=ControlResult,
          responses={code: {"model": ControlError} for code in [400, 401, 403, 409, 503]})
def pair_moonlight(request: PairingPin, user: User):
    pairing_allowed(user)
    try:
        accepted = moonshine_pair(request.pin)
    except (OSError, ValueError) as exc:
        print(f"Moonshine pairing failed: {exc}", flush=True)
        return JSONResponse({"error": "Moonshine is not responding. Check moonshine.service."}, 503)
    if not accepted:
        return JSONResponse({"error": "No Moonlight client is waiting for this PIN. Start pairing in Moonlight, then enter the PIN it shows."}, 409)
    return ControlResult(message="PIN accepted. Finish pairing in Moonlight.")


@app.get("/api/control/status", response_model=ControlStatus)
def read_control_status(user: User):
    return ControlStatus(display=display_status(), screen=screen_status(), ambilight=ambilight_status())


@app.get("/api/control/os", response_model=HostOS)
def read_host_os(user: User):
    try:
        nixos = platform.freedesktop_os_release().get("ID") == "nixos"
    except OSError:
        nixos = False
    return HostOS(state="nixos" if nixos else "unknown")


@app.post("/api/control", response_model=ControlResult,
          responses={code: {"model": ControlError} for code in [400, 401, 403, 503]})
def execute_control(command: ControlCommand,
                    x_erebor_control: Annotated[Literal["1"], Header(alias="X-Erebor-Control")],
                    user: User):
    # FastAPI runs this synchronous handler in its thread pool.
    if command.action not in ENABLED_ACTIONS:
        return JSONResponse({"error": "Action is disabled"}, 403)
    if command.action not in catalog().view(user)["actions"]:
        return JSONResponse({"error": "Your account cannot use this action"}, 403)
    try:
        action(command.action)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"{command.action} failed: {exc}", flush=True)
        return JSONResponse({"error": "Action failed. Check the desktop session and control service log."}, 503)
    return ControlResult(message="Command sent")


def control_openapi():
    if app.openapi_schema is None:
        app.openapi_schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        # Validation keeps the existing 400 response contract rather than 422.
        app.openapi_schema["paths"]["/api/control"]["post"]["responses"].pop("422", None)
    return app.openapi_schema


app.openapi = control_openapi

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=18090, proxy_headers=False, server_header=False)
