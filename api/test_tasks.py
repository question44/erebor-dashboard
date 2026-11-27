import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from control import settings, tasks
from fixtures import HEADERS, capture_logs, isolated_store, setup_admin

BOARD = {"today": "2026-11-08", "counts": {"ACTIVE": 1, "OPEN": 2, "REVIEW": 0}, "overdue": 1,
         "epics": [{"id": "SYS-087", "title": "Epic", "status": "OPEN", "path": "tasks/SYS-087.md",
                    "overdue": False, "review": "2026-11-22", "done": 0, "total": 6}],
         "tasks": [{"id": "SYS-092", "title": "Board", "status": "OPEN", "path": "tasks/SYS-092.md",
                    "overdue": False, "review": "2026-12-13"},
                   {"id": "SYS-068", "title": "Accounts", "status": "ACTIVE", "path": "../../etc/passwd.md",
                    "overdue": True, "review": "2026-11-06", "extra": "dropped"}]}


def fake_run(output):
    return lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=output, stderr="")


def task(n, status="OPEN", **extra):
    return {"id": f"SYS-{n:03d}", "title": f"Task {n}", "status": status, "path": f"tasks/SYS-{n:03d}.md",
            "overdue": False, "review": "2026-12-01", **extra}


class TaskBoard(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)
        for name, value in [("TASKS_COMMAND", ["erebor-tasks", "list", "--json"]),
                            ("TASKS_LINK_BASE", "https://git.example/blob/master/")]:
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        tasks.once.reset()
        self.logs = capture_logs(self, only={"dashboard"})

    def board(self, raw):
        with patch.object(tasks.subprocess, "run", fake_run(raw if isinstance(raw, str) else json.dumps(raw))):
            return self.client.get("/api/control/tasks")

    def test_admin_reads_board_with_links(self):
        self.assertTrue(self.client.get("/api/control/config").json()["taskBoard"])
        board = self.board(BOARD).json()
        self.assertEqual(board["counts"], BOARD["counts"])
        self.assertEqual(board["epics"][0]["done"], 0)
        self.assertEqual(board["epics"][0]["total"], 6)
        self.assertEqual(board["epics"][0]["url"], "https://git.example/blob/master/tasks/SYS-087.md")
        self.assertEqual(board["tasks"][0]["url"], "https://git.example/blob/master/tasks/SYS-092.md")
        self.assertNotIn("url", board["tasks"][1])  # path escaping the repository gets no link
        self.assertNotIn("extra", board["tasks"][1])
        self.assertNotIn("today", board)
        # Without a producer list, the order and labels come from counts.
        self.assertEqual(board["statuses"], [{"id": "ACTIVE", "label": "Active"}, {"id": "OPEN", "label": "Open"},
                                             {"id": "REVIEW", "label": "Review"}])

    def test_ready_and_future_statuses_are_accepted(self):
        raw = copy.deepcopy(BOARD)
        raw["counts"] = {"ACTIVE": 1, "READY": 1, "OPEN": 1, "REVIEW": 0, "ON_HOLD": 1}
        raw["tasks"] += [task(200, "READY"), task(201, "ON_HOLD")]
        response = self.board(raw)
        self.assertEqual(response.status_code, 200)
        board = response.json()
        self.assertEqual(board["counts"]["READY"], 1)
        self.assertEqual([t["status"] for t in board["tasks"]][-2:], ["READY", "ON_HOLD"])
        self.assertEqual([s["id"] for s in board["statuses"]], ["ACTIVE", "READY", "OPEN", "REVIEW", "ON_HOLD"])
        self.assertEqual(board["statuses"][-1]["label"], "On hold")
        self.assertEqual(self.logs.lines, [])

    def test_producer_status_list_is_passed_through(self):
        raw = {**BOARD, "schema": 1, "statuses": [
            {"id": "REVIEW", "label": "In review"}, {"id": "OPEN", "label": "Open"}, {"id": "bad", "label": "x"},
            {"id": "ACTIVE", "label": "Doing", "colour": "red"}]}
        board = self.board(raw).json()
        self.assertEqual(board["statuses"], [{"id": "REVIEW", "label": "In review"}, {"id": "OPEN", "label": "Open"},
                                             {"id": "ACTIVE", "label": "Doing"}])

    def test_one_malformed_task_is_dropped_and_logged_once(self):
        raw = copy.deepcopy(BOARD)
        raw["tasks"] += [task(300, "lowercase"), "not a task", task(301, title="x" * 500), task(302)]
        for _ in range(3):
            response = self.board(raw)
            self.assertEqual(response.status_code, 200)
            self.assertEqual([t["id"] for t in response.json()["tasks"]], ["SYS-092", "SYS-068", "SYS-302"])
        dropped = [line for line in self.logs.lines if "entry dropped" in line]
        self.assertEqual(len(dropped), 3)  # once each, not once per refresh
        self.assertTrue(all("\n" not in line for line in self.logs.lines))
        self.assertIn("id=SYS-300 field=status", self.logs.text())

    def test_strict_mode_for_contract_checks(self):
        tasks.parse_board(BOARD, strict=True)
        for raw in [{**BOARD, "tasks": [task(1, "lower")]}, {**BOARD, "counts": {"bad": 1}},
                    {**BOARD, "tasks": [task(n) for n in range(1, 1002)]}]:
            with self.assertRaises(tasks.BoardShapeError):
                tasks.parse_board(raw, strict=True)

    def test_bad_counts_are_dropped(self):
        board = self.board({**BOARD, "counts": {"OPEN": 2, "bogus": 1, "READY": -1, "ACTIVE": "1"}}).json()
        self.assertEqual(board["counts"], {"OPEN": 2})

    def test_oversized_lists_are_truncated(self):
        raw = {**BOARD, "tasks": [task(n) for n in range(1, 1201)],
               "epics": [{**task(n), "done": 0, "total": 1} for n in range(1, 151)],
               "counts": {f"S{chr(65 + n // 26)}{chr(65 + n % 26)}": 1 for n in range(40)}}
        response = self.board(raw)
        self.assertEqual(response.status_code, 200)
        board = response.json()
        self.assertEqual((len(board["tasks"]), len(board["epics"])), (tasks.MAX_TASKS, tasks.MAX_EPICS))
        self.assertEqual(len(board["counts"]), tasks.MAX_STATUSES)
        self.assertLessEqual(len(board["statuses"]), tasks.MAX_STATUSES)
        self.assertIn("task board truncated", self.logs.text())

    def test_other_accounts_cannot_see_it(self):
        self.client.post("/api/control/users", headers=HEADERS,
                         json={"username": "guest", "password": "guest password",
                               "permissions": {"modules": ["services"]}})
        self.client.cookies.clear()
        self.client.post("/api/control/auth/login", headers=HEADERS,
                         json={"username": "guest", "password": "guest password"})
        self.assertNotIn("taskBoard", self.client.get("/api/control/config").json())
        with patch.object(tasks.subprocess, "run") as run:
            self.assertEqual(self.client.get("/api/control/tasks").status_code, 403)
        run.assert_not_called()
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/control/tasks").status_code, 401)

    def test_wrong_shape_fails_the_board(self):
        for raw in ["not json", "[]", {**BOARD, "tasks": {}}, {**BOARD, "counts": []}, {**BOARD, "overdue": "1"},
                    {k: v for k, v in BOARD.items() if k != "epics"}]:
            with self.subTest(raw=raw):
                response = self.board(raw)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"error": "Could not read the task board", "code": "tasks-unavailable"})

    def test_failures_are_logged_once_and_unconfigured(self):
        def fail(*args, **kwargs):
            raise subprocess.CalledProcessError(2, args[0], stderr="Traceback ...\nboom\n")
        with patch.object(tasks.subprocess, "run", fail):
            for _ in range(5):
                self.assertEqual(self.client.get("/api/control/tasks").status_code, 503)
        self.assertEqual(len(self.logs.lines), 1)
        self.assertIn("task board command failed", self.logs.lines[0])
        self.assertNotIn("\n", self.logs.lines[0])
        with patch.object(settings, "TASKS_COMMAND", None):
            self.assertEqual(self.client.get("/api/control/tasks").status_code, 404)
            self.assertNotIn("taskBoard", self.client.get("/api/control/config").json())


if __name__ == "__main__":
    unittest.main()
