"""Shared test setup: a temporary account store with a signed-in administrator."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import auth
import control

HEADERS = {"Host": "erebor.jzon.pl", "Origin": "https://erebor.jzon.pl",
           "Content-Type": "application/json", "X-Erebor-Control": "1"}
SETTINGS = {"title": "erebor", "description": "Test", "domainSuffix": ".jzon.pl",
            "wakeUrl": "https://kvm.example/wake", "outletUrl": "https://kvm.example/outlet",
            "actions": ["wake", *sorted(control.ACTIONS), "moonshine-pair"],
            "services": [{"id": "media", "label": "Media", "description": "", "icon": "media", "url": "https://m"},
                         {"id": "code", "label": "Code", "description": "", "icon": "code", "url": "https://c"}]}


def isolated_store(case):
    """Give a TestCase a fresh store and fast password hashing; returns a client."""
    directory = tempfile.TemporaryDirectory()
    case.addCleanup(directory.cleanup)
    for target, name, value in [(control, "_STORE", auth.Store(Path(directory.name) / "users.sqlite3")),
                                (control, "SETTINGS", SETTINGS),
                                (control, "HOSTS", {"localhost", "erebor.jzon.pl", "moria.jzon.pl"}),
                                (control, "ENABLED_ACTIONS", control.ACTIONS | {control.MOONSHINE_PAIR})]:
        patcher = patch.object(target, name, value)
        patcher.start()
        case.addCleanup(patcher.stop)
    client = TestClient(control.app)
    case.addCleanup(client.close)
    return client


def setup_admin(client, username="admin", password="correct horse"):
    response = client.post("/api/control/auth/setup", headers=HEADERS,
                           json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


# Keep hashing realistic but fast in tests.
auth.SCRYPT = {"n": 2 ** 10, "r": 8, "p": 1}
auth.DUMMY_HASH = auth.hash_password("dummy")
