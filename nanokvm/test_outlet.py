import json
import subprocess
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from wake import OutletReader, handler


class OutletTests(unittest.TestCase):
    def telemetry(self):
        return {'on': True, 'energy': {'current_power': 90655, 'today_energy': 1505,
                                      'month_energy': 3474}, 'password': 'must-not-leak'}

    def test_units_filtering_and_shared_cache(self):
        reader = OutletReader()
        with patch('wake.subprocess.run', return_value=Mock(stdout=json.dumps(self.telemetry()))) as run:
            status, data = reader.status()
            self.assertEqual(status, 200)
            self.assertEqual(data, {'on': True, 'watts': 90.655, 'todayKwh': 1.505, 'monthKwh': 3.474})
            self.assertEqual(reader.status(), (status, data))
            run.assert_called_once()

    def test_failed_refresh_discards_old_reading_and_hides_error_details(self):
        reader = OutletReader()
        with patch('wake.subprocess.run', return_value=Mock(stdout=json.dumps(self.telemetry()))):
            self.assertEqual(reader.status()[0], 200)
        reader.expires = 0
        for error in [subprocess.TimeoutExpired('query', 22), OSError('secret detail')]:
            with patch('wake.subprocess.run', side_effect=error):
                reader.expires = 0
                self.assertEqual(reader.status(), (503, {'error': 'Outlet reading unavailable.'}))

    def test_invalid_telemetry_fails_closed(self):
        for value in [True, -1, '90', float('nan'), None]:
            data = self.telemetry()
            data['energy']['current_power'] = value
            with patch('wake.subprocess.run', return_value=Mock(stdout=json.dumps(data))):
                self.assertEqual(OutletReader().status()[0], 503)

    def test_endpoint_requires_identity_and_opt_in_and_never_wakes(self):
        controller, outlet = Mock(), Mock()
        config = {'login': 'owner', 'origins': ['https://hub.example'], 'outletEnabled': True}
        outlet.status.return_value = (200, {'on': True, 'watts': 90})
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(config, controller, outlet=outlet))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, headers):
            connection = HTTPConnection(*server.server_address)
            connection.request(method, '/outlet', headers=headers)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            connection.close()
            return result
        try:
            self.assertEqual(request('GET', {})[0], 403)
            outlet.status.assert_not_called()
            status, headers, _ = request('GET', {'Tailscale-User-Login': 'owner', 'Origin': 'https://hub.example'})
            self.assertEqual(status, 200)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(headers['Access-Control-Allow-Origin'], 'https://hub.example')
            self.assertEqual(request('OPTIONS', {'Origin': 'https://hub.example'})[0], 204)
            self.assertEqual(request('OPTIONS', {'Origin': 'https://evil.example'})[0], 403)
            config['outletEnabled'] = False
            self.assertEqual(request('GET', {'Tailscale-User-Login': 'owner'})[0], 404)
            controller.wake.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
