"""Actions through a system control daemon (a fake on a socket)."""

import json
import os
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import control
import sysd
from fixtures import HEADERS, isolated_store, setup_admin


class FakeSysd:
    """Answers each request with replies[command] (a list of lines) and records it."""

    def __init__(self, replies):
        self.replies = replies
        self.requests = []
        directory = tempfile.mkdtemp()
        self.path = os.path.join(directory, "sysd.sock")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(8)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn, conn.makefile("rwb") as stream:
                request = json.loads(stream.readline())
                self.requests.append(request)
                for line in self.replies.get(request["command"], [{"ok": False, "error": "unknown"}]):
                    stream.write((json.dumps(line) + "\n").encode())
                stream.flush()
            # Both closed: the client sees the end.

    def close(self):
        self.sock.close()


class ThroughSysd(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)
        self.fake = None

    def daemon(self, replies):
        self.fake = FakeSysd(replies)
        self.addCleanup(self.fake.close)
        patcher = patch.object(control, "SYSD_SOCKET", self.fake.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.fake

    def post(self, action):
        return self.client.post("/api/control", headers=HEADERS, json={"action": action})

    @patch.object(control, "run")
    def test_actions_go_through_the_daemon_with_origin_and_user(self, run):
        fake = self.daemon({"set-power-profile": [{"ok": True, "profile": "balanced"}],
                            "reboot-windows": [{"event": "approval-pending", "methods": ["phone"]}, {"ok": True}]})
        self.assertEqual(self.post("profile-balanced").json(), {"message": "Command sent"})
        self.assertEqual(self.post("reboot-windows").status_code, 200)
        self.assertEqual(fake.requests, [
            {"command": "set-power-profile", "origin": "dashboard", "profile": "balanced", "origin_user": "admin"},
            {"command": "reboot-windows", "origin": "dashboard", "origin_user": "admin"},
        ])
        run.assert_not_called()

    @patch.object(control, "run")
    def test_other_actions_still_run_directly(self, run):
        fake = self.daemon({})
        with patch.object(control, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "niri"}):
            self.assertEqual(self.post("display-off").status_code, 200)
        self.assertEqual(fake.requests, [])
        run.assert_called_once()

    def test_refusals(self):
        self.daemon({
            "power-off": [{"event": "approval-pending"}, {"ok": False, "error": "not approved (denied)",
                                                          "code": "not-approved", "result": "denied"}],
            "sleep": [{"ok": False, "error": "refused by policy", "code": "forbidden"}],
            "reboot-windows": [{"ok": False, "error": "x", "code": "failed"}],
        })
        r = self.post("power-off")
        self.assertEqual((r.status_code, r.json()), (403, {"error": "Not approved (denied)."}))
        r = self.post("sleep")
        self.assertEqual((r.status_code, r.json()), (403, {"error": "The system policy does not allow this action."}))
        self.assertEqual(self.post("reboot-windows").status_code, 503)

    def test_daemon_down_is_a_failure(self):
        with patch.object(control, "SYSD_SOCKET", "/nonexistent/sysd.sock"):
            self.assertEqual(self.post("sleep").status_code, 503)

    def test_config_marks_approval_actions(self):
        self.daemon({"list-actions": [{"ok": True, "caller": "local", "session": "up", "actions": {
            "set-power-profile": "none", "sleep": "none", "power-off": "approve",
            "reboot-windows": "approve", "get-power-profile": "none"}}]})
        view = self.client.get("/api/control/config", headers=HEADERS).json()
        self.assertEqual(view["approvals"], {"power-off": "approve", "reboot-windows": "approve"})
        self.assertEqual(self.fake.requests[0]["origin"], "dashboard")

    def test_config_without_daemon_has_no_approvals(self):
        view = self.client.get("/api/control/config", headers=HEADERS).json()
        self.assertNotIn("approvals", view)


class Client(unittest.TestCase):
    def test_progress_lines_are_skipped_and_errors_raised(self):
        fake = FakeSysd({"a": [{"event": "approval-pending"}, {"event": "approval-progress"}, {"ok": True, "x": 1}],
                         "b": [{"ok": False, "error": "nope", "code": "forbidden"}], "c": []})
        self.addCleanup(fake.close)
        self.assertEqual(sysd.call(fake.path, "a"), {"ok": True, "x": 1})
        with self.assertRaises(sysd.SysdError) as e:
            sysd.call(fake.path, "b")
        self.assertEqual(e.exception.code, "forbidden")
        with self.assertRaises(sysd.SysdError):
            sysd.call(fake.path, "c")
