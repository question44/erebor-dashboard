"""GET /api/control/status and /os: device state for the cards.

One refresh reads the session environment and the desktop-mode state once, runs
the independent probes concurrently, and is shared by every client asking
within settings.STATUS_TTL seconds (one refresh at a time). Actions invalidate it.
"""

import platform
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from . import ambilight, display, modes, power, settings, system
from .access import User
from .display import DeviceStatus, ScreenStatus
from .power import ProfileStatus

router = APIRouter()
PROBES = ThreadPoolExecutor(max_workers=4, thread_name_prefix="probe")


class ControlStatus(BaseModel):
    display: DeviceStatus
    screen: ScreenStatus
    ambilight: DeviceStatus
    profile: ProfileStatus


class HostOS(BaseModel):
    state: Literal["nixos", "unknown"]


_cache: tuple[float, int, ControlStatus] | None = None
_generation = 0
_lock = threading.Lock()  # guards _cache and _generation
_refresh = threading.Lock()  # one probe at a time


def invalidate():
    global _cache, _generation
    with _lock:
        _cache, _generation = None, _generation + 1


def probe() -> ControlStatus:
    try:
        env = system.session_environment(timeout=2)
    except (OSError, subprocess.SubprocessError):
        env = None
    state = modes.modes_state()
    unknown = DeviceStatus(state="unknown", detail="Display power status is unavailable.")
    futures = {
        "display": PROBES.submit(display.display_status, env) if env else None,
        "screen": PROBES.submit(display.screen_status, env) if env else None,
        "ambilight": PROBES.submit(ambilight.ambilight_status, state),
        "profile": PROBES.submit(power.profile_status, state),
    }
    return ControlStatus(
        display=futures["display"].result() if env else unknown,
        screen=futures["screen"].result() if env else ScreenStatus(state="unknown", detail="Screen status is unavailable."),
        ambilight=futures["ambilight"].result(),
        profile=futures["profile"].result())


def current(ttl: float | None = None) -> ControlStatus:
    """A fresh-enough status; concurrent callers wait for the one refresh in flight."""
    global _cache
    ttl = settings.STATUS_TTL if ttl is None else ttl
    with _refresh:
        with _lock:
            if _cache and time.monotonic() - _cache[0] < ttl and _cache[1] == _generation:
                return _cache[2]
            generation = _generation
        result = probe()
        with _lock:
            # An action during the probe makes this result stale: do not keep it.
            if generation == _generation:
                _cache = (time.monotonic(), generation, result)
        return result


@router.get("/api/control/status", response_model=ControlStatus)
def read_control_status(user: User):
    return current()


@router.get("/api/control/os", response_model=HostOS)
def read_host_os(user: User):
    try:
        nixos = platform.freedesktop_os_release().get("ID") == "nixos"
    except OSError:
        nixos = False
    return HostOS(state="nixos" if nixos else "unknown")
