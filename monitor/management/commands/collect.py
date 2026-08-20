"""Collect from monitored sources.

Usage:
    python manage.py collect
    python manage.py collect --source 1
"""

from django.core.management.base import BaseCommand, CommandError

from monitor.collector.runner import collect_all
from monitor.models import Source


class Command(BaseCommand):
    help = "Fetch every active source and store any new articles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=int,
            help="Collect from one source by id instead of all active ones.",
        )

    def handle(self, *args, **options):
        sources = self._select(options["source"])

        if not sources:
            raise CommandError("No active sources to collect from.")

        summary = collect_all(sources)

        self.stdout.write(
            f"Attempted {summary.sources_attempted}, "
            f"succeeded {summary.sources_ok}, "
            f"failed {summary.sources_failed}, "
            f"new articles {summary.articles_new}"
        )

        # A non-zero exit code when nothing worked lets a scheduler notice
        # the failure without anyone reading the log. A partial failure is
        # still a success: the run did its job for the sources it could
        # reach.
        if summary.sources_ok == 0:
            raise CommandError("Every source failed.")

    def _select(self, source_id):
        if source_id is None:
            return list(Source.objects.filter(is_active=True))

        source = Source.objects.filter(id=source_id).first()
        if source is None:
            raise CommandError(f"No source with id {source_id}")
        return [source]