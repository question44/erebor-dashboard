import json
import subprocess
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import control


class DisplayStatus(unittest.TestCase):
    @patch.object(control, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "umbriel"})
    @patch.object(control, "run")
    def test_physical_outputs_and_mixed_states(self, run, env):
        outputs = [{"name": "DP-1", "powered": False, "enabled": True},
                   {"name": "ambilight", "powered": True, "enabled": True},
                   {"name": "sunshine", "powered": True, "enabled": True}]
        run.return_value = json.dumps(outputs)
        self.assertEqual(control.display_status().state, "off")
        outputs.append({"name": "HDMI-A-1", "powered": True, "enabled": True})
        run.return_value = json.dumps(outputs)
        self.assertEqual(control.display_status().state, "mixed")
        outputs[0]["powered"] = True
        run.return_value = json.dumps(outputs)
        self.assertEqual(control.display_status().state, "on")
        run.assert_called_with("umbriel", "outputs", "--json", env=env.return_value, timeout=2)

    @patch.object(control, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "umbriel"})
    @patch.object(control, "run")
    def test_disabled_output_is_off_but_missing_power_is_unknown(self, run, env):
        run.return_value = '[{"name":"DP-1","enabled":false}]'
        self.assertEqual(control.display_status().state, "off")
        for value in ['[{"name":"DP-1","enabled":true}]', '[]', '[{"name":"ambilight","powered":true}]', '{}']:
            run.return_value = value
            self.assertEqual(control.display_status().state, "unknown")

    @patch.object(control, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "umbriel"})
    @patch.object(control, "run")
    def test_switched_away_screen_is_not_counted(self, run, env):
        run.return_value = json.dumps([{"name": "DP-1", "powered": True, "enabled": True},
                                       {"name": "HDMI-A-1", "powered": False, "enabled": False}])
        self.assertEqual(control.display_status().state, "on")

    @patch.object(control, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "mango"})
    @patch.object(control, "run")
    def test_wlopm_query_and_virtual_output_exclusion(self, run, env):
        run.return_value = '[{"output":"DP-1","power-mode":"off"},{"output":"ambilight","power-mode":"on"}]'
        self.assertEqual(control.display_status().state, "off")
        run.assert_called_once_with("wlopm", "--json", env=env.return_value, timeout=2)
        env.return_value = {"XDG_CURRENT_DESKTOP": "niri"}
        run.return_value = '[{"output":"DP-1","power-mode":"on"}]'
        self.assertEqual(control.display_status().state, "on")

    @patch.object(control, "session_environment", side_effect=subprocess.TimeoutExpired("systemctl", 2))
    def test_missing_session_or_timeout_is_unknown(self, env):
        self.assertEqual(control.display_status().state, "unknown")


class AmbilightStatus(unittest.TestCase):
    def probe(self, on=False, live=False, override=2, brightness=255, led_output=False, master=True):
        with patch.dict(control.os.environ, {"DASHBOARD_WLED_URL": "http://wled.test"}), \
                patch.object(control, "urlopen") as urlopen, \
                patch.object(control.socket, "create_connection") as connect:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
                "state": {"on": on, "lor": override, "bri": brightness}, "info": {"live": live}}).encode()
            sock = connect.return_value.__enter__.return_value
            sock.makefile.return_value.__enter__.return_value.readline.return_value = json.dumps({
                "success": True, "info": {"components": [{"name": "LEDDEVICE", "enabled": led_output}, {"name": "ALL", "enabled": master}]}}).encode()
            result = control.ambilight_status()
            urlopen.assert_called_once_with("http://wled.test/json/si", timeout=2)
            if result.state != "unknown":
                sock.sendall.assert_called_once_with(b'{"command":"serverinfo","tan":1}\n')
            return result

    def test_consistent_off_and_on(self):
        self.assertEqual(self.probe().state, "off")
        self.assertEqual(self.probe(on=True, override=0, led_output=True).state, "on")

    def test_realtime_can_be_on_despite_wled_power_flag(self):
        result = self.probe(on=False, live=True, override=0, led_output=True)
        self.assertEqual(result.state, "on")
        self.assertIn("active", result.detail)

    def test_override_and_zero_brightness(self):
        self.assertEqual(self.probe(on=False, live=True, override=2).state, "off")
        self.assertEqual(self.probe(on=True, brightness=0).state, "off")

    def test_inconsistent_components_are_mixed(self):
        self.assertEqual(self.probe(on=True, led_output=False).state, "mixed")
        self.assertEqual(self.probe(on=False, led_output=True).state, "mixed")

    def test_global_output_disable_overrides_component_flag(self):
        self.assertEqual(self.probe(on=True, led_output=True, master=False).state, "mixed")

    def test_malformed_wled_is_unknown(self):
        self.assertEqual(self.probe(on="true").state, "unknown")
        self.assertEqual(self.probe(override=5).state, "unknown")

    @patch.dict(control.os.environ, {"DASHBOARD_WLED_URL": "http://wled.test"})
    @patch.object(control, "urlopen", side_effect=OSError("unreachable"))
    def test_unreachable_controller_is_unknown(self, urlopen):
        self.assertEqual(control.ambilight_status().state, "unknown")

    def test_missing_hyperhdr_component_is_unknown(self):
        self.assertEqual(self.probe(led_output=None).state, "unknown")


class ScreenStatus(unittest.TestCase):
    @patch.object(control, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "umbriel"})
    @patch.object(control, "run")
    def test_reports_helper_state(self, run, env):
        for state in ["dp", "hdmi", "both", "none"]:
            run.return_value = state + "\n"
            self.assertEqual(control.screen_status().state, state)
        run.assert_called_with("umbriel-display-mode", "status", env=env.return_value, timeout=2)
        run.return_value = "sideways\n"
        self.assertEqual(control.screen_status().state, "unknown")
        run.side_effect = subprocess.CalledProcessError(1, "umbriel-display-mode")
        self.assertEqual(control.screen_status().state, "unknown")

    @patch.object(control, "session_environment", return_value={"XDG_CURRENT_DESKTOP": "niri"})
    @patch.object(control, "run")
    def test_other_desktops_are_unknown(self, run, env):
        self.assertEqual(control.screen_status().state, "unknown")
        run.assert_not_called()


class StatusEndpoint(unittest.TestCase):
    @patch.object(control, "action")
    @patch.object(control, "display_status")
    @patch.object(control, "ambilight_status")
    @patch.object(control.platform, "freedesktop_os_release")
    def test_fast_os_identity_is_uncached_read_only_and_checks_distro(self, release, ambilight, display, action):
        with TestClient(control.app) as client:
            for distro, expected in [('nixos', 'nixos'), ('fedora', 'unknown'), ('', 'unknown')]:
                release.return_value = {'ID': distro}
                response = client.get('/api/control/os')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.json(), {'state': expected})
            release.side_effect = OSError('unavailable')
            self.assertEqual(client.get('/api/control/os').json(), {'state': 'unknown'})
        action.assert_not_called()
        display.assert_not_called()
        ambilight.assert_not_called()

    @patch.object(control, "action")
    @patch.object(control, "display_status", return_value=control.DeviceStatus(state="off", detail="Displays off"))
    @patch.object(control, "ambilight_status", return_value=control.DeviceStatus(state="unknown", detail="Unavailable"))
    def test_get_is_read_only_uncached_and_preserves_partial_results(self, ambilight, display, action):
        with TestClient(control.app) as client:
            response = client.get("/api/control/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["display"]["state"], "off")
        self.assertEqual(response.json()["ambilight"]["state"], "unknown")
        action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
