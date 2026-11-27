"""Power profiles (power-profiles-daemon), sleep, power off and the Windows reboot,
run directly (without a system control daemon)."""

import json
import subprocess
from typing import Literal

from pydantic import BaseModel

from . import settings, system
from .errors import ActionError
from .modes import profile_mode_summary

PROFILES = ("performance", "balanced", "power-saver")
POWER_PROFILES = ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles",
                  "org.freedesktop.UPower.PowerProfiles")
PROFILE_DETAILS = {"performance": "Maximum CPU performance; uses more power.",
                   "balanced": "Default balance of performance and power use.",
                   "power-saver": "Reduced CPU performance to save power."}


class ProfileStatus(BaseModel):
    state: Literal["performance", "balanced", "power-saver", "unknown"]
    detail: str


def set_profile(profile: str):
    if profile not in PROFILES:
        raise ActionError("unknown-profile", "Unknown power profile.", 400)
    # power-profiles-daemon checks polkit; a rule grants this user without a session.
    system.run("busctl", "--system", "--allow-interactive-authorization=no", "set-property",
               *POWER_PROFILES, "ActiveProfile", "s", profile)


def login1(method: str):
    # login1 applies the existing wheel policy and respects inhibitors.
    system.run("busctl", "--system", "--allow-interactive-authorization=no", "call",
               "org.freedesktop.login1", "/org/freedesktop/login1",
               "org.freedesktop.login1.Manager", method, "b", "false")


def reboot_windows():
    # Only this fixed unit may write the one-time boot selection (granted by polkit).
    system.run("systemctl", "--system", "--no-ask-password", "start", settings.REBOOT_WINDOWS_UNIT, timeout=65)


def power_property(name):
    result = json.loads(system.run("busctl", "--system", "--json=short", "get-property", *POWER_PROFILES, name,
                                   timeout=2))
    if result.get("type") != "s" or not isinstance(result.get("data"), str):
        raise ValueError("Unexpected power profile property")
    return result["data"]


def profile_status(modes=None) -> ProfileStatus:
    try:
        state = power_property("ActiveProfile")
        detail = PROFILE_DETAILS[state]
        if state == "performance":
            degraded = power_property("PerformanceDegraded")
            if degraded:
                detail = f"Performance is limited ({degraded})."
        summary = profile_mode_summary(modes) if modes else ""
        if summary:
            detail = f"{detail} {summary}"
        return ProfileStatus(state=state, detail=detail)
    except (OSError, ValueError, KeyError, AttributeError, subprocess.SubprocessError):
        return ProfileStatus(state="unknown", detail="Power profile status is unavailable.")
