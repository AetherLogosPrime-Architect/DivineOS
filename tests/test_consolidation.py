"""Tests for the memory consolidation system."""

import pytest
from divineos.ledger import init_db, log_event
from divineos.consolidation import (
    init_knowledge_table,
    store_knowledge,
    get_knowledge,
    search_knowledge,
    update_knowledge,
    get_unconsolidated_events,
    find_similar,
    generate_briefing,
    knowledge_stats,
)
import divineos.ledger as ledger_mod


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Use a temporary database for each test."""
    test_db = tmp_path / "test_ledger.db"
    monkeypatch.setattr(ledger_mod, "DB_PATH", test_db)
    init_db()
    init_knowledge_table()
    yield
    if test_db.exists():
        test_db.unlink()


class TestStoreKnowledge:
    def test_returns_knowledge_id(self):
        kid = store_knowledge("FACT", "Python uses indentation")
        assert isinstance(kid, str)
        assert len(kid) > 0

    def test_stores_with_correct_type(self):
        store_knowledge("PATTERN", "Errors cluster in parsing code")
        entries = get_knowledge(knowledge_type="PATTERN")
        assert len(entries) == 1
        assert entries[0]["knowledge_type"] == "PATTERN"

    def test_rejects_invalid_type(self):
        with pytest.raises(ValueError, match="Invalid knowledge_type"):
            store_knowledge("INVALID", "nope")

    def test_dedup_exact_match(self):
        kid1 = store_knowledge("FACT", "The sky is blue")
        kid2 = store_knowledge("FACT", "The sky is blue")
        assert kid1 == kid2
        entries = get_knowledge()
        assert len(entries) == 1
        assert entries[0]["access_count"] == 1  # incremented once by dedup

    def test_stores_source_events(self):
        store_knowledge("FACT", "test fact", source_events=["evt-1", "evt-2"])
        entries = get_knowledge()
        assert entries[0]["source_events"] == ["evt-1", "evt-2"]


class TestGetKnowledge:
    def test_empty(self):
        assert get_knowledge() == []

    def test_filter_by_type(self):
        store_knowledge("FACT", "fact one")
        store_knowledge("MISTAKE", "mistake one")
        facts = get_knowledge(knowledge_type="FACT")
        assert len(facts) == 1
        assert facts[0]["content"] == "fact one"

    def test_filter_by_confidence(self):
        store_knowledge("FACT", "low confidence", confidence=0.3)
        store_knowledge("FACT", "high confidence", confidence=0.9)
        results = get_knowledge(min_confidence=0.5)
        assert len(results) == 1
        assert results[0]["content"] == "high confidence"

    def test_excludes_superseded_by_default(self):
        kid = store_knowledge("FACT", "old fact")
        update_knowledge(kid, "new fact")
        results = get_knowledge()
        assert len(results) == 1
        assert results[0]["content"] == "new fact"


class TestSearchKnowledge:
    def test_finds_matching(self):
        store_knowledge("FACT", "Python uses pytest for testing")
        store_knowledge("FACT", "JavaScript uses Jest")
        results = search_knowledge("pytest")
        assert len(results) == 1
        assert "pytest" in results[0]["content"]

    def test_no_matches(self):
        store_knowledge("FACT", "hello world")
        assert search_knowledge("zzzzz") == []

    def test_searches_tags(self):
        store_knowledge("PREFERENCE", "use ruff for linting", tags=["tooling", "linting"])
        results = search_knowledge("tooling")
        assert len(results) == 1


class TestUpdateKnowledge:
    def test_creates_new_entry(self):
        kid1 = store_knowledge("FACT", "version 1")
        kid2 = update_knowledge(kid1, "version 2")
        assert kid1 != kid2

    def test_supersedes_old(self):
        kid1 = store_knowledge("FACT", "old")
        update_knowledge(kid1, "new")
        all_entries = get_knowledge(include_superseded=True)
        old_entry = [e for e in all_entries if e["knowledge_id"] == kid1][0]
        assert old_entry["superseded_by"] is not None

    def test_preserves_source_chain(self):
        kid1 = store_knowledge("FACT", "v1", source_events=["evt-1"])
        update_knowledge(kid1, "v2", additional_sources=["evt-2"])
        new_entry = get_knowledge()
        assert len(new_entry) == 1
        assert "evt-1" in new_entry[0]["source_events"]
        assert "evt-2" in new_entry[0]["source_events"]


class TestGetUnconsolidated:
    def test_all_unconsolidated_when_empty_knowledge(self):
        log_event("TEST", "user", {"content": "hello"})
        log_event("TEST", "user", {"content": "world"})
        events = get_unconsolidated_events()
        assert len(events) == 2

    def test_excludes_referenced_events(self):
        eid1 = log_event("TEST", "user", {"content": "first"})
        eid2 = log_event("TEST", "user", {"content": "second"})
        store_knowledge("FACT", "learned from first", source_events=[eid1])
        events = get_unconsolidated_events()
        event_ids = [e["event_id"] for e in events]
        assert eid1 not in event_ids
        assert eid2 in event_ids


class TestGenerateBriefing:
    def test_empty_briefing(self):
        result = generate_briefing()
        assert "No knowledge" in result

    def test_includes_types(self):
        store_knowledge("FACT", "a fact")
        store_knowledge("PATTERN", "a pattern")
        result = generate_briefing()
        assert "FACTS" in result
        assert "PATTERNS" in result

    def test_respects_max_items(self):
        for i in range(10):
            store_knowledge("FACT", f"fact number {i}")
        result = generate_briefing(max_items=3)
        assert result.count("fact number") == 3


class TestKnowledgeStats:
    def test_empty_stats(self):
        stats = knowledge_stats()
        assert stats["total"] == 0

    def test_counts_by_type(self):
        store_knowledge("FACT", "f1")
        store_knowledge("FACT", "f2")
        store_knowledge("MISTAKE", "m1")
        stats = knowledge_stats()
        assert stats["total"] == 3
        assert stats["by_type"]["FACT"] == 2
        assert stats["by_type"]["MISTAKE"] == 1


class TestFindSimilar:
    def test_finds_exact_match(self):
        store_knowledge("FACT", "exact content here")
        results = find_similar("exact content here")
        assert len(results) == 1

    def test_no_match(self):
        store_knowledge("FACT", "something")
        results = find_similar("completely different")
        assert len(results) == 0
