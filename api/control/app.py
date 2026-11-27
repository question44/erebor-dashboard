"""The control API: loopback-only, behind a reverse proxy that supplies the network boundary."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException

from . import accounts, actions, clientlog, layout, logs, moonshine, settings, status, tasks
from .access import User, catalog, store
from .boundary import AccessLog, DashboardBoundary
from .models import error

app = FastAPI(title="Dashboard control API", version=settings.VERSION, redirect_slashes=False,
              docs_url="/api/control/docs", redoc_url=None, openapi_url="/api/control/openapi.json")
app.add_middleware(DashboardBoundary)
# Added last, so outermost: it also sees the boundary's refusals.
app.add_middleware(AccessLog)
for module in (accounts, layout, tasks, moonshine, status, actions, clientlog):
    app.include_router(module.router)


def invalid(request: Request) -> str:
    return "Invalid action" if request.url.path == "/api/control" else "Invalid request"


@app.exception_handler(RequestValidationError)
async def invalid_command(request: Request, exc: RequestValidationError):
    return error(400, invalid(request))


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return error(exc.status_code, invalid(request) if exc.status_code == 400 else str(exc.detail), headers=exc.headers)


@app.get("/api/control/config")
def read_config(user: User):
    """Dashboard settings reduced to what this account may see."""
    view = {**catalog().view(user), "user": {"username": user["username"], "admin": user["admin"]},
            "layout": store().layout(user["username"])}
    if user["admin"] and settings.TASKS_COMMAND:
        view["taskBoard"] = True
    if any(a.startswith("screen-") for a in view["actions"]):
        view["screens"] = dict(settings.SCREEN_MODES)
    approvals = actions.approvals(user)
    if approvals:
        view["approvals"] = approvals
    return view


def control_openapi():
    if app.openapi_schema is None:
        app.openapi_schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        # Validation keeps the existing 400 response contract rather than 422.
        for path in app.openapi_schema["paths"].values():
            for operation in path.values():
                operation.get("responses", {}).pop("422", None)
    return app.openapi_schema


app.openapi = control_openapi


def main():
    import uvicorn
    logs.setup(settings.LOG_LEVEL)
    uvicorn.run(app, host="127.0.0.1", port=settings.PORT, proxy_headers=False, server_header=False,
                access_log=False, log_config=None)
