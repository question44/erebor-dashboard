import asyncio
import json
import unittest
from unittest.mock import patch

from control import actions, ambilight, display, power, settings, system
from control.app import app
from fixtures import ACTIONS, HEADERS, HOST, isolated_store, setup_admin


class Requests(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)

    def request(self, action="display-on", headers=None, method="POST", body=None, path="/api/control"):
        return self.client.request(method, path,
                                   content=json.dumps({"action": action}) if body is None else body,
                                   headers={**HEADERS, **(headers or {})})

    @patch.object(actions, "perform")
    def test_allowed_actions_and_response_contract(self, action):
        for name in ACTIONS:
            response = self.request(name)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"message": "Command sent"})
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            action.assert_called_with(name, "admin")

    @patch.object(actions, "perform")
    def test_reject_cross_origin_and_simple_requests(self, action):
        for headers in [{"Origin": "https://attacker.example"}, {"Origin": "null"},
                        {"Host": "attacker.example", "Origin": "https://attacker.example"},
                        {"X-Erebor-Control": ""}, {"Content-Type": "text/plain"},
                        {"Origin": f"https://{HOST}:8443"},
                        {"Origin": f"https://{HOST}/path"}, {"Origin": "https://[broken"}]:
            with self.subTest(headers=headers):
                response = self.request(headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        action.assert_not_called()

    @patch.object(actions, "perform")
    def test_private_and_tailnet_origins(self, action):
        for host in ["10.0.0.5", "100.64.0.10", "localhost", "other.example.org", "[fd00::1]"]:
            self.assertEqual(self.request(headers={"Host": host, "Origin": f"https://{host}"}).status_code, 200)
        for host in ["8.8.8.8", "0.0.0.0", "224.0.0.1"]:
            self.assertEqual(self.request(headers={"Host": host, "Origin": f"https://{host}"}).status_code, 403)

    @patch.object(actions, "perform")
    def test_invalid_bodies_do_not_execute(self, action):
        for body in ['{"action":"reboot"}', '{}', '[]', 'null', '1', '"display-on"',
                     '{"action":[]}', '{"action":1}', '{"action":null}',
                     '{"action":"sleep","extra":true}', '{broken', b'\xff']:
            with self.subTest(body=body):
                response = self.request(body=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"error": "Invalid action"})
        action.assert_not_called()

    @patch.object(actions, "perform")
    def test_body_size_and_content_length(self, action):
        for headers in [{"Content-Length": "0"}, {"Content-Length": "-1"},
                        {"Content-Length": "257"}, {"Content-Length": "invalid"},
                        {"Content-Length": "1"}]:
            self.assertEqual(self.request(headers=headers).status_code, 400)
        self.assertEqual(self.request(body=' ' * 257).status_code, 400)
        action.assert_not_called()

    @patch.object(actions, "perform")
    def test_other_routes_and_methods_cannot_execute(self, action):
        for method in ["GET", "PUT", "DELETE", "OPTIONS"]:
            self.assertEqual(self.request(method=method).status_code, 405)
        for path in ["/api/control/", "/api/control?unexpected=1", "/api/control/anything"]:
            self.assertEqual(self.request(path=path).status_code, 404)
        action.assert_not_called()

    @patch.object(actions, "perform")
    def test_schema_and_docs_are_read_only(self, action):
        response = self.client.get("/api/control/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertEqual(set(schema["components"]["schemas"]["ControlCommand"]["properties"]["action"]["enum"]), ACTIONS)
        responses = schema["paths"]["/api/control"]["post"]["responses"]
        self.assertIn("400", responses)
        self.assertNotIn("422", responses)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/api/control/docs").status_code, 200)
        action.assert_not_called()

    @patch.object(actions, "perform", side_effect=RuntimeError("internal failure details"))
    def test_action_failure_is_reported(self, action):
        response = self.request()
        self.assertEqual(response.status_code, 503)
        self.assertIn("error", response.json())
        self.assertNotIn("internal failure details", response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    @patch.object(actions, "perform")
    def test_disabled_actions_cannot_execute(self, action):
        with patch.object(settings, "ENABLED_ACTIONS", {"display-on"}):
            response = self.request("power-off")
        self.assertEqual(response.status_code, 403)
        action.assert_not_called()

    @patch.object(actions, "perform")
    def test_duplicate_origin_rejected(self, action):
        headers = list(HEADERS.items()) + [("Origin", HEADERS["Origin"])]
        response = self.client.post("/api/control", headers=headers, content='{"action":"sleep"}')
        self.assertEqual(response.status_code, 403)
        action.assert_not_called()

    @patch.object(actions, "perform")
    def test_stream_cannot_bypass_declared_size_limit(self, action):
        async def exercise(chunks):
            headers = {**HEADERS, "Content-Length": "24"}
            scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "scheme": "https", "path": "/api/control",
                     "raw_path": b"/api/control", "query_string": b"",
                     "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                     "client": ("127.0.0.1", 12345), "server": (HOST, 443)}
            messages = iter({"type": "http.request", "body": chunk, "more_body": i < len(chunks)-1}
                            for i, chunk in enumerate(chunks))
            sent = []
            async def receive():
                return next(messages)
            async def send(message):
                sent.append(message)
            await app(scope, receive, send)
            self.assertEqual(sent[0]["status"], 400)
        asyncio.run(exercise([b" " * 200, b" " * 200]))
        asyncio.run(exercise([b"{}", b" "]))
        action.assert_not_called()


class Dispatch(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(settings, "SYSD_SOCKET", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch.object(settings, "WLED_URL", "http://localhost:9999")
    @patch.object(ambilight, "urlopen")
    def test_wled_uses_outgoing_http_request(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"success":true}'
        ambilight.wled_state({"on": True})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:9999/json/state")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"on": True})

    @patch.object(ambilight, "wled_state")
    @patch.object(ambilight, "hyperhdr_leds")
    def test_ambilight_releases_realtime_override_on_enable(self, leds, wled):
        calls = unittest.mock.Mock()
        calls.attach_mock(leds, "leds")
        calls.attach_mock(wled, "wled")
        actions.perform("ambilight-off")
        actions.perform("ambilight-on")
        self.assertEqual(calls.mock_calls, [
            unittest.mock.call.leds(False),
            unittest.mock.call.wled({"on": False, "live": False, "lor": 2}),
            unittest.mock.call.wled({"lor": 0, "on": True}),
            unittest.mock.call.leds(True),
        ])

    @patch.object(settings, "HYPERHDR", ("127.0.0.2", 1234))
    @patch.object(ambilight.socket, "create_connection", side_effect=ConnectionRefusedError)
    def test_hyperhdr_address_is_configurable_and_failure_is_explained(self, connect):
        with self.assertRaises(actions.ActionError) as caught:
            ambilight.hyperhdr_leds(True)
        self.assertEqual(caught.exception.code, "hyperhdr-unreachable")
        self.assertEqual(connect.call_args.args[0], ("127.0.0.2", 1234))

    @patch.object(system, "run")
    def test_power_uses_login1_without_forcing_inhibitors(self, run):
        for name, method in [("sleep", "Suspend"), ("power-off", "PowerOff")]:
            actions.perform(name)
            self.assertEqual(run.call_args.args[-3:], (method, "b", "false"))

    @patch.object(system, "run")
    def test_windows_reboot_uses_only_fixed_privileged_service(self, run):
        actions.perform("reboot-windows")
        run.assert_called_once_with("systemctl", "--system", "--no-ask-password", "start",
                                    "dashboard-reboot-windows.service", timeout=65)

    def test_every_control_action_has_a_handler(self):
        self.assertEqual(set(actions.DIRECT), ACTIONS)
        self.assertLessEqual(set(actions.SYSD_ROUTES), ACTIONS)

    @patch.object(system, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "umbriel"})
    @patch.object(system, "run")
    def test_umbriel_dpms_skips_switched_away_and_virtual_outputs(self, run, environment):
        outputs = [{"name": "DP-1", "enabled": True}, {"name": "HDMI-A-1", "enabled": False},
                   {"name": "ambilight", "enabled": True}]
        for state in ["off", "on"]:
            with self.subTest(state=state):
                run.reset_mock()
                run.side_effect = [json.dumps(outputs), ""]
                actions.perform("display-" + state)
                self.assertEqual(run.call_args.args, ("umbriel", "msg", "dpms-" + state, "DP-1"))
                self.assertEqual(run.call_count, 2)

    @patch.object(settings, "IGNORED_OUTPUTS", frozenset({"DP-1"}))
    @patch.object(system, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "umbriel"})
    @patch.object(system, "run")
    def test_ignored_outputs_are_configurable(self, run, environment):
        run.side_effect = [json.dumps([{"name": "DP-1"}, {"name": "ambilight"}]), ""]
        actions.perform("display-off")
        self.assertEqual(run.call_args.args, ("umbriel", "msg", "dpms-off", "ambilight"))

    @patch.object(system, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "umbriel"})
    @patch.object(system, "run", return_value="[]")
    def test_no_physical_display_is_an_error(self, run, environment):
        with self.assertRaises(actions.ActionError) as caught:
            actions.perform("display-on")
        self.assertEqual(caught.exception.code, "no-display")

    @patch.object(system, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "umbriel"})
    @patch.object(system, "run")
    def test_screen_actions_use_shared_helper(self, run, environment):
        for screen in ["dp", "hdmi"]:
            actions.perform("screen-" + screen)
            run.assert_called_with("umbriel-display-mode", screen, env=environment.return_value, timeout=12)

    @patch.object(system, "run")
    def test_unsupported_desktops_fail_clearly(self, run):
        for desktop in ["niri", "mango", "", "GNOME"]:
            with self.subTest(desktop=desktop), \
                    patch.object(system, "session_environment", return_value={"XDG_CURRENT_DESKTOP": desktop}):
                for name in ["screen-dp", "display-off"]:
                    with self.assertRaises(actions.ActionError) as caught:
                        actions.perform(name)
                    self.assertEqual(caught.exception.code, "no-desktop")
        run.assert_not_called()

    def test_adapter_is_chosen_from_a_desktop_list(self):
        from control import compositors
        self.assertIsInstance(compositors.for_session({"XDG_CURRENT_DESKTOP": "foo:Umbriel"}, system.run),
                              compositors.Umbriel)
        self.assertIsNone(compositors.for_session({}, system.run))
        self.assertIn("UMBRIEL_SOCKET", compositors.session_keys())


class PowerProfiles(unittest.TestCase):
    @patch.object(settings, "SYSD_SOCKET", None)
    @patch.object(system, "run")
    def test_switch_uses_fixed_dbus_property(self, run):
        actions.perform("profile-power-saver")
        run.assert_called_once_with("busctl", "--system", "--allow-interactive-authorization=no", "set-property",
                                    *power.POWER_PROFILES, "ActiveProfile", "s", "power-saver")

    def test_status(self):
        replies = {"ActiveProfile": "performance", "PerformanceDegraded": "high-operating-temperature"}
        fake = lambda *args, **kwargs: json.dumps({"type": "s", "data": replies[args[-1]]})
        with patch.object(system, "run", side_effect=fake):
            status = power.profile_status()
        self.assertEqual(status.state, "performance")
        self.assertIn("high-operating-temperature", status.detail)
        with patch.object(system, "run", return_value='{"type":"s","data":"turbo"}'):
            self.assertEqual(power.profile_status().state, "unknown")
        with patch.object(system, "run", side_effect=OSError):
            self.assertEqual(power.profile_status().state, "unknown")


if __name__ == "__main__":
    unittest.main()
