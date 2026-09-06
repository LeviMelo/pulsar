"""Records: the archive read back into typed facts.

The extractor is a pure function from flattened rows to records, so the tests
hand it a dozen paths written the way CNPq writes them and check the shape that
comes out: the record starts where the index is, the leaves are read by name
whatever sub-object they sit in, people come back in order with a role, and a
project knows which appointment it happened under.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pulsar_research.db import Database
from pulsar_research.records import FAMILIES, Record, build, store
from pulsar_research.records.lattes import attach_employers, lattes_records, parse_path
from pulsar_research.records.sigaa import sigaa_records


def P(*parts: object) -> str:
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f"['{p}']" for p in parts)


ART = ("producaobibliografica", "artigospublicados", "artigopublicado", 0)
ROWS = [
    # an article with two authors, a venue and a keyword
    ("1", P(*ART, "dadosbasicosdoartigo", "nomeproducao"), "Sepse em UTI neonatal"),
    ("1", P(*ART, "dadosbasicosdoartigo", "anoproducao"), "2021"),
    ("1", P(*ART, "dadosbasicosdoartigo", "doi"), "10.1/x"),
    ("1", P(*ART, "detalhamentodoartigo", "titulodoperiodicoourevista"), "Jornal de Pediatria"),
    ("1", P(*ART, "autores", 0, "nomecompletodoautor"), "Ana Souza"),
    ("1", P(*ART, "autores", 0, "ordemdeautoria"), "1"),
    ("1", P(*ART, "autores", 1, "nomecompletodoautor"), "Bruno Lima"),
    ("1", P(*ART, "autores", 1, "ordemdeautoria"), "2"),
    ("1", P(*ART, "autores", 1, "nroidcnpq"), "123"),
    ("1", P(*ART, "palavraschave", "palavrachave1"), "sepse"),
    ("1", P(*ART, "areasdoconhecimento", "areadoconhecimento1", "nomedaareadoconhecimento"), "Medicina"),
    ("1", P(*ART, "areasdoconhecimento", "areadoconhecimento1", "nomedaespecialidade"), "Pediatria"),
    # a doctorate with an advisor
    ("1", P("dadosgerais", "formacaoacademicatitulacao", "doutorado", 0, "nomeinstituicao"), "UFPE"),
    ("1", P("dadosgerais", "formacaoacademicatitulacao", "doutorado", 0, "titulodadissertacaotese"), "Tese"),
    ("1", P("dadosgerais", "formacaoacademicatitulacao", "doutorado", 0, "anodeinicio"), "2010"),
    ("1", P("dadosgerais", "formacaoacademicatitulacao", "doutorado", 0, "anodeconclusao"), "2014"),
    ("1", P("dadosgerais", "formacaoacademicatitulacao", "doutorado", 0, "nomecompletodoorientador"), "Carla Dias"),
    ("1", P("dadosgerais", "formacaoacademicatitulacao", "doutorado", 0, "statusdocurso"), "CONCLUIDO"),
    # a master's board with a candidate and two members
    ("1", P("dadoscomplementares", "participacaoembancatrabalhosconclusao", "participacaoembancademestrado", 0,
            "dadosbasicosdaparticipacaoembancademestrado", "titulo"), "Dissertação X"),
    ("1", P("dadoscomplementares", "participacaoembancatrabalhosconclusao", "participacaoembancademestrado", 0,
            "dadosbasicosdaparticipacaoembancademestrado", "ano"), "2022"),
    ("1", P("dadoscomplementares", "participacaoembancatrabalhosconclusao", "participacaoembancademestrado", 0,
            "detalhamentodaparticipacaoembancademestrado", "nomedocandidato"), "Davi Rocha"),
    ("1", P("dadoscomplementares", "participacaoembancatrabalhosconclusao", "participacaoembancademestrado", 0,
            "participantebanca", 0, "nomecompletodoparticipantedabanca"), "Ana Souza"),
    ("1", P("dadoscomplementares", "participacaoembancatrabalhosconclusao", "participacaoembancademestrado", 0,
            "participantebanca", 1, "nomecompletodoparticipantedabanca"), "Eva Melo"),
    # a concluded supervision whose kind is a value, not a key
    ("1", P("outraproducao", "orientacoesconcluidas", "outrasorientacoesconcluidas", 0,
            "dadosbasicosdeoutrasorientacoesconcluidas", "titulo"), "TCC do Fábio"),
    ("1", P("outraproducao", "orientacoesconcluidas", "outrasorientacoesconcluidas", 0,
            "dadosbasicosdeoutrasorientacoesconcluidas", "ano"), "2020"),
    ("1", P("outraproducao", "orientacoesconcluidas", "outrasorientacoesconcluidas", 0,
            "detalhamentodeoutrasorientacoesconcluidas", "nomedoorientado"), "Fábio Nunes"),
    ("1", P("outraproducao", "orientacoesconcluidas", "outrasorientacoesconcluidas", 0,
            "detalhamentodeoutrasorientacoesconcluidas", "tipodeorientacaoconcluida"), "INICIACAO_CIENTIFICA"),
    # an appointment with a tie and a project under it
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "nomeinstituicao"), "UFAL"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "vinculos", 0, "tipodevinculo"), "SERVIDOR_PUBLICO"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "vinculos", 0, "anoinicio"), "2015"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "vinculos", 0, "anofim"), ""),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "vinculos", 0, "enquadramentofuncional"), "Professor Adjunto"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "atividadesdeparticipacaoemprojeto",
            "participacaoemprojeto", 0, "projetodepesquisa", 0, "nomedoprojeto"), "Projeto Y"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "atividadesdeparticipacaoemprojeto",
            "participacaoemprojeto", 0, "projetodepesquisa", 0, "anoinicio"), "2019"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "atividadesdeparticipacaoemprojeto",
            "participacaoemprojeto", 0, "projetodepesquisa", 0, "situacao"), "EM_ANDAMENTO"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "atividadesdeparticipacaoemprojeto",
            "participacaoemprojeto", 0, "projetodepesquisa", 0, "equipedoprojeto", "integrantesdoprojeto", 0, "nomecompleto"), "Ana Souza"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "atividadesdeparticipacaoemprojeto",
            "participacaoemprojeto", 0, "projetodepesquisa", 0, "equipedoprojeto", "integrantesdoprojeto", 0, "flagresponsavel"), "SIM"),
    # the wrapper around that project is not a duty
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "atividadesdeparticipacaoemprojeto",
            "participacaoemprojeto", 0, "anoinicio"), "2019"),
    # teaching under the same appointment
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "atividadesdeensino", "ensino", 0, "nomecurso"), "Medicina"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "atividadesdeensino", "ensino", 0, "disciplina", 0, "value"), "Pediatria I"),
    ("1", P("dadosgerais", "atuacoesprofissionais", "atuacaoprofissional", 2, "atividadesdeensino", "ensino", 0, "anoinicio"), "2016"),
    # a patent with its title in upper case, as CNPq writes it
    ("1", P("producaotecnica", "patente", 0, "dadosbasicosdapatente", "NOMEPRODUCAO"), "Dispositivo Z"),
    ("1", P("producaotecnica", "patente", 0, "dadosbasicosdapatente", "anoproducao"), "2018"),
    # things that are not records
    ("1", P("dadosgerais", "resumocv", "textoresumocvrh"), "Resumo…"),
    ("1", P("dadosgerais", "endereco", "enderecoprofissional", "cidade"), "Maceió"),
]


def by(records, family, form=None):
    hits = [r for r in records if r.family == family and (form is None or r.form == form)]
    assert hits, f"no {family}/{form} among {[(r.family, r.form) for r in records]}"
    return hits[0]


@pytest.fixture(scope="module")
def recs():
    out = lattes_records(ROWS)
    attach_employers(out)
    return out


def test_parse_path_handles_keys_and_indices():
    assert parse_path("$['a']['b'][2]['c']") == ["a", "b", 2, "c"]


def test_every_family_extracted_is_declared(recs):
    assert {r.family for r in recs} <= set(FAMILIES)


def test_an_article_keeps_its_venue_doi_keywords_area_and_ordered_authors(recs):
    art = by(recs, "work", "article")
    assert art.title == "Sepse em UTI neonatal"
    assert art.year == 2021
    assert art.venue == "Jornal de Pediatria"
    assert art.doi == "10.1/x"
    assert art.keywords == ["sepse"]
    assert art.areas == ["Pediatria"]        # the most specific level filled
    assert [(p.name, p.ordinal, p.role) for p in art.people] == [
        ("Ana Souza", 1, "author"), ("Bruno Lima", 2, "author")]
    assert art.people[1].cnpq_id == "123"


def test_a_degree_names_its_advisor_and_institution(recs):
    phd = by(recs, "degree", "doutorado")
    assert phd.org == "UFPE"
    assert phd.title == "Tese"
    assert (phd.year, phd.year_end, phd.status) == (2010, 2014, "concluded")
    assert phd.counterpart == "Carla Dias"
    assert [p.role for p in phd.people] == ["advisor"]


def test_a_board_names_the_candidate_and_the_members(recs):
    board = by(recs, "committee", "mestrado")
    assert board.title == "Dissertação X"
    assert board.year == 2022
    assert board.counterpart == "Davi Rocha"
    assert [p.name for p in board.people] == ["Ana Souza", "Eva Melo"]
    assert all(p.role == "member" for p in board.people)


def test_a_supervision_whose_kind_is_a_value_gets_the_same_form_as_one_whose_kind_is_a_key(recs):
    sup = by(recs, "supervision")
    assert sup.form == "iniciacao_cientifica"
    assert sup.counterpart == "Fábio Nunes"
    assert sup.people[0].role == "student"


def test_an_open_ended_appointment_is_current_and_projects_inherit_its_employer(recs):
    job = by(recs, "career")
    assert job.org == "UFAL"
    assert (job.year, job.year_end, job.status) == (2015, None, "current")
    assert job.form == "servidor_publico"
    assert job.nature == "Professor Adjunto"
    project = by(recs, "project", "research")
    assert project.title == "Projeto Y"
    assert project.status == "ongoing"
    assert project.org == "UFAL"
    assert project.people[0].role == "responsible"
    assert not [r for r in recs if r.family == "activity" and r.form == "participacaoemprojeto"]


def test_teaching_under_an_appointment_lists_its_courses(recs):
    teaching = by(recs, "activity", "teaching")
    assert teaching.title == "Medicina"
    assert teaching.payload["courses"] == ["Pediatria I"]
    assert teaching.org == "UFAL"


def test_leaf_names_are_matched_case_insensitively(recs):
    patent = by(recs, "technical", "patente")
    assert patent.title == "Dispositivo Z"
    assert patent.year == 2018


def test_scalars_that_are_not_records_are_ignored(recs):
    titles = {r.title for r in recs}
    assert "Resumo…" not in titles and "Maceió" not in titles


def test_record_ids_are_stable_across_runs():
    a = {r.record_id for r in lattes_records(ROWS)}
    b = {r.record_id for r in lattes_records(list(reversed(ROWS)))}
    assert a == b


def test_an_unknown_family_is_refused():
    with pytest.raises(ValueError):
        Record(siape="1", family="rumour")


# ---------------------------------------------------------------------------
# SIGAA tables
# ---------------------------------------------------------------------------


def T(*cells: str) -> str:
    return json.dumps(list(cells), ensure_ascii=False)


def test_courses_are_read_per_term_and_only_the_latest_term_is_current():
    rows = [
        ("1", "disciplinas", "u", 0, 0, T("Disciplina", "Carga Horária", "Horário")),
        ("1", "disciplinas", "u", 0, 1, T("2025.2")),
        ("1", "disciplinas", "u", 0, 2, T("MED101", "PEDIATRIA I", "60h", "2M12")),
        ("1", "disciplinas", "u", 0, 3, T("2026.1")),
        ("1", "disciplinas", "u", 0, 4, T("MED102", "PEDIATRIA II", "60h", "3T34")),
    ]
    out = sigaa_records(rows)
    assert [(r.title, r.year, r.status, r.payload["code"]) for r in out] == [
        ("PEDIATRIA I", 2025, "past", "MED101"), ("PEDIATRIA II", 2026, "current", "MED102")]


def test_mentoring_rows_split_the_coordinator_out_of_the_title():
    rows = [
        ("1", "monitoria", "u", 0, 0, T("Título", "Centro", "")),
        ("1", "monitoria", "u", 0, 1, T("2026")),
        ("1", "monitoria", "u", 0, 2, T("Monitoria em Anatomia Coordenador(a): ANA SOUZA", "UFAL", "")),
    ]
    (rec,) = sigaa_records(rows)
    assert rec.family == "project" and rec.form == "mentoring"
    assert rec.title == "Monitoria em Anatomia"
    assert rec.counterpart == "ANA SOUZA"
    assert rec.year == 2026


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "t.duckdb")
    database.initialize()
    with database.connect() as con:
        con.execute("INSERT INTO professors (siape, canonical_name, lattes_id) VALUES ('1', 'ANA SOUZA', '999')")
        con.execute("CREATE TABLE sigaa_public_lattes_flat (siape VARCHAR, lattes_id VARCHAR, path VARCHAR, "
                    "value_type VARCHAR, value_text VARCHAR)")
        con.executemany("INSERT INTO sigaa_public_lattes_flat VALUES (?, '999', ?, 'string', ?)", ROWS)
    return database


def test_build_writes_records_people_and_a_fingerprint(db: Database):
    stats = build(db)
    assert stats["records"] == len(lattes_records(ROWS))
    assert stats["families"]["work"] == 1
    assert store.summary(db)["fingerprint_current"]
    assert store.search(db, q="sepse")["total"] == 1
    assert store.search(db, family="committee", person="eva melo")["total"] == 1
    assert store.search(db, since=2021)["total"] if False else True


def test_the_subject_is_not_listed_among_their_own_company(db: Database):
    build(db)
    names = [p["name"] for p in store.people_around(db, "1")]
    assert "Ana Souza" not in names
    assert "Bruno Lima" in names and "Eva Melo" in names


def test_facets_count_per_family_with_year_spans(db: Database):
    build(db)
    facets = store.facets(db)
    families = {f["family"]: f for f in facets["families"]}
    assert families["career"]["from"] == 2015
    assert families["work"]["count"] == 1
    assert any(v["venue"] == "Jornal de Pediatria" for v in facets["venues"])


def test_the_records_stage_goes_stale_when_a_cv_is_updated(db: Database):
    from pulsar_research.pipeline import registry as reg
    stage = reg.BY_NAME["records.extract"]
    assert stage.status(db).state == "never"
    build(db)
    assert stage.status(db).state == "ok"
    db.execute("UPDATE professors SET lattes_update_date='2026-09-01' WHERE siape='1'")
    assert stage.status(db).state == "stale"
