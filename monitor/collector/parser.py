"""Parsing RSS 2.0 and Atom documents into a uniform record shape.

The two formats disagree about almost every field name, so this module
normalises them into one dataclass and hands the result upward. It does
not touch the network and does not touch the database.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

ATOM_NS = "{http://www.w3.org/2005/Atom}"

IDENTITY_FROM_GUID = "guid"
IDENTITY_FROM_URL = "url"

IDENTITY_STRATEGIES = (IDENTITY_FROM_GUID, IDENTITY_FROM_URL)


class ParseError(Exception):
    """The document could not be understood as a feed."""

    error_type = "parse_error"


@dataclass
class ParsedEntry:
    """One entry, normalised across RSS and Atom."""

    identity: str
    url: str
    title: str
    published_at: datetime | None
    summary: str


@dataclass
class ParsedFeed:
    """The outcome of parsing one document."""

    title: str
    entries: list[ParsedEntry]
    skipped: int


def parse(body, identity_strategy=IDENTITY_FROM_GUID):
    """Parse a feed document into entries.

    Raises ParseError if the document is not usable at all. Individual
    malformed entries do not raise - they are skipped and counted, so the
    caller can report partial loss instead of discovering it later.

    identity_strategy selects how the deduplication key is derived, and is
    a plain string rather than a model enum so that this module stays
    testable without a database. The values match Source.IdentityStrategy
    by agreement; an unknown value raises rather than falling back to the
    default, because quietly parsing under the wrong rule produces exactly
    the silent corruption the strategy exists to prevent.
    """
    if identity_strategy not in IDENTITY_STRATEGIES:
        raise ParseError(f"Unknown identity strategy: {identity_strategy!r}")

    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise ParseError(f"Document is not well-formed XML: {exc}") from exc

    if root.tag == f"{ATOM_NS}feed":
        return _parse_atom(root, identity_strategy)
    if root.tag == "rss" or root.find("channel") is not None:
        return _parse_rss(root, identity_strategy)

    raise ParseError(f"Unrecognised root element: {root.tag}")


def _parse_rss(root, identity_strategy):
    channel = root.find("channel")
    if channel is None:
        raise ParseError("RSS document has no <channel>")

    entries = []
    skipped = 0

    for item in channel.findall("item"):
        url = _text(item, "link")
        if not url:
            # An entry with no link cannot be opened, deduplicated by url,
            # or shown to anyone. It is dropped - but counted, because
            # silent data loss is the failure this project exists to catch.
            skipped += 1
            continue

        entries.append(
            ParsedEntry(
                identity=_identity(
                    guid=_text(item, "guid"),
                    url=url,
                    title=_text(item, "title"),
                    raw_date=_text(item, "pubDate"),
                    strategy=identity_strategy,
                ),
                url=url,
                title=_text(item, "title"),
                published_at=_parse_date(_text(item, "pubDate")),
                summary=_text(item, "description"),
            )
        )

    return ParsedFeed(
        title=_text(channel, "title") or "Untitled feed",
        entries=entries,
        skipped=skipped,
    )


def _parse_atom(root, identity_strategy):
    entries = []
    skipped = 0

    for entry in root.findall(f"{ATOM_NS}entry"):
        url = _atom_link(entry)
        if not url:
            skipped += 1
            continue

        raw_date = _text(entry, f"{ATOM_NS}published") or _text(
            entry, f"{ATOM_NS}updated"
        )

        entries.append(
            ParsedEntry(
                identity=_identity(
                    guid=_text(entry, f"{ATOM_NS}id"),
                    url=url,
                    title=_text(entry, f"{ATOM_NS}title"),
                    raw_date=raw_date,
                    strategy=identity_strategy,
                ),
                url=url,
                title=_text(entry, f"{ATOM_NS}title"),
                published_at=_parse_date(raw_date),
                summary=_text(entry, f"{ATOM_NS}summary")
                or _text(entry, f"{ATOM_NS}content"),
            )
        )

    return ParsedFeed(
        title=_text(root, f"{ATOM_NS}title") or "Untitled feed",
        entries=entries,
        skipped=skipped,
    )


def _identity(guid, url, title, raw_date, strategy=IDENTITY_FROM_GUID):
    """Derive the deduplication key for an entry.

    Under the default strategy, three levels falling downward:
        1. the feed's own guid or Atom id, when present
        2. the entry url
        3. a hash of title and raw publication date

    Under IDENTITY_FROM_URL the first level is skipped. That is not an
    improvement on the rule - it is a concession to a publisher who does
    not honour it. A guid outranks a url precisely because a url can be
    rewritten while the entry stays the same, so demoting it costs real
    protection and is only worth doing where the guid has been observed to
    be unstable.

    The rule is deterministic and stateless: the same entry produces the
    same key on every run, on any machine, with no memory of previous runs.
    That is what makes a repeated collection a no-op rather than a source
    of duplicates.

    The hash uses the raw date string rather than the parsed value, because
    a parse failure must not change the key. An entry whose date we cannot
    read is still the same entry.
    """
    if strategy == IDENTITY_FROM_GUID and guid and guid.strip():
        return guid.strip()[:200]
    if url:
        return url[:200]
    return hashlib.sha1(f"{title}|{raw_date}".encode("utf-8")).hexdigest()


def _parse_date(raw):
    """Convert a feed date into an aware datetime, or None.

    Feeds carry RFC 822 dates (RSS) and ISO 8601 dates (Atom), and plenty
    of them carry something that is neither. A date we cannot read yields
    None: losing the whole entry over a formatting problem would be the
    worse outcome.

    Naive values are assumed to be UTC. Guessing a local zone would be a
    silent, unrecorded assumption applied to every interval we later
    compute.
    """
    if not raw:
        return None

    raw = raw.strip()

    for parse_attempt in (_parse_rfc822, _parse_iso8601):
        parsed = parse_attempt(raw)
        if parsed is not None:
            return parsed

    return None


def _parse_rfc822(raw):
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_iso8601(raw):
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _text(element, tag):
    """Return the stripped text of a child element, or an empty string."""
    child = element.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _atom_link(entry):
    """Find the entry's canonical link.

    Atom allows several <link> elements distinguished by rel. The
    alternate link is the article; without a rel attribute, alternate is
    the specified default. Anything else - enclosure, via, self - points
    somewhere other than the entry itself.
    """
    for link in entry.findall(f"{ATOM_NS}link"):
        rel = link.get("rel", "alternate")
        if rel == "alternate" and link.get("href"):
            return link.get("href")
    return ""