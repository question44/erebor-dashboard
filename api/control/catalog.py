"""The action catalog (actions.json): one list of action ids for the API, the
Nix module's option type and the frontend build."""

import json

from . import settings

CATALOG = json.loads(settings.data_file("actions.json").read_text())
ACTIONS_BY_ID = {a["id"]: a for a in CATALOG["actions"]}
# Actions POST /api/control runs (moonshine-pair has its own endpoint, wake runs in the browser).
CONTROL_ACTIONS = tuple(a["id"] for a in CATALOG["actions"] if a["endpoint"] == "/api/control")
# Every action the API can enable.
HOST_ACTIONS = tuple(a["id"] for a in CATALOG["actions"] if a["endpoint"])
MOONSHINE_PAIR = "moonshine-pair"
