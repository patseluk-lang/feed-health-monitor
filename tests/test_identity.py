"""Tests for the deduplication key derived by the parser.

Идempotency depends on one property: the same entry must produce the
same key on every run. These tests pin that property down, along with
the priority order of the three identity levels.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.collector.parser import _identity


def test_guid_wins_over_url():
    """A feed's own guid is the most reliable identifier available."""
    key = _identity(
        guid="tag:example.com,2026:post-1",
        url="https://example.com/post-1",
        title="Any title",
        raw_date="Mon, 24 Aug 2026 10:00:00 +0000",
    )
    assert key == "tag:example.com,2026:post-1"


def test_url_used_when_guid_missing():
    """Without a guid, the url is the next best stable identifier."""
    key = _identity(
        guid="",
        url="https://example.com/post-1",
        title="Any title",
        raw_date="Mon, 24 Aug 2026 10:00:00 +0000",
    )
    assert key == "https://example.com/post-1"


def test_blank_guid_is_ignored():
    """A guid of whitespace is not a guid."""
    key = _identity(
        guid="   ",
        url="https://example.com/post-1",
        title="Any title",
        raw_date="",
    )
    assert key == "https://example.com/post-1"


def test_hash_used_when_guid_and_url_missing():
    """The last resort is a hash - deterministic, but content-derived."""
    key = _identity(guid="", url="", title="A title", raw_date="Mon, 24 Aug 2026")
    assert len(key) == 40
    assert key != "A title"


def test_same_entry_yields_same_key():
    """The property the whole collector rests on."""
    args = dict(guid="", url="", title="A title", raw_date="Mon, 24 Aug 2026")
    assert _identity(**args) == _identity(**args)


def test_key_survives_an_unparseable_date():
    """The hash uses the raw date string, so a parse failure is irrelevant.

    An entry whose date we cannot read is still the same entry, and must
    not be collected twice because of it.
    """
    key_one = _identity(guid="", url="", title="A title", raw_date="not a date")
    key_two = _identity(guid="", url="", title="A title", raw_date="not a date")
    assert key_one == key_two


def test_keys_are_capped_at_two_hundred_characters():
    """The database column is 200 characters wide."""
    long_url = "https://example.com/" + "x" * 500
    key = _identity(guid="", url=long_url, title="", raw_date="")
    assert len(key) == 200