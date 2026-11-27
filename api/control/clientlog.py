"""POST /api/control/client-log: frontend errors, into the service log.

Signed-in accounts only, behind the same browser boundary (body ≤ 2 KiB),
rate limited per account and overall; fields are truncated and control
characters dropped before they are logged at WARNING.
"""

import logging
import threading
import time
from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .access import User
from .logs import event
from .models import ControlResult, error, responses

router = APIRouter()
client_log = logging.getLogger("dashboard.client")

Text = Annotated[str, StringConstraints(max_length=2000)]
Position = Annotated[int, Field(ge=0, le=10_000_000)]
LIMITS = {"message": 300, "source": 200, "stack": 600}


class ClientReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: Text
    kind: Literal["error", "unhandledrejection", "http", "other"] = "error"
    source: Text | None = None
    line: Position | None = None
    column: Position | None = None
    stack: Text | None = None


def sanitize(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = "".join(c if c.isprintable() else " " for c in text).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


class RateLimit:
    """Token buckets: `burst` reports at once, refilled at `per_minute`; one per key plus a global one."""

    def __init__(self, burst=5, per_minute=10, global_per_minute=60, keys=64):
        self.burst, self.rate, self.keys = burst, per_minute / 60, keys
        self.global_rate = global_per_minute / 60
        self.buckets: dict[str, tuple[float, float]] = {}
        self.overall = (float(global_per_minute), time.monotonic())
        self.global_burst = float(global_per_minute)
        self.lock = threading.Lock()

    @staticmethod
    def _take(bucket, rate, burst, now):
        tokens, stamp = bucket
        tokens = min(burst, tokens + (now - stamp) * rate)
        return (tokens - 1, now) if tokens >= 1 else None, (tokens, now)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            taken_all, kept_all = self._take(self.overall, self.global_rate, self.global_burst, now)
            bucket = self.buckets.get(key, (float(self.burst), now))
            taken, kept = self._take(bucket, self.rate, self.burst, now)
            if taken_all is None or taken is None:
                self.overall, self.buckets[key] = kept_all, kept
                return False
            self.overall, self.buckets[key] = taken_all, taken
            if len(self.buckets) > self.keys:
                self.buckets.pop(next(iter(self.buckets)))
            return True


LIMIT = RateLimit()


@router.post("/api/control/client-log", response_model=ControlResult, responses=responses(400, 401, 429))
def report(entry: ClientReport, user: User):
    if not LIMIT.allow(user["username"]):
        return error(429, "Too many client reports", "rate-limited")
    event(client_log, logging.WARNING, "frontend error", user=user["username"], kind=entry.kind,
          message=sanitize(entry.message, LIMITS["message"]), source=sanitize(entry.source, LIMITS["source"]),
          line=entry.line, column=entry.column, stack=sanitize(entry.stack, LIMITS["stack"]))
    return ControlResult(message="Logged")
