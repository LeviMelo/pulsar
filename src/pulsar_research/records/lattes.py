"""Records out of the flattened Lattes object.

`sigaa_public_lattes_flat` holds one row per leaf of each professor's CV, keyed
by a JSONPath-like string: `$['producaobibliografica']['artigospublicados']
['artigopublicado'][3]['autores'][1]['nomecompletodoautor']`. Rebuilding the
records from those paths is a matter of knowing where a record starts — the
`[3]` above — and then folding every leaf under it back into a small nested
object, which is what `_gather` does.

The specs below say where records start and what family they belong to. They
are prefixes with two wildcards: `*` for "any key here" (the level of a degree,
the kind of a committee, the type of an event, all of which CNPq encodes in the
key name) and `[]` for an index. Inside a record, field names vary by family in
ways that are systematic but tedious — `dadosbasicosdoartigo.nomeproducao`,
`dadosbasicosdotrabalho.nomeproducao`, `dadosbasicosdapatente.NOMEPRODUCAO` —
so fields are read by leaf name with `_leaf`, which searches the gathered object
case-insensitively and takes the first hit in document order. That is a
heuristic; it is the right one here because a CV record never carries two
leaves with the same name meaning different things.

Nothing here touches the network or the corpus. It is a pure function from rows
to records, which is what makes it testable on a dozen hand-written paths.
"""

from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from fnmatch import fnmatch
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..semantics.normalize import clean_text
from .model import Person, Record

SOURCE = "lattes.embedded"

_SEGMENT = re.compile(r"\['((?:[^'\\]|\\.)*)'\]|\[(\d+)\]")


def parse_path(path: str) -> list[str | int]:
    """`$['a']['b'][2]['c']` → `['a', 'b', 2, 'c']`."""
    out: list[str | int] = []
    for key, index in _SEGMENT.findall(path):
        if index:
            out.append(int(index))
        else:
            out.append(key.replace("\\'", "'").replace("\\\\", "\\"))
    return out


# ---------------------------------------------------------------------------
# Where records start
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Spec:
    family: str
    prefix: tuple[str, ...]          #: dotted prefix split on "."; "*" is a key wildcard
    #: How to name the form. A string is a constant; "*" takes the wildcard
    #: key; a callable gets (wildcard key, gathered object).
    form: str | Callable[[str, dict[str, Any]], str] = "*"
    #: Whether the last prefix key is followed by an index. CNPq writes most
    #: collections as arrays, and a few as one object when there is one item.
    indexed: bool = True

    def match(self, segments: list[str | int]) -> tuple[str, int, int] | None:
        """(wildcard key, record index, number of segments consumed) or None."""
        wildcard = ""
        i = 0
        for part in self.prefix:
            # Collections between two named levels carry an index the prefix
            # does not spell out: `participacaoemprojeto[0].projetodepesquisa`.
            while i < len(segments) and isinstance(segments[i], int):
                i += 1
            if i >= len(segments):
                return None
            key = str(segments[i])
            if part == "*":
                wildcard = key
            elif "*" in part:
                if not fnmatch(key, part):
                    return None
                wildcard = key
            elif key != part:
                return None
            i += 1
        index = 0
        if i < len(segments) and isinstance(segments[i], int):
            index = segments[i]
            i += 1
        elif self.indexed:
            # An unindexed key where an array was expected is the one-item
            # object case; treat it as index 0 rather than dropping the record.
            pass
        return wildcard, index, i


def _p(expr: str) -> tuple[str, ...]:
    return tuple(expr.split("."))


_ATUACAO = "dadosgerais.atuacoesprofissionais.atuacaoprofissional"
_PARTICIPACAO = f"{_ATUACAO}.atividadesdeparticipacaoemprojeto.participacaoemprojeto"


def _supervision_form(key: str, obj: dict[str, Any]) -> str:
    key = key.replace("orientacaoemandamentode", "").replace("orientacoesconcluidaspara", "")
    if key.startswith("outras"):
        kind = _leaf(obj, "tipodeorientacaoconcluida", "tipodeorientacao", "natureza")
        return _slugish(kind) or "outras"
    return key


def _committee_form(key: str, obj: dict[str, Any]) -> str:
    return (key.replace("participacaoembancade", "")
               .replace("bancajulgadorapara", "")
               .replace("outrasbancasjulgadoras", "outras"))


def _event_form(key: str, obj: dict[str, Any]) -> str:
    return key.replace("participacaoem", "").replace("outrasparticipacoesemeventoscongressos", "outros")


def _training_form(key: str, obj: dict[str, Any]) -> str:
    return key.replace("formacaocomplementar", "").replace("de", "", 1) if key.startswith("formacaocomplementar") else key


def _activity_form(key: str, obj: dict[str, Any]) -> str:
    return {
        "ensino": "teaching",
        "direcaoeadministracao": "administration",
        "conselhocomissaoeconsultoria": "committee_service",
        "extensaouniversitaria": "extension",
        "estagio": "internship",
        "servicotecnicoespecializado": "technical_service",
        "treinamentoministrado": "training_given",
        "pesquisaedesenvolvimento": "research",
    }.get(key, key)


#: Order matters only where prefixes nest: a research line sits inside a
#: research activity, a project inside a project participation, and the more
#: specific prefix must be tried first so the leaf lands in the inner record.
SPECS: tuple[Spec, ...] = (
    # -- production ----------------------------------------------------------
    Spec("work", _p("producaobibliografica.artigospublicados.artigopublicado"), "article"),
    Spec("work", _p("producaobibliografica.trabalhosemeventos.trabalhoemeventos"), "conference"),
    Spec("work", _p("producaobibliografica.livrosecapitulos.capitulosdelivrospublicados.capitulodelivropublicado"), "chapter"),
    Spec("work", _p("producaobibliografica.livrosecapitulos.livrospublicadosouorganizados.livropublicadoouorganizado"), "book"),
    Spec("work", _p("producaobibliografica.textosemjornaisourevistas.textoemjornalourevista"), "newspaper"),
    Spec("work", _p("producaobibliografica.demaistiposdeproducaobibliografica.*"), "other"),
    Spec("work", _p("outraproducao.demaistrabalhos.*"), "other"),
    Spec("work", _p("outraproducao.producaoartisticacultural.*"), "artistic"),
    Spec("technical", _p("producaotecnica.demaistiposdeproducaotecnica.*"), "*"),
    Spec("technical", _p("producaotecnica.*"), "*"),
    # -- projects, lines, appointments and what happened inside them ---------
    Spec("project", _p(f"{_PARTICIPACAO}.projetodepesquisa"), "research"),
    Spec("line", _p(f"{_ATUACAO}.atividadesdepesquisaedesenvolvimento.pesquisaedesenvolvimento.linhadepesquisa"), "line"),
    Spec("activity", _p(f"{_ATUACAO}.atividadesde*.*"), _activity_form),
    Spec("career", _p(_ATUACAO), "appointment"),
    # -- the person -----------------------------------------------------------
    Spec("degree", _p("dadosgerais.formacaoacademicatitulacao.*"), "*"),
    Spec("training", _p("dadoscomplementares.formacaocomplementar.*"), _training_form),
    Spec("award", _p("dadosgerais.premiostitulos.premiotitulo"), "award"),
    Spec("language", _p("dadosgerais.idiomas.idioma"), "language"),
    Spec("area", _p("dadosgerais.areasdeatuacao.areadeatuacao"), "area"),
    # -- the social record ----------------------------------------------------
    Spec("committee", _p("dadoscomplementares.participacaoembancatrabalhosconclusao.*"), _committee_form),
    Spec("committee", _p("dadoscomplementares.participacaoembancajulgadora.*"), _committee_form),
    Spec("supervision", _p("dadoscomplementares.orientacoesemandamento.*"), _supervision_form),
    Spec("supervision", _p("outraproducao.orientacoesconcluidas.*"), _supervision_form),
    Spec("event", _p("dadoscomplementares.participacaoemeventoscongressos.*"), _event_form),
)

#: Specs indexed by their first two literal keys, so each of a million rows is
#: checked against a handful of candidates rather than all of them.
_INDEX: dict[tuple[str, str], list[Spec]] = {}
for _spec in SPECS:
    _INDEX.setdefault((_spec.prefix[0], _spec.prefix[1]), []).append(_spec)


def _find_spec(segments: list[str | int]) -> tuple[Spec, str, int, int] | None:
    if len(segments) < 2 or isinstance(segments[0], int) or isinstance(segments[1], int):
        return None
    head = str(segments[0])
    candidates = _INDEX.get((head, str(segments[1])), []) + _INDEX.get((head, "*"), [])
    for spec in candidates:
        hit = spec.match(segments)
        if hit is not None:
            return (spec, *hit)
    return None


# ---------------------------------------------------------------------------
# Gathering leaves back into objects
# ---------------------------------------------------------------------------


def _set(obj: dict[str, Any], rest: list[str | int], value: str) -> None:
    """Place a leaf at a nested path; integer segments become lists."""
    node: Any = obj
    for i, seg in enumerate(rest):
        last = i == len(rest) - 1
        if isinstance(seg, int):
            if not isinstance(node, list):
                return
            while len(node) <= seg:
                node.append({} if not last else "")
            if last:
                node[seg] = value
            else:
                if not isinstance(node[seg], (dict, list)):
                    node[seg] = {}
                node = node[seg]
        else:
            if not isinstance(node, dict):
                return
            if last:
                node[seg] = value
            else:
                nxt = rest[i + 1]
                if seg not in node or not isinstance(node[seg], (dict, list)):
                    node[seg] = [] if isinstance(nxt, int) else {}
                node = node[seg]


def _leaf(obj: Any, *names: str) -> str:
    """The first non-empty leaf whose key matches any name, case-insensitively,
    searching dicts breadth-first and never descending into lists (people and
    sub-collections are read on purpose, not by accident)."""
    wanted = [n.lower() for n in names]
    queue: list[Any] = [obj]
    while queue:
        node = queue.pop(0)
        if not isinstance(node, dict):
            continue
        lowered = {k.lower(): v for k, v in node.items()}
        for name in wanted:
            value = lowered.get(name)
            if isinstance(value, str) and value.strip():
                return clean_text(value)
        queue.extend(v for v in node.values() if isinstance(v, dict))
    return ""


def _leaves_matching(obj: Any, pattern: re.Pattern[str]) -> list[str]:
    out: list[str] = []
    queue: list[Any] = [obj]
    while queue:
        node = queue.pop(0)
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str) and pattern.match(key.lower()) and value.strip():
                    out.append(clean_text(value))
                elif isinstance(value, dict):
                    queue.append(value)
    return out


_YEAR = re.compile(r"(?:19|20)\d{2}")


def _year(value: str) -> int | None:
    m = _YEAR.search(value or "")
    return int(m.group(0)) if m else None


def _slugish(value: str) -> str:
    """A form name: ASCII, lower, underscored, accents folded rather than dropped
    so "orientação" and "orientacao" spell the same form."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


#: CNPq spells the same kind two ways depending on whether the supervision is
#: ongoing (a key name) or concluded (a value). One spelling here.
_FORM_ALIASES = {
    "iniciacaocientifica": "iniciacao_cientifica",
    "aperfeicoamentoespecializacao": "aperfeicoamento_especializacao",
    "monografia_de_conclusao_de_curso_aperfeicoamento_e_especializacao": "monografia_especializacao",
    "trabalho_de_conclusao_de_curso_graduacao": "tcc_graduacao",
    "orientacao_de_outra_natureza": "outra_natureza",
    "examequalificacao": "qualificacao",
    "concursopublico": "concurso_publico",
    "professortitular": "professor_titular",
    "livredocencia": "livre_docencia",
    "cursocurtaduracao": "curso_curta_duracao",
    "extensaouniversitaria": "extensao",
}


_KEYWORD = re.compile(r"^palavrachave\d*$")
_AREA_LEVELS = ("nomedaespecialidade", "nomedasubareadoconhecimento",
                "nomedaareadoconhecimento", "nomegrandeareadoconhecimento")

#: Sub-collections that name people, and what they are to the record.
_PEOPLE_KEYS = {
    "autores": "author",
    "integrantesdoprojeto": "team",
    "participantebanca": "member",
    "participantedeeventoscongressos": "participant",
}
_PERSON_NAME = ("nomecompletodoautor", "nomecompleto", "nomecompletodoparticipantedabanca",
                "nomecompletodoparticipantedeeventoscongressos")
_PERSON_ORDER = ("ordemdeautoria", "ordemdeintegracao", "ordemparticipante")


def _people(obj: Any) -> list[Person]:
    found: list[Person] = []
    queue: list[Any] = [obj]
    while queue:
        node = queue.pop(0)
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            role = _PEOPLE_KEYS.get(key.lower())
            if role:
                items = value if isinstance(value, list) else [value]
                for i, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    name = _leaf(item, *_PERSON_NAME)
                    if not name:
                        continue
                    order = _leaf(item, *_PERSON_ORDER)
                    responsible = _leaf(item, "flagresponsavel").upper() in ("SIM", "TRUE", "S")
                    found.append(Person(
                        name=name, cnpq_id=_leaf(item, "nroidcnpq"),
                        ordinal=int(order) if order.isdigit() else i + 1,
                        role="responsible" if responsible else role))
            elif isinstance(value, dict):
                queue.append(value)
    found.sort(key=lambda p: p.ordinal)
    return found


def _areas(obj: Any) -> list[str]:
    """One string per declared area, most specific level that is filled."""
    out: list[str] = []
    queue: list[Any] = [obj]
    while queue:
        node = queue.pop(0)
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            if key.lower().startswith("areadoconhecimento") and isinstance(value, dict):
                for level in _AREA_LEVELS:
                    text = _leaf(value, level)
                    if text:
                        out.append(text)
                        break
            elif isinstance(value, dict):
                queue.append(value)
    return list(OrderedDict.fromkeys(out))


# ---------------------------------------------------------------------------
# Shaping a gathered object into a record, per family
# ---------------------------------------------------------------------------


def _common(rec: Record, obj: dict[str, Any]) -> Record:
    rec.keywords = list(OrderedDict.fromkeys(_leaves_matching(obj, _KEYWORD)))[:12]
    rec.areas = _areas(obj)
    rec.people = _people(obj) + rec.people     # named sub-collections first, then the counterpart
    rec.doi = rec.doi or _leaf(obj, "doi")
    rec.language = rec.language or _leaf(obj, "idioma")
    return rec


def _shape_work(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "nomeproducao", "titulo", "titulodotrabalho")
    rec.year = _year(_leaf(obj, "anoproducao", "ano"))
    rec.nature = _leaf(obj, "natureza", "tipo")
    rec.venue = _leaf(obj, "titulodoperiodicoourevista", "nomedoevento", "titulodolivro",
                      "titulodojornalourevista", "titulodosanaisouproceedings", "nomedaeditora",
                      "editora")
    rec.org = _leaf(obj, "nomedaeditora", "editora", "instituicaofinanciadora")
    rec.payload = {
        "issn": _leaf(obj, "issn"), "isbn": _leaf(obj, "isbn"),
        "volume": _leaf(obj, "volume"), "pages": _pages(obj),
        "homepage": _leaf(obj, "homepagedotrabalho", "homepage"),
        "country": _leaf(obj, "paisdepublicacao", "pais", "paisdoevento"),
        "city": _leaf(obj, "cidadedoevento", "cidadedaeditora"),
        "relevant": _leaf(obj, "flagrelevancia").upper() == "SIM",
        "outreach": _leaf(obj, "flagdivulgacaocientifica").upper() == "SIM",
    }
    return _common(rec, obj)


def _pages(obj: dict[str, Any]) -> str:
    start, end = _leaf(obj, "paginainicial"), _leaf(obj, "paginafinal")
    if start and end:
        return f"{start}–{end}"
    return start or end or _leaf(obj, "numerodepaginas")


def _shape_technical(rec: Record, obj: dict[str, Any]) -> Record:
    rec = _shape_work(rec, obj)
    rec.org = _leaf(obj, "instituicaofinanciadora", "instituicaodepositoregistro", "nomedotitular",
                    "instituicaopromotoradoevento", "nomedaeditora") or rec.org
    rec.payload.update({
        "purpose": _leaf(obj, "finalidade"),
        "availability": _leaf(obj, "disponibilidade"),
        "platform": _leaf(obj, "plataforma", "ambiente"),
        "registration": _leaf(obj, "codigodoregistrooupatente"),
        "deposit_date": _leaf(obj, "datapedidodedeposito", "datadepositopct"),
        "grant_date": _leaf(obj, "datadeconcessao"),
        "duration_months": _leaf(obj, "duracaoemmeses"),
        "innovation": _leaf(obj, "flagpotencialinovacao").upper() == "SIM",
    })
    return rec


def _shape_project(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "nomedoprojeto")
    rec.year = _year(_leaf(obj, "anoinicio"))
    rec.year_end = _year(_leaf(obj, "anofim"))
    rec.status = {"EM_ANDAMENTO": "ongoing", "CONCLUIDO": "concluded", "DESATIVADO": "inactive"}.get(
        _leaf(obj, "situacao").upper(), _leaf(obj, "situacao").lower())
    rec.nature = _leaf(obj, "natureza")
    rec.payload = {
        "description": _leaf(obj, "descricaodoprojeto"),
        "funders": _leaves_matching(obj, re.compile(r"^nomedainstituicao$|^nomeinstituicao$")),
        "students": {k: _leaf(obj, k) for k in (
            "numerograduacao", "numeromestradoacademico", "numeromestradoprof",
            "numerodoutorado", "numeroespecializacao") if _leaf(obj, k) not in ("", "0")},
        "outputs": len(_leaves_matching(obj, re.compile(r"^tituloproducaoctproj|^titulodaproducao"))),
    }
    return _common(rec, obj)


def _shape_line(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "titulodalinhadepesquisa")
    rec.status = "active" if _leaf(obj, "flaglinhadepesquisaativa").upper() == "SIM" else "inactive"
    rec.payload = {"objectives": _leaf(obj, "objetivoslinhadepesquisa")}
    return _common(rec, obj)


def _shape_career(rec: Record, obj: dict[str, Any]) -> Record:
    rec.org = _leaf(obj, "nomeinstituicao")
    rec.org_code = _leaf(obj, "codigoinstituicao")
    rec.title = rec.org
    ties = obj.get("vinculos")
    ties = ties if isinstance(ties, list) else ([ties] if isinstance(ties, dict) else [])
    spans = []
    for tie in ties:
        if not isinstance(tie, dict):
            continue
        spans.append({
            "kind": _leaf(tie, "tipodevinculo"),
            "role": _leaf(tie, "enquadramentofuncional", "outroenquadramentofuncionalinformado",
                          "outrovinculoinformado"),
            "from": _year(_leaf(tie, "anoinicio")),
            "to": _year(_leaf(tie, "anofim")),
            "hours": _leaf(tie, "cargahorariasemanal"),
            "exclusive": _leaf(tie, "flagdedicacaoexclusiva").upper() == "SIM",
            "employment": _leaf(tie, "flagvinculoempregaticio").upper() == "SIM",
            "notes": _leaf(tie, "outrasinformacoes"),
        })
    spans.sort(key=lambda s: (s["from"] or 0, s["to"] or 9999))
    starts = [s["from"] for s in spans if s["from"]]
    ends = [s["to"] for s in spans if s["to"]]
    rec.year = min(starts) if starts else None
    open_ended = any(s["from"] and not s["to"] for s in spans)
    rec.year_end = None if open_ended else (max(ends) if ends else None)
    rec.status = "current" if open_ended else ("past" if ends else "")
    current = [s for s in spans if s["from"] and not s["to"]] or spans
    rec.form = _slugish(current[-1]["kind"]) if current and current[-1]["kind"] else "appointment"
    rec.nature = current[-1]["role"] if current else ""
    rec.payload = {"ties": spans}
    return rec


def _shape_activity(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "cargooufuncao", "especificacao", "atividadedeextensaorealizada",
                      "estagiorealizado", "servicorealizado", "nomecurso", "linhadepesquisa")
    courses = obj.get("disciplina")
    if isinstance(courses, list):
        names = [_leaf(c, "value") if isinstance(c, dict) else str(c) for c in courses]
        names = [n for n in names if n]
        if names:
            rec.payload["courses"] = names
            rec.title = rec.title or "; ".join(names[:4])
    trainings = obj.get("treinamento")
    if isinstance(trainings, list):
        rec.payload["trainings"] = [_leaf(t, "value") if isinstance(t, dict) else str(t) for t in trainings]
        rec.title = rec.title or "; ".join(rec.payload["trainings"][:3])
    rec.year = _year(_leaf(obj, "anoinicio"))
    rec.year_end = _year(_leaf(obj, "anofim"))
    rec.status = "current" if rec.year and not rec.year_end else ""
    rec.org = _leaf(obj, "nomeorgao")
    rec.payload["unit"] = _leaf(obj, "nomeunidade")
    rec.payload["kind"] = _leaf(obj, "tipoensino")
    rec.title = rec.title or rec.form
    return rec


def _shape_degree(rec: Record, obj: dict[str, Any]) -> Record:
    rec.org = _leaf(obj, "nomeinstituicao", "nomeinstituicaograd", "nomeinstituicaodout")
    rec.org_code = _leaf(obj, "codigoinstituicao")
    rec.title = _leaf(obj, "titulodadissertacaotese", "titulodotrabalhodeconclusaodecurso",
                      "titulodamonografia", "titulodotrabalho", "titulodaresidenciamedica",
                      "nomecurso")
    rec.year = _year(_leaf(obj, "anodeinicio"))
    rec.year_end = _year(_leaf(obj, "anodeconclusao", "anodeobtencaodotitulo"))
    rec.status = {"CONCLUIDO": "concluded", "EM_ANDAMENTO": "ongoing", "INCOMPLETO": "incomplete"}.get(
        _leaf(obj, "statusdocurso").upper(), _leaf(obj, "statusdocurso").lower())
    rec.counterpart = _leaf(obj, "nomecompletodoorientador", "nomedoorientador",
                            "nomeorientadordout", "nomeorientadorgrad")
    rec.counterpart_id = _leaf(obj, "numeroidorientador")
    rec.payload = {
        "course": _leaf(obj, "nomecurso"),
        "co_advisor": _leaf(obj, "nomedocoorientador"),
        "sandwich_advisor": _leaf(obj, "nomedoorientadorsanduiche"),
        "funded": _leaf(obj, "flagbolsa").upper() == "SIM",
        "agency": _leaf(obj, "nomeagencia", "nomedaagencia"),
        "kind": _leaf(obj, "tipodoutorado", "tipomestrado", "tipograduacao"),
    }
    if rec.counterpart:
        rec.people.append(Person(rec.counterpart, rec.counterpart_id, 1, "advisor"))
    if rec.payload["co_advisor"]:
        rec.people.append(Person(rec.payload["co_advisor"], "", 2, "advisor"))
    return rec


def _shape_training(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "nomecurso")
    rec.org = _leaf(obj, "nomeinstituicao")
    rec.year = _year(_leaf(obj, "anodeinicio"))
    rec.year_end = _year(_leaf(obj, "anodeconclusao"))
    rec.payload = {"hours": _leaf(obj, "cargahoraria"), "level": _leaf(obj, "nivel")}
    return rec


def _shape_award(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "nomedopremiooutitulo")
    rec.year = _year(_leaf(obj, "anodapremiacao"))
    rec.org = _leaf(obj, "nomedaentidadepromotora")
    return rec


def _shape_language(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "descricaodoidioma", "idioma")
    rec.payload = {k: _leaf(obj, f"proficienciade{k}") for k in ("leitura", "escrita", "fala", "compreensao")}
    return rec


def _shape_area(rec: Record, obj: dict[str, Any]) -> Record:
    levels = [_leaf(obj, level) for level in _AREA_LEVELS]
    levels = [l for l in levels if l]
    rec.title = levels[0] if levels else ""
    rec.areas = list(OrderedDict.fromkeys(levels))
    return rec


def _shape_committee(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "titulo")
    rec.year = _year(_leaf(obj, "ano"))
    rec.nature = _leaf(obj, "natureza", "tipo")
    rec.counterpart = _leaf(obj, "nomedocandidato")
    rec.org = _leaf(obj, "nomeinstituicao")
    rec.org_code = _leaf(obj, "codigoinstituicao")
    rec.payload = {"course": _leaf(obj, "nomecurso"), "country": _leaf(obj, "pais")}
    return _common(rec, obj)


def _shape_supervision(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "titulodotrabalho", "titulo")
    rec.year = _year(_leaf(obj, "ano"))
    rec.nature = _leaf(obj, "natureza")
    rec.counterpart = _leaf(obj, "nomedoorientando", "nomedoorientado")
    rec.counterpart_id = _leaf(obj, "numeroidorientando", "numeroidorientado")
    rec.org = _leaf(obj, "nomeinstituicao", "nomedainstituicao")
    rec.org_code = _leaf(obj, "codigoinstituicao")
    rec.payload = {
        "course": _leaf(obj, "nomecurso", "nomedocurso"),
        "funded": _leaf(obj, "flagbolsa").upper() == "SIM",
        "agency": _leaf(obj, "nomedaagencia"),
        "kind": _leaf(obj, "tipodeorientacaoconcluida", "tipodeorientacao"),
        "unit": _leaf(obj, "nomeorgao"),
    }
    if rec.counterpart:
        rec.people.append(Person(rec.counterpart, rec.counterpart_id, 1, "student"))
    return _common(rec, obj)


def _shape_event(rec: Record, obj: dict[str, Any]) -> Record:
    rec.title = _leaf(obj, "titulo")
    rec.year = _year(_leaf(obj, "ano"))
    rec.nature = _leaf(obj, "natureza")
    rec.venue = _leaf(obj, "nomedoevento")
    rec.org = _leaf(obj, "instituicaopromotora", "nomeinstituicao")
    rec.payload = {"participation": _leaf(obj, "formaparticipacao"),
                   "place": _leaf(obj, "localdoevento"), "city": _leaf(obj, "cidadedoevento")}
    return _common(rec, obj)


_SHAPERS: dict[str, Callable[[Record, dict[str, Any]], Record]] = {
    "work": _shape_work, "technical": _shape_technical, "project": _shape_project,
    "line": _shape_line, "career": _shape_career, "activity": _shape_activity,
    "degree": _shape_degree, "training": _shape_training, "award": _shape_award,
    "language": _shape_language, "area": _shape_area, "committee": _shape_committee,
    "supervision": _shape_supervision, "event": _shape_event,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def lattes_records(rows: Iterable[tuple[str, str, str]]) -> list[Record]:
    """Records from `(siape, path, value_text)` rows, in first-seen order.

    One pass, memory proportional to the number of records rather than leaves,
    and the appointment each project or activity happened under is recovered
    from its path so a project can say which employer it was done at.
    """
    buckets: "OrderedDict[tuple[str, int, str], dict[str, Any]]" = OrderedDict()
    meta: dict[tuple[str, int, str], tuple[Spec, str, str]] = {}
    for siape, path, value in rows:
        if value is None or value == "":
            continue
        segments = parse_path(path)
        hit = _find_spec(segments)
        if hit is None:
            continue
        spec, wildcard, index, consumed = hit
        rest = segments[consumed:]
        if not rest:
            continue
        # Activities and projects nest under an appointment; the key keeps the
        # whole prefix (appointment index included) so siblings stay apart.
        prefix = "$" + "".join(f"[{s}]" if isinstance(s, int) else f"['{s}']" for s in segments[:consumed])
        key = (siape, id(spec), prefix)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = buckets[key] = {}
            meta[key] = (spec, wildcard, prefix)
        _set(bucket, rest, value)

    out: list[Record] = []
    for key, obj in buckets.items():
        spec, wildcard, prefix = meta[key]
        form = spec.form
        if callable(form):
            form_name = form(wildcard, obj)
        elif form == "*":
            form_name = wildcard
        else:
            form_name = form
        if spec.family == "activity" and wildcard == "participacaoemprojeto":
            continue        # the wrapper around a project, not a duty in itself
        form_name = _slugish(form_name) or spec.family
        rec = Record(siape=key[0], family=spec.family, form=_FORM_ALIASES.get(form_name, form_name),
                     source=SOURCE, source_ref=prefix)
        rec = _SHAPERS[spec.family](rec, obj)
        if not rec.title and spec.family not in ("career",):
            continue
        if spec.family in ("project", "activity", "line"):
            rec.payload["appointment"] = _appointment_of(prefix)
        out.append(rec)
    return out


_APPOINTMENT = re.compile(r"^\$\['dadosgerais'\]\['atuacoesprofissionais'\]\['atuacaoprofissional'\]\[(\d+)\]")


def _appointment_of(prefix: str) -> int | None:
    m = _APPOINTMENT.match(prefix)
    return int(m.group(1)) if m else None


def attach_employers(records: list[Record]) -> None:
    """Give projects, lines and activities the employer of the appointment
    they sit under, now that every record for the person is known."""
    employers: dict[tuple[str, int], str] = {}
    for rec in records:
        if rec.family == "career":
            idx = _appointment_of(rec.source_ref)
            if idx is not None:
                employers[(rec.siape, idx)] = rec.org
    for rec in records:
        if rec.family in ("project", "line", "activity") and not rec.org:
            idx = rec.payload.get("appointment")
            if idx is not None:
                rec.org = employers.get((rec.siape, idx), "")
