"""Display power and the screen switcher, through the session's compositor adapter."""

import subprocess
from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints

from . import compositors, settings, system
from .errors import ActionError


class DeviceStatus(BaseModel):
    state: Literal["on", "off", "mixed", "unknown"]
    detail: str


class ScreenStatus(BaseModel):
    # A screen mode id (settings.SCREEN_MODES), "both", "none" or "unknown".
    state: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,32}$")]
    detail: str


def compositor(env) -> compositors.Compositor:
    adapter = compositors.for_session(env, system.run)
    if adapter is None:
        raise ActionError("no-desktop", "No supported desktop session is running.")
    return adapter


def physical(outputs):
    return [o for o in outputs if o.name not in settings.IGNORED_OUTPUTS]


def set_power(on: bool, env=None):
    adapter = compositor(env or system.session_environment())
    # A screen switched away by the screen switcher stays disabled.
    targets = [o for o in physical(adapter.outputs()) if o.enabled is not False]
    if not targets:
        raise ActionError("no-display", "No physical display found.")
    for output in targets:
        adapter.set_power(output.name, on)


def set_screen(mode: str, env=None):
    if mode not in settings.SCREEN_MODES:
        raise ActionError("unknown-screen", "That screen is not configured.", 400)
    compositor(env or system.session_environment()).set_screen_mode(mode)


def plural(n, word):
    return f"{n} {word}{'s' if n != 1 else ''}"


def display_status(env) -> DeviceStatus:
    try:
        adapter = compositors.for_session(env, system.run)
        if adapter is None:
            return DeviceStatus(state="unknown", detail="No supported desktop session is running.")
        outputs = physical(adapter.outputs(timeout=2))
        # A switched-away screen is not "off"; with every screen disabled, all are off.
        enabled = [o for o in outputs if o.enabled is not False]
        powers = [o.powered for o in enabled] if enabled else [False for _ in outputs]
        if not powers:
            return DeviceStatus(state="unknown", detail="No physical display found.")
        if any(power is None for power in powers):
            return DeviceStatus(state="unknown", detail="Desktop did not report display power.")
        count, total = sum(powers), len(powers)
        if count == total:
            return DeviceStatus(state="on", detail=f"{plural(total, 'display')} powered on by the desktop.")
        if count == 0:
            return DeviceStatus(state="off", detail=f"{plural(total, 'display')} powered off by the desktop.")
        return DeviceStatus(state="mixed", detail=f"Desktop reports {count} of {total} displays powered on.")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        return DeviceStatus(state="unknown", detail="Display power status is unavailable.")


def join(labels):
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def screen_detail(state) -> str:
    modes = settings.SCREEN_MODES
    if state == "none":
        return "No physical screen is enabled."
    if state == "both":
        labels = list(modes.values())
        return f"{join(labels)} are {'both' if len(labels) == 2 else 'all'} enabled."
    others = [label for mode, label in modes.items() if mode != state]
    return f"Showing on {modes[state]}" + (f"; {join(others)} {'is' if len(others) == 1 else 'are'} disabled." if others else ".")


def screen_status(env) -> ScreenStatus:
    try:
        adapter = compositors.for_session(env, system.run)
        if adapter is None:
            return ScreenStatus(state="unknown", detail="No supported desktop session is running.")
        state = adapter.screen_mode(timeout=2)
        if state not in {*settings.SCREEN_MODES, "both", "none"}:
            raise ValueError(f"unknown screen mode {state!r}")
        return ScreenStatus(state=state, detail=screen_detail(state))
    except compositors.Unsupported as exc:
        return ScreenStatus(state="unknown", detail=str(exc))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return ScreenStatus(state="unknown", detail="Screen status is unavailable.")
