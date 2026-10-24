import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from wake import OSDetector, WakeController, handler


class OSTests(unittest.TestCase):
    def setUp(self):
        self.peers = [
            {'state': 'nixos', 'nodeId': 'linux-node', 'ip': '100.64.0.1', 'tailscaleOS': 'linux'},
            {'state': 'windows', 'nodeId': 'windows-node', 'ip': '100.64.0.2', 'tailscaleOS': 'windows'},
        ]
        self.network = {'BackendState': 'Running', 'Peer': {
            peer['nodeId']: {'ID': peer['nodeId'], 'TailscaleIPs': [peer['ip']], 'OS': peer['tailscaleOS']}
            for peer in self.peers}}
        self.detector = OSDetector(self.peers)

    def probe(self, replies):
        def request(method, path):
            if path == 'status':
                return self.network
            ip = path.split('ip=')[1].split('&')[0]
            return {'NodeIP': ip, 'Err': '' if ip in replies else 'timeout', 'LatencySeconds': 0.01}
        with patch.object(self.detector, 'request', side_effect=request):
            return self.detector.status('awake')['state']

    def test_only_fresh_responder_identifies_os(self):
        self.assertEqual(self.probe({'100.64.0.1'}), 'nixos')
        self.assertEqual(self.probe({'100.64.0.2'}), 'windows')
        self.assertEqual(self.probe(set()), 'unknown')
        self.assertEqual(self.probe({'100.64.0.1', '100.64.0.2'}), 'unknown')
        self.assertEqual(self.probe(set()), 'unknown')  # Never retain a previous success.

    def test_no_probe_for_sleep_off_or_unknown_power(self):
        with patch.object(self.detector, 'request') as run:
            for state in ['asleep', 'off', 'unknown']:
                self.assertEqual(self.detector.status(state)['state'], 'unknown')
            run.assert_not_called()

    def test_identity_address_and_os_must_all_match(self):
        for key, value in [('ID', 'vm-node'), ('TailscaleIPs', ['100.64.0.3']), ('OS', 'windows')]:
            with self.subTest(key=key):
                node = self.network['Peer']['linux-node']
                previous, node[key] = node[key], value
                self.assertEqual(self.probe({'100.64.0.1'}), 'unknown')
                node[key] = previous

    def test_daemon_errors_fail_to_unknown(self):
        for failure in [OSError('missing'), TimeoutError('timeout'), ValueError('invalid json')]:
            with patch.object(self.detector, 'request', side_effect=failure):
                self.assertEqual(self.detector.status('awake')['state'], 'unknown')
        self.network['BackendState'] = 'Stopped'
        self.assertEqual(self.probe({'100.64.0.1'}), 'unknown')

    def test_probe_timeout_does_not_hide_other_os_and_malformed_status_is_unknown(self):
        def request(method, path):
            if path == 'status':
                return self.network
            if '100.64.0.1' in path:
                return {'NodeIP': '100.64.0.1', 'Err': '', 'LatencySeconds': 0.01}
            raise TimeoutError('offline Windows')
        with patch.object(self.detector, 'request', side_effect=request):
            self.assertEqual(self.detector.status('awake')['state'], 'nixos')
        for invalid in [{}, None, []]:
            with patch.object(self.detector, 'request', return_value=invalid):
                self.assertEqual(self.detector.status('awake')['state'], 'unknown')

    def test_probe_error_wrong_address_or_missing_latency_is_not_a_response(self):
        for probe in [{}, {'Err': 'no reply'},
                      {'NodeIP': '100.64.0.3', 'Err': '', 'LatencySeconds': 0.01},
                      {'NodeIP': '100.64.0.1', 'Err': ''}]:
            with patch.object(self.detector, 'request', side_effect=lambda method, path:
                              self.network if path == 'status' else probe):
                self.assertEqual(self.detector.status('awake')['state'], 'unknown')

    def test_local_api_uses_unix_socket_and_closes_on_failure(self):
        with patch('wake.socket.socket') as socket, patch('wake.HTTPConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = b'{"BackendState":"Running"}'
            self.assertEqual(self.detector.request('GET', 'status'), {'BackendState': 'Running'})
            socket.return_value.connect.assert_called_once_with('/var/run/tailscale/tailscaled.sock')
            connection.return_value.request.assert_called_once_with('GET', '/localapi/v0/status')
            response.status = 403
            with self.assertRaises(OSError):
                self.detector.request('GET', 'status')
            self.assertEqual(connection.return_value.close.call_count, 2)


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.led, self.power = Mock(), Mock()
        self.power.read_text.return_value = '0'
        self.control = WakeController(self.led, self.power)
        self.sleep = patch('wake.time.sleep').start()
        self.addCleanup(patch.stopall)

    def test_running_pc_never_receives_press(self):
        self.led.read_text.return_value = '0'
        status, result = self.control.wake()
        self.assertEqual((status, result['state']), (200, 'already-on'))
        self.power.write_text.assert_not_called()

    def test_power_states_are_read_without_pressing(self):
        for values, state in [(['0'] * 40, 'awake'), (['1'] * 40, 'off'), (['0', '1'] * 20, 'asleep')]:
            self.led.read_text.side_effect = values
            status, result = self.control.status()
            self.assertEqual((status, result['state']), (200, state))
        self.power.write_text.assert_not_called()

    def test_off_and_blinking_pc_get_one_short_press_and_cooldown(self):
        for values in [['1'] * 40, ['0', '1'] * 20]:
            with self.subTest(values=values[:2]):
                led, power = Mock(), Mock()
                led.read_text.side_effect = values
                power.read_text.return_value = '0'
                controller = WakeController(led, power)
                self.assertEqual(controller.wake()[1]['state'], 'wake-sent')
                self.assertEqual([c.args[0] for c in power.write_text.call_args_list], ['1', '0'])
                self.sleep.assert_any_call(0.2)
                self.assertEqual(controller.wake()[0], 429)

    def test_invalid_or_missing_led_fails_closed(self):
        for invalid in ['invalid', OSError('missing')]:
            self.led.read_text.side_effect = invalid if isinstance(invalid, Exception) else None
            self.led.read_text.return_value = invalid
            self.assertEqual(self.control.wake()[0], 503)
            self.power.write_text.assert_not_called()

    def test_concurrent_press_is_rejected(self):
        self.control.lock.acquire()
        self.assertEqual(self.control.wake()[0], 409)
        self.power.write_text.assert_not_called()

    def test_release_attempted_when_press_fails(self):
        self.led.read_text.return_value = '1'
        self.power.write_text.side_effect = [OSError('write failed'), None]
        self.assertEqual(self.control.wake()[0], 503)
        self.assertEqual(self.power.write_text.call_args_list[-1].args, ('0',))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = Mock()
        cls.detector = Mock()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(
            {'login': 'owner@example.org', 'origins': ['https://dashboard.example.org']}, cls.controller, cls.detector))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.controller.reset_mock()
        self.detector.reset_mock()
        self.detector.status.return_value = {'state': 'windows', 'detail': 'Fresh OS response'}
        self.controller.wake.return_value = (200, {'state': 'wake-sent'})

    def request(self, method='POST', headers=None, body='{}', path='/wake'):
        conn = HTTPConnection(*self.server.server_address)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def headers(self):
        return {'Origin': 'https://dashboard.example.org', 'Tailscale-User-Login': 'owner@example.org',
                'Content-Type': 'application/json', 'X-Erebor-Wake': '1'}

    def test_valid_request(self):
        status, headers, body = self.request(headers=self.headers())
        self.assertEqual(status, 200)
        self.assertEqual(headers['Access-Control-Allow-Origin'], 'https://dashboard.example.org')
        self.assertEqual(json.loads(body)['state'], 'wake-sent')
        self.controller.wake.assert_called_once()

    def test_status_is_read_only_and_requires_identity(self):
        self.controller.status.return_value = (200, {'state': 'asleep', 'detail': 'Blinking LED'})
        status, _, body = self.request('GET', self.headers(), path='/status')
        self.assertEqual((status, json.loads(body)['state']), (200, 'asleep'))
        self.assertEqual(json.loads(body)['operatingSystem']['state'], 'windows')
        self.detector.status.assert_called_once_with('asleep')
        self.assertEqual(self.request('GET', path='/status')[0], 403)
        self.controller.wake.assert_not_called()

    def test_foreign_identity_origin_and_missing_header_cannot_press(self):
        for field, value in [('Origin', 'https://evil.example'), ('Tailscale-User-Login', 'other@example.org'),
                             ('X-Erebor-Wake', ''), ('Content-Type', 'text/plain')]:
            with self.subTest(field=field):
                headers = self.headers()
                headers[field] = value
                self.assertIn(self.request(headers=headers)[0], [400, 403])
        self.controller.wake.assert_not_called()

    def test_preflight_does_not_press(self):
        self.assertEqual(self.request('OPTIONS', {'Origin': 'https://dashboard.example.org'})[0], 204)
        self.assertEqual(self.request('OPTIONS', {'Origin': 'https://evil.example'})[0], 403)
        self.controller.wake.assert_not_called()

    def test_arbitrary_body_and_paths_cannot_press(self):
        for body, path in [('{"duration":5000}', '/wake'), ('[]', '/wake'), ('{}', '/reset')]:
            self.assertIn(self.request(headers=self.headers(), body=body, path=path)[0], [400, 404])
        self.controller.wake.assert_not_called()


if __name__ == '__main__':
    unittest.main()
