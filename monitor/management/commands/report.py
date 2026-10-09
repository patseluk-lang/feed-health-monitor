"""Print one of the analytics queries as JSON.

Usage:
    python manage.py report reliability
    python manage.py report silence
"""

import json

from django.core.management.base import BaseCommand

from monitor.analytics import queries

REPORTS = {
    "interval": queries.median_publish_interval,
    "volume": queries.volume_trend,
    "reliability": queries.collection_reliability,
    "duplicates": queries.duplicate_titles,
    "silence": queries.silent_sources,
    "rhythm": queries.publication_rhythm,
}


class Command(BaseCommand):
    help = "Run one analytics query and print the result as JSON."

    def add_arguments(self, parser):
        parser.add_argument("name", choices=sorted(REPORTS))

    def handle(self, *args, **options):
        rows = REPORTS[options["name"]]()
        # default=str covers datetime values returned by the database.
        self.stdout.write(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
