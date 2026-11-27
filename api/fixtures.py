"""Shared test setup: a temporary account store with a signed-in administrator."""

import logging
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from control import access, auth, catalog, settings, status
from control.app import app

HOST = "dash.example.org"
HEADERS = {"Host": HOST, "Origin": f"https://{HOST}",
           "Content-Type": "application/json", "X-Erebor-Control": "1"}
SETTINGS = {"title": "Dashboard", "description": "Test", "domainSuffix": ".example.org",
            "wakeUrl": "https://kvm.example/wake", "outletUrl": "https://kvm.example/outlet",
            "actions": ["wake", *catalog.CONTROL_ACTIONS, "moonshine-pair"],
            "services": [{"id": "media", "label": "Media", "description": "", "icon": "media", "url": "https://m"},
                         {"id": "code", "label": "Code", "description": "", "icon": "code", "url": "https://c"}]}
ACTIONS = set(catalog.CONTROL_ACTIONS)


def patch_all(case, target, **values):
    for name, value in values.items():
        patcher = patch.object(target, name, value)
        patcher.start()
        case.addCleanup(patcher.stop)


def isolated_store(case):
    """Give a TestCase a fresh store and settings; returns a client."""
    directory = tempfile.TemporaryDirectory()
    case.addCleanup(directory.cleanup)
    patch_all(case, access, _STORE=auth.Store(Path(directory.name) / "users.sqlite3"))
    patch_all(case, settings, SETTINGS=SETTINGS, HOSTS={"localhost", HOST, "other.example.org"},
              ENABLED_ACTIONS=ACTIONS | {catalog.MOONSHINE_PAIR}, SYSD_SOCKET=None, TASKS_COMMAND=None,
              MODES_STATE=None)
    status.invalidate()
    case.addCleanup(status.invalidate)
    client = TestClient(app)
    case.addCleanup(client.close)
    return client


def setup_admin(client, username="admin", password="correct horse"):
    response = client.post("/api/control/auth/setup", headers=HEADERS,
                           json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


class Captured(logging.Handler):
    """Collects formatted lines from the dashboard loggers (all, or only those named)."""

    def __init__(self, only=None):
        super().__init__(logging.DEBUG)
        self.only = only
        from control.logs import LineFormatter
        self.setFormatter(LineFormatter(journal=False))
        self.lines: list[str] = []

    def emit(self, record):
        if self.only is None or record.name in self.only:
            self.lines.append(self.format(record))

    def text(self):
        return "\n".join(self.lines)


def capture_logs(case, level=logging.DEBUG, only=None) -> Captured:
    handler = Captured(only)
    logger = logging.getLogger("dashboard")
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(level)
    case.addCleanup(logger.removeHandler, handler)
    case.addCleanup(logger.setLevel, previous)
    return handler


# Keep hashing realistic but fast in tests.
auth.SCRYPT = {"n": 2 ** 10, "r": 8, "p": 1}
auth.DUMMY_HASH = auth.hash_password("dummy")
# Without a handler, warnings would go to stderr through logging's last resort.
logging.getLogger("dashboard").addHandler(logging.NullHandler())
logging.getLogger("dashboard").propagate = False
