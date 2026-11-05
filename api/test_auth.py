import unittest
from unittest.mock import patch

import auth
import control
from fixtures import HEADERS, isolated_store, setup_admin


class FirstRun(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)

    def test_setup_wizard_creates_one_admin_only(self):
        self.assertEqual(self.client.get("/api/control/auth/session").json(), {"setupRequired": True, "user": None})
        self.assertEqual(self.client.get("/api/control/config").status_code, 401)
        response = setup_admin(self.client, "Owner")
        self.assertEqual(response.json()["user"], {"username": "owner", "admin": True})
        cookie = response.headers["set-cookie"].lower()
        for attribute in ["httponly", "samesite=strict", "path=/api/control"]:
            self.assertIn(attribute, cookie)
        self.assertEqual(self.client.get("/api/control/auth/session").json()["user"]["username"], "owner")
        again = self.client.post("/api/control/auth/setup", headers=HEADERS,
                                 json={"username": "intruder", "password": "another password"})
        self.assertEqual(again.status_code, 409)
        self.assertEqual([u["username"] for u in control.store().users()], ["owner"])

    def test_setup_validates_input_and_origin(self):
        for body in [{"username": "admin", "password": "short"}, {"username": "Bad Name!", "password": "long enough"},
                     {"username": "admin"}, {"username": "admin", "password": "long enough", "admin": False}]:
            self.assertEqual(self.client.post("/api/control/auth/setup", headers=HEADERS, json=body).status_code, 400)
        cross = self.client.post("/api/control/auth/setup", json={"username": "a", "password": "long enough"},
                                 headers={**HEADERS, "Origin": "https://attacker.example"})
        self.assertEqual(cross.status_code, 403)
        self.assertTrue(control.store().setup_required())

    def test_secure_cookie_behind_https_proxy(self):
        response = self.client.post("/api/control/auth/setup", json={"username": "a", "password": "long enough"},
                                    headers={**HEADERS, "X-Forwarded-Proto": "https"})
        self.assertIn("secure", response.headers["set-cookie"].lower())


class Accounts(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)

    def add(self, username="guest", password="guest password", **extra):
        return self.client.post("/api/control/users", headers=HEADERS,
                                json={"username": username, "password": password, **extra})

    def sign_in(self, username="guest", password="guest password"):
        self.client.cookies.clear()
        return self.client.post("/api/control/auth/login", headers=HEADERS,
                                json={"username": username, "password": password})

    def test_admin_sees_everything(self):
        config = self.client.get("/api/control/config").json()
        self.assertEqual(config["actions"], control.SETTINGS["actions"])
        self.assertEqual([s["id"] for s in config["services"]], ["media", "code"])
        self.assertIn("outletUrl", config)
        self.assertTrue(config["hostStatus"])

    @patch.object(control, "action")
    def test_permissions_filter_config_and_enforce_actions(self, action):
        self.assertEqual(self.add(permissions={"modules": ["quick-actions", "services"], "services": ["media", "nope"],
                                               "actions": ["display-on", "wake", "unknown"]}).status_code, 200)
        self.assertEqual(self.sign_in().status_code, 200)
        config = self.client.get("/api/control/config").json()
        self.assertEqual(config["actions"], ["wake", "display-on"])
        self.assertEqual([s["id"] for s in config["services"]], ["media"])
        self.assertNotIn("outletUrl", config)
        self.assertIn("wakeUrl", config)
        self.assertFalse(config["hostStatus"])
        self.assertEqual(config["user"], {"username": "guest", "admin": False})
        ok = self.client.post("/api/control", headers=HEADERS, json={"action": "display-on"})
        self.assertEqual(ok.status_code, 200)
        denied = self.client.post("/api/control", headers=HEADERS, json={"action": "power-off"})
        self.assertEqual(denied.status_code, 403)
        action.assert_called_once_with("display-on")
        self.assertEqual(self.client.get("/api/control/users").status_code, 403)
        self.assertEqual(self.add("other").status_code, 403)

    def test_hidden_module_hides_its_items(self):
        self.add(permissions={"modules": [], "services": ["media"], "actions": ["sleep"]})
        self.sign_in()
        config = self.client.get("/api/control/config").json()
        self.assertEqual((config["actions"], config["services"]), ([], []))
        self.assertNotIn("wakeUrl", config)
        self.assertEqual(self.client.post("/api/control", headers=HEADERS, json={"action": "sleep"}).status_code, 403)

    def test_update_delete_and_self_protection(self):
        self.add()
        change = {"admin": True, "permissions": {"modules": ["services"]}}
        self.assertEqual(self.client.put("/api/control/users/guest", headers=HEADERS, json=change).status_code, 200)
        users = {u["username"]: u for u in self.client.get("/api/control/users").json()["users"]}
        self.assertTrue(users["guest"]["admin"])
        self.assertEqual(users["guest"]["permissions"]["modules"], ["services"])
        self.assertEqual(self.client.put("/api/control/users/admin", headers=HEADERS, json={"admin": False}).status_code, 409)
        self.assertEqual(self.client.request("DELETE", "/api/control/users/admin", headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.request("DELETE", "/api/control/users/guest", headers=HEADERS).status_code, 200)
        self.assertEqual(self.client.request("DELETE", "/api/control/users/guest", headers=HEADERS).status_code, 404)
        self.assertEqual(self.add("admin").status_code, 409)

    def test_deleted_user_and_password_reset_revoke_sessions(self):
        self.add()
        self.sign_in()
        guest_cookie = dict(self.client.cookies)
        self.sign_in("admin", "correct horse")
        self.client.put("/api/control/users/guest", headers=HEADERS, json={"password": "brand new password"})
        self.client.cookies.clear()
        self.client.cookies.update(guest_cookie)
        self.assertEqual(self.client.get("/api/control/config").status_code, 401)
        self.assertEqual(self.sign_in("guest", "guest password").status_code, 401)
        self.assertEqual(self.sign_in("guest", "brand new password").status_code, 200)

    def test_own_password_change_keeps_current_session(self):
        wrong = self.client.post("/api/control/auth/password", headers=HEADERS,
                                 json={"current": "nope", "password": "a new password"})
        self.assertEqual(wrong.status_code, 403)
        right = self.client.post("/api/control/auth/password", headers=HEADERS,
                                 json={"current": "correct horse", "password": "a new password"})
        self.assertEqual(right.status_code, 200)
        self.assertEqual(self.client.get("/api/control/config").status_code, 200)
        self.assertEqual(self.sign_in("admin", "a new password").status_code, 200)

    def test_logout_and_throttle(self):
        self.client.post("/api/control/auth/logout", headers=HEADERS, content="{}")
        self.assertEqual(self.client.get("/api/control/config").status_code, 401)
        self.assertIsNone(self.client.get("/api/control/auth/session").json()["user"])
        for _ in range(4):
            self.assertEqual(self.sign_in("admin", "wrong").status_code, 401)
        self.assertEqual(self.sign_in("admin", "wrong").status_code, 401)
        locked = self.sign_in("admin", "correct horse")
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(self.sign_in("ghost", "whatever").status_code, 401)


class Passwords(unittest.TestCase):
    def test_hash_roundtrip_and_salt(self):
        first, second = auth.hash_password("secret pw"), auth.hash_password("secret pw")
        self.assertNotEqual(first, second)
        self.assertTrue(auth.verify_password("secret pw", first))
        self.assertFalse(auth.verify_password("other", first))
        self.assertFalse(auth.verify_password("secret pw", "garbage"))


class Layouts(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)

    def save(self, layout):
        return self.client.put("/api/control/layout", headers=HEADERS, json=layout)

    def test_round_trip_is_per_account(self):
        empty = {"cards": [], "services": [], "hiddenCards": [], "hiddenServices": [], "showHidden": False,
                 "folded": [], "foldRows": {}}
        self.assertEqual(self.client.get("/api/control/config").json()["layout"], empty)
        layout = {"cards": ["profile", "display", "profile"], "services": ["code", "media"],
                  "hiddenCards": ["screen"], "hiddenServices": ["media"], "showHidden": True,
                  "folded": ["services"], "foldRows": {"cards": 2, "services": 1}}
        self.assertEqual(self.save(layout).status_code, 200)
        saved = self.client.get("/api/control/config").json()["layout"]
        self.assertEqual(saved, {**layout, "cards": ["profile", "display"]})
        self.client.post("/api/control/users", headers=HEADERS,
                         json={"username": "guest", "password": "guest password"})
        self.client.post("/api/control/auth/logout", headers=HEADERS, json={})
        self.client.post("/api/control/auth/login", headers=HEADERS,
                         json={"username": "guest", "password": "guest password"})
        self.assertEqual(self.client.get("/api/control/config").json()["layout"], empty)

    def test_rejects_invalid_layouts(self):
        for layout in [{"cards": "display"}, {"extra": []}, {"cards": [""]}, {"cards": ["x"] * 49},
                       {"folded": ["header"]}, {"foldRows": {"cards": 0}}, {"foldRows": {"other": 1}},
                       {"services": [1]}]:
            with self.subTest(layout=layout):
                self.assertEqual(self.save(layout).status_code, 400)

    def test_requires_sign_in(self):
        self.client.post("/api/control/auth/logout", headers=HEADERS, json={})
        self.assertEqual(self.save({}).status_code, 401)

    def test_migrates_existing_database(self):
        import sqlite3, tempfile
        from contextlib import closing
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE users (username TEXT PRIMARY KEY, password TEXT NOT NULL, "
                           "admin INTEGER NOT NULL DEFAULT 0, permissions TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL)")
                db.execute("INSERT INTO users VALUES ('old', 'x', 1, '{}', 0)")
            store = auth.Store(path)
            self.assertEqual(store.layout("old")["cards"], [])
            store.set_layout("old", {"cards": ["profile"]})
            self.assertEqual(store.layout("old")["cards"], ["profile"])


if __name__ == "__main__":
    unittest.main()
