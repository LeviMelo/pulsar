"""Conversation state, and the one rule the domain puts on it.

A campaign that reports "sent: 32" and nothing else cannot tell its operator
which threads are still alive, and the PIBIC/PIBITI rules allow exactly one
SIGAA indication — so a store that cheerfully accepts two of them is storing a
situation that cannot exist.
"""
from __future__ import annotations

import pytest

from pulsar_research.outreach import outcomes as oc


@pytest.fixture()
def campaign(db):
    """A two-recipient campaign whose messages both went out."""
    with db.connect() as con:
        con.execute("INSERT INTO campaigns (campaign_id, name, status) VALUES ('c1','Test','sent')")
        for siape, name in (("1", "Ana"), ("2", "Bruno")):
            con.execute(
                "INSERT INTO campaign_recipients (campaign_id, siape, professor_name, selected) "
                "VALUES ('c1', ?, ?, TRUE)", [siape, name])
            con.execute(
                "INSERT INTO campaign_messages (campaign_id, siape, subject, status) "
                "VALUES ('c1', ?, 'hello', 'sent')", [siape])
    return "c1"


def test_a_sent_message_with_no_record_is_awaiting_not_missing(db, campaign):
    """The funnel must account for every delivered message, recorded or not."""
    assert oc.funnel(db, campaign) == {
        "awaiting": 2, "replied": 0, "open": 0, "declined": 0, "indicated": 0, "closed": 0}


def test_an_unsent_draft_is_not_a_conversation(db, campaign):
    with db.connect() as con:
        con.execute("INSERT INTO campaign_recipients (campaign_id, siape, professor_name, selected) "
                    "VALUES ('c1','3','Carla',TRUE)")
        con.execute("INSERT INTO campaign_messages (campaign_id, siape, subject, status) "
                    "VALUES ('c1','3','hello','draft')")
    assert sum(oc.funnel(db, campaign).values()) == 2, "the draft must not enter the funnel"


def test_state_and_note_are_independently_updatable(db, campaign):
    oc.set_state(db, campaign, "1", state="open", note="asked about the stack")
    oc.set_state(db, campaign, "1", note="sent the leptospirosis draft")
    record = oc.outcomes(db, campaign)[(campaign, "1")]
    assert record["state"] == "open", "a note-only save must not reset the state"
    assert record["note"] == "sent the leptospirosis draft"

    oc.set_state(db, campaign, "1", state="declined")
    assert oc.outcomes(db, campaign)[(campaign, "1")]["note"] == "sent the leptospirosis draft", \
        "a state-only save must not erase the note"


def test_the_first_reply_timestamp_is_kept_once_and_never_moved(db, campaign):
    oc.set_state(db, campaign, "1", state="replied")
    first = oc.outcomes(db, campaign)[(campaign, "1")]["replied_at"]
    assert first
    oc.set_state(db, campaign, "1", state="open")
    assert oc.outcomes(db, campaign)[(campaign, "1")]["replied_at"] == first, \
        "how long they took to answer is only knowable from the first reply"


def test_awaiting_never_invents_a_reply_timestamp(db, campaign):
    oc.set_state(db, campaign, "1", state="awaiting", note="nothing yet")
    assert oc.outcomes(db, campaign)[(campaign, "1")]["replied_at"] is None


def test_the_indication_is_unique(db, campaign):
    oc.set_state(db, campaign, "1", state="indicated")
    with pytest.raises(oc.IndicationConflict) as excinfo:
        oc.set_state(db, campaign, "2", state="indicated")
    assert "unique" in str(excinfo.value)
    assert oc.indicated(db) == (campaign, "1"), "the refused write must change nothing"


def test_forcing_moves_the_indication_rather_than_creating_a_second(db, campaign):
    oc.set_state(db, campaign, "1", state="indicated")
    oc.set_state(db, campaign, "2", state="indicated", force=True)
    states = {siape: rec["state"] for (_, siape), rec in oc.outcomes(db, campaign).items()}
    assert states == {"1": "open", "2": "indicated"}
    assert oc.indicated(db) == (campaign, "2")
    assert oc.funnel(db, campaign)["indicated"] == 1, "two indications can never both exist"


def test_reasserting_the_same_indication_is_not_a_conflict(db, campaign):
    oc.set_state(db, campaign, "1", state="indicated")
    oc.set_state(db, campaign, "1", state="indicated", note="confirmed in SIGAA")
    assert oc.outcomes(db, campaign)[(campaign, "1")]["note"] == "confirmed in SIGAA"


def test_an_unknown_state_is_refused_rather_than_stored(db, campaign):
    with pytest.raises(ValueError):
        oc.set_state(db, campaign, "1", state="maybe")
    assert not oc.outcomes(db, campaign)


def test_every_state_carries_a_label_for_the_interface():
    assert set(oc.LABELS) == set(oc.STATES)
    assert oc.LIVE_STATES <= set(oc.STATES)
