"""Ambilight: a WLED LED controller fed by HyperHDR (JSON API, default port 19444)."""

import json
import socket
import threading
from urllib.request import Request, urlopen

from . import settings
from .display import DeviceStatus
from .errors import ActionError
from .modes import mode_holding

LOCK = threading.Lock()


def wled_url(path):
    if not settings.WLED_URL:
        raise ActionError("not-configured", "No LED controller is configured.")
    return settings.WLED_URL + path


def wled_state(state):
    request = Request(wled_url("/json/state"), data=json.dumps(state).encode(),
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            result = json.load(response)
    except OSError as exc:
        raise ActionError("wled-unreachable", "The LED controller (WLED) is not responding.") from exc
    if not result.get("success"):
        raise ActionError("wled-rejected", "The LED controller (WLED) rejected the command.")


def hyperhdr(request: dict, timeout, limit=65536) -> dict:
    with socket.create_connection(settings.HYPERHDR, timeout=timeout) as sock:
        sock.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
        with sock.makefile("rb") as stream:
            return json.loads(stream.readline(limit))


def hyperhdr_leds(enabled):
    try:
        response = hyperhdr({"command": "componentstate", "componentstate": {
            "component": "LEDDEVICE", "state": enabled}, "tan": 1}, timeout=5)
    except OSError as exc:
        raise ActionError("hyperhdr-unreachable", "HyperHDR is not responding.") from exc
    if not response.get("success"):
        raise ActionError("hyperhdr-rejected", "HyperHDR rejected the command.")


def set_ambilight(on: bool):
    # WLED can retain realtime output despite acknowledging power off.
    # Override it until explicitly enabled, preserving PipeWire capture.
    with LOCK:
        if on:
            wled_state({"lor": 0, "on": True})
            hyperhdr_leds(True)
        else:
            hyperhdr_leds(False)
            wled_state({"on": False, "live": False, "lor": 2})


def ambilight_status(modes=None) -> DeviceStatus:
    status = device_status()
    mode = mode_holding(modes, "ambilight")
    if mode:
        status.detail = f"{status.detail} Dimmed by {mode}."
    return status


def device_status() -> DeviceStatus:
    try:
        with urlopen(wled_url("/json/si"), timeout=2) as response:
            wled = json.load(response)
        state, info = wled["state"], wled["info"]
        on, brightness, live, override = state["on"], state["bri"], info["live"], state["lor"]
        if (type(on) is not bool or type(live) is not bool
                or type(brightness) is not int or not 0 <= brightness <= 255
                or type(override) is not int or override not in {0, 1, 2}):
            raise ValueError("Missing WLED status")
        # Realtime data can light LEDs even when the normal WLED power flag is off.
        lights_on = (live and override == 0) or (on and brightness > 0)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ActionError):
        return DeviceStatus(state="unknown", detail="LED controller status is unavailable.")
    try:
        response = hyperhdr({"command": "serverinfo", "tan": 1}, timeout=2, limit=1048576)
        if not response.get("success"):
            raise ValueError("HyperHDR status failed")
        components = {c["name"]: c["enabled"] for c in response["info"]["components"]}
        led_output = components["LEDDEVICE"]
        if type(led_output) is not bool or type(components.get("ALL", True)) is not bool:
            raise ValueError("Missing LED output status")
        led_output = led_output and components.get("ALL", True)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return DeviceStatus(state="unknown", detail="Ambilight output status is unavailable.")
    if lights_on and led_output:
        detail = "Live LED stream active." if live and override == 0 else "Enabled; waiting for live LED data."
        return DeviceStatus(state="on", detail=detail)
    if not lights_on and not led_output:
        return DeviceStatus(state="off", detail="LEDs and Ambilight output are off.")
    detail = "Lights are on; Ambilight output is off." if lights_on else "Ambilight output is enabled; LEDs report off."
    return DeviceStatus(state="mixed", detail=detail)
