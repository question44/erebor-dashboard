"""A read-only task board for administrators, from a configured command's JSON.

Contract (README "Task board"): the command prints an object with "counts"
(status -> int), "overdue" (int), "epics" and "tasks" (lists of objects) and
optionally "statuses" ([{"id", "label"}], display order) and "schema". Any
status id matching STATUS is accepted, so a new status on the producer's side
never breaks the board; one malformed task is dropped (and logged once), only
a wrong top-level shape fails the board. Only the documented fields are
passed on, and links are only built for safe repository-relative paths.
"""

import json
import logging
import re
import subprocess
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from . import settings
from .access import Admin
from .logs import Once, event, log
from .models import error

router = APIRouter()

STATUS = r"^[A-Z][A-Z_-]{0,15}$"
TASK_PATH = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*\.md$")
MAX_TASKS, MAX_EPICS, MAX_STATUSES = 1000, 100, 32
OUTPUT_LIMIT = 4 * 1024 * 1024
once = Once()

StatusId = Annotated[str, StringConstraints(pattern=STATUS)]
TaskText = Annotated[str, StringConstraints(max_length=200)]
Count = Annotated[int, Field(ge=0)]


class TaskItem(BaseModel):
    id: Annotated[str, StringConstraints(pattern=r"^[A-Z]+-[0-9]{1,6}$")]
    title: TaskText
    status: StatusId
    overdue: bool
    review: Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")] | None = None
    epic: TaskText | None = None
    kind: TaskText | None = None
    url: str | None = None


class TaskEpic(TaskItem):
    done: Count
    total: Count


class TaskStatus(BaseModel):
    id: StatusId
    label: Annotated[str, StringConstraints(min_length=1, max_length=40)]


class TaskBoard(BaseModel):
    counts: Annotated[dict[StatusId, Count], Field(max_length=MAX_STATUSES)]
    # Every status in display order, with a label; unknown ones are rendered generically.
    statuses: Annotated[list[TaskStatus], Field(max_length=MAX_STATUSES)]
    overdue: Count
    epics: Annotated[list[TaskEpic], Field(max_length=MAX_EPICS)]
    tasks: Annotated[list[TaskItem], Field(max_length=MAX_TASKS)]


class BoardShapeError(ValueError):
    """The command's output is not a task board at all."""


def warn_once(key: str, message: str, **kv):
    if once(key):
        event(log, logging.WARNING, message, **kv)


def link(path) -> str | None:
    if settings.TASKS_LINK_BASE and isinstance(path, str) and TASK_PATH.match(path) and ".." not in path:
        return settings.TASKS_LINK_BASE + path
    return None


def items(raw, model: type[TaskItem], limit: int, kind: str) -> list[TaskItem]:
    if len(raw) > limit:
        warn_once(f"{kind}-truncated", "task board truncated", list=kind, received=len(raw), kept=limit)
    kept = []
    for entry in raw[:limit]:
        if not isinstance(entry, dict):
            warn_once(f"{kind}-not-object", "task board entry dropped", list=kind, reason="not an object")
            continue
        fields = {key: entry.get(key) for key in model.model_fields if key != "url"}
        try:
            kept.append(model.model_validate({**fields, "url": link(entry.get("path"))}))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            ident = str(entry.get("id"))[:20]
            warn_once(f"{kind}-{ident}-{where}-{first['type']}", "task board entry dropped",
                      list=kind, id=ident, field=where, reason=first["type"])
    return kept


def label(status: str) -> str:
    return status.replace("_", " ").replace("-", " ").capitalize()


def statuses(raw, counts: dict, tasks: list[TaskItem]) -> list[TaskStatus]:
    """The producer's ordered list when valid, then any status it did not name."""
    result: dict[str, TaskStatus] = {}
    if isinstance(raw, list):
        for entry in raw[:MAX_STATUSES]:
            try:
                status = TaskStatus.model_validate(entry)
            except ValidationError:
                warn_once("status-invalid", "task board status dropped", value=str(entry)[:60])
                continue
            result.setdefault(status.id, status)
    for status in [*counts, *(t.status for t in tasks)]:
        if status not in result and len(result) < MAX_STATUSES:
            result[status] = TaskStatus(id=status, label=label(status))
    return list(result.values())


def clean_counts(raw: dict) -> dict:
    counts = {}
    for status, count in raw.items():
        if not (isinstance(status, str) and re.match(STATUS, status) and type(count) is int and count >= 0):
            warn_once(f"count-{str(status)[:20]}", "task board count dropped", status=str(status)[:20])
            continue
        if len(counts) < MAX_STATUSES:
            counts[status] = count
    return counts


def parse_board(raw) -> TaskBoard:
    if not isinstance(raw, dict):
        raise BoardShapeError("not an object")
    for key, kind in [("counts", dict), ("epics", list), ("tasks", list)]:
        if not isinstance(raw.get(key), kind):
            raise BoardShapeError(f"{key} is not a {kind.__name__}")
    if type(raw.get("overdue")) is not int or raw["overdue"] < 0:
        raise BoardShapeError("overdue is not a count")
    counts = clean_counts(raw["counts"])
    epics = items(raw["epics"], TaskEpic, MAX_EPICS, "epics")
    tasks = items(raw["tasks"], TaskItem, MAX_TASKS, "tasks")
    return TaskBoard(counts=counts, statuses=statuses(raw.get("statuses"), counts, [*epics, *tasks]),
                     overdue=raw["overdue"], epics=epics, tasks=tasks)


def task_board() -> TaskBoard:
    """Run the configured task command and keep only the documented fields."""
    result = subprocess.run(settings.TASKS_COMMAND, capture_output=True, text=True, timeout=10, check=True)
    if len(result.stdout) > OUTPUT_LIMIT:
        raise BoardShapeError("output too large")
    return parse_board(json.loads(result.stdout))


@router.get("/api/control/tasks", response_model=TaskBoard, response_model_exclude_none=True)
def read_tasks(admin: Admin):
    if not settings.TASKS_COMMAND:
        return error(404, "No task board is configured")
    try:
        board = task_board()
    except subprocess.CalledProcessError as exc:
        warn_once(f"exit-{exc.returncode}", "task board command failed", status=exc.returncode,
                  stderr=(exc.stderr or "").strip()[-200:])
    except subprocess.TimeoutExpired:
        warn_once("timeout", "task board command timed out")
    except OSError as exc:
        warn_once(f"os-{type(exc).__name__}", "task board command failed", error=exc)
    except ValueError as exc:  # bad JSON or BoardShapeError
        warn_once(f"shape-{exc}"[:80], "task board output rejected", error=str(exc)[:120])
    else:
        return board
    return error(503, "Could not read the task board", "tasks-unavailable")
