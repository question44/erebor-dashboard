"""Runtime configuration, read once from the environment (see nix/module.nix).

Modules read these as ``settings.NAME`` at call time, so tests can patch them.
"""

import json
import os
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def data_file(name: str) -> Path:
    """A repository data file: installed next to the package, or at the repository root."""
    for candidate in (PACKAGE / name, PACKAGE.parents[1] / name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(name)


def _json_env(name: str, default):
    raw = os.environ.get(name)
    return json.loads(raw) if raw else default


def _list_env(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


def load_settings() -> dict:
    path = os.environ.get("DASHBOARD_SETTINGS")
    if not path:
        return {}
    with open(path) as stream:
        return json.load(stream)


def state_path() -> Path:
    base = (os.environ.get("DASHBOARD_STATE_DIR") or os.environ.get("STATE_DIRECTORY")
            or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "erebor-dashboard")
    return Path(base) / "users.sqlite3"


VERSION = data_file("VERSION").read_text().strip()
LOG_LEVEL = os.environ.get("DASHBOARD_LOG_LEVEL", "INFO").upper()
PORT = int(os.environ.get("DASHBOARD_PORT", "18090"))

# The same-origin boundary: accepted hostnames (private and tailnet IPs always are).
HOSTS = set(_list_env("DASHBOARD_HOSTS", "localhost,127.0.0.1"))
# Actions the API may execute (ids from actions.json).
ENABLED_ACTIONS = set(_list_env("DASHBOARD_ACTIONS"))
# Public dashboard settings (title, services, actions) served per account.
SETTINGS = load_settings()

# Moonshine (Sunshine-compatible game streaming host) for `moonshine-pair`.
MOONSHINE_URL = os.environ.get("DASHBOARD_MOONSHINE_URL", "http://127.0.0.1:47989").rstrip("/")
MOONSHINE_UNIT = os.environ.get("DASHBOARD_MOONSHINE_UNIT", "moonshine.service")

# Ambilight: WLED over HTTP plus HyperHDR's JSON API (its default port).
WLED_URL = (os.environ.get("DASHBOARD_WLED_URL") or "").rstrip("/") or None
_hyperhdr = os.environ.get("DASHBOARD_HYPERHDR", "127.0.0.1:19444").rpartition(":")
HYPERHDR = (_hyperhdr[0] or "127.0.0.1", int(_hyperhdr[2]))

# Display: compositor outputs that are not physical screens (capture or streaming outputs).
IGNORED_OUTPUTS = frozenset(_list_env("DASHBOARD_IGNORED_OUTPUTS", "ambilight,sunshine"))
# Screen modes of the compositor's screen switcher: mode id -> label.
SCREEN_MODES: dict[str, str] = _json_env("DASHBOARD_SCREEN_MODES", {"dp": "DP-1", "hdmi": "HDMI"})

# Optional task board for administrators: a command printing the board as JSON
# (argv as a JSON array) and the URL prefix its repository-relative paths join.
TASKS_COMMAND = _json_env("DASHBOARD_TASKS_COMMAND", None)
TASKS_LINK_BASE = os.environ.get("DASHBOARD_TASKS_LINK_BASE", "")

# Optional desktop-mode state: a JSON file (erebor-modes' state.json) naming the
# active modes and what each changed. Read on each status refresh.
MODES_STATE = os.environ.get("DASHBOARD_MODES_STATE") or None

# Optional system control daemon (erebor-sysd): when set, power profile, sleep,
# power off and the Windows reboot go through it and its approval policy;
# otherwise they run directly.
SYSD_SOCKET = os.environ.get("DASHBOARD_SYSD_SOCKET") or None
# The fixed system unit the direct path starts for `reboot-windows`.
REBOOT_WINDOWS_UNIT = os.environ.get("DASHBOARD_REBOOT_WINDOWS_UNIT", "dashboard-reboot-windows.service")

# How long one /api/control/status result is shared between clients.
STATUS_TTL = float(os.environ.get("DASHBOARD_STATUS_TTL", "2"))
