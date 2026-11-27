"""Compositor adapters, chosen by the session's XDG_CURRENT_DESKTOP."""

from .base import Compositor, Output, Unsupported
from .umbriel import Umbriel

ADAPTERS: dict[str, type[Compositor]] = {c.name: c for c in (Umbriel,)}


def session_keys() -> set[str]:
    return set().union(*(c.session_keys for c in ADAPTERS.values()))


def for_session(env: dict, run) -> Compositor | None:
    """The adapter for the running desktop, or None when none is supported."""
    for desktop in env.get("XDG_CURRENT_DESKTOP", "").lower().split(":"):
        if desktop in ADAPTERS:
            return ADAPTERS[desktop](env, run)
    return None


__all__ = ["ADAPTERS", "Compositor", "Output", "Unsupported", "for_session", "session_keys"]
