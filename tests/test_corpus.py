from __future__ import annotations

from pulsar_research.semantics.corpus import (SPECIFICITY, load_corpus, lattes_atoms,
                                              project_records, public_sigaa_atoms)
from pulsar_research.semantics.normalize import join_unique, scrub_citations


def test_citation_scrubbing_keeps_content_and_drops_author_year():
    out = scrub_citations("Segundo SILVA et al., 2024, o método é robusto (PEREIRA et al., 2021).")
    assert "2024" not in out and "2021" not in out
    assert "método" in out


def test_join_unique_suppresses_copied_blocks():
    text = join_unique(["mesmo parágrafo", "MESMO PARÁGRAFO", "outro"])
    assert text.count("arágrafo") == 1
    assert "outro" in text


def test_sibling_plans_become_one_project(rows):
    projects = project_records(rows)
    assert len(projects) == 2
    p1 = next(p for p in projects if p["project_code"] == "P1")
    assert set(p1["opportunity_ids"]) == {"1", "2"}
    assert p1["funded_slots"] == 3
    assert "geoprocessamento" in p1["methods"].lower() or "geoprocessamento" in p1["domain"].lower()


def test_lattes_atoms_group_leaves_of_one_record():
    prefix = ("$['dadosgerais']['atuacoesprofissionais']['atuacaoprofissional'][0]"
              "['atividadesdeparticipacaoemprojeto']['participacaoemprojeto'][0]"
              "['projetodepesquisa'][0]")
    atoms = lattes_atoms([
        ("10", prefix + "['nomedoprojeto']", "Vigilância espacial de arboviroses"),
        ("10", prefix + "['descricaodoprojeto']", "Projeto de análise espacial de casos notificados."),
        ("10", prefix + "['anoinicio']", "2021"),
        ("10", prefix + "['anofim']", "2024"),
    ])
    assert len(atoms) == 1
    atom = atoms[0]
    assert atom.kind == "lattes_project"
    assert atom.year == 2021 and atom.year_end == 2024
    assert "arboviroses" in atom.title


def test_generic_knowledge_areas_are_low_specificity():
    assert SPECIFICITY["knowledge_area"] < SPECIFICITY["opportunity"] / 5
    assert SPECIFICITY["opportunity"] >= SPECIFICITY["lattes_project"]


def test_public_sigaa_rows_parse_only_project_coded_lines():
    atoms = public_sigaa_atoms([
        ("10", "pesquisa", '["Projeto de Pesquisa","\\u00c1rea de Conhecimento"]'),
        ("10", "pesquisa", '["2026"]'),
        ("10", "pesquisa", '["PVCB5359-2026","Vigil\\u00e2ncia de arboviroses","Epidemiologia"]'),
    ])
    assert len(atoms) == 1
    assert atoms[0].kind == "sigaa_project" and atoms[0].year == 2026


def test_fingerprint_changes_when_professor_evidence_changes(db):
    before = load_corpus(db).fingerprint()
    with db.connect() as con:
        con.execute("UPDATE professors SET profile_summary=? WHERE siape='10'",
                    ["Agora trabalho com séries temporais e modelagem preditiva em saúde pública."])
    after = load_corpus(db).fingerprint()
    assert before != after, "professor/Lattes changes must invalidate the semantic space"


def test_training_corpus_is_larger_than_the_opportunity_table(db):
    corpus = load_corpus(db)
    assert len(corpus.training_documents()) >= len(corpus.opportunities)
    assert any(a.kind == "opportunity" for a in corpus.atoms)
    assert any(a.kind == "profile_summary" for a in corpus.atoms)


def test_currency_is_temporal_not_merely_kind_based():
    """A 2014 SIGAA project is not evidence of supervision capacity today."""
    from datetime import datetime, timezone

    from pulsar_research.semantics.corpus import Atom

    year = datetime.now(timezone.utc).year

    def atom(**kw):
        return Atom(atom_id="a", siape="1", kind=kw.pop("kind", "sigaa_project"),
                    title="t", **kw)

    assert atom(kind="opportunity", year=2001).is_current, "an open call is current by definition"
    assert atom(year=year).is_current
    assert not atom(year=2014).is_current
    assert not atom(year=2005, is_active=True).is_current, "an unmaintained Lattes flag is not currency"
    assert atom(kind="research_line", is_active=True).is_current, "no dates: trust the active flag"
    assert not atom(kind="research_line", is_active=False).is_current
    assert not atom(year=year, year_end=year - 1).is_current, "an ended record is not current"
    assert not atom(kind="article", year=year).is_current, "a publication is trajectory, not capacity"


def test_trajectory_excludes_the_opportunities_it_is_matched_against():
    """Scoring a career on the open call it is being compared to is circular."""
    import numpy as np

    from pulsar_research.semantics.corpus import Atom
    from pulsar_research.semantics.evidence import score_portfolios

    atoms = [
        Atom(atom_id="opp", siape="1", kind="opportunity", title="epidemiologia espacial da dengue"),
        Atom(atom_id="art", siape="1", kind="article", title="cultura celular tumoral", year=2019),
    ]
    result = score_portfolios(atoms, ["1"], {"fused": np.array([1.0, 0.1])})
    kinds = {(e.scope, e.atom.kind) for e in result.evidence}
    assert ("current", "opportunity") in kinds
    assert ("trajectory", "opportunity") not in kinds
    assert result.scores["current"]["fused"][0] > result.scores["trajectory"]["fused"][0]
