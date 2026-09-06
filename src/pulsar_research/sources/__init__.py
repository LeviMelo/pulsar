"""How data enters the store.

A *source* is a declared way of acquiring data: what it is, how it is reached
(a crawl, a driven browser, a file, an object embedded in another source), what
it needs from the operator, what tables it owns, and how to run it. The
pipeline, the CLI and the console all read the declarations here rather than
carrying their own list.

    from pulsar_research.sources import SOURCES, get, journalled_run

Nothing in this package fetches anything itself; the run bodies call into
`acquisition/`, which keeps the scrapers where they were. What changes is that
they are now reachable by id, journalled uniformly, and described to the
operator before they run.
"""

from .model import Access, Capture, Method, NETWORK_METHODS, RunOptions, Source
from .registry import BY_ID, BY_STAGE, SOURCES, describe, get
from .runs import SourceUnavailable, history, journalled_run, last_run, readiness

__all__ = [
    "Access", "Capture", "Method", "NETWORK_METHODS", "RunOptions", "Source",
    "BY_ID", "BY_STAGE", "SOURCES", "describe", "get",
    "SourceUnavailable", "history", "journalled_run", "last_run", "readiness",
]
