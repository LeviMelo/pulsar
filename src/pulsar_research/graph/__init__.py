"""The entity graph: one id space, one relation vocabulary, one projection.

`model` declares what exists and what can join what. `project` translates
acquired facts into it. `store` reads and writes it. Nothing outside this
package should know that a person is a row in `professors`.
"""

from .model import Edge, Entity, Kind, Relation, eid, is_indexed_person, person_id
from .project import build
from .store import (
    adjacency, entity, metrics_for, neighbours, record_build, summary,
    top_by_metric, write_graph, write_metrics,
)

__all__ = [
    "Edge", "Entity", "Kind", "Relation", "eid", "is_indexed_person", "person_id",
    "build", "adjacency", "entity", "metrics_for", "neighbours", "record_build",
    "summary", "top_by_metric", "write_graph", "write_metrics",
]
