"""Register a feed for monitoring.

Usage:
    python manage.py add_source https://example.com/feed.xml
    python manage.py add_source https://example.com/feed.xml --title "Example"
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from monitor.collector.fetcher import FetchError, fetch
from monitor.collector.parser import ParseError, parse
from monitor.models import Source


class Command(BaseCommand):
    help = "Add a feed to the list of monitored sources."

    def add_arguments(self, parser):
        parser.add_argument("feed_url", help="URL of the RSS or Atom feed")
        parser.add_argument(
            "--title",
            default="",
            help="Display name. Taken from the feed itself when omitted.",
        )
        parser.add_argument(
            "--no-verify",
            action="store_true",
            help="Register without fetching the feed first.",
        )

    def handle(self, *args, **options):
        feed_url = options["feed_url"]
        title = options["title"]

        if Source.objects.filter(feed_url=feed_url).exists():
            raise CommandError(f"Already monitored: {feed_url}")

        if not options["no_verify"]:
            # Verifying at registration is cheap and moves the failure to
            # the moment a human is watching. An unverified source fails
            # later, in an unattended run, where it looks like the feed
            # went quiet rather than like it was never valid.
            title = self._verify(feed_url) or title

        if not title:
            raise CommandError(
                "No title found in the feed; pass --title explicitly."
            )

        try:
            source = Source.objects.create(feed_url=feed_url, title=title)
        except IntegrityError as exc:
            raise CommandError(f"Could not add source: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(f"Added #{source.id}: {source.title}")
        )

    def _verify(self, feed_url):
        """Fetch and parse the feed once, returning its own title."""
        try:
            result = fetch(feed_url)
        except FetchError as exc:
            raise CommandError(f"Could not fetch the feed: {exc.message}") from exc

        try:
            feed = parse(result.body)
        except ParseError as exc:
            raise CommandError(f"Could not parse the feed: {exc}") from exc

        self.stdout.write(
            f"Feed reachable: {len(feed.entries)} entries, {feed.skipped} skipped"
        )
        return feed.title