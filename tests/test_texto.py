"""The message is read by strangers who owe it nothing, so its surface is pinned.

Every case here was a defect found by rendering all 32 drafts and reading them.
None was caught by the tests that existed at the time, because each lived in
data that only some recipients carry: a title SIGAA stored in capitals, a skill
label that is a single letter, a highlight promised to the 23 recipients whose
annexes carry no highlight.
"""
from __future__ import annotations

from pulsar_research.apps.outreach.caixa import desgritar, esta_gritando
from pulsar_research.graph.identity import fold as dobrar
from pulsar_research.apps.outreach.render import _limpo, _minuscula, _resumo, _siglas

from test_render import body, render


# --------------------------------------------------------------------------
# The filters, at the cases that actually occur in the corpus.
# --------------------------------------------------------------------------

def test_scraped_acronyms_are_not_left_title_cased():
    """SIGAA stores 'Edital 03 Pibiti Ufal 2026-2027'; that is the first line."""
    assert _siglas("Edital 03 Pibiti Ufal 2026-2027") == "Edital 03 PIBITI UFAL 2026-2027"
    assert _siglas("dados do Sus e do Ibge") == "dados do SUS e do IBGE"
    assert _siglas("Cnpq") == "CNPq"


def test_ordinary_words_that_contain_an_acronym_are_left_alone():
    for word in ("Publicado", "Sustentável", "Música", "Discussão"):
        assert _siglas(word) == word


def test_a_hard_line_break_inside_a_scraped_title_is_collapsed():
    """Ten of the 187 titles carry one, and it rendered mid-paragraph."""
    assert _limpo("Poluição por Plástico:\nEstabelecendo Linhas") == \
        "Poluição por Plástico: Estabelecendo Linhas"


def test_a_title_ending_in_a_period_does_not_produce_a_double_stop():
    """The title is quoted mid-sentence: '...PeNSE 2024.”.' read as a typo."""
    assert not _limpo("Pesquisa nacional de saúde do escolar - PeNSE 2024.").endswith(".")


def test_a_skill_label_keeps_its_case_when_lowering_it_would_destroy_it():
    """A blind |lower turned the R language into 'o seu plano pede r'."""
    assert _minuscula("R") == "R"
    assert _minuscula("SUS") == "SUS"
    assert _minuscula("Bioestatística") == "bioestatística"
    assert _minuscula("Análise de dados do SUS") == "análise de dados do SUS"


def test_the_subject_is_bounded_whatever_the_plan_title_does():
    long_title = "Avaliação da saúde mental, saúde sexual e autopercepção corporal " \
                 "em escolares brasileiros para a identificação de fatores"
    out = _resumo(long_title)
    assert len(out) <= 96 and out.endswith("…")
    assert not out.rstrip("…").endswith(" "), "must cut on a word boundary"
    assert _resumo("Plano curto") == "Plano curto", "a short title is untouched"


# --------------------------------------------------------------------------
# De-shouting, which must not rename the molecules the recipient studies.
# --------------------------------------------------------------------------

SHOUTED = ("ESTUDO DOS MICRORNAS miR-31-5p, miR-10b-5p E miR-451a "
           "NA EPILEPTOGÊNESE EM MODELO EXPERIMENTAL DE EPILEPSIA")


def test_a_shouted_title_is_lowered_without_touching_its_identifiers():
    out = desgritar(SHOUTED)
    for identifier in ("miR-31-5p", "miR-10b-5p", "miR-451a"):
        assert identifier in out, "the casing of a miRNA name is the name"
    assert "epileptogênese" in out and "EPILEPTOGÊNESE" not in out
    assert " e " in out and " na " in out and " em " in out, "function words too"
    assert out[0].isupper()


def test_a_normally_cased_title_is_never_touched():
    for title in ("Ecologia trófica de espécies pelágicas no Arquipélago de São Pedro",
                  "Análise de dados do SUS com R e Python"):
        assert not esta_gritando(title)
        assert desgritar(title) == title


def test_a_known_acronym_survives_de_shouting():
    out = desgritar("PRODUÇÃO CIENTÍFICA E DISSEMINAÇÃO DOS RESULTADOS DO SUS",
                    siglas=frozenset({"SUS"}))
    assert out.endswith("do SUS"), "a known sigla is not lowered"
    assert "científica e disseminação dos resultados" in out


# --------------------------------------------------------------------------
# Name recovery: the salutation spells the recipient's own name.
# --------------------------------------------------------------------------

def test_folding_ignores_accents_case_and_punctuation_but_not_missing_names():
    assert dobrar("Diego Figueiredo Nóbrega") == dobrar("diego figueiredo nobrega")
    assert dobrar("Abelardo Silva-Júnior") == dobrar("abelardo silva junior")
    assert dobrar("Auxiliadora Damianne Pereira Vieira da Costa") != \
        dobrar("auxiliadora damianne pereira vieira da costa e silva"), \
        "a dropped surname is a different person, not an accent difference"


def test_the_salutation_prefers_the_recovered_spelling_over_the_matching_key():
    from pulsar_research.apps.outreach.render import build_context
    from test_render import PROFILE, SIGNATURE, recipient

    plain = recipient(98.0)
    assert build_context(plain, SIGNATURE, PROFILE)["professor_name"] == \
        "Maria das Gracas Taveira"
    accented = {**plain, "professor_display_name": "Maria das Graças Taveira"}
    assert build_context(accented, SIGNATURE, PROFILE)["professor_name"] == \
        "Maria das Graças Taveira"


# --------------------------------------------------------------------------
# What the prose may and may not do.
# --------------------------------------------------------------------------

def test_the_corpus_size_is_no_longer_recited_at_the_recipient():
    """It said "os 187 planos deste edital", which was both false (187 spans
    PIBIC, PIBIC-AF and PIBITI) and a statistic the recipient did not ask for."""
    text = body(98.0)
    assert "deste edital" not in text
    assert "187" not in text
    assert "sistema de prospecção que desenvolvi" in text


def test_the_annexes_are_offered_rather_than_disclaimed_or_catalogued():
    """They were a five-line annotated list introduced as "rascunhos avançados,
    não publicações" — downgraded in the same sentence that offered them,
    and long enough to read as credential saturation."""
    text = body(98.0)
    assert "Anexei três trabalhos meus" in text
    for disclaimer in ("não publicações", "rascunhos"):
        assert disclaimer not in text
    # One sentence, not a bulleted catalogue: the only list is the offer.
    assert text.count(chr(10) + "- ") <= 3


def test_the_closest_annex_is_named_only_when_one_actually_matches():
    marked = body(98.0, skills=("time_series", "sinan"))
    assert "o mais próximo do seu plano é o de leptospirose no Brasil" in marked
    unmarked = body(98.0, skills=("nonexistent_skill",))
    assert "o mais próximo do seu plano" not in unmarked
    assert "para sua apreciação" in unmarked


def test_the_message_never_warns_the_recipient_that_it_may_be_nonsense():
    """"se algo soar deslocado, a limitação é dele" asked the reader to discount
    the personalised content in advance. PULSAR is supposed to support the
    message's credibility, not ship with a warning label."""
    text = body(98.0)
    for hedge in ("soar deslocado", "limitação é dele", "Nem sempre acertam"):
        assert hedge not in text


def test_the_offer_is_capability_not_self_assignment():
    """"eu poderia assumir" put the student in charge of the supervisor's
    methodology before a first conversation."""
    text = body(98.0)
    assert "eu poderia contribuir mais diretamente" in text
    assert "eu poderia assumir" not in text


def test_authorship_of_the_software_is_stated_not_defended():
    """"Escrevo eu mesmo todo o código por trás disso" read as though the
    recipient were expected to suspect the projects were not his."""
    text = body(98.0)
    assert "Desenvolvi também o CMapDoc" in text
    assert "eu mesmo" not in text


def test_the_reply_route_is_unambiguous():
    """'o e-mail institucional abaixo' named neither of the two shown."""
    text = body(98.0)
    assert "e-mail institucional abaixo" not in text
    assert "levi.amorim@famed.ufal.br" in text


def test_the_offer_reaches_the_reader_as_one_movement():
    """The competence claim and the tailored list were two paragraphs, and the
    first floated after a paragraph about scholarship logistics."""
    text = body(98.0)
    paragraph = next(p for p in text.split(2 * chr(10)) if "dois anos" in p)
    assert paragraph.rstrip().endswith("nestas frentes:")


def test_every_draft_names_the_built_software_and_the_annexes():
    for percentile in (7.0, 98.0):
        _, text, markup = render(percentile)
        assert "https://levimelo.github.io/mapdoc/" in text
        assert "https://github.com/LeviMelo" in text
        assert "Anexei" in text and "Anexei" in markup
