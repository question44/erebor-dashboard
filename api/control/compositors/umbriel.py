"""Umbriel: `umbriel outputs --json`, `umbriel msg dpms-on|off`, and the
`umbriel-display-mode` screen switcher (both must be on the service's PATH)."""

import json

from .base import Compositor, Output


def _flag(value):
    return value if type(value) is bool else None


class Umbriel(Compositor):
    name = "umbriel"
    session_keys = frozenset({"UMBRIEL_SOCKET"})

    def outputs(self, timeout=12):
        reply = json.loads(self.run("umbriel", "outputs", "--json", timeout=timeout))
        if not isinstance(reply, list) or not all(isinstance(o, dict) and isinstance(o.get("name"), str) for o in reply):
            raise ValueError("Unexpected umbriel outputs reply")
        return [Output(o["name"], _flag(o.get("enabled")), _flag(o.get("powered"))) for o in reply]

    def set_power(self, output, on, timeout=12):
        self.run("umbriel", "msg", "dpms-on" if on else "dpms-off", output, timeout=timeout)

    def screen_mode(self, timeout=12):
        return self.run("umbriel-display-mode", "status", timeout=timeout).strip()

    def set_screen_mode(self, mode, timeout=12):
        self.run("umbriel-display-mode", mode, timeout=timeout)
