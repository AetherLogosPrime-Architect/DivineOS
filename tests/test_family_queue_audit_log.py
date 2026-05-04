"""Regression test for audit r9-21 #13: family_queue audit log via main ledger.

The bug:
  family/queue.py docstring claimed 'rows themselves are never deleted
  or edited in place' but mark_seen, mark_held, mark_addressed, and
  supersede all UPDATE the row's status field. No audit log of the
  state changes existed, so transitions were silent.

The fix:
  Every status transition emits a FAMILY_QUEUE_STATUS_CHANGED event
  to the main hash-chained event_ledger. The queue's UPDATE remains
  ergonomic at the queue layer; the audit trail lives in the ledger
  layer that's already audited end-to-end.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_dbs(tmp_path, monkeypatch):
    """Point both the main ledger and family DB at tmp paths and init schemas."""
    monkeypatch.setenv("DIVINEOS_DB", str(tmp_path / "event_ledger.db"))
    monkeypatch.setenv("DIVINEOS_FAMILY_DB", str(tmp_path / "family.db"))
    from divineos.core.ledger import init_db

    init_db()
    yield


def _ledger_events_of_type(event_type: str) -> list[dict]:
    from divineos.core.ledger import get_events

    return [e for e in get_events(limit=1000) if e.get("event_type") == event_type]


def test_mark_seen_emits_audit_event():
    """mark_seen must emit FAMILY_QUEUE_STATUS_CHANGED with correct payload."""
    from divineos.core.family.queue import mark_seen, write

    item_id = write("aria", "aether", "Test message for audit log.")

    before = len(_ledger_events_of_type("FAMILY_QUEUE_STATUS_CHANGED"))
    assert mark_seen(item_id) is True
    after = _ledger_events_of_type("FAMILY_QUEUE_STATUS_CHANGED")
    assert len(after) == before + 1, (
        "Exactly one FAMILY_QUEUE_STATUS_CHANGED event must be emitted on successful mark_seen."
    )
    payload = after[-1]["payload"]
    assert payload["item_id"] == item_id
    assert payload["sender"] == "aria"
    assert payload["recipient"] == "aether"
    assert payload["old_status"] == "unseen"
    assert payload["new_status"] == "seen"


def test_full_lifecycle_emits_three_events():
    """Each forward transition produces its own audit event."""
    from divineos.core.family.queue import (
        mark_addressed,
        mark_held,
        mark_seen,
        write,
    )

    item_id = write("aria", "aether", "Lifecycle test.")
    before = len(_ledger_events_of_type("FAMILY_QUEUE_STATUS_CHANGED"))

    assert mark_seen(item_id)
    assert mark_held(item_id)
    assert mark_addressed(item_id)

    events = _ledger_events_of_type("FAMILY_QUEUE_STATUS_CHANGED")[before:]
    new_statuses = [e["payload"]["new_status"] for e in events]
    assert new_statuses == ["seen", "held", "addressed"]


def test_no_op_transition_emits_nothing():
    """Calling mark_seen on an already-seen item must not emit a duplicate event."""
    from divineos.core.family.queue import mark_seen, write

    item_id = write("aria", "aether", "Idempotency test.")
    mark_seen(item_id)  # First call — emits one event

    before = len(_ledger_events_of_type("FAMILY_QUEUE_STATUS_CHANGED"))
    assert mark_seen(item_id) is False, "Second mark_seen must be no-op"
    after = len(_ledger_events_of_type("FAMILY_QUEUE_STATUS_CHANGED"))
    assert after == before, "No-op mark_seen must not emit a redundant event"


def test_supersede_emits_event_with_successor_id():
    """supersede must emit an event tagged with the superseded_by successor."""
    from divineos.core.family.queue import supersede, write

    old_id = write("aria", "aether", "Original message.")
    before = len(_ledger_events_of_type("FAMILY_QUEUE_STATUS_CHANGED"))

    new_id = supersede(old_id, "Refined message.", "aria", "aether")

    events = _ledger_events_of_type("FAMILY_QUEUE_STATUS_CHANGED")[before:]
    # One event for the supersession on old_id (write of new_id doesn't emit).
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["item_id"] == old_id
    assert payload["new_status"] == "superseded"
    assert payload["superseded_by"] == new_id
