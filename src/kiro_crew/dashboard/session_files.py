"""``GET /api/project/session-files``: the files one chat session edited.

The side panel's Files tab leads with this list. It is derived on the server
from the session's own transcript -- the ``file_changes`` rows the turn runner
attaches to assistant messages -- plus the change snapshots of the turn still
running, so it survives a page reload and reads the same from any browser.

Nothing here admits a path to another route. The list names files; opening
one goes through the file-read route and its own gate, and the tree keeps its
project-only boundary.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from aiohttp import web

from kiro_crew.dashboard.chat_utils_slots import effective_session_key, slot_history_key
from kiro_crew.dashboard.slot_ownership import deny_app_slot_session_access, slot_not_found
from kiro_crew.security import is_sensitive_path
from kiro_crew.session_environment import session_runs_remotely

if TYPE_CHECKING:
    from kiro_crew.dashboard.state import DashboardState

#: Files one response lists, newest first. The tab shows a short list; a
#: session that edited more says so through ``truncated``.
MAX_SESSION_FILES = 200

#: Longest path kept, in characters. A longer one is dropped, never shortened:
#: a cut path names a different file.
_MAX_PATH_CHARS = 4096


def _sel() -> Any:
    import kiro_crew.dashboard.handlers as _pkg

    return _pkg.sel()


def _change_paths(rows: Iterable[Mapping[str, Any]]) -> Iterable[tuple[str, str]]:
    """``(path, ts)`` for every change an assistant row names, in row order. Pure.

    Only assistant rows count: the runner attaches file changes to those alone,
    so metadata on another role is not the agent's work. A write whose outcome
    could not be proven (``omitted_reason: unverified_write``) is skipped, as
    the Git tab's transcript seed skips it.
    """
    for row in rows:
        if not isinstance(row, Mapping) or row.get("role") != "assistant":
            continue
        meta = row.get("meta")
        changes = meta.get("file_changes") if isinstance(meta, Mapping) else None
        if not isinstance(changes, list):
            continue
        ts = row.get("ts")
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            if change.get("omitted_reason") == "unverified_write":
                continue
            path = change.get("path")
            if isinstance(path, str) and path:
                yield path, ts if isinstance(ts, str) else ""


def rows_omit_files(rows: Iterable[Mapping[str, Any]]) -> bool:
    """Whether an assistant row records changed files its turn left out. Pure.

    The turn runner caps one turn's ``file_changes`` and counts the rest in
    ``meta.file_changes_omitted_files``; those files are edits this list cannot
    name, so the response says it is incomplete.
    """
    for row in rows:
        if not isinstance(row, Mapping) or row.get("role") != "assistant":
            continue
        meta = row.get("meta")
        omitted = meta.get("file_changes_omitted_files") if isinstance(meta, Mapping) else None
        if isinstance(omitted, int) and not isinstance(omitted, bool) and omitted > 0:
            return True
    return False


def newest_first(
    persisted: Iterable[tuple[str, str]], pending: Iterable[str], now: str
) -> list[tuple[str, str]]:
    """Distinct paths, the most recently changed first. Pure.

    *persisted* is in transcript order (oldest first); *pending* are the paths
    of the running turn, which are newer than anything persisted and stamped
    *now*. A path changed twice is listed once, at its latest change.
    """
    latest: dict[str, str] = {}
    for path, ts in persisted:
        latest.pop(path, None)
        latest[path] = ts
    for path in pending:
        latest.pop(path, None)
        latest[path] = now
    return list(reversed(latest.items()))


def _resolve(path: str, project: str) -> str | None:
    """The absolute form of a recorded *path*, or None when it has none."""
    if len(path) > _MAX_PATH_CHARS or "\0" in path:
        return None
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    if not project:
        return None
    return os.path.normpath(os.path.join(os.path.expanduser(project), expanded))


def _describe(entries: list[tuple[str, str]], project: str) -> tuple[list[dict], bool]:
    """The response rows for *entries*. Worker thread only: it stats each path.

    A sensitive path is dropped, and so is one the credential redactor would
    mask: the masked string opens nothing, so its row could only fail. Either
    drop sets the returned flag, as the cap does.
    """
    # Through the handlers facade, which binds the redactor's own helpers.
    from kiro_crew.dashboard.handlers.files import _redact_project_path

    rows: list[dict] = []
    dropped = False
    seen: set[str] = set()
    for raw, ts in entries:
        path = _resolve(raw, project)
        if path is None or path in seen:
            dropped = dropped or path is None
            continue
        seen.add(path)
        if is_sensitive_path(path) or _redact_project_path(path) != path:
            dropped = True
            continue
        if len(rows) >= MAX_SESSION_FILES:
            dropped = True
            break
        rows.append({"path": path, "ts": ts, "exists": os.path.lexists(path)})
    return rows, dropped


def _read_rows(clog: Any, key: str) -> list[Mapping[str, Any]]:
    """Every file-change row of the tab's transcript chain, oldest first.

    Per chain member, in chain order: the rows size-rotation archived out of
    that member's file, then its live rows. Rotation keeps only the newest
    rows in the live file, and an edit in the archived head is still this
    chat's. Only ``rotate`` segments are read (``read_rotated_messages``),
    never content a rewind or compaction discarded. Each member's live rows
    are read before its archive: a rotation landing between the two reads can
    then only list a row twice, which ``newest_first`` folds, never drop it.

    A read that fails raises: an unreadable transcript is not an empty one,
    and the route answers it as an error the tab can retry.
    """
    chained = getattr(clog, "chained_keys", None)
    keys = (chained(key) if callable(chained) else None) or [key]
    rotated = getattr(clog, "read_rotated_messages", None)
    rows: list[Mapping[str, Any]] = []
    for each in keys:
        live = clog.read_file_change_messages(each)
        if callable(rotated):
            rows.extend(rotated(each))
        rows.extend(live)
    return rows


def _pending_paths(slot: Any) -> list[str]:
    """Paths the running turn has snapshotted so far. Event loop only.

    A snapshot whose write failed or was refused is marked ``unverified_write``
    and persists as ``omitted_reason: unverified_write``, which the transcript
    read skips; it is skipped here too, so a failed write never shows as edited.
    """
    held = getattr(slot, "_file_changes", None)
    if not isinstance(held, list):
        return []
    return [
        c["path"]
        for c in held
        if isinstance(c, dict) and isinstance(c.get("path"), str) and not c.get("unverified_write")
    ]


async def api_project_session_files(request: web.Request) -> web.Response:
    """GET /api/project/session-files?slot=... -- files this session edited.

    ``{files: [{path, ts, exists}], truncated}``: absolute paths, newest
    change first, at most :data:`MAX_SESSION_FILES`. ``ts`` is the transcript
    row's timestamp (the current time for the running turn); ``exists`` is
    false for a file deleted since. A relative recorded path is resolved
    against the slot's project directory and dropped without one. A transcript
    that cannot be read answers 503 ``transcript_unreadable``.
    """
    state: DashboardState = request.app["state"]
    caller = request.get("user", "dashboard")
    key = request.query.get("slot", "").strip()
    if not key:
        return web.json_response({"error": "slot required", "code": "slot_required"}, status=400)
    slot = state.get_slot(key)
    denied = deny_app_slot_session_access(
        request.get("app", ""), slot, key, "project_session_files"
    )
    if denied is not None:
        return denied
    if slot is None:
        return slot_not_found()
    project = slot.project or ""
    # A remote session edits files in its own workspace, not on this gateway.
    if await asyncio.to_thread(session_runs_remotely, effective_session_key(slot)):
        return web.json_response({"files": [], "truncated": False})
    clog = getattr(state, "conversation_log", None)
    pending = _pending_paths(slot)
    history_key = slot_history_key(slot)

    def work() -> tuple[list[dict], bool]:
        from datetime import datetime, timezone

        rows = _read_rows(clog, history_key) if clog is not None else []
        now = datetime.now(timezone.utc).isoformat()
        files, dropped = _describe(newest_first(_change_paths(rows), pending, now), project)
        return files, dropped or rows_omit_files(rows)

    try:
        files, truncated = await asyncio.to_thread(work)
    except (OSError, ValueError) as exc:
        _sel().log_api_access(
            caller=caller,
            operation="project_session_files",
            outcome="error",
            resources=f"slot={key}",
            error=type(exc).__name__,
        )
        return web.json_response(
            {"error": "Transcript could not be read", "code": "transcript_unreadable"},
            status=503,
        )
    _sel().log_api_access(
        caller=caller,
        operation="project_session_files",
        outcome="allowed",
        resources=f"slot={key}",
    )
    return web.json_response({"files": files, "truncated": truncated})
