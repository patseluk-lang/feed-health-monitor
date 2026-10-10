"""Database models for the feed health monitor.

Four tables:
    Source          - a feed being watched
    FetchRun        - one attempt to collect from a source, successful or not
    Article         - one item retrieved from a feed
    HealthSnapshot  - a computed verdict on a source at a point in time
"""

from django.db import models


class Source(models.Model):
    """A feed being watched.

    `identity_strategy` records how articles from this source are to be
    deduplicated. The default assumes the feed's guid is stable across
    fetches, which is what the RSS specification intends. Some feeds
    violate it: BBC News derives the guid from the item's position in the
    document, so the same article arrives under a new guid on every fetch
    and the unique constraint stops protecting anything.

    The setting is per source rather than global on purpose. Instability is
    a property of a particular publisher, not of feeds in general, and a
    global rule that demotes guid would give up the protection guid exists
    to provide - resilience to a url that changes.

    Switching a source is a deliberate act, prompted by the identity
    stability report, and the reason belongs in `note`.
    """

    class IdentityStrategy(models.TextChoices):
        GUID = "guid", "Feed guid, falling back to url"
        URL = "url", "Url only"

    title = models.CharField(max_length=200)
    feed_url = models.URLField(max_length=500, unique=True)
    site_url = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    identity_strategy = models.CharField(
        max_length=10,
        choices=IdentityStrategy.choices,
        default=IdentityStrategy.GUID,
    )
    added_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class FetchRun(models.Model):
    """One attempt to collect from a source.

    A row is written whether the attempt succeeded or failed. Without the
    failures recorded, a silent source cannot be told apart from a source
    we simply could not reach.
    """

    class Status(models.TextChoices):
        OK = "ok", "OK"
        ERROR = "error", "Error"

    source = models.ForeignKey(
        Source, on_delete=models.CASCADE, related_name="runs"
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_type = models.CharField(max_length=50, blank=True)
    error_message = models.TextField(blank=True)
    items_seen = models.PositiveIntegerField(default=0)
    items_new = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(
                fields=["source", "-started_at"],
                name="fetchrun_source_started_idx",
            ),
        ]

    def __str__(self):
        return f"{self.source.title} {self.started_at:%Y-%m-%d %H:%M} {self.status}"


class Article(models.Model):
    """One item retrieved from a feed.

    `identity` is the deduplication key, derived from the feed entry by the
    rule the source declares in `identity_strategy`: normally the feed's
    guid when present, otherwise the url, otherwise a hash of title and
    publication date; for a source marked `url`, the guid is skipped
    entirely.

    Whichever rule applies, it must be deterministic - the same entry has
    to produce the same identity on every run. A rule that does not is not
    a slow rule or an inaccurate one; it is a rule that silently fills the
    table with duplicates while every run reports success.
    """

    source = models.ForeignKey(
        Source, on_delete=models.CASCADE, related_name="articles"
    )
    identity = models.CharField(max_length=200)
    url = models.URLField(max_length=1000)
    title = models.CharField(max_length=500, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    summary = models.TextField(blank=True)

    class Meta:
        ordering = ["-published_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "identity"],
                name="unique_article_per_source",
            ),
        ]
        indexes = [
            models.Index(
                fields=["source", "-published_at"],
                name="article_source_published_idx",
            ),
        ]

    def __str__(self):
        return self.title or self.url


class HealthSnapshot(models.Model):
    """A computed verdict on a source at a point in time.

    `unknown` exists so that a source with too little history is not
    reported as silent. Stating that we do not know is a result; guessing
    is not.
    """

    class State(models.TextChoices):
        HEALTHY = "healthy", "Healthy"
        SLOWING = "slowing", "Slowing"
        SILENT = "silent", "Silent"
        FAILING = "failing", "Failing"
        UNKNOWN = "unknown", "Unknown"

    source = models.ForeignKey(
        Source, on_delete=models.CASCADE, related_name="snapshots"
    )
    computed_at = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=15, choices=State.choices)
    last_published_at = models.DateTimeField(null=True, blank=True)
    median_interval_hours = models.FloatField(null=True, blank=True)
    articles_30d = models.PositiveIntegerField(default=0)
    articles_prev_30d = models.PositiveIntegerField(default=0)
    failed_runs_7d = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-computed_at"]
        indexes = [
            models.Index(
                fields=["source", "-computed_at"],
                name="snapshot_source_computed_idx",
            ),
        ]

    def __str__(self):
        return f"{self.source.title}: {self.state}"