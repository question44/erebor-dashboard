"""Single-line, journald-friendly logging: ``logger: message key=value ...``.

Under systemd (JOURNAL_STREAM set) each line starts with a syslog priority
prefix, so ``journalctl -p warning`` works. Values are quoted when needed and
control characters are escaped, so a field can never start a new line.
Tracebacks appear only for unexpected exceptions, logged at ERROR.
"""

import json
import logging
import os
import sys
import threading
import time

log = logging.getLogger("dashboard")
audit_log = logging.getLogger("dashboard.audit")
access_log = logging.getLogger("dashboard.access")

PRIORITIES = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4, logging.ERROR: 3, logging.CRITICAL: 2}
MAX_VALUE = 1000


def value(v) -> str:
    """One key=value value: bare when safe, else a JSON string (escapes control characters)."""
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    text = str(v)
    if len(text) > MAX_VALUE:
        text = text[:MAX_VALUE - 1] + "…"
    if text and all(c.isprintable() and c not in ' "=\\' for c in text):
        return text
    return json.dumps(text, ensure_ascii=False)


def fields(**kv) -> str:
    return " ".join(f"{k}={value(v)}" for k, v in kv.items())


class LineFormatter(logging.Formatter):
    def __init__(self, journal: bool):
        super().__init__()
        self.journal = journal

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage().replace("\n", " ")
        kv = getattr(record, "kv", None)
        line = f"{record.name}: {message}" + (" " + fields(**kv) if kv else "")
        if self.journal:
            line = f"<{PRIORITIES.get(record.levelno, 6)}>{line}"
        else:
            line = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname} {line}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup(level: str = "INFO"):
    """Route the app's and uvicorn's loggers to one stderr handler."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(LineFormatter(journal=bool(os.environ.get("JOURNAL_STREAM"))))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, level, logging.INFO))
    # uvicorn's own access log is replaced by boundary.AccessLog.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True


def event(logger: logging.Logger, level: int, message: str, /, **kv):
    logger.log(level, message, extra={"kv": kv})


def audit(name: str, /, **kv):
    """An audit record: who did what and how it ended. Never pass secrets."""
    event(audit_log, logging.INFO, name, **kv)


class Once:
    """True the first time a key is seen and then at most once per `every` seconds,
    so a condition that repeats on every refresh is logged once an hour, not each minute."""

    def __init__(self, every: float = 3600, size: int = 256):
        self.every, self.size = every, size
        self.logged: dict[str, float] = {}
        self.lock = threading.Lock()

    def __call__(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            last = self.logged.get(key)
            if last is not None and now - last < self.every:
                return False
            self.logged[key] = now
            if len(self.logged) > self.size:
                for old in sorted(self.logged, key=self.logged.get)[: len(self.logged) - self.size]:
                    del self.logged[old]
            return True

    def reset(self):
        with self.lock:
            self.logged.clear()
