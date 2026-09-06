"""What a source is, independent of any particular one.

PULSAR reads from an authenticated portal driven by a browser, from a public
portal crawled over HTTP, from a CV object embedded in one of those pages, and
from a CSV a person exported by hand. Those are four different ways of getting
data, with different costs, different failure modes and different things they
need from the operator — a password, a rate limit, a file on disk — and until
now the only place that knowledge lived was in the body of the function that did
the fetching. A new source meant a new function, a new CLI command, a new
pipeline stage and a new `doctor` line, all written by hand and all slightly
different.

This module is the contract those four (and the next ones) are declared
against. A source says what it is, how it is reached, what it needs, what tables
it owns and how to run it; everything else — the pipeline stage, the CLI
listing, the freshness probe, the readiness check, the run journal — is derived
from the declaration rather than written per source.

The vocabulary is deliberately small and closed. The distinctions that matter
operationally are the ones encoded here: whether running the source touches
somebody else's server (`Method`), and whether it can run at all on this machine
right now (`Access` plus the declared secrets).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import AppConfig
    from ..db import Database


class Method(str, Enum):
    """How the data is obtained. This decides what a run costs and risks."""

    HTTP_CRAWL = "http_crawl"            #: polite GET over a public site, archived raw
    BROWSER_SESSION = "browser_session"  #: a driven, logged-in browser (Playwright)
    EMBEDDED = "embedded"                #: carved out of another source's archive
    FILE_IMPORT = "file_import"          #: a file the operator produced or was given
    API = "api"                          #: a documented remote interface


#: Methods that reach the network. `pipeline run` refuses to run these unless
#: asked with `--acquire`, because a scrape is not a recomputation.
NETWORK_METHODS = frozenset({Method.HTTP_CRAWL, Method.BROWSER_SESSION, Method.API})


class Access(str, Enum):
    PUBLIC = "public"                #: anyone can read it
    AUTHENTICATED = "authenticated"  #: needs the operator's credentials
    LOCAL = "local"                  #: already on this machine


@dataclass(frozen=True, slots=True)
class RunOptions:
    """What an operator can vary about a run without editing code."""

    refresh: bool = False      #: ignore cached fetches and re-read everything
    limit: int | None = None   #: cap on the number of units (professors, rows) to process
    dry_run: bool = False


@dataclass(slots=True)
class Capture:
    """What one run produced. Journalled verbatim, so a run can be audited later.

    `rows` is per table, because "imported 15 tables" tells nobody anything and
    "0 rows into lattes_flat" is the line that explains why the corpus is empty.
    """

    source_id: str
    rows: dict[str, int] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()       #: paths on disk the run left behind
    notes: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_id,
            "rows": dict(self.rows),
            "artifacts": list(self.artifacts),
            "notes": dict(self.notes),
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class Source:
    """One declared way data enters the store.

    `yields` names the ACQUIRED tables this source owns. Ownership is exclusive
    on purpose: two sources writing the same table is how an import quietly
    deletes a crawl.

    `secrets` are environment-variable *names*, never values, and `needs` are
    the config keys or paths a run reads. Both exist so that `pulsar sources
    list` can say whether a source is runnable here before anyone tries.
    """

    id: str
    title: str
    provider: str
    method: Method
    access: Access
    why: str
    yields: tuple[str, ...]
    stage: str                                     #: the pipeline stage name it becomes
    run: Callable[["AppConfig", "Database", RunOptions], Capture] = field(repr=False)
    depends_on: tuple[str, ...] = ()               #: other source ids
    secrets: tuple[str, ...] = ()                  #: env-var names
    config_section: str = ""
    rate: str = ""                                 #: what politeness the run observes
    cost: str = ""                                 #: what a run typically takes
    #: The table whose row count witnesses that the source has ever run, for
    #: stores that predate the journal.
    witness: str = ""
    #: A probe answering "has the input changed since the last run" for sources
    #: whose input is local — a file's mtime, another source's fingerprint.
    #: Network sources have no such probe: staleness there is a matter of time.
    changed: Callable[["AppConfig", "Database"], str | None] | None = field(
        default=None, repr=False)
    #: False for sources the pipeline must never run on its own initiative — a
    #: file import that overwrites what a crawl fetched is an operator's call.
    #: They still appear as stages, and `pulsar sources run` runs them.
    implicit: bool = True

    @property
    def reaches_network(self) -> bool:
        return self.method in NETWORK_METHODS
