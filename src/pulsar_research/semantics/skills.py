"""Deterministic multi-label skill extraction.

The v2 skills NMF failed for a structural reason, not a tuning reason: 74 of 89
projects collapsed onto one factor of "leitura crítica / comunicação / escrita
científica". Every work plan promises those, so they carry no discriminative
information, and a *soft-partition* model (which forces topics to compete for
each document's mass) is the wrong shape for capabilities anyway.

Skills behave like a **set**: a project can involve Python *and* PCR *and*
systematic review, with no competition between them. So PULSAR extracts them as
independent labels from a curated gazetteer, with the matched span kept as
evidence. It is boring, inspectable, extensible and — unlike the NMF — correct.

`discover_candidate_skills` closes the loop: it surfaces salient technical terms
the gazetteer does not yet cover, so the taxonomy grows from the real corpus
instead of from imagination.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .normalize import PORTUGUESE_STOP_WORDS, fold


@dataclass(frozen=True, slots=True)
class Skill:
    skill_id: str
    label: str
    category: str
    patterns: tuple[str, ...]
    #: Patterns matched against the ORIGINAL text, before accent folding and
    #: lowercasing. Needed for single-letter identifiers like the R language,
    #: whose only reliable signal is that it is capitalized and standalone.
    raw_patterns: tuple[str, ...] = ()
    #: Generic skills are recorded but never used as a discriminative signal:
    #: "scientific writing" is true of every project and explains nothing.
    generic: bool = False


def _s(skill_id: str, label: str, category: str, *patterns: str,
       raw: tuple[str, ...] = (), generic: bool = False) -> Skill:
    return Skill(skill_id, label, category, tuple(patterns), tuple(raw), generic)


SKILL_TAXONOMY: tuple[Skill, ...] = (
    # -- programming and computation ----------------------------------------
    _s("python", "Python", "computing", r"\bpython\b", r"\bpandas\b", r"\bnumpy\b", r"\bscikit-?learn\b", r"\bjupyter\b"),
    _s("r_lang", "R", "computing", r"\brstudio\b", r"\br studio\b", r"\bggplot2?\b",
       r"\btidyverse\b", r"\bcran\b", r"\brmarkdown\b",
       # A lone capital R in Portuguese academic prose is the language essentially
       # always; `_R_EXCLUSIONS` covers the handful of cases where it is not.
       raw=(r"(?<![\wÀ-ÿ])R(?![\wÀ-ÿ$])",)),
    _s("sql", "SQL / bancos relacionais", "computing", r"\bsql\b", r"\bpostgres\w*\b", r"\bmysql\b", r"\bduckdb\b", r"\bbanco de dados relacional\b"),
    _s("matlab", "MATLAB", "computing", r"\bmatlab\b", r"\boctave\b"),
    _s("spss", "SPSS / Stata / SAS", "computing", r"\bspss\b", r"\bstata\b", r"\bsas\b", r"\bjamovi\b", r"\bjasp\b"),
    _s("machine_learning", "Aprendizado de máquina", "computing", r"\bmachine learning\b", r"\baprendizado de m[aá]quina\b",
       r"\bredes? neur\w+\b", r"\bdeep learning\b", r"\binteligencia artificial\b", r"\bmodelos? preditivos?\b", r"\brandom forest\b"),
    _s("web_dev", "Desenvolvimento web / dashboards", "computing", r"\bdashboards?\b", r"\bjavascript\b", r"\breact\b",
       r"\bstreamlit\b", r"\bshiny\b", r"\bapi rest\b", r"\bplataforma (?:web|interativa)\b"),
    _s("image_processing", "Processamento de imagens", "computing", r"\bimagej\b", r"\bfiji\b", r"\bprocessamento de imagens?\b",
       r"\bsegmenta[cç][aã]o de imagens?\b", r"\bmorfometria digital\b"),
    _s("geoprocessing", "Geoprocessamento / SIG", "computing", r"\bgeoprocessamento\b", r"\bqgis\b", r"\barcgis\b",
       r"\bgeorreferenc\w+\b", r"\bsistemas? de informa[cç][aã]o geogr[aá]fic\w+\b", r"\bsig\b", r"\bgeocodifica[cç][aã]o\b"),

    # -- statistics and study design ----------------------------------------
    _s("biostatistics", "Bioestatística", "statistics", r"\bbioestat[ií]stic\w+\b", r"\ban[aá]lise estat[ií]stic\w+\b",
       r"\btestes? estat[ií]stic\w+\b", r"\bqui-?quadrado\b", r"\banova\b", r"\bteste t de student\b", r"\bp-?valor\b"),
    _s("regression", "Modelagem de regressão", "statistics", r"\bregress[aã]o (?:log[ií]stica|linear|de poisson|de cox|m[uú]ltipla)\b",
       r"\bmodelos? (?:lineares? generalizados?|mistos?|multin[ií]vel)\b", r"\brazao de chances\b", r"\bodds ratio\b"),
    _s("spatial_stats", "Estatística espacial", "statistics", r"\ban[aá]lise espacial\b", r"\bautocorrela[cç][aã]o espacial\b",
       r"\bmoran\b", r"\bkernel density\b", r"\bvarredura espacial\b", r"\bsatscan\b", r"\bcluster\w* espaci\w+\b"),
    _s("time_series", "Séries temporais", "statistics", r"\bs[eé]ries? temporais\b", r"\barima\b", r"\bprais-?winsten\b",
       r"\btend[eê]ncia temporal\b", r"\bjoinpoint\b", r"\ban[aá]lise temporal\b"),
    _s("survival", "Análise de sobrevida", "statistics", r"\bsobrevid\w+\b", r"\bkaplan-?meier\b", r"\bhazard ratio\b", r"\bcox\b"),
    _s("epi_design", "Desenho epidemiológico", "statistics", r"\bcoorte\b", r"\bcaso-?controle\b", r"\btransversal\b",
       r"\becol[oó]gic\w+ (?:estudo|desenho)\b", r"\bestudo (?:ecol[oó]gico|observacional)\b", r"\bincid[eê]ncia\b", r"\bpreval[eê]ncia\b"),
    _s("clinical_trial", "Ensaio clínico", "statistics", r"\bensaios? cl[ií]nicos?\b", r"\brandomiz\w+\b", r"\bplacebo\b",
       r"\bcego\b", r"\bduplo-?cego\b"),
    _s("systematic_review", "Revisão sistemática", "statistics", r"\brevis[aã]o sistem[aá]tica\b", r"\bprisma\b",
       r"\bscoping review\b", r"\brevis[aã]o de escopo\b", r"\bprospero\b", r"\brayyan\b"),
    _s("meta_analysis", "Metanálise", "statistics", r"\bmetan[aá]lise\b", r"\bmeta-?an[aá]lise\b", r"\brevman\b",
       r"\bheterogeneidade\s+i2\b", r"\bforest plot\b"),
    _s("psychometrics", "Psicometria / validação de instrumentos", "statistics", r"\bpsicometr\w+\b",
       r"\bvalida[cç][aã]o de (?:instrumento|question[aá]rio|escala)\b", r"\balfa de cronbach\b", r"\ban[aá]lise fatorial\b"),
    _s("qualitative", "Métodos qualitativos", "statistics", r"\ban[aá]lise de conte[uú]do\b", r"\bbardin\b",
       r"\bgrupo focal\b", r"\bentrevistas? semiestruturad\w+\b", r"\bpesquisa qualitativa\b", r"\biramuteq\b", r"\batlas\.ti\b"),

    # -- health data sources -------------------------------------------------
    _s("datasus", "DATASUS / sistemas de informação em saúde", "data_source", r"\bdatasus\b", r"\btabnet\b",
       r"\bsistemas? de informa[cç][aã]o em sa[uú]de\b", r"\bmicrodados do sus\b"),
    _s("sinan", "SINAN", "data_source", r"\bsinan\b", r"\bnotifica[cç][aã]o compuls[oó]ria\b"),
    _s("sim_sinasc", "SIM / SINASC", "data_source", r"\bsim/sus\b", r"\bsistema de informa[cç][aã]o sobre mortalidade\b",
       r"\bsinasc\b", r"\bdeclara[cç][aã]o de [oó]bito\b"),
    _s("sih_sia", "SIH/SUS e SIA/SUS", "data_source", r"\bsih/?sus\b", r"\bsia/?sus\b",
       r"\bautoriza[cç][aã]o de interna[cç][aã]o hospitalar\b", r"\binterna[cç][oõ]es hospitalares?\b"),
    _s("esus_siab", "e-SUS / Atenção Básica", "data_source", r"\be-?sus\b", r"\bsiab\b", r"\bsisab\b", r"\baten[cç][aã]o b[aá]sica\b"),
    _s("ibge", "IBGE / dados sociodemográficos", "data_source", r"\bibge\b", r"\bcenso demogr[aá]fico\b", r"\bsidra\b", r"\bpnad\b"),
    _s("redcap", "REDCap / captura eletrônica de dados", "data_source", r"\bredcap\b", r"\bkobotoolbox\b",
       r"\bformul[aá]rios? eletr[oô]nicos?\b", r"\bgoogle forms\b"),
    _s("prontuario", "Prontuários e registros clínicos", "data_source", r"\bprontu[aá]rios?\b", r"\bregistros? m[eé]dicos?\b",
       r"\blaudos?\b", r"\bficha de notifica[cç][aã]o\b"),
    _s("literature_db", "Bases bibliográficas", "data_source", r"\bpubmed\b", r"\bmedline\b", r"\bscopus\b",
       r"\bweb of science\b", r"\bscielo\b", r"\blilacs\b", r"\bembase\b", r"\bcochrane\b"),

    # -- wet lab -------------------------------------------------------------
    _s("pcr", "PCR / qPCR / RT-PCR", "wet_lab", r"\bpcr\b", r"\bqpcr\b", r"\brt-?pcr\b", r"\breal[- ]time pcr\b", r"\beletroforese\b"),
    _s("sequencing", "Sequenciamento e bioinformática", "wet_lab", r"\bsequenciamento\b", r"\bngs\b", r"\bsanger\b",
       r"\bbioinform[aá]tic\w+\b", r"\bgen[oô]mic\w+\b", r"\btranscriptom\w+\b", r"\bblast\b"),
    _s("cell_culture", "Cultura celular", "wet_lab", r"\bcultura(?:s)? celular(?:es)?\b", r"\blinhagens? celulares?\b",
       r"\bcultivo celular\b", r"\bmtt\b", r"\bviabilidade celular\b"),
    _s("flow_cytometry", "Citometria de fluxo", "wet_lab", r"\bcitometria de fluxo\b", r"\bcit[oô]metro\b", r"\bfacs\b"),
    _s("microscopy", "Microscopia", "wet_lab", r"\bmicroscopia\b", r"\bmicrosc[oó]pio\b", r"\bhistol[oó]gic\w+\b",
       r"\bhematoxilina\b", r"\bimuno-?histoqu[ií]mic\w+\b", r"\blaminas? histol[oó]gicas?\b"),
    _s("immunoassay", "Imunoensaios", "wet_lab", r"\belisa\b", r"\bwestern blot\b", r"\bimunofluoresc[eê]ncia\b",
       r"\bprnt\b", r"\bsorologi\w+\b"),
    _s("microbiology", "Microbiologia", "wet_lab", r"\bmicrobiolog\w+\b", r"\bantibiograma\b", r"\bcepas?\b",
       r"\bmeio de cultura\b", r"\b[aá]gar\b", r"\bcim\b", r"\bconcentra[cç][aã]o inibit[oó]ria m[ií]nima\b"),
    _s("parasitology", "Parasitologia", "wet_lab", r"\bparasitol[oó]g\w+\b", r"\bkato-?katz\b", r"\bhelmint\w+\b",
       r"\bmolusc\w+\b", r"\bcercari\w+\b", r"\bovos de\b"),
    _s("animal_model", "Modelos animais / experimentação", "wet_lab", r"\bcamundongos?\b", r"\bratos? wistar\b",
       r"\bmodelo animal\b", r"\bbiot[eé]rio\b", r"\bgalleria mellonella\b", r"\bmellonella\b", r"\bzebrafish\b", r"\bdanio rerio\b"),
    _s("phytochemistry", "Fitoquímica / produtos naturais", "wet_lab", r"\bfitoqu[ií]mic\w+\b", r"\bextratos? (?:vegetais?|brutos?|etan[oó]lic\w+)\b",
       r"\bcromatografia\b", r"\bhplc\b", r"\b[oó]leos? essenciais?\b", r"\bmetab[oó]litos secund[aá]rios\b"),
    _s("molecular_docking", "Modelagem molecular / docking", "wet_lab", r"\bdocking\b", r"\bancoragem molecular\b",
       r"\bdin[aâ]mica molecular\b", r"\bin silico\b"),

    # -- clinical / field ----------------------------------------------------
    _s("clinical_assessment", "Avaliação clínica de pacientes", "clinical", r"\bexame f[ií]sico\b", r"\banamnese\b",
       r"\bavalia[cç][aã]o cl[ií]nica\b", r"\batendimento ambulatorial\b", r"\bacompanhamento de pacientes?\b"),
    _s("imaging", "Imagem diagnóstica", "clinical", r"\bressonancia magn[eé]tica\b", r"\btomografia\b",
       r"\bultrassonografi\w+\b", r"\bradiografi\w+\b", r"\becocardiograf\w+\b", r"\bimaginol[oó]g\w+\b"),
    _s("physiological_measure", "Medidas fisiológicas", "clinical", r"\beletrocardiograma\b", r"\bpress[aã]o arterial\b",
       r"\bespirometria\b", r"\bacelerometr\w+\b", r"\bantropometri\w+\b", r"\bbioimped[aâ]ncia\b"),
    _s("fieldwork", "Trabalho de campo / coleta", "clinical", r"\btrabalho de campo\b", r"\bcoleta em campo\b",
       r"\bvisitas? domiciliar\w*\b", r"\bbusca ativa\b", r"\bexpedi[cç][oõ]es?\b"),
    _s("ethics", "Ética em pesquisa", "clinical", r"\bcomit[eê] de [eé]tica\b", r"\bcep\b", r"\btcle\b",
       r"\btermo de consentimento\b", r"\bplataforma brasil\b", r"\bceua\b"),
    _s("education_research", "Pesquisa em ensino", "clinical", r"\bsequ[eê]ncia did[aá]tica\b", r"\blivros? did[aá]ticos?\b",
       r"\bmetodologias? ativas?\b", r"\bcurr[ií]culo escolar\b", r"\bbncc\b", r"\bpr[aá]tica pedag[oó]gica\b"),

    # -- generic scholarly competencies (recorded, never discriminative) -----
    _s("scientific_writing", "Redação científica", "scholarly", r"\bredac[aã]o cient[ií]fic\w+\b",
       r"\belabora[cç][aã]o de (?:artigos?|manuscritos?|resumos?)\b", r"\bescrita cient[ií]fica\b", generic=True),
    _s("critical_reading", "Leitura crítica de literatura", "scholarly", r"\bleitura cr[ií]tica\b",
       r"\bfichamento\b", r"\blevantamento bibliogr[aá]fic\w+\b", generic=True),
    _s("presentation", "Apresentação e comunicação científica", "scholarly", r"\bapresenta[cç][aã]o (?:oral|em congressos?|de trabalhos?)\b",
       r"\bcongressos? cient[ií]ficos?\b", r"\bsemin[aá]rios?\b", generic=True),
    _s("data_management", "Organização e gestão de dados", "scholarly", r"\bbanco de dados\b", r"\btabula[cç][aã]o\b",
       r"\bplanilh\w+\b", r"\bexcel\b", r"\bcura[cç][aã]o de dados\b", r"\blimpeza de dados\b"),
    _s("reference_manager", "Gerenciadores de referências", "scholarly", r"\bmendeley\b", r"\bzotero\b", r"\bendnote\b"),
)

SKILLS_BY_ID: dict[str, Skill] = {s.skill_id: s for s in SKILL_TAXONOMY}

_COMPILED: tuple[tuple[Skill, re.Pattern[str]], ...] = tuple(
    (skill, re.compile("|".join(f"(?:{p})" for p in skill.patterns)))
    for skill in SKILL_TAXONOMY
)

_COMPILED_RAW: dict[str, re.Pattern[str]] = {
    skill.skill_id: re.compile("|".join(f"(?:{p})" for p in skill.raw_patterns))
    for skill in SKILL_TAXONOMY if skill.raw_patterns
}

#: Contexts where a capital R is not the language. Cheap, explicit, extensible.
_R_EXCLUSIONS = re.compile(r"(?:vitamina|anexo|classe|receptor|resolu[cç][aã]o|R\$)\s*R?", re.I)


@dataclass(frozen=True, slots=True)
class SkillHit:
    skill_id: str
    label: str
    category: str
    generic: bool
    count: int
    evidence: str


def extract_skills(text: str, *, context: int = 60) -> list[SkillHit]:
    """Label a text with every skill it mentions, keeping one evidence span each."""
    raw = str(text or "")
    folded = fold(raw)
    hits: list[SkillHit] = []
    for skill, pattern in _COMPILED:
        matches = list(pattern.finditer(folded))
        raw_pattern = _COMPILED_RAW.get(skill.skill_id)
        if raw_pattern is not None:
            for m in raw_pattern.finditer(raw):
                window = raw[max(0, m.start() - 12): m.end() + 2]
                if not _R_EXCLUSIONS.search(window):
                    matches.append(m)
        if not matches:
            continue
        m = matches[0]
        lo = max(0, m.start() - context // 2)
        hi = min(len(folded), m.end() + context)
        hits.append(SkillHit(skill.skill_id, skill.label, skill.category, skill.generic,
                             len(matches), folded[lo:hi].strip()))
    return sorted(hits, key=lambda h: (h.generic, -h.count, h.skill_id))


def skill_vector(text: str) -> dict[str, int]:
    return {h.skill_id: h.count for h in extract_skills(text)}


def skill_profile(texts: Iterable[str]) -> dict[str, int]:
    """Union of skills over a portfolio, summing occurrence counts."""
    total: Counter[str] = Counter()
    for text in texts:
        total.update(skill_vector(text))
    return dict(total)


def skill_match(candidate: Mapping[str, int], operator: Sequence[str]) -> tuple[float, list[str]]:
    """Jaccard-style overlap between a record's skills and the operator's skills.

    Generic skills are excluded: "you will read papers" is not a reason to prefer
    one project over another.
    """
    wanted = {s for s in operator if s in SKILLS_BY_ID and not SKILLS_BY_ID[s].generic}
    have = {s for s in candidate if s in SKILLS_BY_ID and not SKILLS_BY_ID[s].generic}
    if not wanted:
        return 0.0, []
    shared = sorted(wanted & have)
    return len(shared) / len(wanted), shared


# ---------------------------------------------------------------------------
# Taxonomy growth
# ---------------------------------------------------------------------------

_CANDIDATE_TOKEN = re.compile(r"[a-z][a-z0-9+./-]{3,}")


def discover_candidate_skills(texts: Sequence[str], *, top_n: int = 60, min_docs: int = 3,
                              max_doc_fraction: float = 0.25) -> list[tuple[str, int]]:
    """Salient technical terms the gazetteer does not cover yet.

    A term qualifies if it appears in several documents but not most of them
    (so it discriminates), and matches no existing skill pattern. This is the
    maintenance loop for the taxonomy: run it after each corpus sync and fold the
    real hits into ``SKILL_TAXONOMY``.
    """
    n = max(len(texts), 1)
    df: Counter[str] = Counter()
    for text in texts:
        folded = fold(text)
        seen = {t.rstrip("._-/") for t in _CANDIDATE_TOKEN.findall(folded)}
        df.update(t for t in seen if len(t) > 3 and t not in PORTUGUESE_STOP_WORDS)
    covered = set()
    for text in texts:
        for h in extract_skills(text):
            covered.update(_CANDIDATE_TOKEN.findall(fold(h.label)))
    out = [
        (term, count)
        for term, count in df.most_common()
        if min_docs <= count <= max_doc_fraction * n and term not in covered
        and not any(pattern.search(term) for _, pattern in _COMPILED)
    ]
    return out[:top_n]
