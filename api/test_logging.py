"""Log lines: single-line records, the request log, audit records and frontend reports."""

import logging
import unittest
from unittest.mock import patch

from control import actions, clientlog, logs, moonshine
from fixtures import HEADERS, capture_logs, isolated_store, setup_admin


class Format(unittest.TestCase):
    def line(self, message, journal=False, **kv):
        record = logging.LogRecord("dashboard.test", logging.WARNING, __file__, 1, message, None, None)
        record.kv = kv
        return logs.LineFormatter(journal).format(record)

    def test_values_are_quoted_and_cannot_break_the_line(self):
        line = self.line("hello\nworld", user="a b", path="/x\n<1>forged", n=3, flag=True, none=None)
        self.assertNotIn("\n", line)
        self.assertIn('user="a b"', line)
        self.assertIn('path="/x\\n<1>forged"', line)
        self.assertIn("n=3 flag=yes none=-", line)

    def test_journal_priority_prefix(self):
        self.assertTrue(self.line("x", journal=True).startswith("<4>dashboard.test: x"))

    def test_long_values_are_cut(self):
        self.assertLess(len(self.line("x", v="y" * 5000)), 1100)

    def test_once(self):
        once = logs.Once(every=3600)
        self.assertEqual([once("a"), once("a"), once("b")], [True, False, True])


class RequestsAndAudit(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        self.logs = capture_logs(self, logging.INFO)

    def test_successful_gets_are_quiet_and_the_rest_is_logged(self):
        setup_admin(self.client)
        self.client.get("/api/control/auth/session")
        self.client.get("/api/control/config")
        access = [line for line in self.logs.lines if "dashboard.access" in line]
        self.assertEqual(len(access), 1)  # the setup POST only
        self.assertIn("method=POST path=/api/control/auth/setup status=200", access[0])
        self.assertIn("user=admin", access[0])
        self.client.get("/api/control/nothing")
        self.client.post("/api/control", headers={**HEADERS, "Origin": "https://attacker.example"}, json={})
        access = [line for line in self.logs.lines if "dashboard.access" in line]
        self.assertIn("status=404", access[1])
        self.assertIn("status=403", access[2])

    def test_sign_in_audit_never_contains_passwords(self):
        setup_admin(self.client, password="setup secret pw")
        self.client.cookies.clear()
        for _ in range(5):
            self.client.post("/api/control/auth/login", headers=HEADERS,
                             json={"username": "admin", "password": "wrong secret pw"})
        self.client.post("/api/control/auth/login", headers={**HEADERS, "X-Forwarded-For": "10.1.2.3"},
                         json={"username": "admin", "password": "setup secret pw"})
        audit = [line for line in self.logs.lines if "dashboard.audit" in line]
        self.assertIn("audit: setup user=admin result=ok", audit[0])
        self.assertIn("audit: login user=admin result=failed", audit[1])
        self.assertIn("result=locked", audit[-1])
        self.assertIn("client=10.1.2.3", audit[-1])
        self.assertIn("retry_after=", audit[-1])
        self.assertNotIn("secret", self.logs.text())

    def test_account_changes_and_logout_are_audited(self):
        setup_admin(self.client)
        self.client.post("/api/control/users", headers=HEADERS,
                         json={"username": "guest", "password": "guest password",
                               "permissions": {"modules": ["services"]}})
        self.client.put("/api/control/users/guest", headers=HEADERS, json={"password": "another password"})
        self.client.request("DELETE", "/api/control/users/guest", headers=HEADERS)
        self.client.request("DELETE", "/api/control/users/ghost", headers=HEADERS)
        self.client.post("/api/control/auth/password", headers=HEADERS,
                         json={"current": "correct horse", "password": "a new password"})
        self.client.post("/api/control/auth/logout", headers=HEADERS, json={})
        audit = "\n".join(line for line in self.logs.lines if "dashboard.audit" in line)
        for part in ["user-create user=admin target=guest result=ok admin=no modules=services",
                     "user-update user=admin target=guest result=ok password=yes",
                     "user-delete user=admin target=guest result=ok",
                     "user-delete user=admin target=ghost result=refused status=404",
                     "password-change user=admin result=ok", "logout user=admin"]:
            self.assertIn(part, audit)
        self.assertNotIn("password", audit.replace("password-change", "").replace("password=yes", ""))

    @patch.object(actions, "perform")
    def test_actions_are_audited(self, perform):
        setup_admin(self.client)
        self.client.post("/api/control", headers=HEADERS, json={"action": "display-on"})
        audit = [line for line in self.logs.lines if "audit: action" in line]
        self.assertIn("user=admin action=display-on via=direct result=ok ms=", audit[0])

    @patch.object(moonshine, "moonshine_pair", return_value=False)
    def test_pairing_is_audited_without_the_pin(self, pair):
        setup_admin(self.client)
        self.client.post("/api/control/moonshine/pair", headers=HEADERS, json={"pin": "4826"})
        audit = [line for line in self.logs.lines if "audit: moonshine-pair" in line]
        self.assertIn("user=admin result=refused code=no-client", audit[0])
        self.assertNotIn("4826", self.logs.text())

    @patch.object(actions, "perform", side_effect=KeyError("bug"))
    def test_unexpected_failures_get_a_traceback(self, perform):
        setup_admin(self.client)
        response = self.client.post("/api/control", headers=HEADERS, json={"action": "display-on"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("Traceback", self.logs.text())
        self.assertIn("ERROR dashboard: action display-on failed unexpectedly", self.logs.text())


class ClientLog(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)
        patcher = patch.object(clientlog, "LIMIT", clientlog.RateLimit(burst=3, per_minute=1, global_per_minute=60))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.logs = capture_logs(self, logging.INFO, only={"dashboard.client"})

    def report(self, body, headers=HEADERS):
        return self.client.post("/api/control/client-log", headers=headers, json=body)

    def test_reports_are_sanitized(self):
        response = self.report({"message": "boom\n\x1b[31mred\r\n" + "x" * 1500, "source": "main.js\x00",
                                "line": 12, "column": 3, "kind": "unhandledrejection"})
        self.assertEqual(response.status_code, 200)
        line = self.logs.lines[0]
        self.assertNotIn("\n", line)
        self.assertNotIn("\x1b", line)
        self.assertNotIn("\x00", line)
        self.assertIn("WARNING dashboard.client: frontend error user=admin kind=unhandledrejection", line)
        self.assertIn("line=12 column=3", line)
        self.assertLess(len(line), 700)

    def test_limits_and_validation(self):
        for body in [{}, {"message": "x", "extra": 1}, {"message": "x" * 2001}, {"message": "x", "line": -1},
                     {"message": "x", "kind": "debug"}]:
            with self.subTest(body=body):
                self.assertEqual(self.report(body).status_code, 400)
        big = '{"message":"' + "x" * 2100 + '"}'
        self.assertEqual(self.client.post("/api/control/client-log", headers=HEADERS, content=big).status_code, 400)
        self.assertEqual(self.report({"message": "x"}, {**HEADERS, "Origin": "https://attacker.example"}).status_code,
                         403)
        self.client.cookies.clear()
        self.assertEqual(self.report({"message": "x"}).status_code, 401)
        self.assertEqual(self.logs.lines, [])

    def test_rate_limit(self):
        codes = [self.report({"message": f"error {n}"}).status_code for n in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])
        self.assertEqual(len(self.logs.lines), 3)

    def test_global_limit(self):
        limit = clientlog.RateLimit(burst=5, per_minute=60, global_per_minute=2)
        self.assertEqual([limit.allow("a"), limit.allow("b"), limit.allow("c")], [True, True, False])


if __name__ == "__main__":
    unittest.main()
