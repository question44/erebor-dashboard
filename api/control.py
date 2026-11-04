"""Loopback-only dashboard actions; Caddy supplies the private-network boundary."""

import ipaddress
import json
import os
import platform
import socket
import subprocess
import threading
from typing import Annotated, Literal, get_args
from urllib.parse import urlsplit
from urllib.request import Request as URLRequest, urlopen

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
import uvicorn


HOSTS = set(os.environ.get("DASHBOARD_HOSTS", "localhost,127.0.0.1").split(","))
ENABLED_ACTIONS = set(filter(None, os.environ.get("DASHBOARD_ACTIONS", "").split(",")))
ActionName = Literal["display-on", "display-off", "screen-dp", "screen-hdmi", "ambilight-on", "ambilight-off",
                     "sleep", "power-off", "reboot-windows"]
ACTIONS = set(get_args(ActionName))
AMBILIGHT_LOCK = threading.Lock()


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
                    run("umbriel", "msg", "dpms-" + state + ":" + output["name"], env=env)
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

        if scope["method"] == "POST":
            headers = Headers(scope=scope)
            if not same_origin(headers):
                await JSONResponse({"error": "Same-origin dashboard requests only"}, 403)(scope, receive, no_store)
                return
            if scope["path"] != "/api/control" or scope.get("query_string"):
                await JSONResponse({"error": "Unknown endpoint"}, 404)(scope, receive, no_store)
                return
            try:
                length = int(headers.get("content-length", "0"))
                if not 0 < length <= 256 or len(headers.getlist("content-length")) != 1:
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
                    if received > 256 or (not message.get("more_body", False) and received != length):
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
    return JSONResponse({"error": "Invalid action"}, 400)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": "Invalid action" if exc.status_code == 400 else str(exc.detail)},
                        exc.status_code, headers=exc.headers)


@app.get("/api/control/status", response_model=ControlStatus)
def read_control_status():
    return ControlStatus(display=display_status(), screen=screen_status(), ambilight=ambilight_status())


@app.get("/api/control/os", response_model=HostOS)
def read_host_os():
    try:
        nixos = platform.freedesktop_os_release().get("ID") == "nixos"
    except OSError:
        nixos = False
    return HostOS(state="nixos" if nixos else "unknown")


@app.post("/api/control", response_model=ControlResult,
          responses={code: {"model": ControlError} for code in [400, 403, 503]})
def execute_control(command: ControlCommand,
                    x_erebor_control: Annotated[Literal["1"], Header(alias="X-Erebor-Control")]):
    # FastAPI runs this synchronous handler in its thread pool.
    if command.action not in ENABLED_ACTIONS:
        return JSONResponse({"error": "Action is disabled"}, 403)
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
