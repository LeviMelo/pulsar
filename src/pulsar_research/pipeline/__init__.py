"""The build graph: declared stages, their dependencies, and their freshness.

`registry` says what exists and how to tell whether it is current; `runner`
executes it. The ordering that used to live in one CLI function lives here, so
"what is stale" and "what would re-running this invalidate" are answerable
without reading the source.
"""

from .registry import STAGES, Freshness, Stage, dependents, order, status, with_dependencies
from .runner import plan, run

__all__ = ["STAGES", "Freshness", "Stage", "dependents", "order", "status",
           "with_dependencies", "plan", "run"]
