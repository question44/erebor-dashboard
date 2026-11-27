"""Why an action did not run, as an HTTP status, a stable code and a sentence.

Responses are ``{"error": sentence, "code": code}``. Every refusal of the
system control daemon or of its approval service (approvald) has its own
code; codes are stable, sentences may change.
"""

import grp
import os
import subprocess
from dataclasses import dataclass

from . import compositors, settings, sysd

GENERIC = "Action failed. Check the desktop session and control service log."


class ActionError(Exception):
    """An expected failure with a message safe to show."""

    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Failure:
    status: int
    code: str
    message: str
    # Refused by policy or approval, as opposed to failed.
    refused: bool = False
    # A failure this module did not anticipate (logged with a traceback).
    unexpected: bool = False

    def body(self) -> dict:
        return {"error": self.message, "code": self.code}


# approvald's answer, which erebor-sysd reports as {"code": "not-approved", "result": ...}.
# All are 403: the action was refused, not lost, and clients show 403 messages as they are.
APPROVAL = {
    "denied": ("approval-denied", "Approval was declined."),
    "timeout": ("approval-timeout", "Nobody approved the request in time, so it expired. Try again and answer within a minute."),
    "cancelled": ("approval-cancelled", "The approval request was cancelled before it was answered."),
    "no-method": ("approval-unavailable", "No approval method is reachable right now (phone, security key, Mac). "
                  "Check that the approval agent runs in the desktop session and that an enrolled phone, "
                  "security key or Mac is available, then try again."),
    "busy": ("approval-busy", "Another approval request is still waiting. Answer it or let it expire, then try again."),
    "rate-limited": ("approval-rate-limited", "Too many approval requests in a short time. Wait a minute, then try again."),
    "forbidden": ("approval-forbidden", "The approval service does not accept requests from the system control daemon. "
                  "Check the approval configuration."),
    "bad-request": ("approval-bad-request", "The approval service rejected the request as malformed. "
                    "Check the control daemon's log."),
    "failed": ("approval-failed", "The approval could not be collected: the approval service is unreachable or "
               "every approval method failed. Check its log."),
}

# erebor-sysd's own refusal codes.
SYSD = {
    "forbidden": (403, "forbidden", "The system policy does not allow this action."),
    "no-session": (503, "no-session", "No desktop session is running, so the system control daemon cannot do this now."),
    "session-timeout": (504, "session-timeout", "The desktop session did not answer the system control daemon in time."),
    "unavailable": (503, "unavailable", "This action is not available on this machine."),
    "bad-request": (502, "sysd-bad-request", "The system control daemon did not understand the request "
                    "(are the dashboard and the daemon the same version?)."),
    "failed": (503, "sysd-failed", "The system control daemon could not complete the action. Check its log."),
}


def socket_group(path) -> str:
    try:
        return grp.getgrgid(os.stat(path).st_gid).gr_name
    except (OSError, KeyError, TypeError):
        return "socket's"


def explain(exc: BaseException) -> Failure:
    if isinstance(exc, ActionError):
        return Failure(exc.status, exc.code, str(exc))
    if isinstance(exc, sysd.SysdError):
        if exc.code == "not-approved":
            code, message = APPROVAL.get(exc.result or "failed", APPROVAL["failed"])
            return Failure(403, code, message, refused=True)
        status, code, message = SYSD.get(exc.code, SYSD["failed"])
        return Failure(status, code, message, refused=status == 403)
    if isinstance(exc, sysd.Unreachable):
        if exc.reason == "permission":
            group = socket_group(settings.SYSD_SOCKET)
            return Failure(503, "sysd-permission",
                           f"The dashboard can't reach the system control daemon: its user isn't in the {group} group "
                           "yet (log out and back in, or reboot).")
        if exc.reason in {"missing", "refused"}:
            return Failure(503, "sysd-down", "The system control daemon (erebor-sysd) isn't running.")
        return Failure(503, "sysd-unreachable", "The dashboard can't reach the system control daemon. Check its log.")
    if isinstance(exc, sysd.NoAnswer):
        return Failure(504, "sysd-timeout", "The system control daemon did not answer in time; "
                       "if the action needed approval, the approval timed out.")
    if isinstance(exc, subprocess.TimeoutExpired):
        return Failure(504, "timeout", "The command did not finish in time.")
    if isinstance(exc, compositors.Unsupported):
        return Failure(503, "unsupported", str(exc))
    if isinstance(exc, (subprocess.SubprocessError, OSError, ValueError, RuntimeError)):
        return Failure(503, "failed", GENERIC)
    return Failure(503, "failed", GENERIC, unexpected=True)
