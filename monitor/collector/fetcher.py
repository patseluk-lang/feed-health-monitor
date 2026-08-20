"""Fetching feed documents over HTTP.

This module returns bytes or explains why it could not. It does not parse,
does not know about the database, and does not decide what a failure means
for a source - that judgement belongs further up.
"""

import urllib.error
import urllib.request
from dataclasses import dataclass

USER_AGENT = "FeedHealthMonitor/0.1 (+https://github.com/patseluk-lang)"
TIMEOUT_SECONDS = 20
MAX_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 64 * 1024


class FetchError(Exception):
    """A feed could not be retrieved.

    `error_type` is a short machine-readable code, stored as-is in
    FetchRun.error_type. Classifying at the point of failure keeps the
    column groupable: "how often does this source time out" has to be a
    query, not a text search through exception messages.
    """

    error_type = "unknown"

    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


class TimeoutError_(FetchError):
    error_type = "timeout"


class HttpError(FetchError):
    error_type = "http_error"


class ConnectionError_(FetchError):
    error_type = "connection_error"


class TooLargeError(FetchError):
    error_type = "too_large"


class EmptyResponseError(FetchError):
    error_type = "empty_response"


@dataclass
class FetchResult:
    """What a successful fetch produced."""

    body: bytes
    http_status: int
    content_type: str


def fetch(url):
    """Retrieve a feed document.

    Returns a FetchResult on success. Raises a FetchError subclass on any
    failure, carrying the classification the caller needs to record.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, "
                      "application/xml, text/xml, */*",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = _read_capped(response)
            http_status = response.status
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise HttpError(
            f"Server returned HTTP {exc.code}", http_status=exc.code
        ) from exc
    except TimeoutError as exc:
        raise TimeoutError_(
            f"No response within {TIMEOUT_SECONDS}s"
        ) from exc
    except urllib.error.URLError as exc:
        # URLError wraps both DNS failures and socket timeouts; the timeout
        # case is checked first because it is the more specific diagnosis.
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError_(
                f"No response within {TIMEOUT_SECONDS}s"
            ) from exc
        raise ConnectionError_(f"Could not reach the host: {exc.reason}") from exc

    if not body.strip():
        # HTTP 200 with an empty body means something broke upstream. It does
        # not mean the feed has no news, and it must not be recorded as a
        # successful run with zero items.
        raise EmptyResponseError(
            "Server returned an empty document", http_status=http_status
        )

    return FetchResult(body=body, http_status=http_status, content_type=content_type)


def _read_capped(response):
    """Read the response body, refusing to exceed MAX_BYTES.

    The size is enforced while reading rather than from Content-Length,
    which is optional and occasionally wrong. Reading first and checking
    afterwards would mean a hostile or broken feed decides how much memory
    this process uses.
    """
    chunks = []
    total = 0

    while True:
        chunk = response.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise TooLargeError(
                f"Document exceeds {MAX_BYTES // (1024 * 1024)} MB",
                http_status=response.status,
            )
        chunks.append(chunk)

    return b"".join(chunks)