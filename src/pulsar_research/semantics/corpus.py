"""The PULSAR semantic corpus.

Three ideas drive this module.

1. **Atoms, not mega-documents.** A professor is a *portfolio* of atomic research
   evidence (one project, one article, one work plan), never one concatenated
   blob. Ranking a portfolio item preserves *why* a professor matched.
2. **Field-aware views.** A research record is not one bag of words. Domain
   ("what is it about"), methods ("how is it done") and skills ("what would the
   student do") are separate views over the same record.
3. **One fingerprint per input set.** Everything that can change a semantic
   space is hashed here, so staleness is detectable rather than silent.

The atom corpus is deliberately much larger than the 187 opportunities: Lattes
projects, publications, orientations, research lines and current public SIGAA
projects all become atoms, which is the training material a corpus-native
distributional model actually needs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from ..db import Database
from .normalize import clean_text, join_unique, scrub_citations

# ---------------------------------------------------------------------------
# Atom taxonomy
# ---------------------------------------------------------------------------

#: How specific a piece of evidence is as an explanation. "Medicina" is a true
#: statement about a professor and a useless reason to contact them; a funded
#: 2026 work plan is both true and actionable. Used to rank displayed evidence,
#: never to silently suppress a match.
SPECIFICITY: dict[str, float] = {
    "opportunity": 1.00,
    "sigaa_project": 0.92,
    "lattes_project": 0.90,
    "research_line": 0.80,
    "article": 0.78,
    "orientation": 0.70,
    "book_chapter": 0.68,
    "conference": 0.55,
    "technical": 0.55,
    "sigaa_extension": 0.55,
    "profile_summary": 0.45,
    "knowledge_area": 0.12,
}

#: Evidence that describes *what the professor can supervise right now* rather
#: than where they have been. Drives the CURRENT vs TRAJECTORY split.
CURRENT_KINDS = frozenset({"opportunity", "sigaa_project", "sigaa_extension", "research_line"})
# How far back a *started* record still counts as ongoing supervision capacity.
CURRENT_WINDOW_YEARS = 2

_PROJECT_CODE = re.compile(r"^[A-Z]{2,}\d{3,}-(\d{4})$")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


@dataclass(slots=True)
class Atom:
    """One indivisible piece of research evidence attributable to a professor."""

    atom_id: str
    siape: str
    kind: str
    title: str
    body: str = ""
    keywords: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    year: int | None = None
    year_end: int | None = None
    is_active: bool = False
    source_ref: str = ""

    @property
    def specificity(self) -> float:
        return SPECIFICITY.get(self.kind, 0.5)

    @property
    def is_current(self) -> bool:
        """Is this evidence that the professor is doing this work *now*?

        Kind alone is not currency. The public SIGAA project tab lists projects
        back to 2014, and a Lattes project with no end year is "ongoing" only in
        the sense that nobody updated it. Scoping by kind made CURRENT
        OPPORTUNITY FIT a near-duplicate of RESEARCH TRAJECTORY FIT (Spearman
        0.96), which defeated the point of separating them.
        """
        if self.kind == "opportunity":
            return True  # an open call is current by definition
        if self.kind not in CURRENT_KINDS and not self.is_active:
            return False
        today = datetime.now(timezone.utc).year
        if self.year_end is not None:
            return self.year_end >= today
        if self.year is not None:
            return self.year >= today - CURRENT_WINDOW_YEARS
        # No dates at all: trust an explicit active flag on a current-capable kind.
        return bool(self.is_active) and self.kind in CURRENT_KINDS

    @property
    def text(self) -> str:
        """The analytical view of this atom (title first, then body/keywords)."""
        return join_unique([self.title, self.body, "; ".join(self.keywords)], limit=8000)

    @property
    def label(self) -> str:
        """Human-facing one-line description used in evidence tables."""
        return clean_text(self.title)[:300]


# ---------------------------------------------------------------------------
# Opportunity / project views
# ---------------------------------------------------------------------------


def opportunity_views(row: Mapping[str, Any], *, topic_clean: bool = False) -> dict[str, str]:
    """Field-aware semantic views of one SIGAA work plan.

    Field labels are never concatenated into the text; that would manufacture
    bigrams like ``saude medicina`` that mean nothing.
    """
    cleaner = scrub_citations if topic_clean else clean_text
    return {
        "domain_title": join_unique([row.get("project_title"), row.get("plan_title")], sep=" — "),
        "domain_area": join_unique([row.get("large_area"), row.get("area")], sep="; "),
        "domain_intro": cleaner(row.get("introduction_justification")),
        "domain_objectives": cleaner(row.get("objectives")),
        "method_title": clean_text(row.get("plan_title") or row.get("project_title")),
        "method_objectives": cleaner(row.get("objectives")),
        "method_methodology": cleaner(row.get("methodology")),
        "skill_title": clean_text(row.get("plan_title")),
        "skill_skills": cleaner(row.get("acquired_skills")),
        "skill_methodology": cleaner(row.get("methodology")),
    }


FACET_FIELDS: dict[str, tuple[str, ...]] = {
    "domain": ("domain_title", "domain_area", "domain_intro", "domain_objectives"),
    "methods": ("method_title", "method_methodology"),
    "skills": ("skill_title", "skill_skills"),
}

#: BM25F field weights per facet. Titles are short and high-precision; narrative
#: introduction text is long and full of shared background, so it is damped.
FACET_BM25_WEIGHTS: dict[str, dict[str, float]] = {
    "domain": {"domain_title": 3.0, "domain_area": 1.2, "domain_intro": 0.65, "domain_objectives": 1.5},
    "methods": {"method_title": 1.2, "method_methodology": 3.0},
    "skills": {"skill_title": 0.6, "skill_skills": 3.5},
}


def opportunity_document(row: Mapping[str, Any]) -> str:
    """Whole-record analytical document (all facets), used for full-text views."""
    v = opportunity_views(row)
    return join_unique(
        [v["domain_title"], v["domain_area"], v["domain_intro"], v["domain_objectives"],
         v["method_methodology"], v["skill_skills"]],
        sep="\n\n",
    )


def project_records(opportunities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate sibling work plans into one independent project document.

    Sibling plans under one project share huge copied paragraphs. Modelling them
    as independent documents inflates the corpus with near-duplicates and makes
    every topic model rediscover the same project several times.
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in opportunities:
        key = str(row.get("project_code") or row.get("project_title") or row.get("id_opportunity") or "")
        grouped.setdefault(key, []).append(row)

    projects: list[dict[str, Any]] = []
    for key in sorted(grouped):
        rows = grouped[key]
        first = rows[0]
        rec: dict[str, Any] = {
            "project_code": str(first.get("project_code") or key),
            "project_title": clean_text(first.get("project_title")),
            "professor_siape": str(first.get("professor_siape") or ""),
            "professor_name": clean_text(first.get("professor_name")),
            "center": clean_text(first.get("center")),
            "area": clean_text(first.get("area")),
            "large_area": clean_text(first.get("large_area")),
            "opportunity_ids": [str(r.get("id_opportunity") or "") for r in rows],
            "funded_slots": int(sum(int(r.get("funded_slots") or 0) for r in rows)),
        }
        rec["domain"] = join_unique(
            [first.get("project_title")]
            + [r.get("plan_title") for r in rows]
            + [r.get("large_area") for r in rows]
            + [r.get("area") for r in rows]
            + [scrub_citations(r.get("introduction_justification")) for r in rows]
            + [scrub_citations(r.get("objectives")) for r in rows],
            limit=50000,
        )
        rec["methods"] = join_unique(
            [r.get("plan_title") for r in rows]
            + [scrub_citations(r.get("methodology")) for r in rows],
            limit=50000,
        )
        rec["skills"] = join_unique(
            [scrub_citations(r.get("acquired_skills")) for r in rows],
            limit=30000,
        )
        rec["document"] = join_unique([rec["domain"], rec["methods"], rec["skills"]], sep="\n\n")
        projects.append(rec)
    return projects


# ---------------------------------------------------------------------------
# Lattes atom extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LattesAtomSpec:
    """Declarative mapping from a Lattes JSON path family to an atom kind.

    ``prefix`` matches the path up to and including the record's array index, so
    every leaf of one record (title, year, keywords) is grouped together. Roles
    are matched on the *leaf* key name, which keeps the spec readable and immune
    to the deeply nested container names CNPq uses.
    """

    kind: str
    prefix: re.Pattern[str]
    title_leaves: tuple[str, ...]
    body_leaves: tuple[str, ...] = ()
    year_leaves: tuple[str, ...] = ("anoproducao",)
    year_end_leaves: tuple[str, ...] = ()
    keyword_prefixes: tuple[str, ...] = ("palavrachave",)
    area_leaves: tuple[str, ...] = ("nomedaareadoconhecimento",)
    active_leaves: tuple[str, ...] = ()
    min_title_chars: int = 12


def _p(expr: str) -> re.Pattern[str]:
    """Compile a Lattes path prefix written with ``.`` for ``['...']`` segments."""
    parts = expr.split(".")
    out = [r"^\$"]
    for part in parts:
        if part.endswith("[]"):
            out.append(r"\['" + re.escape(part[:-2]) + r"'\]\[\d+\]")
        else:
            out.append(r"\['" + re.escape(part) + r"'\]")
    return re.compile("".join(out))


_PROJECT_PREFIX = "dadosgerais.atuacoesprofissionais.atuacaoprofissional[].atividadesdeparticipacaoemprojeto.participacaoemprojeto[].projetodepesquisa[]"

LATTES_ATOM_SPECS: tuple[LattesAtomSpec, ...] = (
    LattesAtomSpec(
        kind="lattes_project",
        prefix=_p(_PROJECT_PREFIX),
        title_leaves=("nomedoprojeto",),
        body_leaves=("descricaodoprojeto",),
        year_leaves=("anoinicio",),
        year_end_leaves=("anofim",),
        active_leaves=("situacao",),
    ),
    LattesAtomSpec(
        kind="research_line",
        prefix=_p("dadosgerais.atuacoesprofissionais.atuacaoprofissional[].atividadesdepesquisaedesenvolvimento.pesquisaedesenvolvimento[].linhadepesquisa[]"),
        title_leaves=("titulodalinhadepesquisa",),
        body_leaves=("objetivoslinhadepesquisa",),
        year_leaves=(),
        active_leaves=("flaglinhadepesquisaativa",),
    ),
    LattesAtomSpec(
        kind="article",
        prefix=_p("producaobibliografica.artigospublicados.artigopublicado[]"),
        title_leaves=("nomeproducao",),
    ),
    LattesAtomSpec(
        kind="conference",
        prefix=_p("producaobibliografica.trabalhosemeventos.trabalhoemeventos[]"),
        title_leaves=("nomeproducao",),
    ),
    LattesAtomSpec(
        kind="book_chapter",
        prefix=_p("producaobibliografica.livrosecapitulos.capitulosdelivrospublicados.capitulodelivropublicado[]"),
        title_leaves=("nomeproducao",),
    ),
    LattesAtomSpec(
        kind="book_chapter",
        prefix=_p("producaobibliografica.livrosecapitulos.livrospublicadosouorganizados.livropublicadoouorganizado[]"),
        title_leaves=("nomeproducao",),
    ),
    LattesAtomSpec(
        kind="technical",
        prefix=_p("producaotecnica.trabalhotecnico[]"),
        title_leaves=("nomeproducao",),
    ),
    LattesAtomSpec(
        kind="orientation",
        prefix=_p("outraproducao.orientacoesconcluidas[].outrasorientacoesconcluidas[]"),
        title_leaves=("titulo",),
        year_leaves=("ano",),
    ),
    LattesAtomSpec(
        kind="orientation",
        prefix=_p("outraproducao.orientacoesconcluidas.orientacoesconcluidasparamestrado[]"),
        title_leaves=("titulo",),
        year_leaves=("ano",),
    ),
    LattesAtomSpec(
        kind="orientation",
        prefix=_p("outraproducao.orientacoesconcluidas.orientacoesconcluidasparadoutorado[]"),
        title_leaves=("titulo",),
        year_leaves=("ano",),
    ),
    LattesAtomSpec(
        kind="knowledge_area",
        prefix=_p("dadosgerais.areasdeatuacao.areadeatuacao[]"),
        title_leaves=("nomedaareadoconhecimento", "nomedaespecialidade", "nomedasubareadoconhecimento"),
        year_leaves=(),
        area_leaves=(),
        min_title_chars=3,
    ),
)

_LEAF = re.compile(r"\['([^']+)'\]$")


def _leaf_name(path: str) -> str:
    m = _LEAF.search(path)
    return m.group(1).lower() if m else ""


def _to_year(value: object) -> int | None:
    m = _YEAR.search(str(value or ""))
    if not m:
        return None
    year = int(m.group(1))
    return year if 1950 <= year <= datetime.now(timezone.utc).year + 3 else None


def _atom_id(*parts: object) -> str:
    payload = "|".join(str(p) for p in parts)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def lattes_atoms(rows: Iterable[tuple[str, str, str]]) -> list[Atom]:
    """Turn ``(siape, path, value)`` Lattes leaves into atoms.

    One pass, grouping leaves by ``(spec, matched record prefix)``. Rows must be
    sorted by ``(siape, path)`` for deterministic output.
    """
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for siape, path, value in rows:
        text = clean_text(value)
        if not text:
            continue
        lowered = path.lower()
        for spec in LATTES_ATOM_SPECS:
            match = spec.prefix.match(lowered)
            if not match:
                continue
            key = (str(siape), spec.kind, match.group(0))
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {"titles": [], "bodies": [], "keywords": [], "areas": [], "year": None,
                          "year_end": None, "active": False}
                buckets[key] = bucket
                order.append(key)
            leaf = _leaf_name(path)
            if leaf in spec.title_leaves:
                bucket["titles"].append(text)
            elif leaf in spec.body_leaves:
                bucket["bodies"].append(text)
            elif spec.year_leaves and leaf in spec.year_leaves:
                bucket["year"] = bucket["year"] or _to_year(text)
            elif spec.year_end_leaves and leaf in spec.year_end_leaves:
                bucket["year_end"] = bucket["year_end"] or _to_year(text)
            elif any(leaf.startswith(p) for p in spec.keyword_prefixes):
                bucket["keywords"].append(text)
            elif spec.area_leaves and leaf in spec.area_leaves:
                bucket["areas"].append(text)
            elif spec.active_leaves and leaf in spec.active_leaves:
                bucket["active"] = bucket["active"] or clean_text(text).upper() in {"SIM", "EM ANDAMENTO", "ATIVO"}
            break

    specs_by_kind = {spec.kind: spec for spec in LATTES_ATOM_SPECS}
    atoms: list[Atom] = []
    for key in order:
        siape, kind, prefix = key
        bucket = buckets[key]
        title = join_unique(bucket["titles"], sep=" ")
        spec = specs_by_kind[kind]
        if len(title) < spec.min_title_chars:
            continue
        atoms.append(
            Atom(
                atom_id=_atom_id(siape, kind, prefix),
                siape=siape,
                kind=kind,
                title=title,
                body=scrub_citations(join_unique(bucket["bodies"], sep=" ")),
                keywords=tuple(dict.fromkeys(bucket["keywords"])),
                areas=tuple(dict.fromkeys(bucket["areas"])),
                year=bucket["year"],
                year_end=bucket["year_end"],
                is_active=bool(bucket["active"]) or (bucket["year_end"] is None and kind == "lattes_project" and bucket["year"] is not None),
                source_ref=prefix,
            )
        )
    return atoms


def public_sigaa_atoms(rows: Iterable[tuple[str, str, str]]) -> list[Atom]:
    """Atoms from the public SIGAA research/extension tables.

    These rows are ``(siape, page_type, cell_texts_json)``. The tables interleave
    header rows, bare year rows and data rows, so we key off the UFAL project
    code pattern (``PVCB5359-2026``) rather than positional parsing.
    """
    atoms: list[Atom] = []
    for siape, page_type, cells_json in rows:
        try:
            cells = [clean_text(c) for c in json.loads(cells_json or "[]")]
        except Exception:
            continue
        if len(cells) < 2:
            continue
        code = cells[0]
        m = _PROJECT_CODE.match(code)
        if not m:
            continue
        title = cells[1]
        if len(title) < 8:
            continue
        area = cells[2] if len(cells) > 2 else ""
        year = int(m.group(1))
        kind = "sigaa_project" if page_type == "pesquisa" else "sigaa_extension"
        atoms.append(
            Atom(
                atom_id=_atom_id(siape, kind, code),
                siape=str(siape),
                kind=kind,
                title=title,
                areas=(area,) if area else (),
                year=year,
                is_active=year >= datetime.now(timezone.utc).year,
                source_ref=code,
            )
        )
    return atoms


# ---------------------------------------------------------------------------
# Corpus assembly
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SemanticCorpus:
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    professors: list[dict[str, Any]] = field(default_factory=list)
    atoms: list[Atom] = field(default_factory=list)

    # -- convenience indexes -------------------------------------------------
    @property
    def opportunity_ids(self) -> list[str]:
        return [str(r.get("id_opportunity") or "") for r in self.opportunities]

    @property
    def project_codes(self) -> list[str]:
        return [str(p["project_code"]) for p in self.projects]

    @property
    def siapes(self) -> list[str]:
        return [str(p["siape"]) for p in self.professors]

    def atoms_by_siape(self) -> dict[str, list[Atom]]:
        out: dict[str, list[Atom]] = {}
        for atom in self.atoms:
            out.setdefault(atom.siape, []).append(atom)
        return out

    def training_documents(self) -> list[str]:
        """The full corpus-native training material for distributional models.

        Deliberately much larger than the opportunity table: PPMI/LSA quality is
        bounded by how much domain language the corpus actually contains.
        """
        docs = [p["document"] for p in self.projects]
        docs += [opportunity_document(r) for r in self.opportunities]
        docs += [a.text for a in self.atoms]
        return [d for d in docs if len(d) >= 20]

    def fingerprint(self) -> str:
        """Hash of every input that can change the semantic geometry.

        Includes professor/Lattes atoms, which the previous engine ignored — a
        professor corpus refresh must invalidate the professor semantic space.
        """
        h = hashlib.sha256()
        for row in self.opportunities:
            h.update(json.dumps({k: str(row.get(k) or "") for k in (
                "id_opportunity", "project_code", "project_title", "plan_title", "professor_siape",
                "large_area", "area", "introduction_justification", "objectives", "methodology",
                "acquired_skills")}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            h.update(b"\x1e")
        h.update(b"\x1d")
        for atom in self.atoms:
            h.update(f"{atom.siape}\x1f{atom.kind}\x1f{atom.atom_id}\x1f{atom.title}\x1f{atom.body}".encode("utf-8"))
            h.update(b"\x1e")
        h.update(b"\x1d")
        for prof in self.professors:
            h.update(f"{prof['siape']}\x1f{prof.get('canonical_name') or ''}\x1f{prof.get('profile_summary') or ''}".encode("utf-8"))
            h.update(b"\x1e")
        return h.hexdigest()


def _table_exists(con, name: str) -> bool:
    return bool(con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name=?", [name]
    ).fetchone()[0])


def load_corpus(db: Database) -> SemanticCorpus:
    """Read every semantic input from DuckDB in a deterministic order."""
    with db.connect(read_only=True) as con:
        cols = [d[0] for d in con.execute("SELECT * FROM opportunities LIMIT 0").description]
        opportunities = [
            dict(zip(cols, r))
            for r in con.execute("SELECT * FROM opportunities ORDER BY id_opportunity").fetchall()
        ]
        pcols = [d[0] for d in con.execute("SELECT * FROM professors LIMIT 0").description]
        professors = [
            dict(zip(pcols, r))
            for r in con.execute("SELECT * FROM professors ORDER BY siape").fetchall()
        ]

        atoms: list[Atom] = []
        if _table_exists(con, "sigaa_public_lattes_flat"):
            leaf_filter = " OR ".join(
                f"LOWER(path) LIKE '%{leaf}%'"
                for leaf in sorted({
                    leaf
                    for spec in LATTES_ATOM_SPECS
                    for leaf in (
                        spec.title_leaves + spec.body_leaves + spec.year_leaves
                        + spec.year_end_leaves + spec.area_leaves + spec.active_leaves
                        + ("palavrachave",)
                    )
                })
            )
            rows = con.execute(
                f"""
                SELECT siape, path, value_text
                FROM sigaa_public_lattes_flat
                WHERE value_type='string' AND COALESCE(value_text,'')<>'' AND ({leaf_filter})
                ORDER BY siape, path
                """
            ).fetchall()
            atoms.extend(lattes_atoms(rows))
        if _table_exists(con, "sigaa_public_table_rows"):
            rows = con.execute(
                """
                SELECT siape, page_type, cell_texts_json
                FROM sigaa_public_table_rows
                WHERE page_type IN ('pesquisa','extensao')
                ORDER BY siape, page_type, table_index, row_index
                """
            ).fetchall()
            atoms.extend(public_sigaa_atoms(rows))

    # Opportunity atoms come from the canonical table, not the scraper archive.
    for row in opportunities:
        siape = str(row.get("professor_siape") or "")
        if not siape:
            continue
        atoms.append(
            Atom(
                atom_id=_atom_id(siape, "opportunity", row.get("id_opportunity")),
                siape=siape,
                kind="opportunity",
                title=clean_text(row.get("plan_title") or row.get("project_title")),
                body=join_unique([
                    clean_text(row.get("project_title")),
                    scrub_citations(row.get("objectives")),
                    scrub_citations(row.get("introduction_justification")),
                ], limit=8000),
                areas=tuple(x for x in [clean_text(row.get("area")), clean_text(row.get("large_area"))] if x),
                year=_to_year(row.get("edital")) or _to_year(row.get("discovered_at")),
                is_active=True,
                source_ref=str(row.get("id_opportunity") or ""),
            )
        )

    for prof in professors:
        summary = clean_text(prof.get("profile_summary"))
        if len(summary) >= 40:
            atoms.append(
                Atom(
                    atom_id=_atom_id(prof["siape"], "profile_summary"),
                    siape=str(prof["siape"]),
                    kind="profile_summary",
                    title=clean_text(prof.get("canonical_name")),
                    body=scrub_citations(summary),
                    source_ref="lattes_resumocv",
                )
            )

    # Deterministic order + duplicate suppression. Professors legitimately list
    # the same project under several professional affiliations.
    seen: set[tuple[str, str]] = set()
    unique: list[Atom] = []
    for atom in sorted(atoms, key=lambda a: (a.siape, -a.specificity, a.kind, a.atom_id)):
        key = (atom.siape, atom.text.casefold()[:400])
        if key in seen:
            continue
        seen.add(key)
        unique.append(atom)

    return SemanticCorpus(
        opportunities=opportunities,
        projects=project_records(opportunities),
        professors=professors,
        atoms=unique,
    )
