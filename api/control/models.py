"""Response models shared by the routers."""

from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ControlResult(BaseModel):
    message: str


class ControlError(BaseModel):
    error: str
    # Stable machine-readable reason (see README "Error codes"); absent on plain validation errors.
    code: str | None = None


def error(status: int, message: str, code: str | None = None, headers=None) -> JSONResponse:
    return JSONResponse({"error": message, **({"code": code} if code else {})}, status, headers=headers)


def responses(*codes):
    return {code: {"model": ControlError} for code in codes}
