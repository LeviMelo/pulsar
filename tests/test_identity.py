"""Identity across spellings: the same person written four ways is one node.

Co-authors arrive as "Richard James Ladle", "Richard J Ladle", "LADLE, R. J."
and "R. Ladle" on one CV. Faculty arrive on other people's teams as "A. C. M.
Malhado". Neither is a different person, and a graph that says otherwise
mismeasures everyone around them. These tests pin what is merged and — just as
important — what is left apart.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pulsar_research.db import Database
from pulsar_research.graph.identity import PersonResolver, given_first, merge_names, name_key


def test_a_citation_form_is_reordered_to_given_name_first():
    assert given_first("LADLE, Richard J.") == "Richard J. LADLE"
    assert given_first("Richard Ladle") == "Richard Ladle"
    assert given_first("Silva, Ana, Souza") == "Silva, Ana, Souza"   # two commas: not a name form


def test_the_key_is_first_initial_and_surname_ignoring_particles():
    assert name_key("Ana Cláudia Mendes Malhado") == ("A", "MALHADO")
    assert name_key("Malhado, Ana C. M.") == ("A", "MALHADO")
    assert name_key("João de Souza") == ("J", "SOUZA")


def test_spellings_of_one_person_merge_to_the_fullest_one():
    merged = merge_names(["Richard J Ladle", "LADLE, R. J.", "Richard James Ladle", "R. Ladle"])
    assert set(merged.values()) == {"Richard James Ladle"}


def test_a_different_first_name_with_the_same_surname_stays_apart():
    merged = merge_names(["Richard James Ladle", "Ricardo Ladle"])
    assert merged["Ricardo Ladle"] == "Ricardo Ladle"
    assert merged["Richard James Ladle"] == "Richard James Ladle"


def test_a_reordered_middle_name_is_not_the_same_person():
    merged = merge_names(["Ana Souza Lima Costa", "Ana Lima Souza Costa"])
    assert len(set(merged.values())) == 2


def test_an_initial_matches_only_names_it_begins():
    merged = merge_names(["A. Malhado", "Ana Malhado", "Beatriz Malhado"])
    assert merged["A. Malhado"] == "Ana Malhado"
    assert merged["Beatriz Malhado"] == "Beatriz Malhado"


@pytest.fixture
def faculty(tmp_path: Path) -> Database:
    db = Database(tmp_path / "f.duckdb")
    db.initialize()
    with db.connect() as con:
        con.execute("INSERT INTO professors (siape, canonical_name) VALUES "
                    "('1', 'ana claudia mendes malhado'), ('2', 'richard james ladle'), "
                    "('3', 'roberto lima'), ('4', 'renata lima')")
    return db


def test_faculty_are_recognised_in_citation_form_and_by_initials(faculty: Database):
    r = PersonResolver(faculty)
    assert r.resolve("Malhado, Ana C. M.") == "1"
    assert r.resolve("A. C. M. Malhado") == "1"
    assert r.resolve("R. J. Ladle") == "2"
    assert r.resolve("Richard Ladle") == "2"


def test_an_initial_that_fits_two_professors_resolves_to_neither(faculty: Database):
    r = PersonResolver(faculty)
    assert r.resolve("R. Lima") == ""
    assert r.resolve("Roberto Lima") == "3"


def test_inferences_are_counted(faculty: Database):
    r = PersonResolver(faculty)
    r.resolve("A. Malhado")
    r.resolve("richard james ladle")     # exact: not an inference
    assert len(r.inferred) == 1
