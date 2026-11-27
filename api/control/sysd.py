"""Client for a system control daemon speaking JSON per line (erebor-sysd).

The daemon decides whether an action runs at once, waits for the owner's
approval (it then streams progress lines first) or is refused. Each request
names the dashboard as its origin and the signed-in account as the origin's
user; the daemon may require approval for that origin.
"""

import json
import socket

ORIGIN = "dashboard"


class SysdError(Exception):
    """A refusal: the daemon's final line had "ok": false."""

    def __init__(self, reply: dict):
        super().__init__(reply.get("error") or "failed")
        self.code = reply.get("code") or "failed"
        self.result = reply.get("result")


class Unreachable(OSError):
    """The socket could not be opened; `reason` is permission, missing, refused or other."""

    def __init__(self, reason: str, cause: OSError):
        super().__init__(f"{reason}: {cause}")
        self.reason = reason


class NoAnswer(TimeoutError):
    """Connected, but no final line arrived in time."""


def _reason(error: OSError) -> str:
    if isinstance(error, PermissionError):
        return "permission"
    if isinstance(error, FileNotFoundError):
        return "missing"
    if isinstance(error, ConnectionRefusedError):
        return "refused"
    if isinstance(error, TimeoutError):
        return "timeout"
    return "other"


def call(path: str, command: str, *, user: str | None = None, timeout: float = 90, **fields) -> dict:
    """Send one request and return the final reply; raise SysdError on a refusal."""
    request = {"command": command, "origin": ORIGIN, **fields}
    if user:
        request["origin_user"] = user
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(path)
        except OSError as error:
            raise Unreachable(_reason(error), error) from error
        stream = sock.makefile("rwb")
        try:
            stream.write((json.dumps(request) + "\n").encode())
            stream.flush()
            for raw in stream:
                line = json.loads(raw)
                if not isinstance(line, dict) or "ok" not in line:
                    continue  # approval progress
                if not line["ok"]:
                    raise SysdError(line)
                return line
        except TimeoutError as error:
            raise NoAnswer(str(error)) from error
    raise SysdError({"error": "connection closed", "code": "failed"})
