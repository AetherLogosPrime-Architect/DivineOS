"""Tests for FTS5 search on system_events (claim 48043a7e)."""

from __future__ import annotations

import pytest

from divineos.core.ledger import (
    _fts_query_from_keyword,
    backfill_fts_index,
    get_connection,
    init_db,
    log_event,
    search_events,
    search_events_full_text,
)


@pytest.fixture
def fresh_ledger(tmp_path, monkeypatch):
    db_path = tmp_path / "fts_test.db"
    monkeypatch.setenv("DIVINEOS_DB", str(db_path))
    init_db()
    yield db_path


class TestFtsQueryFromKeyword:
    def test_single_word(self):
        assert _fts_query_from_keyword("hello") == '"hello"'

    def test_multi_word_anded(self):
        assert _fts_query_from_keyword("hello world") == '"hello" AND "world"'

    def test_short_token_dropped(self):
        assert _fts_query_from_keyword("hello a world") == '"hello" AND "world"'

    def test_pure_punctuation_returns_none(self):
        assert _fts_query_from_keyword("!!!") is None

    def test_empty_returns_none(self):
        assert _fts_query_from_keyword("") is None

    def test_whitespace_only_returns_none(self):
        assert _fts_query_from_keyword("   ") is None

    def test_fts_reserved_words_filtered(self):
        assert _fts_query_from_keyword("AND") is None
        assert _fts_query_from_keyword("hello AND world") == '"hello" AND "world"'


class TestFtsTableExists:
    def test_fts_virtual_table_present(self, fresh_ledger):
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='system_events_fts'"
            ).fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_fts_triggers_present(self, fresh_ledger):
        conn = get_connection()
        try:
            rows = list(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'system_events_a%_fts'"
                )
            )
            names = {r[0] for r in rows}
            assert "system_events_ai_fts" in names
            assert "system_events_ad_fts" in names
            assert "system_events_au_fts" in names
        finally:
            conn.close()


class TestInsertTriggerSync:
    def test_insert_propagates_to_fts(self, fresh_ledger):
        log_event("TEST", "user", {"content": "first event payload"}, validate=False)
        conn = get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM system_events_fts").fetchone()[0]
            assert count == 1
        finally:
            conn.close()


class TestSearchEventsWithFts:
    def test_substring_match_returns_event(self, fresh_ledger):
        log_event("TEST", "user", {"content": "hello world"}, validate=False)
        results = search_events("hello")
        assert len(results) == 1
        assert "hello" in results[0]["payload"]["content"]

    def test_no_match_returns_empty(self, fresh_ledger):
        log_event("TEST", "user", {"content": "hello world"}, validate=False)
        results = search_events("nonexistent")
        assert results == []

    def test_substring_semantics_preserved(self, fresh_ledger):
        log_event("TEST", "user", {"content": "eventually we got there"}, validate=False)
        results = search_events("event")
        assert len(results) == 1

    def test_multi_word_anded_match(self, fresh_ledger):
        log_event("TEST", "user", {"content": "hello world goes the saying"}, validate=False)
        log_event("TEST", "user", {"content": "hello there friend"}, validate=False)
        results = search_events("hello world")
        assert len(results) == 1
        assert "hello world" in results[0]["payload"]["content"]

    def test_punctuation_falls_back_to_like(self, fresh_ledger):
        log_event("TEST", "user", {"content": "wait... the answer is 42"}, validate=False)
        results = search_events("...")
        assert len(results) == 1


class TestSearchEventsFullText:
    def test_word_boundary_respected(self, fresh_ledger):
        log_event("TEST", "user", {"content": "eventually we got there"}, validate=False)
        log_event("TEST", "user", {"content": "this is an event"}, validate=False)
        results = search_events_full_text("event")
        assert len(results) == 1
        assert "this is an event" in results[0]["payload"]["content"]

    def test_fts_or_operator(self, fresh_ledger):
        log_event("TEST", "user", {"content": "alpha beta"}, validate=False)
        log_event("TEST", "user", {"content": "gamma delta"}, validate=False)
        log_event("TEST", "user", {"content": "epsilon zeta"}, validate=False)
        results = search_events_full_text("alpha OR delta")
        assert len(results) == 2


class TestBackfillFtsIndex:
    def test_backfill_returns_event_count(self, fresh_ledger):
        log_event("TEST", "user", {"content": "first event"}, validate=False)
        log_event("TEST", "user", {"content": "second event"}, validate=False)

        result = backfill_fts_index()
        assert result["backfilled"] == 2

    def test_backfill_idempotent(self, fresh_ledger):
        log_event("TEST", "user", {"content": "event one"}, validate=False)
        result1 = backfill_fts_index()
        result2 = backfill_fts_index()
        # FTS5 'rebuild' is idempotent — rebuilding twice gives the
        # same result (number of rows in the source table).
        assert result1["backfilled"] == result2["backfilled"]

    def test_backfill_results_in_searchable_index(self, fresh_ledger):
        log_event("TEST", "user", {"content": "searchable text"}, validate=False)
        backfill_fts_index()
        # After backfill, FTS-based search should find the event
        results = search_events_full_text("searchable")
        assert len(results) == 1


class TestBackwardsCompat:
    def test_returns_same_shape_as_legacy(self, fresh_ledger):
        log_event("TEST", "user", {"content": "compat test"}, validate=False)
        results = search_events("compat")
        assert len(results) == 1
        r = results[0]
        assert "event_id" in r
        assert "timestamp" in r
        assert "event_type" in r
        assert "actor" in r
        assert "payload" in r
        assert "content_hash" in r

    def test_limit_respected(self, fresh_ledger):
        for i in range(10):
            log_event("TEST", "user", {"content": f"item match-{i}"}, validate=False)
        results = search_events("match", limit=3)
        assert len(results) == 3

    def test_ordered_by_timestamp_asc(self, fresh_ledger):
        for i in range(3):
            log_event("TEST", "user", {"content": f"ordered match-{i}"}, validate=False)
        results = search_events("ordered")
        assert len(results) == 3
        assert results[0]["timestamp"] <= results[1]["timestamp"] <= results[2]["timestamp"]
