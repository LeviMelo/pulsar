import pytest
pytest.importorskip("duckdb")
from pulsar_research.ingest.legacy import normalize_name, parse_slots


def test_slots():
    assert parse_slots("2 vaga(s)") == 2
    assert parse_slots("") == 0


def test_name_normalization():
    assert normalize_name("Diego Figueiredo Nóbrega") == "DIEGO FIGUEIREDO NOBREGA"
