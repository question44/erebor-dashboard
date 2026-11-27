"""Subprocesses and the desktop session's environment."""

import os
import subprocess

from . import compositors

# Variables a desktop session exports to the user manager that commands need.
SESSION_KEYS = {"WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP"}


def run(*args, env=None, timeout=12) -> str:
    return subprocess.run(args, env=env, check=True, capture_output=True, text=True, timeout=timeout).stdout


def session_environment(timeout=12) -> dict:
    """This process's environment plus the current session's display variables.

    Read for each request (once per status refresh): the service outlives compositor logins.
    """
    env = os.environ.copy()
    keys = SESSION_KEYS | compositors.session_keys()
    for line in run("systemctl", "--user", "show-environment", timeout=timeout).splitlines():
        key, sep, value = line.partition("=")
        if sep and key in keys:
            env[key] = value
    return env
