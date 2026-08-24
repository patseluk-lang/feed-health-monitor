"""Tests for whole-document parsing.

Two formats disagree about every field name and must arrive at the same
shape. Malformed entries are dropped, but never silently: the count is
the whole point of a health monitor.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.collector.parser import ParseError, parse

RSS_DOCUMENT = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>First post</title>
      <link>https://example.com/first</link>
      <guid>tag:example.com,2026:first</guid>
      <pubDate>Mon, 24 Aug 2026 10:00:00 +0000</pubDate>
      <description>A summary.</description>
    </item>
    <item>
      <title>Second post</title>
      <link>https://example.com/second</link>
      <pubDate>Tue, 25 Aug 2026 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_DOCUMENT = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Feed</title>
  <entry>
    <title>First post</title>
    <id>tag:example.com,2026:first</id>
    <link rel="alternate" href="https://example.com/first"/>
    <published>2026-08-24T10:00:00Z</published>
    <summary>A summary.</summary>
  </entry>
</feed>
"""

RSS_WITH_LINKLESS_ITEM = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>Has a link</title>
      <link>https://example.com/first</link>
    </item>
    <item>
      <title>Has no link</title>
    </item>
  </channel>
</rss>
"""

ATOM_WITH_ENCLOSURE_ONLY = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Feed</title>
  <entry>
    <title>Podcast episode</title>
    <link rel="enclosure" href="https://example.com/audio.mp3"/>
  </entry>
</feed>
"""


def test_rss_document_is_parsed():
    feed = parse(RSS_DOCUMENT)
    assert feed.title == "Example Feed"
    assert len(feed.entries) == 2
    assert feed.skipped == 0


def test_atom_document_is_parsed():
    feed = parse(ATOM_DOCUMENT)
    assert feed.title == "Example Feed"
    assert len(feed.entries) == 1
    assert feed.skipped == 0


def test_both_formats_produce_the_same_shape():
    """The reason this module exists."""
    rss_entry = parse(RSS_DOCUMENT).entries[0]
    atom_entry = parse(ATOM_DOCUMENT).entries[0]

    assert rss_entry.identity == atom_entry.identity
    assert rss_entry.url == atom_entry.url
    assert rss_entry.title == atom_entry.title
    assert rss_entry.summary == atom_entry.summary
    assert rss_entry.published_at == atom_entry.published_at


def test_entry_without_a_link_is_skipped_and_counted():
    """Silent data loss is the failure this project exists to catch."""
    feed = parse(RSS_WITH_LINKLESS_ITEM)
    assert len(feed.entries) == 1
    assert feed.skipped == 1


def test_enclosure_link_is_not_mistaken_for_the_article():
    """Atom allows several links; only alternate points at the entry."""
    feed = parse(ATOM_WITH_ENCLOSURE_ONLY)
    assert len(feed.entries) == 0
    assert feed.skipped == 1


def test_malformed_xml_raises_parse_error():
    with pytest.raises(ParseError):
        parse("<rss><channel><title>Unclosed")


def test_unrecognised_root_raises_parse_error():
    with pytest.raises(ParseError):
        parse("<html><body>Not a feed at all</body></html>")


def test_parse_error_carries_an_error_type():
    """Classification happens at the point of origin, not downstream."""
    assert ParseError.error_type == "parse_error"


def test_feed_without_a_title_gets_a_placeholder():
    document = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><link>https://example.com/first</link></item>
    </channel></rss>"""
    assert parse(document).title == "Untitled feed"