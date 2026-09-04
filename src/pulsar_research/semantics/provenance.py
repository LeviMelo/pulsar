"""Semantic provenance: two identities, not one.

The previous engine hashed corpus + config + *the operator's profile queries*
into a single ``model_id``. Editing a personal interest therefore invalidated the
topic model and the landscape, which is wrong: Levi's interests do not change
what UFAL researches. Conversely the corpus hash covered only opportunities, so
refreshing the professor/Lattes corpus silently left professor scores stale.

PULSAR now separates:

``semantic_space_id``
    corpus fingerprint + preprocessing + model architecture + embedding model
    identity + library versions. Topics, geometry and document embeddings belong
    to a *space*.

``profile_run_id``
    a semantic space + the operator's profile/query configuration. Rankings and
    per-entity fit scores belong to a *run*.

Both are content hashes, so an identical rebuild is a no-op and any drift is
detectable rather than silent.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..db import Database, set_meta, utcnow


def _digest(payload: Mapping[str, Any], prefix: str, length: int = 16) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"{prefix}-{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:length]}"


def library_versions() -> dict[str, str]:
    """Pin the numerical stack. An sklearn upgrade can move every coordinate."""
    import numpy, scipy, sklearn
    return {
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "scikit-learn": sklearn.__version__,
    }


@dataclass(slots=True)
class SemanticSpaceIdentity:
    corpus_fingerprint: str
    architecture: dict[str, Any]
    embedding_identity: dict[str, Any] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=library_versions)

    @property
    def space_id(self) -> str:
        return _digest(
            {
                "corpus": self.corpus_fingerprint,
                "architecture": self.architecture,
                "embedding": self.embedding_identity,
                "versions": self.versions,
            },
            "space",
        )

    def payload(self) -> dict[str, Any]:
        return {
            "corpus_fingerprint": self.corpus_fingerprint,
            "architecture": self.architecture,
            "embedding": self.embedding_identity,
            "versions": self.versions,
        }


@dataclass(slots=True)
class ProfileRunIdentity:
    space_id: str
    profile: dict[str, Any]

    @property
    def run_id(self) -> str:
        return _digest({"space": self.space_id, "profile": self.profile}, "run")


@dataclass(slots=True)
class Staleness:
    space_id: str
    stored_fingerprint: str
    current_fingerprint: str
    entities_added: int = 0

    @property
    def is_stale(self) -> bool:
        return self.stored_fingerprint != self.current_fingerprint

    def describe(self) -> str:
        if not self.is_stale:
            return f"semantic space {self.space_id} matches the current corpus"
        return (
            f"semantic space {self.space_id} was built from corpus "
            f"{self.stored_fingerprint[:12]} but the database now holds "
            f"{self.current_fingerprint[:12]}. Run `pulsar semantics build` before "
            f"trusting rankings, campaigns or the landscape."
        )


class StaleSemanticSpace(RuntimeError):
    """Raised when an action would mix fresh entities with stale semantic scores."""


def current_space_id(db: Database) -> str:
    with db.connect(read_only=True) as con:
        row = con.execute("SELECT value FROM meta WHERE key='current_semantic_space_id'").fetchone()
    return str(row[0]) if row else ""


def current_run_id(db: Database) -> str:
    with db.connect(read_only=True) as con:
        row = con.execute("SELECT value FROM meta WHERE key='current_profile_run_id'").fetchone()
    return str(row[0]) if row else ""


def check_staleness(db: Database, current_fingerprint: str) -> Staleness:
    """Compare the active semantic space against the corpus as it is right now."""
    space_id = current_space_id(db)
    stored = ""
    if space_id:
        with db.connect(read_only=True) as con:
            row = con.execute(
                "SELECT corpus_fingerprint FROM semantic_spaces WHERE space_id=?", [space_id]
            ).fetchone()
        stored = str(row[0]) if row else ""
    return Staleness(space_id or "(none)", stored, current_fingerprint)


def require_fresh(db: Database, current_fingerprint: str, *, action: str) -> None:
    """Refuse to perform a consequential action on stale semantics."""
    state = check_staleness(db, current_fingerprint)
    if state.is_stale or not state.stored_fingerprint:
        raise StaleSemanticSpace(f"Refusing to {action}: {state.describe()}")


def record_space(db: Database, identity: SemanticSpaceIdentity, stats: Mapping[str, Any]) -> str:
    space_id = identity.space_id
    now = utcnow()
    with db.connect() as con:
        con.execute("DELETE FROM semantic_spaces WHERE space_id=?", [space_id])
        con.execute(
            "INSERT INTO semantic_spaces VALUES (?,?,?,?,?,?)",
            [space_id, identity.corpus_fingerprint,
             json.dumps(identity.payload(), ensure_ascii=False, default=str),
             json.dumps(dict(stats), ensure_ascii=False, default=str),
             json.dumps(identity.versions, ensure_ascii=False), now],
        )
        set_meta(con, "current_semantic_space_id", space_id)
    return space_id


def record_run(db: Database, identity: ProfileRunIdentity, stats: Mapping[str, Any]) -> str:
    run_id = identity.run_id
    now = utcnow()
    with db.connect() as con:
        con.execute("DELETE FROM profile_runs WHERE run_id=?", [run_id])
        con.execute(
            "INSERT INTO profile_runs VALUES (?,?,?,?,?)",
            [run_id, identity.space_id,
             json.dumps(identity.profile, ensure_ascii=False, default=str),
             json.dumps(dict(stats), ensure_ascii=False, default=str), now],
        )
        set_meta(con, "current_profile_run_id", run_id)
    return run_id
