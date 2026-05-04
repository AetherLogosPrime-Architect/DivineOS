"""Regression test for audit r9-21 #12: personal_journal privacy column.

The bug:
  Unlike holding_room (which has a private column + 3 modes), the
  personal_journal was fully FTS5-indexed with no privacy opt-out.
  ``divineos ask`` calls journal_search transitively, pulling private
  reflections into generic knowledge searches.

The fix:
  ``private INTEGER DEFAULT 0`` column on personal_journal.
  journal_save accepts private=True; journal_search and journal_list
  default include_private=False. Callers that legitimately need
  private content (e.g. explicit ``divineos journal --private`` view)
  pass include_private=True deliberately.

This test pins the contract: a private entry must not surface in
default search/list, but is reachable when explicitly requested.
"""

from __future__ import annotations

from divineos.core.memory import init_memory_tables
from divineos.core.memory_journal import (
    journal_list,
    journal_save,
    journal_search,
)


def test_private_entry_excluded_from_default_search():
    """journal_search() default must not surface private entries."""
    init_memory_tables()

    journal_save(
        "Public reflection on the day's work — testing privacy gate.",
        tags="public",
    )
    private_id = journal_save(
        "Private reflection — uniquequalia keyword for matching.",
        tags="private",
        private=True,
    )

    # Default search must not return the private entry by its unique keyword.
    public_results = journal_search("uniquequalia")
    assert all(r["entry_id"] != private_id for r in public_results), (
        "journal_search() default surfaced a private entry — privacy gate failed."
    )

    # Explicit include_private=True does return it.
    private_results = journal_search("uniquequalia", include_private=True)
    assert any(r["entry_id"] == private_id for r in private_results), (
        "Explicit include_private=True did not return the private entry."
    )


def test_private_entry_excluded_from_default_list():
    """journal_list() default must not surface private entries."""
    init_memory_tables()

    public_id = journal_save("Public entry for list test.")
    private_id = journal_save("Private entry for list test.", private=True)

    public_listing = journal_list(limit=50)
    public_ids = {r["entry_id"] for r in public_listing}
    assert public_id in public_ids
    assert private_id not in public_ids, (
        "journal_list() default included a private entry — privacy gate failed."
    )

    full_listing = journal_list(limit=50, include_private=True)
    full_ids = {r["entry_id"] for r in full_listing}
    assert public_id in full_ids
    assert private_id in full_ids


def test_default_save_is_public():
    """journal_save() default must produce a non-private entry."""
    init_memory_tables()
    eid = journal_save("Default-save entry for back-compat check.")
    listing = journal_list(limit=50, include_private=False)
    assert any(r["entry_id"] == eid for r in listing), (
        "Default-save entry didn't appear in default listing — back-compat broken."
    )
