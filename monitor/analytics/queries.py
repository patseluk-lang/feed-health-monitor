"""Hand-written SQL analytics.

These queries deliberately bypass the ORM. Each one answers a question about
source health that requires window functions, conditional aggregation or
outer joins - constructs the ORM either hides or expresses awkwardly.
"""

from django.db import connection


def _fetch_all_as_dicts(cursor):
    """Return all rows from a cursor as a list of dicts keyed by column name."""
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


MEDIAN_PUBLISH_INTERVAL_SQL = """
WITH gaps AS (
    SELECT
        article.source_id AS source_id,
        (
            julianday(article.published_at)
            - julianday(
                LAG(article.published_at) OVER (
                    PARTITION BY article.source_id
                    ORDER BY article.published_at
                )
            )
        ) * 24.0 AS interval_hours
    FROM monitor_article AS article
    WHERE article.published_at IS NOT NULL
),
ranked AS (
    SELECT
        source_id,
        interval_hours,
        ROW_NUMBER() OVER (
            PARTITION BY source_id
            ORDER BY interval_hours
        ) AS position,
        COUNT(*) OVER (PARTITION BY source_id) AS total
    FROM gaps
    WHERE interval_hours IS NOT NULL
),
medians AS (
    SELECT
        source_id,
        AVG(interval_hours) AS median_interval_hours,
        MAX(total) AS interval_count
    FROM ranked
    WHERE position IN ((total + 1) / 2, (total + 2) / 2)
    GROUP BY source_id
)
SELECT
    source.id AS source_id,
    source.title AS title,
    source.is_active AS is_active,
    medians.median_interval_hours AS median_interval_hours,
    COALESCE(medians.interval_count, 0) AS interval_count
FROM monitor_source AS source
LEFT JOIN medians ON medians.source_id = source.id
ORDER BY source.title
"""


def median_publish_interval():
    """Median gap in hours between consecutive articles, per source.

    The median is used instead of the mean because feeds publish in bursts:
    a handful of articles within a minute followed by hours of silence. The
    mean smears such bursts into a figure that never occurred in practice,
    while the median reports the typical rhythm.

    SQLite has no percentile function, so the median is assembled by hand:
    intervals are numbered by ascending duration and the middle one (or the
    average of the two middle ones, for an even count) is selected.

    Sources with fewer than two dated articles yield no intervals at all.
    They are still returned, with median_interval_hours set to None and
    interval_count set to 0, so that a newly added source is distinguishable
    from a source that does not exist.

    Returns:
        list[dict]: source_id, title, is_active, median_interval_hours,
        interval_count.
    """
    with connection.cursor() as cursor:
        cursor.execute(MEDIAN_PUBLISH_INTERVAL_SQL)
        return _fetch_all_as_dicts(cursor)

VOLUME_TREND_SQL = """
WITH windows AS (
    SELECT
        article.source_id AS source_id,
        SUM(
            CASE
                WHEN article.published_at >= datetime('now', '-30 days')
                THEN 1
                ELSE 0
            END
        ) AS articles_30d,
        SUM(
            CASE
                WHEN article.published_at >= datetime('now', '-60 days')
                 AND article.published_at < datetime('now', '-30 days')
                THEN 1
                ELSE 0
            END
        ) AS articles_prev_30d
    FROM monitor_article AS article
    WHERE article.published_at IS NOT NULL
    GROUP BY article.source_id
)
SELECT
    source.id AS source_id,
    source.title AS title,
    COALESCE(windows.articles_30d, 0) AS articles_30d,
    COALESCE(windows.articles_prev_30d, 0) AS articles_prev_30d,
    CASE
        WHEN COALESCE(windows.articles_prev_30d, 0) = 0 THEN NULL
        ELSE ROUND(
            (COALESCE(windows.articles_30d, 0) * 100.0)
            / windows.articles_prev_30d,
            1
        )
    END AS percent_of_previous
FROM monitor_source AS source
LEFT JOIN windows ON windows.source_id = source.id
ORDER BY source.title
"""


def volume_trend():
    """Article counts for the last 30 days against the 30 days before that.

    Both windows are produced in a single table scan through conditional
    aggregation. Counting them as two separate queries would read the article
    table twice and would express nothing the ORM cannot already do.

    Window boundaries are anchored to the current time rather than to the
    newest article. A source that fell silent two months ago must report zero
    in both windows; anchoring to its own last article would quietly rescale
    the comparison and hide exactly the failure this project exists to detect.

    percent_of_previous is None when the earlier window is empty, since a
    ratio against zero carries no meaning. Callers must treat that as unknown
    rather than as a drop to zero.

    Returns:
        list[dict]: source_id, title, articles_30d, articles_prev_30d,
        percent_of_previous.
    """
    with connection.cursor() as cursor:
        cursor.execute(VOLUME_TREND_SQL)
        return _fetch_all_as_dicts(cursor)

COLLECTION_RELIABILITY_SQL = """
WITH runs AS (
    SELECT
        fetch_run.source_id AS source_id,
        COUNT(*) AS total_runs,
        SUM(CASE WHEN fetch_run.status = 'ok' THEN 1 ELSE 0 END) AS ok_runs,
        SUM(CASE WHEN fetch_run.status = 'error' THEN 1 ELSE 0 END) AS failed_runs,
        GROUP_CONCAT(
            DISTINCT CASE
                WHEN fetch_run.error_type = '' THEN NULL
                ELSE fetch_run.error_type
            END
        ) AS error_types,
        MAX(fetch_run.started_at) AS last_run_at
    FROM monitor_fetchrun AS fetch_run
    WHERE fetch_run.started_at >= datetime('now', '-14 days')
    GROUP BY fetch_run.source_id
)
SELECT
    source.id AS source_id,
    source.title AS title,
    source.is_active AS is_active,
    COALESCE(runs.total_runs, 0) AS total_runs,
    COALESCE(runs.ok_runs, 0) AS ok_runs,
    COALESCE(runs.failed_runs, 0) AS failed_runs,
    CASE
        WHEN COALESCE(runs.total_runs, 0) = 0 THEN NULL
        ELSE ROUND((runs.ok_runs * 100.0) / runs.total_runs, 1)
    END AS success_rate,
    runs.error_types AS error_types,
    runs.last_run_at AS last_run_at
FROM monitor_source AS source
LEFT JOIN runs ON runs.source_id = source.id
ORDER BY success_rate IS NULL DESC, success_rate ASC, source.title
"""


def collection_reliability():
    """Fetch success rate over the last 14 days, per source.

    The outer join is load-bearing rather than decorative. A source that was
    never attempted in the window must still appear, carrying zeros: dropping
    it would make an unmonitored source indistinguishable from a healthy one,
    which is precisely the confusion the fetch_run table exists to prevent.

    success_rate is None when no run was attempted. Zero would assert that
    every attempt failed, which is a different and much stronger claim than
    having no attempts on record.

    error_types lists the distinct failure classifications observed in the
    window, so that a source failing on timeouts is separable from one
    failing on HTTP status without inspecting individual runs.

    Ordering puts unmonitored sources first, then the least reliable ones,
    since both demand attention before the healthy remainder.

    Returns:
        list[dict]: source_id, title, is_active, total_runs, ok_runs,
        failed_runs, success_rate, error_types, last_run_at.
    """
    with connection.cursor() as cursor:
        cursor.execute(COLLECTION_RELIABILITY_SQL)
        return _fetch_all_as_dicts(cursor)

DUPLICATE_TITLES_SQL = """
SELECT
    source.id AS source_id,
    source.title AS source_title,
    TRIM(LOWER(article.title)) AS normalised_title,
    COUNT(DISTINCT article.identity) AS identity_count,
    MIN(article.fetched_at) AS first_seen_at,
    MAX(article.fetched_at) AS last_seen_at,
    GROUP_CONCAT(DISTINCT article.identity) AS identities
FROM monitor_article AS article
JOIN monitor_source AS source ON source.id = article.source_id
WHERE TRIM(article.title) <> ''
GROUP BY article.source_id, TRIM(LOWER(article.title))
HAVING COUNT(DISTINCT article.identity) > 1
ORDER BY identity_count DESC, source.title, normalised_title
"""


def duplicate_titles():
    """Articles repeated within a source under different identities.

    This query looks for exactly what the database cannot enforce. The unique
    constraint on (source_id, identity) blocks a second insert of the same
    identity, but identity is derived as guid -> url -> sha1. A source that
    republishes an item with a fresh guid, or that alters the item URL,
    produces a different identity, passes the constraint, and leaves two rows
    describing one article. The schema stays valid while the data rots.

    Titles are compared trimmed and lowercased, since feeds routinely vary
    capitalisation and padding between republications.

    The join is inner by design, in contrast to the outer joins used
    elsewhere in this module. Absence from these results is unambiguous - it
    means no duplicates were found - so padding the output with zero rows
    would add noise rather than remove doubt.

    Returns:
        list[dict]: source_id, source_title, normalised_title, identity_count,
        first_seen_at, last_seen_at, identities.
    """
    with connection.cursor() as cursor:
        cursor.execute(DUPLICATE_TITLES_SQL)
        return _fetch_all_as_dicts(cursor)

SILENT_SOURCES_SQL = """
WITH gaps AS (
    SELECT
        article.source_id AS source_id,
        (
            julianday(article.published_at)
            - julianday(
                LAG(article.published_at) OVER (
                    PARTITION BY article.source_id
                    ORDER BY article.published_at
                )
            )
        ) * 24.0 AS interval_hours
    FROM monitor_article AS article
    WHERE article.published_at IS NOT NULL
),
ranked AS (
    SELECT
        source_id,
        interval_hours,
        ROW_NUMBER() OVER (
            PARTITION BY source_id
            ORDER BY interval_hours
        ) AS position,
        COUNT(*) OVER (PARTITION BY source_id) AS total
    FROM gaps
    WHERE interval_hours IS NOT NULL
),
medians AS (
    SELECT
        source_id,
        AVG(interval_hours) AS median_interval_hours,
        MAX(total) AS interval_count
    FROM ranked
    WHERE position IN ((total + 1) / 2, (total + 2) / 2)
    GROUP BY source_id
),
latest AS (
    SELECT
        article.source_id AS source_id,
        MAX(article.published_at) AS last_published_at,
        COUNT(*) AS dated_articles
    FROM monitor_article AS article
    WHERE article.published_at IS NOT NULL
    GROUP BY article.source_id
)
SELECT
    source.id AS source_id,
    source.title AS title,
    source.is_active AS is_active,
    latest.last_published_at AS last_published_at,
    COALESCE(latest.dated_articles, 0) AS dated_articles,
    medians.median_interval_hours AS median_interval_hours,
    COALESCE(medians.interval_count, 0) AS interval_count,
    CASE
        WHEN latest.last_published_at IS NULL THEN NULL
        ELSE ROUND(
            (julianday('now') - julianday(latest.last_published_at)) * 24.0,
            1
        )
    END AS hours_since_last,
    CASE
        WHEN latest.last_published_at IS NULL THEN NULL
        WHEN medians.median_interval_hours IS NULL THEN NULL
        WHEN medians.median_interval_hours <= 0 THEN NULL
        ELSE ROUND(
            ((julianday('now') - julianday(latest.last_published_at)) * 24.0)
            / medians.median_interval_hours,
            2
        )
    END AS silence_ratio
FROM monitor_source AS source
LEFT JOIN latest ON latest.source_id = source.id
LEFT JOIN medians ON medians.source_id = source.id
ORDER BY silence_ratio IS NULL DESC, silence_ratio DESC, source.title
"""


def silent_sources():
    """Time since the last publication, measured against each source's own rhythm.

    Silence cannot be judged against a fixed threshold. A week without output
    is an emergency for a news wire and an ordinary week for an official
    release blog. The comparison therefore has to be relative: the elapsed
    time since the newest article is divided by that source's own median
    publishing interval.

    silence_ratio is the result. A value near 1.0 means the source is as
    quiet as it usually is between items; a value of 10.0 means it has been
    silent ten times longer than its own norm. The figure is comparable
    across sources that publish hourly and sources that publish monthly.

    This query deliberately assigns no state. Classifying a source as healthy,
    slowing or dead requires thresholds that belong to the state computation
    step, and burying them here would hide policy inside SQL. The query
    measures; the caller decides.

    The median is recomputed here rather than joined in from the caller so
    that the whole answer comes from a single pass over the database. Sources
    with no dated articles are still returned, carrying nulls, since a source
    that has never published anything datable is the most suspicious case of
    all and must not be filtered out.

    Ordering places sources with an unknown ratio first, then the loudest
    silences, since both call for attention ahead of the healthy remainder.

    Returns:
        list[dict]: source_id, title, is_active, last_published_at,
        dated_articles, median_interval_hours, interval_count,
        hours_since_last, silence_ratio.
    """
    with connection.cursor() as cursor:
        cursor.execute(SILENT_SOURCES_SQL)
        return _fetch_all_as_dicts(cursor)

PUBLICATION_RHYTHM_SQL = """
SELECT
    source.id AS source_id,
    source.title AS title,
    CAST(strftime('%w', article.published_at) AS INTEGER) AS weekday,
    CAST(strftime('%H', article.published_at) AS INTEGER) AS hour_utc,
    COUNT(*) AS articles
FROM monitor_article AS article
JOIN monitor_source AS source ON source.id = article.source_id
WHERE article.published_at IS NOT NULL
GROUP BY source.id, weekday, hour_utc
ORDER BY source.title, weekday, hour_utc
"""


def publication_rhythm():
    """Article counts grouped by weekday and hour of day, per source.

    Unlike the other queries in this module, this one diagnoses nothing. It
    describes behaviour: when during the week and the day a source actually
    publishes.

    Its value is as input to silence detection. A source that never posts
    between 22:00 and 06:00 is not failing when it goes quiet overnight,
    while the same gap at midday is a real signal. A ratio against the median
    interval cannot make that distinction on its own; the distribution can.

    Weekday follows the SQLite convention where 0 is Sunday through to 6 for
    Saturday. Hours are UTC, because USE_TZ is enabled and published_at is
    stored in UTC - reading a peak at hour 14 as local Kyiv time would be
    wrong. A shared UTC scale is preferable here anyway, since it keeps
    sources comparable with each other.

    The result is returned flat rather than as a 7x24 matrix. Reshaping it for
    display belongs to the presentation layer, not to SQL.

    The join is inner: a source with no dated articles has no rhythm to
    describe, and an empty row would carry no meaning.

    Returns:
        list[dict]: source_id, title, weekday, hour_utc, articles.
    """
    with connection.cursor() as cursor:
        cursor.execute(PUBLICATION_RHYTHM_SQL)
        return _fetch_all_as_dicts(cursor)