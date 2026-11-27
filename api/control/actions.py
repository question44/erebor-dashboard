"""POST /api/control: run one catalog action, directly or through erebor-sysd.

Every action goes through perform(). When a system control daemon is configured,
the actions it owns (SYSD_ROUTES) are sent to it and its policy decides
(possibly after asking the owner to approve); everything else, and everything
without a daemon, runs directly with this service's own rights.
"""

import logging
import time
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from . import ambilight, display, power, settings, status, sysd
from .access import User, catalog
from .catalog import CONTROL_ACTIONS
from .errors import ActionError, explain
from .logs import audit, event, log
from .models import ControlResult, error, responses

router = APIRouter()

ActionName = Literal[CONTROL_ACTIONS]

# The direct path: each action's handler, run as the dashboard's user.
DIRECT = {
    "display-on": lambda: display.set_power(True),
    "display-off": lambda: display.set_power(False),
    "screen-dp": lambda: display.set_screen("dp"),
    "screen-hdmi": lambda: display.set_screen("hdmi"),
    "ambilight-on": lambda: ambilight.set_ambilight(True),
    "ambilight-off": lambda: ambilight.set_ambilight(False),
    "profile-performance": lambda: power.set_profile("performance"),
    "profile-balanced": lambda: power.set_profile("balanced"),
    "profile-power-saver": lambda: power.set_profile("power-saver"),
    "sleep": lambda: power.login1("Suspend"),
    "power-off": lambda: power.login1("PowerOff"),
    "reboot-windows": power.reboot_windows,
}

# The daemon path: action -> (daemon command, request fields).
SYSD_ROUTES = {
    **{f"profile-{p}": ("set-power-profile", {"profile": p}) for p in power.PROFILES},
    "sleep": ("sleep", {}),
    "power-off": ("power-off", {}),
    "reboot-windows": ("reboot-windows", {}),
}


def via_sysd(name: str) -> bool:
    return bool(settings.SYSD_SOCKET) and name in SYSD_ROUTES


def perform(name: str, user: str | None = None):
    """Run one action; raises on failure (see errors.explain)."""
    if via_sysd(name):
        command, fields = SYSD_ROUTES[name]
        sysd.call(settings.SYSD_SOCKET, command, user=user, **fields)
    elif name in DIRECT:
        DIRECT[name]()
    else:
        raise ActionError("unknown-action", "Unknown action.", 400)


def approvals(user) -> dict:
    """Actions the daemon will ask the owner to approve from this dashboard."""
    if not settings.SYSD_SOCKET:
        return {}
    try:
        levels = sysd.call(settings.SYSD_SOCKET, "list-actions", user=user["username"], timeout=5)["actions"]
    except (OSError, ValueError, KeyError, TypeError, sysd.SysdError):
        return {}
    return {name: levels.get(command) for name, (command, _) in SYSD_ROUTES.items()
            if levels.get(command) == "approve"}


class ControlCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: ActionName


@router.post("/api/control", response_model=ControlResult, responses=responses(400, 401, 403, 409, 429, 502, 503, 504))
def execute_control(command: ControlCommand, user: User):
    # FastAPI runs this synchronous handler in its thread pool.
    name, username = command.action, user["username"]
    if name not in settings.ENABLED_ACTIONS:
        audit("action", user=username, action=name, result="refused", code="disabled")
        return error(403, "Action is disabled", "disabled")
    if name not in catalog().view(user)["actions"]:
        audit("action", user=username, action=name, result="refused", code="not-permitted")
        return error(403, "Your account cannot use this action", "not-permitted")
    start = time.monotonic()
    path = "sysd" if via_sysd(name) else "direct"
    try:
        perform(name, username)
    except Exception as exc:
        failure = explain(exc)
        if failure.unexpected:
            log.exception("action %s failed unexpectedly", name)
        else:
            event(log, logging.WARNING, "action failed", action=name, via=path, code=failure.code,
                  error=f"{type(exc).__name__}: {exc}")
        audit("action", user=username, action=name, via=path, result="refused" if failure.refused else "failed",
              code=failure.code, ms=round((time.monotonic() - start) * 1000))
        return error(failure.status, failure.message, failure.code)
    finally:
        status.invalidate()
    audit("action", user=username, action=name, via=path, result="ok", ms=round((time.monotonic() - start) * 1000))
    return ControlResult(message="Command sent")
