"""Tests for feed date parsing.

Feeds carry at least two date formats and a long tail of things that
are neither. The rule under test: a date we cannot read yields None,
never an exception and never a silent local-time assumption.
"""

import sys
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.collector.parser import _parse_date


def test_rfc822_with_offset():
    """The RSS 2.0 format."""
    parsed = _parse_date("Mon, 24 Aug 2026 10:00:00 +0000")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 8
    assert parsed.day == 24
    assert parsed.utcoffset().total_seconds() == 0


def test_rfc822_with_named_zone():
    """Older feeds still use zone abbreviations."""
    parsed = _parse_date("Mon, 24 Aug 2026 10:00:00 GMT")
    assert parsed is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_iso8601_with_z_suffix():
    """The Atom format, in its most common spelling."""
    parsed = _parse_date("2026-08-24T10:00:00Z")
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.utcoffset().total_seconds() == 0


def test_iso8601_with_explicit_offset():
    parsed = _parse_date("2026-08-24T13:00:00+03:00")
    assert parsed is not None
    assert parsed.utcoffset().total_seconds() == 3 * 3600


def test_date_only_value():
    """Some sources publish a day, not a moment.

    Blogger-backed feeds do this. The value is usable, but any interval
    computed from it carries day-level granularity, not minute-level.
    """
    parsed = _parse_date("2026-08-24")
    assert parsed is not None
    assert parsed.day == 24


def test_naive_value_is_treated_as_utc():
    """An explicit, documented assumption rather than a silent one.

    Guessing a local zone would apply an unrecorded offset to every
    interval the analytics layer later computes.
    """
    parsed = _parse_date("2026-08-24T10:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_unreadable_value_yields_none():
    """Losing a whole entry over a formatting problem is the worse outcome."""
    assert _parse_date("sometime last Tuesday") is None


def test_empty_value_yields_none():
    assert _parse_date("") is None
    assert _parse_date(None) is None


def test_surrounding_whitespace_is_tolerated():
    """Feeds indent their XML; the text arrives padded."""
    parsed = _parse_date("  2026-08-24T10:00:00Z  ")
    assert parsed is not None
    assert parsed.year == 2026