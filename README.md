# Feed Health Monitor

A monitoring system for content sources. It collects RSS and Atom feeds
into a database and answers questions a folder of articles cannot:

- Is every source still alive?
- Which one went quiet, and when exactly?
- Where did publication frequency drop by half?
- Which source serves duplicates?
- Which source fails during collection, and how often?

The recurring theme is **silent failure** — the case where a system
reports success while the data is already wrong. A collector that runs
without error and quietly stores nothing is worse than one that crashes,
because nobody looks.

## Relation to rss-feed-collector

This is the second pass at the problem. The first —
[rss-feed-collector](https://github.com/patseluk-lang/rss-feed-collector)
— was built to a client brief: read a list of feeds, write every article
into one text file, no database, no dependencies. It does that job well
and remains the right tool for it.

What it cannot do is answer questions across time. A text file per run
has no memory: it cannot tell you that a source has been quiet for
three times its usual interval, or that one feed has failed eleven runs
in a row. That requires storage, a stable identity for every entry, and
queries over history — which is what this project adds.

## Why this is not a feed reader

A feed reader answers *what was published*. This answers *whether the
collection itself can be trusted*. Those are different questions, and
the second one is the one that goes unasked until the data is needed.

## Stack

- Python 3.12
- Django 6.1
- SQLite in development, PostgreSQL-compatible SQL throughout
- Analytics in hand-written SQL via `connection.cursor()`, no ORM
- Feed parsing on the standard library only — `urllib.request`,
  `xml.etree`, `email.utils`
- pytest

There is no `feedparser` and no `requests` dependency. Both formats are
parsed directly, including the two date encodings they disagree about.
The only runtime dependencies are Django and `python-dotenv`.

## Running it

```
git clone https://github.com/patseluk-lang/feed-health-monitor.git
cd feed-health-monitor
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py add_source "Hacker News" https://news.ycombinator.com/rss
python manage.py collect
```

Configuration is read from `.env` when present; see `.env.example`.
The project runs with development defaults when the file is absent.

## What a collection run looks like

```
Attempted 8, succeeded 7, failed 1, new articles 31
```

**The one failure is deliberate.** One of the eight configured sources
is a synthetic fixture pointing at a URL that returns HTTP 404. It
exists so that the failure path is exercised on every run rather than
discovered the first time a real source breaks. `7/8` is the expected
norm for this repository, not a defect.

## What the analytics answer

Seven hand-written SQL queries. Each one is printed as JSON by
`python manage.py report <name>`, where `<name>` is one of `interval`,
`volume`, `reliability`, `duplicates`, `silence`, `rhythm`, `identity`.
Two examples of real output below.

### Collection reliability

`python manage.py report reliability`

Which sources fail, how often, and with what error class:

```json
[
  {
    "source_id": 2,
    "title": "Broken Feed",
    "total_runs": 11,
    "ok_runs": 0,
    "failed_runs": 11,
    "success_rate": 0.0,
    "error_types": "http_error"
  },
  {
    "source_id": 8,
    "title": "BBC News",
    "total_runs": 10,
    "ok_runs": 10,
    "failed_runs": 0,
    "success_rate": 100.0,
    "error_types": null
  }
]
```

### Silence detection

`python manage.py report silence`

The harder question. A source that stopped publishing looks identical
to a source that is simply slow — unless you know its own rhythm.

```json
[
  {
    "title": "Simon Willison's Weblog",
    "median_interval_hours": 6.19,
    "hours_since_last": 15.2,
    "silence_ratio": 2.46
  },
  {
    "title": "BBC News",
    "median_interval_hours": 0.53,
    "hours_since_last": 0.2,
    "silence_ratio": 0.36
  },
  {
    "title": "Python Insider",
    "median_interval_hours": 192.0,
    "hours_since_last": 59.6,
    "silence_ratio": 0.31
  }
]
```

`silence_ratio` is time-since-last-post divided by the source's own
median interval. It makes sources with wildly different rhythms
comparable: BBC News publishes every 32 minutes, Python Insider every
eight days, and a ratio above 2 means *unusual for this source*
regardless of which one it is.

An absolute threshold would be useless here. Twelve hours of silence is
routine for a monthly release blog and alarming for a news wire.

## Design decisions

### Identity is derived, not assumed

Deduplication uses a three-level rule, falling downward:

1. the feed's own `guid` or Atom `id`, when present
2. the entry url
3. a SHA-1 of title and **raw** publication date

The rule is deterministic and stateless — the same entry produces the
same key on every run, on any machine, with no memory of previous runs.
That is what makes a repeated collection a no-op instead of a source of
duplicates.

Level 3 hashes the *raw* date string rather than the parsed value. A
date we failed to parse must not change the key: an entry whose
timestamp is unreadable is still the same entry.

Level 1 assumes the publisher keeps the guid stable. BBC News does not:
its guid carries the item's position in the feed, so the same article
arrived under a new key on every run. Such a source is switched to
`identity_strategy = "url"`, which skips level 1. The setting is per
source, not global. The incident and the query that exposed it are
described in [docs/identity-instability-bbc.md](docs/identity-instability-bbc.md).

### Malformed entries are dropped, never silently

An entry with no link cannot be opened, deduplicated by url, or shown to
anyone, so it is discarded. But the count is returned alongside the
results. Silent data loss is the exact failure this project exists to
catch; producing it internally would be self-defeating.

### Errors are classified where they occur

Each exception class carries its own `error_type` (`http_error`,
`parse_error`, and so on), set at the point of origin rather than
inferred downstream from a message string. The classification is stored
per run, which is what makes `error_types` in the reliability query
meaningful rather than a free-text field.

### Unparseable dates yield None, not an exception

Feeds carry RFC 822 dates, ISO 8601 dates, and a long tail of things
that are neither. Losing an entire entry over a formatting problem is
the worse outcome. Naive values are treated as UTC — an explicit,
documented assumption, because guessing a local zone would apply a
silent offset to every interval the analytics layer later computes.

### Analytics bypass the ORM

The seven queries are written in SQL and executed through
`connection.cursor()`. Median intervals, weekday-hour distributions, and
volume comparisons across time windows are set operations; expressing
them through an ORM would obscure what the database is actually doing
without making anything simpler.

## Tests

```
pytest
```

25 tests covering the parser: identity derivation and its priority
order, date handling across both formats, document parsing for RSS and
Atom, and the error paths. The parser touches neither the network nor
the database, so the tests need no fixtures and no Django setup.

The property the whole collector rests on — that the same entry yields
the same key on every run — is pinned by an explicit test rather than
left as a comment.

## Known limitations

- **Date-only sources.** Feeds served through Blogger publish a day, not
  a moment. Any interval computed from them carries day-level
  granularity. Python Insider in the sample output is one of these.
- **Small-sample noise.** Percentage-based volume comparisons are
  unstable on sources with few articles. A drop from 3 posts to 1 is
  −67% and means nothing.
- **High-frequency sources need an absolute floor.** For a source
  publishing every 30 minutes, a `silence_ratio` of 2 is four minutes
  past due. The ratio alone is not enough without a minimum duration.
- **Development database is SQLite.** The SQL is written to be
  PostgreSQL-compatible, but is not currently exercised against it.
- Analytics are exposed through the shell; there is no web interface yet.

## Automation

Collection runs daily at 11:00 via Windows Task Scheduler. `run_collect.bat`
handles virtualenv detection, UTF-8 output encoding, and per-day log
files under `logs/`.
