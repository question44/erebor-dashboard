"""Per-account card and service arrangement."""

from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from . import auth
from .access import User, store
from .models import ControlResult, error

router = APIRouter()

LayoutId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
LayoutList = Annotated[list[LayoutId], Field(max_length=48)]


class Layout(BaseModel):
    """Order and visibility of this account's cards and services; unknown IDs are ignored by the UI."""
    model_config = ConfigDict(extra="forbid")
    cards: LayoutList = []
    services: LayoutList = []
    hiddenCards: LayoutList = []
    hiddenServices: LayoutList = []
    showHidden: bool = False
    folded: list[Literal["cards", "services"]] = []
    foldRows: dict[Literal["cards", "services"], Annotated[int, Field(ge=1, le=auth.MAX_FOLD_ROWS)]] = {}


@router.put("/api/control/layout", response_model=ControlResult)
def save_layout(layout: Layout, user: User):
    try:
        store().set_layout(user["username"], layout.model_dump())
    except auth.AuthError as exc:
        return error(exc.status, str(exc))
    return ControlResult(message="Layout saved")
