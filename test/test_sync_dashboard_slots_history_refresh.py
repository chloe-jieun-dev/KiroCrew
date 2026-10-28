"""``_sync_dashboard_slots`` pushes ``history`` when a session becomes an open tab.

The Older Sessions listing leaves out every transcript an open slot holds, and tabs
re-read it only on a ``history`` frame. Every path that opens a tab publishes the slot
set through this sync, so the sync is where a newly open session is noticed. The push
goes through ``push_listing_refresh``, which coalesces a bulk restore into a few frames.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kiro_crew.dashboard import chat_utils_slots
from kiro_crew.dashboard.chat_utils_slots import _sync_dashboard_slots
from kiro_crew.dashboard.state import DashboardState


@pytest.fixture
def _no_surface_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the process-global surface registry out of these tests."""
    monkeypatch.setattr(chat_utils_slots, "set_dashboard_surfaced", lambda _keys: None)


pytestmark = pytest.mark.usefixtures("_no_surface_registry")


def _slot(key: str, linked: str = "") -> SimpleNamespace:
    return SimpleNamespace(key=key, linked_session_key=linked)


def _state(*slots: SimpleNamespace) -> DashboardState:
    state = DashboardState.__new__(DashboardState)
    state._slots = {s.key: s for s in slots}
    state.sessions = MagicMock()
    state.push_listing_refresh = MagicMock()  # type: ignore[method-assign]
    state.push_refresh = MagicMock()  # type: ignore[method-assign]
    return state


def test_gaining_a_key_pushes_history_once() -> None:
    state = _state(_slot("chat-1"))

    _sync_dashboard_slots(state)

    state.push_listing_refresh.assert_called_once_with()  # type: ignore[attr-defined]
    state.push_refresh.assert_not_called()  # type: ignore[attr-defined]


def test_the_same_slots_again_push_nothing() -> None:
    state = _state(_slot("chat-1"), _slot("slack_1.1", linked="slack:1.1"))
    _sync_dashboard_slots(state)
    assert state.push_listing_refresh.call_count == 1  # type: ignore[attr-defined]

    _sync_dashboard_slots(state)

    assert state.push_listing_refresh.call_count == 1  # type: ignore[attr-defined]


def test_dropping_a_slot_pushes_nothing() -> None:
    state = _state(_slot("chat-1"), _slot("chat-2"))
    _sync_dashboard_slots(state)
    assert state.push_listing_refresh.call_count == 1  # type: ignore[attr-defined]

    del state._slots["chat-2"]
    _sync_dashboard_slots(state)

    assert state.push_listing_refresh.call_count == 1  # type: ignore[attr-defined]


def test_a_state_without_push_listing_refresh_still_tracks_the_published_keys() -> None:
    state = SimpleNamespace(_slots={"chat-1": _slot("chat-1")}, sessions=MagicMock())

    _sync_dashboard_slots(state)  # type: ignore[arg-type]

    assert state._published_slot_keys == frozenset({"dashboard:chat-1"})
    state.push_listing_refresh = MagicMock()
    _sync_dashboard_slots(state)  # type: ignore[arg-type]
    state.push_listing_refresh.assert_not_called()
    state._slots["chat-2"] = _slot("chat-2")
    _sync_dashboard_slots(state)  # type: ignore[arg-type]
    state.push_listing_refresh.assert_called_once_with()
