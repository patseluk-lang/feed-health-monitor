"""Running collection against sources and recording what happened.

This module is the only place where fetching, parsing and the database
meet. It decides nothing about how a feed is read - it decides what gets
written down about the attempt.
"""

import logging
from dataclasses import dataclass
from django.db import transaction
from django.utils import timezone

from monitor.models import Article, FetchRun, Source

from .fetcher import FetchError, fetch
from .parser import ParseError, parse

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    """The outcome of collecting from every requested source."""

    sources_attempted: int
    sources_ok: int
    sources_failed: int
    articles_new: int


def collect_all(sources=None):
    """Collect from each source in turn.

    One unreachable or malformed feed does not stop the others. Every
    attempt writes a FetchRun row, successful or not, because a source
    that has gone quiet and a source we could not reach look identical
    in the article table and have to be told apart somewhere.
    """
    if sources is None:
        sources = Source.objects.filter(is_active=True)

    summary = RunSummary(0, 0, 0, 0)

    for source in sources:
        summary.sources_attempted += 1
        run = collect_one(source)

        if run.status == FetchRun.Status.OK:
            summary.sources_ok += 1
            summary.articles_new += run.items_new
        else:
            summary.sources_failed += 1

    return summary


def collect_one(source):
    """Collect from a single source, recording the attempt either way.

    Returns the FetchRun row. Never raises for an ordinary feed problem -
    those are the expected case and belong in the database, not in a
    traceback.
    """
    run = FetchRun.objects.create(
        source=source,
        started_at=timezone.now(),
        status=FetchRun.Status.ERROR,
    )

    try:
        result = fetch(source.feed_url)
        run.http_status = result.http_status

        feed = parse(result.body, identity_strategy=source.identity_strategy)
        items_new = _store(source, feed.entries)

    except FetchError as exc:
        return _finish_failed(run, exc.error_type, exc.message, exc.http_status)

    except ParseError as exc:
        return _finish_failed(run, exc.error_type, str(exc), run.http_status)

    except Exception as exc:
        # An unexpected failure is still this source's failure. Letting it
        # escape would abort the whole collection and leave this run open
        # forever, which is a worse outcome than recording it and moving on.
        logger.exception("Unexpected error collecting %s", source.feed_url)
        return _finish_failed(run, "internal_error", str(exc), run.http_status)

    run.status = FetchRun.Status.OK
    run.finished_at = timezone.now()
    run.items_seen = len(feed.entries) + feed.skipped
    run.items_new = items_new
    run.save()
    return run


@transaction.atomic
def _store(source, entries):
    """Insert entries, letting the database reject the duplicates.

    The unique constraint on (source, identity) is what makes a repeated
    run a no-op. Checking for existence in Python first would be both
    slower and weaker: two runs overlapping in time would each pass the
    check and each attempt the insert.

    ignore_conflicts does not report how many rows it actually wrote, so
    the count is taken as a difference. One extra COUNT buys a number that
    is true.
    """
    before = Article.objects.filter(source=source).count()

    Article.objects.bulk_create(
        [
            Article(
                source=source,
                identity=entry.identity,
                url=entry.url,
                title=entry.title,
                published_at=entry.published_at,
                summary=entry.summary,
            )
            for entry in entries
        ],
        ignore_conflicts=True,
    )

    after = Article.objects.filter(source=source).count()
    return after - before


def _finish_failed(run, error_type, message, http_status):
    """Close a run as failed, with the classification already decided.

    error_type is a short code assigned where the failure happened, not
    derived from the message here. That is what keeps "how often does this
    source time out" a GROUP BY rather than a text search.
    """
    run.status = FetchRun.Status.ERROR
    run.finished_at = timezone.now()
    run.error_type = error_type
    run.error_message = message[:2000]
    run.http_status = http_status
    run.save()
    return run