import json
import subprocess
import unittest
from unittest.mock import patch

import control
from fixtures import HEADERS, isolated_store, setup_admin

BOARD = {"today": "2026-11-08", "counts": {"ACTIVE": 1, "OPEN": 2, "REVIEW": 0}, "overdue": 1,
         "epics": [{"id": "SYS-087", "title": "Epic", "status": "OPEN", "path": "tasks/SYS-087.md",
                    "overdue": False, "review": "2026-11-22", "done": 0, "total": 6}],
         "tasks": [{"id": "SYS-092", "title": "Board", "status": "OPEN", "path": "tasks/SYS-092.md",
                    "overdue": False, "review": "2026-12-13"},
                   {"id": "SYS-068", "title": "Accounts", "status": "ACTIVE", "path": "../../etc/passwd.md",
                    "overdue": True, "review": "2026-11-06", "extra": "dropped"}]}


def fake_run(output):
    return lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=output, stderr="")


class TaskBoard(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)
        for name, value in [("TASKS_COMMAND", ["erebor-tasks", "list", "--json"]),
                            ("TASKS_LINK_BASE", "https://git.example/blob/master/")]:
            patcher = patch.object(control, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_admin_reads_board_with_links(self):
        self.assertTrue(self.client.get("/api/control/config").json()["taskBoard"])
        with patch.object(control.subprocess, "run", fake_run(json.dumps(BOARD))):
            board = self.client.get("/api/control/tasks").json()
        self.assertEqual(board["counts"], BOARD["counts"])
        self.assertEqual(board["epics"][0]["done"], 0)
        self.assertEqual(board["epics"][0]["total"], 6)
        self.assertEqual(board["epics"][0]["url"], "https://git.example/blob/master/tasks/SYS-087.md")
        self.assertEqual(board["tasks"][0]["url"], "https://git.example/blob/master/tasks/SYS-092.md")
        self.assertNotIn("url", board["tasks"][1])  # path escaping the repository gets no link
        self.assertNotIn("extra", board["tasks"][1])

    def test_other_accounts_cannot_see_it(self):
        self.client.post("/api/control/users", headers=HEADERS,
                         json={"username": "guest", "password": "guest password",
                               "permissions": {"modules": ["services"]}})
        self.client.cookies.clear()
        self.client.post("/api/control/auth/login", headers=HEADERS,
                         json={"username": "guest", "password": "guest password"})
        self.assertNotIn("taskBoard", self.client.get("/api/control/config").json())
        with patch.object(control.subprocess, "run") as run:
            self.assertEqual(self.client.get("/api/control/tasks").status_code, 403)
        run.assert_not_called()
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/control/tasks").status_code, 401)

    def test_failures_and_unconfigured(self):
        with patch.object(control.subprocess, "run", fake_run("not json")):
            self.assertEqual(self.client.get("/api/control/tasks").status_code, 503)
        with patch.object(control.subprocess, "run", fake_run(json.dumps({**BOARD, "counts": {"BOGUS": 1}}))):
            self.assertEqual(self.client.get("/api/control/tasks").status_code, 503)
        with patch.object(control, "TASKS_COMMAND", None):
            self.assertEqual(self.client.get("/api/control/tasks").status_code, 404)
            self.assertNotIn("taskBoard", self.client.get("/api/control/config").json())
