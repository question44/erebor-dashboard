"""Actions through a system control daemon (a fake on a socket), and how refusals read."""

import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from control import errors, settings, sysd, system
from fixtures import HEADERS, capture_logs, isolated_store, setup_admin


class FakeSysd:
    """Answers each request with replies[command] (a list of lines) and records it."""

    def __init__(self, replies, hang=False):
        self.replies, self.hang = replies, hang
        self.requests = []
        directory = tempfile.mkdtemp()
        self.path = os.path.join(directory, "sysd.sock")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(8)
        self.release = threading.Event()
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
                if self.hang:
                    self.release.wait(5)
                    continue
                for line in self.replies.get(request["command"], [{"ok": False, "error": "unknown"}]):
                    stream.write((json.dumps(line) + "\n").encode())
                stream.flush()
            # Both closed: the client sees the end.

    def close(self):
        self.release.set()
        self.sock.close()


def not_approved(result):
    return [{"event": "approval-pending"},
            {"ok": False, "error": f"not approved ({result})", "code": "not-approved", "result": result}]


class ThroughSysd(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)
        self.fake = None

    def daemon(self, replies, **options):
        self.fake = FakeSysd(replies, **options)
        self.addCleanup(self.fake.close)
        patcher = patch.object(settings, "SYSD_SOCKET", self.fake.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.fake

    def post(self, action):
        return self.client.post("/api/control", headers=HEADERS, json={"action": action})

    @patch.object(system, "run")
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

    @patch.object(system, "run")
    def test_other_actions_still_run_directly(self, run):
        fake = self.daemon({})
        run.side_effect = ['[{"name":"DP-1","enabled":true}]', ""]
        with patch.object(system, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "umbriel"}):
            self.assertEqual(self.post("display-off").status_code, 200)
        self.assertEqual(fake.requests, [])
        self.assertEqual(run.call_count, 2)

    def test_every_approval_outcome_has_its_own_sentence(self):
        for result, code in [("denied", "approval-denied"), ("timeout", "approval-timeout"),
                             ("cancelled", "approval-cancelled"), ("no-method", "approval-unavailable"),
                             ("busy", "approval-busy"), ("rate-limited", "approval-rate-limited"),
                             ("forbidden", "approval-forbidden"), ("bad-request", "approval-bad-request"),
                             ("failed", "approval-failed"), ("something-new", "approval-failed")]:
            with self.subTest(result=result):
                self.daemon({"power-off": not_approved(result)})
                response = self.post("power-off")
                # Refusals stay 403, which clients show as they are.
                self.assertEqual((response.status_code, response.json()["code"]), (403, code))
                self.assertNotIn("not approved (", response.json()["error"].lower())
        self.daemon({"power-off": not_approved("no-method")})
        self.assertIn("No approval method is reachable right now", self.post("power-off").json()["error"])
        self.daemon({"power-off": not_approved("denied")})
        self.assertEqual(self.post("power-off").json(), {"error": "Approval was declined.", "code": "approval-denied"})

    def test_daemon_refusal_codes(self):
        for code, status, ours in [("forbidden", 403, "forbidden"), ("no-session", 503, "no-session"),
                                   ("session-timeout", 504, "session-timeout"), ("unavailable", 503, "unavailable"),
                                   ("bad-request", 502, "sysd-bad-request"), ("failed", 503, "sysd-failed"),
                                   (None, 503, "sysd-failed")]:
            with self.subTest(code=code):
                reply = {"ok": False, "error": "x"} | ({"code": code} if code else {})
                self.daemon({"sleep": [reply]})
                response = self.post("sleep")
                self.assertEqual((response.status_code, response.json()["code"]), (status, ours))
        self.daemon({"sleep": [{"ok": False, "error": "refused by policy", "code": "forbidden"}]})
        self.assertEqual(self.post("sleep").json(),
                         {"error": "The system policy does not allow this action.", "code": "forbidden"})

    def test_daemon_down_is_explained(self):
        with patch.object(settings, "SYSD_SOCKET", "/nonexistent/sysd.sock"):
            response = self.post("sleep")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "The system control daemon (erebor-sysd) isn't running.",
                                           "code": "sysd-down"})

    def test_socket_permission_names_the_group(self):
        def denied(self_, path):
            raise PermissionError(13, "Permission denied")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = os.path.join(directory.name, "sysd.sock")
        open(path, "w").close()
        with patch.object(settings, "SYSD_SOCKET", path), patch.object(socket.socket, "connect", denied):
            response = self.post("sleep")
        self.assertEqual((response.status_code, response.json()["code"]), (503, "sysd-permission"))
        self.assertIn("isn't in the", response.json()["error"])
        self.assertIn("log out and back in, or reboot", response.json()["error"])

    def test_no_answer_is_a_timeout(self):
        self.daemon({})
        with patch.object(sysd, "call", side_effect=sysd.NoAnswer("timed out")):
            response = self.post("sleep")
        self.assertEqual((response.status_code, response.json()["code"]), (504, "sysd-timeout"))

    def test_refusals_are_audited_without_secrets(self):
        logs = capture_logs(self, only={"dashboard.audit"})
        self.daemon({"power-off": not_approved("denied")})
        self.post("power-off")
        self.assertEqual(len(logs.lines), 1)
        line = logs.lines[0]
        for part in ["audit: action", "user=admin", "action=power-off", "via=sysd", "result=refused",
                     "code=approval-denied", "ms="]:
            self.assertIn(part, line)

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


class Explain(unittest.TestCase):
    def test_direct_failures(self):
        cases = [(subprocess.TimeoutExpired("umbriel", 2), 504, "timeout"),
                 (subprocess.CalledProcessError(1, "busctl"), 503, "failed"),
                 (FileNotFoundError("umbriel"), 503, "failed"),  # a missing command is not a missing daemon
                 (RuntimeError("internal"), 503, "failed"),
                 (errors.ActionError("no-desktop", "No supported desktop session is running."), 503, "no-desktop")]
        for exc, status, code in cases:
            with self.subTest(exc=exc):
                failure = errors.explain(exc)
                self.assertEqual((failure.status, failure.code, failure.unexpected), (status, code, False))
        self.assertTrue(errors.explain(KeyError("x")).unexpected)

    def test_unreachable_reasons(self):
        for error, code in [(PermissionError(), "sysd-permission"), (FileNotFoundError(), "sysd-down"),
                            (ConnectionRefusedError(), "sysd-down"), (OSError(), "sysd-unreachable")]:
            failure = errors.explain(sysd.Unreachable(sysd._reason(error), error))
            self.assertEqual(failure.code, code)


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

    def test_silent_daemon_times_out(self):
        fake = FakeSysd({}, hang=True)
        self.addCleanup(fake.close)
        with self.assertRaises(sysd.NoAnswer):
            sysd.call(fake.path, "sleep", timeout=0.2)

    def test_connect_errors_are_unreachable(self):
        with self.assertRaises(sysd.Unreachable) as e:
            sysd.call("/nonexistent/sysd.sock", "a")
        self.assertEqual(e.exception.reason, "missing")


if __name__ == "__main__":
    unittest.main()
