"""Desktop modes (e.g. erebor-modes' state.json): what an active mode changed."""

import json
from pathlib import Path

from . import settings


def modes_state():
    """Active desktop modes, or None: {"modes": {id: {"label", "kind"}}, "items": {id: {...}}}."""
    if not settings.MODES_STATE:
        return None
    try:
        state = json.loads(Path(settings.MODES_STATE).read_text())
        modes, items = state["modes"], state.get("items") or {}
        if not isinstance(modes, list) or not modes or not isinstance(items, dict):
            return None
        labels, kinds = state.get("mode_labels") or {}, state.get("mode_kinds") or {}
        return {"modes": {m: {"label": str(labels.get(m, m)), "kind": kinds.get(m, "profile")} for m in modes},
                "items": items}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def mode_holding(state, item_id):
    """Label of the mode that covers an item (applied or skipped), or None."""
    item = state["items"].get(item_id) if state else None
    if not isinstance(item, dict) or item.get("status") not in {"applied", "skipped"}:
        return None
    mode = state["modes"].get(item.get("mode"))
    return mode["label"] if mode else None


def profile_mode_summary(state):
    """What the power-profile modes (not toggles such as reduce-transparency) changed."""
    parts = []
    for mode_id, mode in state["modes"].items():
        if mode["kind"] != "profile":
            continue
        labels = sorted(str(i.get("label")) for i in state["items"].values()
                        if isinstance(i, dict) and i.get("status") == "applied" and i.get("mode") == mode_id)
        parts.append(f"{mode['label']} on: " + (", ".join(labels) if labels else "nothing changed") + ".")
    return " ".join(parts)
