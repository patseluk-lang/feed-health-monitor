# Unstable identity: BBC News

## What happened

BBC News derives the `<guid>` of a feed item from the item's position in
the document, not from the item itself:

    <guid isPermaLink="false">https://www.bbc.co.uk/news/articles/c0qxew81y83o#0</guid>

The suffix after `#` is the index. The same article moves down the feed as
newer ones arrive, and its guid changes with it.

The collector derived `Article.identity` from the guid, so a returning
article looked like a new one. `UniqueConstraint(source, identity)` had
nothing to reject: the key really was new. Every collection run inserted
the same articles again, reported success, and incremented `items_new`.

No error was raised at any point. The failure was visible only as the same
url appearing under several identities.

## How it was found

An n8n workflow was built to inspect the raw feed outside this project:
HTTP Request fetched the document as text, an XML node converted it to a
structure without discarding fields. Reading the guids in document order
made the positional suffix obvious.

The hypothesis — that identity was unstable — was then tested against the
collected data, which is where it was confirmed.

## Report before remediation

Query: `monitor.analytics.queries.identity_stability` (Q7). It counts, per
source, how many urls appear under more than one identity.

    id  source                    unstable  tracked   ratio   max
     8  BBC News                        37      119   31.1%     5
     2  Broken Feed                      0        0     n/a     0
     1  Hacker News                      0      160    0.0%     1
     7  Lobsters                         0      109    0.0%     1
     6  Planet Python                    0       35    0.0%     1
     4  Python Insider                   0       51    0.0%     1
     3  Simon Willison's Weblog          0       41    0.0%     1
     5  The Django weblog                0       11    0.0%     1

Seven of eight sources report `max = 1`, meaning one identity per url, as
expected. The signal is confined to a single source, which is the evidence
that the query measures what it claims to.

`Broken Feed` is a deliberate synthetic fixture that always returns HTTP
404. It holds no articles, so the ratio is reported as unknown rather than
as zero — a ratio over an empty set carries no meaning.

The figure of 37 includes duplicates created by the remediation itself:
switching the source to url-based identity gave every currently listed
article a second key alongside the guid it already had. The count before
switching was 23 of 113.

## Remediation

`Source.identity_strategy` was added, defaulting to `guid`. BBC News was
set to `url`, with the reason recorded in `Source.note`.

The setting is per source rather than global. Instability is a property of
a particular publisher, not of feeds in general, and demoting guid
everywhere would give up what guid exists to provide: resilience to a url
that is rewritten while the entry stays the same.

Idempotency was confirmed after the change — a second consecutive run of
`collect --source 8` reported zero new articles.

## What this report does not do

The query measures accumulated state, not current behaviour. It cannot
distinguish a source that is unstable now from one that was unstable and
has been corrected: the historical duplicates keep counting either way.

Removing them is an administrative decision about the operator's data, not
something the monitor does on its own. The system detects and reports; it
does not delete. This snapshot was taken so that the incident survives the
cleanup that followed it.