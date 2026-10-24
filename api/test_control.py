import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import control


HEADERS = {"Host": "erebor.jzon.pl", "Origin": "https://erebor.jzon.pl",
           "Content-Type": "application/json", "X-Erebor-Control": "1"}


class Requests(unittest.TestCase):
    def setUp(self):
        hosts = patch.object(control, "HOSTS", {"localhost", "erebor.jzon.pl", "moria.jzon.pl"})
        hosts.start()
        self.addCleanup(hosts.stop)
        enabled = patch.object(control, "ENABLED_ACTIONS", control.ACTIONS)
        enabled.start()
        self.addCleanup(enabled.stop)
        self.client = TestClient(control.app)
        self.addCleanup(self.client.close)

    def request(self, action="display-on", headers=None, method="POST", body=None, path="/api/control"):
        return self.client.request(method, path,
                                   content=json.dumps({"action": action}) if body is None else body,
                                   headers={**HEADERS, **(headers or {})})

    @patch.object(control, "action")
    def test_allowed_actions_and_response_contract(self, action):
        for name in control.ACTIONS:
            response = self.request(name)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"message": "Command sent"})
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            action.assert_called_with(name)

    @patch.object(control, "action")
    def test_reject_cross_origin_and_simple_requests(self, action):
        for headers in [{"Origin": "https://attacker.example"}, {"Origin": "null"},
                        {"Host": "attacker.example", "Origin": "https://attacker.example"},
                        {"X-Erebor-Control": ""}, {"Content-Type": "text/plain"},
                        {"Origin": "https://erebor.jzon.pl:8443"},
                        {"Origin": "https://erebor.jzon.pl/path"}, {"Origin": "https://[broken"}]:
            with self.subTest(headers=headers):
                response = self.request(headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        action.assert_not_called()

    @patch.object(control, "action")
    def test_private_and_tailnet_origins(self, action):
        for host in ["192.168.1.10", "100.80.1.2", "localhost", "moria.jzon.pl", "[fd00::1]"]:
            self.assertEqual(self.request(headers={"Host": host, "Origin": f"https://{host}"}).status_code, 200)
        for host in ["8.8.8.8", "0.0.0.0", "224.0.0.1"]:
            self.assertEqual(self.request(headers={"Host": host, "Origin": f"https://{host}"}).status_code, 403)

    @patch.object(control, "action")
    def test_invalid_bodies_do_not_execute(self, action):
        for body in ['{"action":"reboot"}', '{}', '[]', 'null', '1', '"display-on"',
                     '{"action":[]}', '{"action":1}', '{"action":null}',
                     '{"action":"sleep","extra":true}', '{broken', b'\xff']:
            with self.subTest(body=body):
                response = self.request(body=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"error": "Invalid action"})
        action.assert_not_called()

    @patch.object(control, "action")
    def test_body_size_and_content_length(self, action):
        for headers in [{"Content-Length": "0"}, {"Content-Length": "-1"},
                        {"Content-Length": "257"}, {"Content-Length": "invalid"},
                        {"Content-Length": "1"}]:
            self.assertEqual(self.request(headers=headers).status_code, 400)
        self.assertEqual(self.request(body=' ' * 257).status_code, 400)
        action.assert_not_called()

    @patch.object(control, "action")
    def test_other_routes_and_methods_cannot_execute(self, action):
        for method in ["GET", "PUT", "DELETE", "OPTIONS"]:
            self.assertEqual(self.request(method=method).status_code, 405)
        for path in ["/api/control/", "/api/control?unexpected=1", "/api/control/anything"]:
            self.assertEqual(self.request(path=path).status_code, 404)
        action.assert_not_called()

    @patch.object(control, "action")
    def test_schema_and_docs_are_read_only(self, action):
        response = self.client.get("/api/control/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertEqual(set(schema["components"]["schemas"]["ControlCommand"]["properties"]["action"]["enum"]), control.ACTIONS)
        responses = schema["paths"]["/api/control"]["post"]["responses"]
        self.assertIn("400", responses)
        self.assertNotIn("422", responses)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/api/control/docs").status_code, 200)
        action.assert_not_called()

    @patch.object(control, "action", side_effect=RuntimeError("internal failure details"))
    def test_action_failure_is_reported(self, action):
        response = self.request()
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.json())
        self.assertNotIn("internal failure details", response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    @patch.object(control, "action")
    def test_disabled_actions_cannot_execute(self, action):
        with patch.object(control, "ENABLED_ACTIONS", {"display-on"}):
            response = self.request("power-off")
        self.assertEqual(response.status_code, 403)
        action.assert_not_called()

    @patch.object(control, "action")
    def test_duplicate_origin_rejected(self, action):
        headers = list(HEADERS.items()) + [("Origin", HEADERS["Origin"])]
        response = self.client.post("/api/control", headers=headers, content='{"action":"sleep"}')
        self.assertEqual(response.status_code, 403)
        action.assert_not_called()

    @patch.object(control, "action")
    def test_stream_cannot_bypass_declared_size_limit(self, action):
        async def exercise(chunks):
            headers = {**HEADERS, "Content-Length": "24"}
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "scheme": "https", "path": "/api/control",
                     "raw_path": b"/api/control", "query_string": b"",
                     "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                     "client": ("127.0.0.1", 12345), "server": ("erebor.jzon.pl", 443)}
            messages = iter({"type": "http.request", "body": chunk, "more_body": i < len(chunks)-1}
                            for i, chunk in enumerate(chunks))
            sent = []
            async def receive():
                return next(messages)
            async def send(message):
                sent.append(message)
            await control.app(scope, receive, send)
            self.assertEqual(sent[0]["status"], 400)
        asyncio.run(exercise([b" " * 200, b" " * 200]))
        asyncio.run(exercise([b"{}", b" "]))
        action.assert_not_called()


class Dispatch(unittest.TestCase):
    @patch.dict(control.os.environ, {"DASHBOARD_WLED_URL": "http://localhost:9999"})
    @patch.object(control, "urlopen")
    def test_wled_uses_outgoing_http_request(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"success":true}'
        control.wled_state({"on": True})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:9999/json/state")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"on": True})

    @patch.object(control, "wled_state")
    @patch.object(control, "hyperhdr_leds")
    def test_ambilight_releases_realtime_override_on_enable(self, leds, wled):
        calls = unittest.mock.Mock()
        calls.attach_mock(leds, "leds")
        calls.attach_mock(wled, "wled")
        control.action("ambilight-off")
        control.action("ambilight-on")
        self.assertEqual(calls.mock_calls, [
            unittest.mock.call.leds(False),
            unittest.mock.call.wled({"on": False, "live": False, "lor": 2}),
            unittest.mock.call.wled({"lor": 0, "on": True}),
            unittest.mock.call.leds(True),
        ])

    @patch.object(control, "run")
    def test_power_uses_login1_without_forcing_inhibitors(self, run):
        for name, method in [("sleep", "Suspend"), ("power-off", "PowerOff")]:
            control.action(name)
            self.assertEqual(run.call_args.args[-3:], (method, "b", "false"))

    @patch.object(control, "run")
    def test_windows_reboot_uses_only_fixed_privileged_service(self, run):
        control.action("reboot-windows")
        run.assert_called_once_with("systemctl", "--system", "--no-ask-password", "start",
                                    "dashboard-reboot-windows.service", timeout=65)

    @patch.object(control, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "mango"})
    @patch.object(control, "run")
    def test_mango_preserves_capture_outputs(self, run, environment):
        monitors = {"monitors": [{"name": n} for n in ["DP-1", "ambilight", "sunshine"]]}
        run.side_effect = [json.dumps(monitors), ""]
        control.action("display-off")
        self.assertEqual(run.call_args.args, ("wlopm", "--off", "DP-1"))
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
