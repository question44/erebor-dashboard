import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from wake import WakeController, handler


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
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(
            {'login': 'owner@example.org', 'origins': ['https://dashboard.example.org']}, cls.controller))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.controller.reset_mock()
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
