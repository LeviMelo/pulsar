"""What happened after the message was sent.

The system could plan an outreach campaign in detail and then lost interest the
moment the mail left the building. But the send is the cheap half: a reply comes
back, a vacancy turns out to be already promised, a conversation goes quiet for
a week, and — under the PIBIC/PIBITI rules — exactly one of those threads may
end in a SIGAA indication. All of that lived in the operator's head.

The states are deliberately few, and ordered by how far the conversation has
travelled, so a board sorted by state reads as a funnel:

``awaiting``   sent, nothing back yet
``replied``    they answered, nothing decided
``open``       an actual conversation, worth pursuing
``declined``   the vacancy is gone, or they said no
``indicated``  the SIGAA indication went to this professor
``closed``     not being pursued, without a no

`indicated` is the one state with a rule attached: the indication is unique, so
`set_state` refuses to create a second one rather than leaving the store in a
shape the domain forbids.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..db import Database

#: In funnel order. `ORDER` is what the UI sorts and groups by.
STATES: tuple[str, ...] = ("awaiting", "replied", "open", "declined", "indicated", "closed")

#: Short, human labels; the UI must not invent its own.
LABELS: dict[str, str] = {
    "awaiting": "Awaiting reply",
    "replied": "Replied",
    "open": "In conversation",
    "declined": "Declined",
    "indicated": "Indicated in SIGAA",
    "closed": "Not pursuing",
}

#: States that still need something from the operator.
LIVE_STATES: frozenset[str] = frozenset({"awaiting", "replied", "open"})

DEFAULT_STATE = "awaiting"


class IndicationConflict(RuntimeError):
    """Raised when a second professor would be marked as indicated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def outcomes(db: Database, campaign_id: str | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """``(campaign_id, siape) -> outcome record``."""
    sql = ("SELECT campaign_id, siape, state, note, replied_at, updated_at "
           "FROM campaign_outcomes")
    params: list[Any] = []
    if campaign_id:
        sql += " WHERE campaign_id=?"
        params.append(campaign_id)
    frame = db.query_df(sql, params)
    return {
        (str(row.campaign_id), str(row.siape)): {
            "state": str(row.state or DEFAULT_STATE),
            "note": str(row.note or ""),
            "replied_at": None if row.replied_at is None else str(row.replied_at),
            "updated_at": None if row.updated_at is None else str(row.updated_at),
        }
        for row in frame.itertuples()
    }


def indicated(db: Database) -> tuple[str, str] | None:
    """The single ``(campaign_id, siape)`` already indicated, across all campaigns."""
    frame = db.query_df(
        "SELECT campaign_id, siape FROM campaign_outcomes WHERE state='indicated' LIMIT 1")
    if not len(frame):
        return None
    row = frame.iloc[0]
    return str(row.campaign_id), str(row.siape)


def set_state(db: Database, campaign_id: str, siape: str, *,
              state: str | None = None, note: str | None = None,
              force: bool = False) -> dict[str, Any]:
    """Record where a conversation stands. Returns the stored record.

    Passing only ``note`` leaves the state alone, and vice versa, so the UI can
    save a note without having to echo back a state it did not change.
    """
    if state is not None and state not in STATES:
        raise ValueError(f"unknown state {state!r}; expected one of {', '.join(STATES)}")

    existing = outcomes(db, campaign_id).get((campaign_id, siape))
    target_state = state or (existing or {}).get("state") or DEFAULT_STATE

    displaced: tuple[str, str] | None = None
    if state == "indicated":
        other = indicated(db)
        if other and other != (campaign_id, siape):
            if not force:
                raise IndicationConflict(
                    f"{other[1]} is already marked as indicated, and the SIGAA indication is "
                    "unique. Clear that one first, or pass force=True to move it."
                )
            # `force` moves the indication; it does not create a second one. An
            # earlier version simply skipped the check and wrote the new row,
            # which left two professors indicated at once — precisely the state
            # this rule exists to make unrepresentable.
            displaced = other

    now = _now()
    replied_at = (existing or {}).get("replied_at")
    # The moment a reply is first acknowledged is worth keeping: it is the only
    # timestamp that says how long a professor took to answer.
    if replied_at is None and target_state in ("replied", "open", "declined", "indicated"):
        replied_at = now

    record = {
        "state": target_state,
        "note": (note if note is not None else (existing or {}).get("note") or ""),
        "replied_at": replied_at,
        "updated_at": now,
    }
    with db.connect() as con:
        if displaced:
            # The professor who held it is still in an active conversation; they
            # have simply stopped holding the indication.
            con.execute("UPDATE campaign_outcomes SET state='open', updated_at=? "
                        "WHERE campaign_id=? AND siape=?", [now, displaced[0], displaced[1]])
        con.execute("DELETE FROM campaign_outcomes WHERE campaign_id=? AND siape=?",
                    [campaign_id, siape])
        con.execute(
            "INSERT INTO campaign_outcomes (campaign_id, siape, state, note, replied_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            [campaign_id, siape, record["state"], record["note"],
             record["replied_at"], record["updated_at"]],
        )
    return record


def funnel(db: Database, campaign_id: str) -> dict[str, int]:
    """Count of every state for a campaign, including the states with none.

    Recipients with no outcome row are counted as `awaiting` only if their
    message was actually sent — an unsent draft is not a conversation.
    """
    sent = db.query_df(
        "SELECT r.siape FROM campaign_recipients r "
        "JOIN campaign_messages m ON m.campaign_id=r.campaign_id AND m.siape=r.siape "
        "WHERE r.campaign_id=? AND m.status='sent'", [campaign_id])
    recorded = outcomes(db, campaign_id)
    counts = {state: 0 for state in STATES}
    for siape in (str(s) for s in sent["siape"]) if len(sent) else ():
        state = recorded.get((campaign_id, siape), {}).get("state", DEFAULT_STATE)
        counts[state] = counts.get(state, 0) + 1
    return counts
