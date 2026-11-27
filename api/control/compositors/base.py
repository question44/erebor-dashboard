"""What the dashboard needs from a Wayland compositor.

An adapter wraps one compositor's command-line interface. Add a compositor by
writing a subclass in its own module and listing it in ``compositors.ADAPTERS``;
the display and screen cards then work in that desktop session.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Output:
    name: str
    # False: switched off by the compositor (e.g. the screen switcher); None: not reported.
    enabled: bool | None
    # DPMS power; None: not reported.
    powered: bool | None


class Unsupported(Exception):
    """This compositor cannot do that (e.g. no screen switcher)."""


class Compositor:
    # Lowercase XDG_CURRENT_DESKTOP entry that selects this adapter.
    name = ""
    # Session variables (beyond WAYLAND_DISPLAY and XDG_CURRENT_DESKTOP) its commands need.
    session_keys: frozenset[str] = frozenset()

    def __init__(self, env: dict, run: Callable[..., str]):
        self.env, self._run = env, run

    def run(self, *args, timeout=12) -> str:
        return self._run(*args, env=self.env, timeout=timeout)

    def outputs(self, timeout=12) -> list[Output]:
        """Every output, including virtual ones (filtered by the caller)."""
        raise NotImplementedError

    def set_power(self, output: str, on: bool, timeout=12):
        """DPMS one output on or off."""
        raise NotImplementedError

    def screen_mode(self, timeout=12) -> str:
        """The screen switcher's state: a mode id, "both" or "none"."""
        raise Unsupported("Screen switching is not supported in this desktop session.")

    def set_screen_mode(self, mode: str, timeout=12):
        raise Unsupported("Screen switching is not supported in this desktop session.")
