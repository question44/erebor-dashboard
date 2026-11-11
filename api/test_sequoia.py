import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import control
from fixtures import isolated_store, setup_admin


class Sequoia(unittest.TestCase):
    def setUp(self):
        self.client = isolated_store(self)
        setup_admin(self.client)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "web.json"
        self.data.write_text(json.dumps({"version": 1}))

    def configure(self, value):
        patcher = patch.object(control, "SEQUOIA_DATA", value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_signed_in_reads_cacheable_data(self):
        self.configure(str(self.data))
        self.assertTrue(self.client.get("/api/control/config").json()["sequoia"])
        response = self.client.get("/api/control/sequoia")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"version": 1})
        self.assertEqual(response.headers["cache-control"], "private, max-age=86400")

    def test_signed_out_and_unconfigured(self):
        self.configure(str(self.data))
        self.client.cookies.clear()
        response = self.client.get("/api/control/sequoia")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_missing_file_hides_it(self):
        self.configure(str(self.data.with_name("missing.json")))
        self.assertNotIn("sequoia", self.client.get("/api/control/config").json())
        self.assertEqual(self.client.get("/api/control/sequoia").status_code, 404)
        self.configure(None)
        self.assertEqual(self.client.get("/api/control/sequoia").status_code, 404)
