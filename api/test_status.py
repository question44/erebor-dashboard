import json
import subprocess
import unittest
from unittest.mock import patch


from control import ambilight, display, modes, power, settings, status, system
from fixtures import isolated_store, setup_admin

UMBRIEL = {"XDG_CURRENT_DESKTOP": "umbriel"}


class DisplayStatus(unittest.TestCase):
    @patch.object(system, "run")
    def test_physical_outputs_and_mixed_states(self, run):
        outputs = [{"name": "DP-1", "powered": False, "enabled": True},
                   {"name": "ambilight", "powered": True, "enabled": True},
                   {"name": "sunshine", "powered": True, "enabled": True}]
        run.return_value = json.dumps(outputs)
        self.assertEqual(display.display_status(UMBRIEL).state, "off")
        outputs.append({"name": "HDMI-A-1", "powered": True, "enabled": True})
        run.return_value = json.dumps(outputs)
        self.assertEqual(display.display_status(UMBRIEL).state, "mixed")
        outputs[0]["powered"] = True
        run.return_value = json.dumps(outputs)
        self.assertEqual(display.display_status(UMBRIEL).state, "on")
        run.assert_called_with("umbriel", "outputs", "--json", env=UMBRIEL, timeout=2)

    @patch.object(system, "run")
    def test_disabled_output_is_off_but_missing_power_is_unknown(self, run):
        run.return_value = '[{"name":"DP-1","enabled":false}]'
        self.assertEqual(display.display_status(UMBRIEL).state, "off")
        for value in ['[{"name":"DP-1","enabled":true}]', '[]', '[{"name":"ambilight","powered":true}]', '{}',
                      '[{"name":"DP-1","powered":"yes"}]', '[1]']:
            run.return_value = value
            self.assertEqual(display.display_status(UMBRIEL).state, "unknown")

    @patch.object(system, "run")
    def test_switched_away_screen_is_not_counted(self, run):
        run.return_value = json.dumps([{"name": "DP-1", "powered": True, "enabled": True},
                                       {"name": "HDMI-A-1", "powered": False, "enabled": False}])
        self.assertEqual(display.display_status(UMBRIEL).state, "on")

    @patch.object(system, "run")
    def test_unsupported_desktop_is_unknown(self, run):
        for desktop in ["niri", "mango", ""]:
            self.assertEqual(display.display_status({"XDG_CURRENT_DESKTOP": desktop}).state, "unknown")
        run.assert_not_called()

    @patch.object(system, "run", side_effect=subprocess.TimeoutExpired("umbriel", 2))
    def test_timeout_is_unknown(self, run):
        self.assertEqual(display.display_status(UMBRIEL).state, "unknown")


class AmbilightStatus(unittest.TestCase):
    def probe(self, on=False, live=False, override=2, brightness=255, led_output=False, master=True):
        with patch.object(settings, "WLED_URL", "http://wled.test"), \
                patch.object(ambilight, "urlopen") as urlopen, \
                patch.object(ambilight.socket, "create_connection") as connect:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
                "state": {"on": on, "lor": override, "bri": brightness}, "info": {"live": live}}).encode()
            sock = connect.return_value.__enter__.return_value
            sock.makefile.return_value.__enter__.return_value.readline.return_value = json.dumps({
                "success": True, "info": {"components": [{"name": "LEDDEVICE", "enabled": led_output}, {"name": "ALL", "enabled": master}]}}).encode()
            result = ambilight.ambilight_status()
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

    @patch.object(settings, "WLED_URL", "http://wled.test")
    @patch.object(ambilight, "urlopen", side_effect=OSError("unreachable"))
    def test_unreachable_controller_is_unknown(self, urlopen):
        self.assertEqual(ambilight.ambilight_status().state, "unknown")

    @patch.object(settings, "WLED_URL", None)
    def test_unconfigured_controller_is_unknown(self):
        self.assertEqual(ambilight.ambilight_status().state, "unknown")

    def test_missing_hyperhdr_component_is_unknown(self):
        self.assertEqual(self.probe(led_output=None).state, "unknown")


class ScreenStatus(unittest.TestCase):
    @patch.object(system, "run")
    def test_reports_helper_state(self, run):
        for state in ["dp", "hdmi", "both", "none"]:
            run.return_value = state + "\n"
            self.assertEqual(display.screen_status(UMBRIEL).state, state)
        run.assert_called_with("umbriel-display-mode", "status", env=UMBRIEL, timeout=2)
        run.return_value = "sideways\n"
        self.assertEqual(display.screen_status(UMBRIEL).state, "unknown")
        run.side_effect = subprocess.CalledProcessError(1, "umbriel-display-mode")
        self.assertEqual(display.screen_status(UMBRIEL).state, "unknown")

    def test_details_name_the_configured_screens(self):
        self.assertEqual(display.screen_detail("dp"), "Showing on DP-1; HDMI is disabled.")
        self.assertEqual(display.screen_detail("both"), "DP-1 and HDMI are both enabled.")
        with patch.object(settings, "SCREEN_MODES", {"dp": "Monitor", "hdmi": "TV"}):
            self.assertEqual(display.screen_detail("hdmi"), "Showing on TV; Monitor is disabled.")
        with patch.object(settings, "SCREEN_MODES", {"dp": "Monitor"}), patch.object(system, "run", return_value="hdmi"):
            self.assertEqual(display.screen_status(UMBRIEL).state, "unknown")

    @patch.object(system, "run")
    def test_other_desktops_are_unknown(self, run):
        self.assertEqual(display.screen_status({"XDG_CURRENT_DESKTOP": "niri"}).state, "unknown")
        run.assert_not_called()


class StatusEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)

    def test_requires_sign_in(self):
        self.client.cookies.clear()
        for path in ["/api/control/os", "/api/control/status"]:
            self.assertEqual(self.client.get(path).status_code, 401)

    @patch.object(status.platform, "freedesktop_os_release")
    @patch.object(status, "probe")
    def test_fast_os_identity_is_uncached_read_only_and_checks_distro(self, probe, release):
        for distro, expected in [('nixos', 'nixos'), ('fedora', 'unknown'), ('', 'unknown')]:
            release.return_value = {'ID': distro}
            response = self.client.get('/api/control/os')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(response.json(), {'state': expected})
        release.side_effect = OSError('unavailable')
        self.assertEqual(self.client.get('/api/control/os').json(), {'state': 'unknown'})
        probe.assert_not_called()

    @patch.object(system, "session_environment", return_value=UMBRIEL)
    @patch.object(display, "display_status", return_value=display.DeviceStatus(state="off", detail="Displays off"))
    @patch.object(display, "screen_status", return_value=display.ScreenStatus(state="dp", detail="DP"))
    @patch.object(ambilight, "ambilight_status", return_value=display.DeviceStatus(state="unknown", detail="Unavailable"))
    @patch.object(power, "profile_status", return_value=power.ProfileStatus(state="balanced", detail="Balanced"))
    def test_get_is_uncached_for_browsers_and_preserves_partial_results(self, profile, ambilight_, screen, display_, env):
        response = self.client.get("/api/control/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["display"]["state"], "off")
        self.assertEqual(response.json()["ambilight"]["state"], "unknown")
        self.assertEqual(response.json()["screen"]["state"], "dp")
        # One environment read per refresh, passed to both desktop probes.
        env.assert_called_once()
        display_.assert_called_once_with(UMBRIEL)
        screen.assert_called_once_with(UMBRIEL)

    @patch.object(system, "session_environment", return_value=UMBRIEL)
    @patch.object(display, "display_status", return_value=display.DeviceStatus(state="on", detail="On"))
    @patch.object(display, "screen_status", return_value=display.ScreenStatus(state="dp", detail="DP"))
    @patch.object(ambilight, "ambilight_status", return_value=display.DeviceStatus(state="off", detail="Off"))
    @patch.object(power, "profile_status", return_value=power.ProfileStatus(state="balanced", detail="Balanced"))
    def test_refresh_is_shared_and_actions_invalidate_it(self, *probes):
        env = probes[-1]
        for _ in range(3):
            self.assertEqual(self.client.get("/api/control/status").status_code, 200)
        env.assert_called_once()
        from fixtures import HEADERS
        with patch("control.actions.perform"):
            self.client.post("/api/control", headers=HEADERS, json={"action": "display-on"})
        self.client.get("/api/control/status")
        self.assertEqual(env.call_count, 2)
        with patch.object(settings, "STATUS_TTL", 0):
            self.client.get("/api/control/status")
        self.assertEqual(env.call_count, 3)

    @patch.object(system, "session_environment", side_effect=subprocess.TimeoutExpired("systemctl", 2))
    @patch.object(ambilight, "ambilight_status", return_value=display.DeviceStatus(state="off", detail="Off"))
    @patch.object(power, "profile_status", return_value=power.ProfileStatus(state="balanced", detail="Balanced"))
    def test_missing_session_leaves_other_probes(self, profile, ambilight_, env):
        body = self.client.get("/api/control/status").json()
        self.assertEqual((body["display"]["state"], body["screen"]["state"]), ("unknown", "unknown"))
        self.assertEqual(body["ambilight"]["state"], "off")

    def test_probes_run_concurrently(self):
        import threading, time
        barrier = threading.Barrier(2, timeout=2)

        def slow(*args):
            barrier.wait()  # deadlocks (and times out) unless both run at once
            return display.DeviceStatus(state="on", detail="On")
        with patch.object(system, "session_environment", return_value=UMBRIEL), \
                patch.object(display, "display_status", side_effect=slow), \
                patch.object(ambilight, "ambilight_status", side_effect=slow), \
                patch.object(display, "screen_status", return_value=display.ScreenStatus(state="dp", detail="DP")), \
                patch.object(power, "profile_status", return_value=power.ProfileStatus(state="balanced", detail="B")):
            self.assertEqual(status.probe().display.state, "on")




class ModesState(unittest.TestCase):
    STATE = {"modes": ["power-saver", "reduce-transparency"],
             "mode_labels": {"power-saver": "Power saver", "reduce-transparency": "Reduce transparency"},
             "mode_kinds": {"power-saver": "profile", "reduce-transparency": "toggle"},
             "items": {"glass": {"label": "Liquid glass off", "status": "applied", "mode": "power-saver"},
                       "ambilight": {"label": "Ambilight dimmed", "status": "applied", "mode": "power-saver"},
                       "gpu": {"label": "GPU power limit 200 W", "status": "failed", "mode": "power-saver"},
                       "opacity": {"label": "Apps opaque", "status": "applied", "mode": "reduce-transparency"}}}

    def with_state(self, state):
        import tempfile
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(lambda: __import__("os").unlink(tmp.name))
        tmp.write(state if isinstance(state, str) else json.dumps(state))
        tmp.close()
        return patch.object(settings, "MODES_STATE", tmp.name)

    @patch.object(power, "power_property", return_value="power-saver")
    def test_profile_lists_applied_items(self, _prop):
        with self.with_state(self.STATE):
            detail = power.profile_status(modes.modes_state()).detail
        self.assertIn("Power saver on: Ambilight dimmed, Liquid glass off.", detail)
        self.assertNotIn("GPU", detail)
        self.assertNotIn("transparency", detail)  # a toggle mode, not a profile

    @patch.object(power, "power_property", return_value="balanced")
    def test_no_mode_or_bad_file_keeps_detail(self, _prop):
        for state in [{"modes": [], "items": {}}, "not json", {"items": {}}]:
            with self.with_state(state):
                self.assertEqual(power.profile_status(modes.modes_state()).detail, power.PROFILE_DETAILS["balanced"])
        with patch.object(settings, "MODES_STATE", None):
            self.assertEqual(power.profile_status(modes.modes_state()).detail, power.PROFILE_DETAILS["balanced"])

    @patch.object(ambilight, "device_status")
    def test_ambilight_says_dimmed(self, device):
        device.return_value = display.DeviceStatus(state="off", detail="LEDs and Ambilight output are off.")
        with self.with_state(self.STATE):
            self.assertEqual(ambilight.ambilight_status(modes.modes_state()).detail,
                             "LEDs and Ambilight output are off. Dimmed by Power saver.")
        device.return_value = display.DeviceStatus(state="off", detail="LEDs and Ambilight output are off.")
        state = json.loads(json.dumps(self.STATE))
        state["items"]["ambilight"]["status"] = "changed"
        with self.with_state(state):
            self.assertEqual(ambilight.ambilight_status(modes.modes_state()).detail, "LEDs and Ambilight output are off.")


if __name__ == "__main__":
    unittest.main()
