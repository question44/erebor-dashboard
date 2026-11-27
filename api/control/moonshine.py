"""Moonlight pairing with Moonshine: hand a client's PIN to the waiting request.

The PIN is never logged.
"""

import logging
import re
import subprocess
import threading
import time
from typing import Annotated
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, StringConstraints
from starlette.exceptions import HTTPException

from . import settings, system
from .access import User, catalog
from .catalog import MOONSHINE_PAIR
from .logs import audit, event, log
from .models import ControlResult, error, responses

router = APIRouter()

# Moonlight clients send this fixed ID; Moonshine logs any other ID it waits for.
MOONLIGHT_DEFAULT_ID = "0123456789ABCDEF"
PIN_WAIT = re.compile(r"Waiting for pin to be sent at \S*[?&]uniqueid=([0-9A-Fa-f]{1,16})\b")
# Pairing status is polled by every open dashboard; share one journal read for a moment.
PENDING_TTL = 3
_pending_cache: tuple[float, list[str]] | None = None
_pending_lock = threading.Lock()


def read_pending() -> list[str]:
    """Client IDs Moonshine is waiting on, from its recent log (newest last)."""
    output = system.run("journalctl", "--unit", settings.MOONSHINE_UNIT, "--since", "-3min",
                        "--output", "cat", "--no-pager", "--quiet", timeout=5)
    pending = []
    for line in output.splitlines():
        if match := PIN_WAIT.search(line):
            unique_id = match.group(1).upper()
            pending = [i for i in pending if i != unique_id] + [unique_id]
        elif "PIN registered successfully" in line:
            pending = []
    return pending


def moonshine_pending(fresh=False) -> list[str]:
    global _pending_cache
    with _pending_lock:
        if not fresh and _pending_cache and time.monotonic() - _pending_cache[0] < PENDING_TTL:
            return list(_pending_cache[1])
        pending = read_pending()
        _pending_cache = (time.monotonic(), pending)
        return list(pending)


def forget_pending():
    global _pending_cache
    with _pending_lock:
        _pending_cache = None


def moonshine_submit(unique_id, pin) -> bool:
    request = URLRequest(settings.MOONSHINE_URL + "/submit-pin",
                         data=urlencode({"uniqueid": unique_id, "pin": pin}).encode(),
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=5) as response:
            return response.status == 200
    except HTTPError as error:
        if error.code == 400:
            return False
        raise


def moonshine_pair(pin) -> bool:
    """Give the PIN to the newest waiting client only, so other requests keep their state."""
    try:
        candidates = moonshine_pending(fresh=True)
    except (OSError, subprocess.SubprocessError):
        candidates = []
    try:
        for unique_id in dict.fromkeys([*reversed(candidates), MOONLIGHT_DEFAULT_ID]):
            # Moonshine returns 400 for IDs that are not waiting, and 200 for the first that is.
            if moonshine_submit(unique_id, pin):
                return True
        return False
    finally:
        forget_pending()


class PairingPin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pin: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}$")]


class PairingStatus(BaseModel):
    waiting: bool | None


def pairing_allowed(user):
    if MOONSHINE_PAIR not in settings.ENABLED_ACTIONS or MOONSHINE_PAIR not in catalog().view(user)["actions"]:
        raise HTTPException(403, "Your account cannot pair Moonlight clients")


@router.get("/api/control/moonshine/pairing", response_model=PairingStatus)
def read_pairing(user: User):
    pairing_allowed(user)
    try:
        return PairingStatus(waiting=bool(moonshine_pending()))
    except (OSError, subprocess.SubprocessError):
        return PairingStatus(waiting=None)


@router.post("/api/control/moonshine/pair", response_model=ControlResult, responses=responses(400, 401, 403, 409, 503))
def pair_moonlight(request: PairingPin, user: User):
    pairing_allowed(user)
    start = time.monotonic()

    def record(result, code=None):
        audit("moonshine-pair", user=user["username"], result=result, code=code,
              ms=round((time.monotonic() - start) * 1000))

    try:
        accepted = moonshine_pair(request.pin)
    except (OSError, ValueError) as exc:
        event(log, logging.WARNING, "moonshine pairing failed", error=f"{type(exc).__name__}: {exc}")
        record("failed", "moonshine-unreachable")
        return error(503, f"Moonshine is not responding. Check {settings.MOONSHINE_UNIT}.", "moonshine-unreachable")
    if not accepted:
        record("refused", "no-client")
        return error(409, "No Moonlight client is waiting for this PIN. Start pairing in Moonlight, "
                     "then enter the PIN it shows.", "no-client")
    record("ok")
    return ControlResult(message="PIN accepted. Finish pairing in Moonlight.")
