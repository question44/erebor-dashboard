import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import control
from fixtures import HEADERS, isolated_store, setup_admin

LOG = """2026-11-05T10:00:00Z  INFO moonshine::webserver::pairing: Waiting for pin to be sent at http://192.168.1.106:47989/pin?uniqueid=0123456789ABCDEF
2026-11-05T10:00:05Z  INFO moonshine::webserver: PIN registered successfully.
2026-11-05T10:01:00Z  INFO moonshine::webserver::pairing: Waiting for pin to be sent at http://192.168.1.106:47989/pin?uniqueid=aaaa1111bbbb2222
2026-11-05T10:01:30Z  INFO moonshine::webserver::pairing: Waiting for pin to be sent at http://192.168.1.106:47989/pin?uniqueid=CCCC3333DDDD4444
"""


def rejected(*args, **kwargs):
    raise HTTPError("http://127.0.0.1:47989/submit-pin", 400, "Bad Request", {}, None)


class Pending(unittest.TestCase):
    @patch.object(control, "run", return_value=LOG)
    def test_registered_requests_are_cleared(self, run):
        self.assertEqual(control.moonshine_pending(), ["AAAA1111BBBB2222", "CCCC3333DDDD4444"])
        self.assertEqual(run.call_args.args[:3], ("journalctl", "--unit", "moonshine.service"))

    @patch.object(control, "moonshine_submit", return_value=True)
    @patch.object(control, "moonshine_pending", return_value=["AAAA1111BBBB2222", "CCCC3333DDDD4444"])
    def test_pin_goes_only_to_newest_request(self, pending, submit):
        self.assertTrue(control.moonshine_pair("1234"))
        submit.assert_called_once_with("CCCC3333DDDD4444", "1234")

    @patch.object(control, "moonshine_pending", side_effect=OSError("no journal"))
    @patch.object(control, "urlopen")
    def test_falls_back_to_moonlight_default_id(self, urlopen, pending):
        urlopen.return_value.__enter__.return_value.status = 200
        self.assertTrue(control.moonshine_pair("1234"))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:47989/submit-pin")
        self.assertEqual(request.data, b"uniqueid=0123456789ABCDEF&pin=1234")

    @patch.object(control, "moonshine_pending", return_value=[])
    @patch.object(control, "urlopen", side_effect=rejected)
    def test_no_waiting_client(self, urlopen, pending):
        self.assertFalse(control.moonshine_pair("1234"))


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)

    def pair(self, pin="1234"):
        return self.client.post("/api/control/moonshine/pair", headers=HEADERS, json={"pin": pin})

    @patch.object(control, "moonshine_pair", return_value=True)
    def test_pin_validation_and_success(self, pair):
        for pin in ["123", "12345", "abcd", " 1234"]:
            self.assertEqual(self.pair(pin).status_code, 400)
        self.assertEqual(self.pair().status_code, 200)
        pair.assert_called_once_with("1234")

    @patch.object(control, "moonshine_pair", return_value=False)
    def test_nothing_waiting_is_conflict(self, pair):
        self.assertEqual(self.pair().status_code, 409)

    @patch.object(control, "moonshine_pair", side_effect=OSError("refused"))
    def test_moonshine_down(self, pair):
        response = self.pair()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("refused", response.text)

    @patch.object(control, "moonshine_pending", return_value=["0123456789ABCDEF"])
    def test_status(self, pending):
        self.assertEqual(self.client.get("/api/control/moonshine/pairing").json(), {"waiting": True})

    @patch.object(control, "moonshine_pair", return_value=True)
    def test_requires_permission_and_cross_origin_block(self, pair):
        self.client.post("/api/control/users", headers=HEADERS, json={
            "username": "guest", "password": "guest password",
            "permissions": {"modules": ["quick-actions"], "actions": ["display-on"]}})
        cross = self.client.post("/api/control/moonshine/pair", json={"pin": "1234"},
                                 headers={**HEADERS, "Origin": "https://attacker.example"})
        self.assertEqual(cross.status_code, 403)
        self.client.cookies.clear()
        self.client.post("/api/control/auth/login", headers=HEADERS, json={"username": "guest", "password": "guest password"})
        self.assertEqual(self.pair().status_code, 403)
        self.assertEqual(self.client.get("/api/control/moonshine/pairing").status_code, 403)
        with patch.object(control, "ENABLED_ACTIONS", control.ACTIONS):
            self.client.cookies.clear()
            setup = self.client.post("/api/control/auth/login", headers=HEADERS,
                                     json={"username": "admin", "password": "correct horse"})
            self.assertEqual(setup.status_code, 200)
            self.assertEqual(self.pair().status_code, 403)
        pair.assert_not_called()


if __name__ == "__main__":
    unittest.main()
